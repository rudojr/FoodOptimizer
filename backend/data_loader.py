"""
data_loader.py – Đọc raw.xlsx và trích xuất 100 món mẫu có phân loại
"""
from __future__ import annotations
import os
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional
import openpyxl

from models import Dish

logger = logging.getLogger(__name__)

# ─── Cấu hình phân loại ────────────────────────────────────────────────────────

# Rau ăn lá (leaf vegetables)
_LEAF_KEYWORDS = [
    'bắp cải', 'cải thảo', 'cải chíp', 'cải xanh',
    'mồng tơi', 'rau dền', 'rau cải', 'cải cúc',
    'rau ngót', 'rau muống', 'dưa cải', 'dưa chua',
]

# Rau củ quả (root/fruit vegetables)
_ROOT_FRUIT_KEYWORDS = [
    'bầu', 'bí xanh', 'bí đỏ', 'củ quả', 'khoai tây',
    'su hào', 'su su', 'cà rốt', 'củ cải', 'ngô',
    'đỗ quả', 'đậu', 'mướp', 'cà chua',
]

# Danh sách món ưu tiên (rule 5 + rule 4: chuối)
_PREFERRED_KEYWORDS = [
    'xá xíu', 'gà viên', 'chiên lắc phomai', 'chiên lắc phô mai',
    'nem lụi', 'sốt chua ngọt', 'xúc xích', 'viên ngũ sắc',
    'chuối',
]

# Danh sách món hạn chế (rule 6 + rule 4: dưa hấu)
_LESS_PREFERRED_KEYWORDS = [
    'cải cúc', 'rang nấm hương', 'mộc nhĩ',
    'gà sốt chua ngọt', 'rang cháy cạnh',
    'chân giò hấp', 'nấu giả cầy', 'rau muống',
    'dưa hấu',
]

# Số lượng tối đa mỗi danh mục (tổng ~100)
_CATEGORY_LIMITS: Dict[str, int] = {
    'CO': 5,
    'C':  18,
    'R':  22,
    'M':  45,
    'Q':  10,
}


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Chuyển về lowercase để so sánh."""
    return s.lower().strip() if s else ''


def _contains_any(text: str, keywords: List[str]) -> bool:
    t = _norm(text)
    return any(k in t for k in keywords)


def _classify_category(code: str, name: str) -> Optional[str]:
    """Phân loại danh mục từ tên món."""
    n = _norm(name)

    # Bỏ qua: AS (ăn sáng)
    if n.startswith('as ') or name.upper().startswith('AS '):
        return None

    # CO – Cơm (check trước)
    if n.startswith('cơm '):
        return 'CO'

    # C – Canh
    if 'canh' in n:
        return 'C'

    # Q – Quà chiều
    if (name.upper().startswith('QC ') or
            n.startswith('qc ') or
            'sữa' in n or
            'bánh + sữa' in n or
            'xôi' in n or
            'chè ' in n or
            'phồng tôm' in n):
        return 'Q'

    # M – Món mặn: check TRƯỚC R để tránh bắt nhầm
    # Nếu tên có từ khóa thịt/cá/gà/trứng... → M
    _M_PATTERNS = [
        'thịt ', 'gà ', 'cá ', 'cánh gà', 'đùi gà',
        'tôm ', 'trứng', 'giò ', 'chả', 'xúc xích',
        'sườn', ' bò ', 'mọc', 'ruốc', 'nem ', 'lươn',
        'viên ', 'bò,', 'cá,',
    ]
    if any(p in n for p in _M_PATTERNS):
        return 'M'
    # Thêm check riêng cho tên kết thúc bằng từ khóa thịt (không cần dấu cách sau)
    _M_SUFFIXES = ['thịt hs', 'gà hs', 'trứng hs']
    if any(n.endswith(s) for s in _M_SUFFIXES):
        return 'M'

    # R – Rau / đậu phụ (KHÔNG chứa thịt/cá/gà làm thành phần chính)
    # Dùng explicit patterns cụ thể hơn
    _R_PATTERNS = [
        'bắp cải xào', 'bắp cải luộc',
        'cải thảo xào', 'cải thảo luộc',
        'cải chíp xào', 'cải chíp luộc',
        'cải xanh xào', 'cải xanh luộc',
        'rau cải xào', 'rau cải luộc',
        'rau ngót xào',
        'bầu xào', 'bầu luộc',
        'bí xanh xào', 'bí xanh luộc',
        'bí đỏ xào', 'bí đỏ luộc',
        'khoai tây xào', 'khoai tây luộc',
        'su hào xào', 'su hào luộc', 'su hào, cà rốt',
        'đỗ quả xào', 'đỗ quả luộc',
        'đậu rán', 'đậu sốt', 'đậu phụ sốt', 'đậu phụ kho',
        'đậu chiên', 'đậu rim', 'đậu sốt chua ngọt',
        'đậu phụ viên',
        'nấm xào',
        'dưa góp',
        'củ quả luộc', 'củ cải xào', 'củ cải cà rốt',
    ]
    if any(p in n for p in _R_PATTERNS):
        return 'R'

    return None


def _get_veg_type(name: str) -> Optional[str]:
    n = _norm(name)
    if _contains_any(n, _LEAF_KEYWORDS):
        return 'leaf'
    if _contains_any(n, _ROOT_FRUIT_KEYWORDS):
        return 'root_fruit'
    # Mặc định: đậu phụ, nấm → coi là root_fruit (không phải rau ăn lá)
    return 'root_fruit'


def _get_soup_type(name: str) -> Optional[str]:
    n = _norm(name)
    # Thêm từ khóa leaf cho canh chua (chua dưa là rau lá)
    if _contains_any(n, _LEAF_KEYWORDS) or 'chua' in n:
        return 'leaf'
    if _contains_any(n, _ROOT_FRUIT_KEYWORDS):
        return 'root_fruit'
    # None = không xác định → không áp ràng buộc veg-soup
    return None


def _build_dish(code: str, name: str,
                ingredient_codes: List[str],
                category: str) -> Dish:
    """Tạo đối tượng Dish từ metadata."""
    n = _norm(name)
    ing = [c.upper() for c in ingredient_codes]

    is_fried   = any(w in n for w in ['chiên', 'rán', ' xù', 'chiên xù', 'rán giòn'])
    is_vien    = 'viên' in n
    has_fish   = ('cá ' in n or 'cá\n' in n or n.startswith('cá')
                  or any(c.startswith('CA0') or c.startswith('CA1') for c in ing))
    has_shrimp = ('tôm' in n
                  or any(c.startswith('TOM') for c in ing))
    has_egg    = ('trứng' in n
                  or any(c.startswith('TRUNG') for c in ing))
    has_beef   = ('bò' in n
                  or any(c.startswith('BO') and not c.startswith('BONG') for c in ing))
    has_basa   = 'basa' in n
    has_milk   = any(c.startswith('SUA') for c in ing)
    is_com_rang = category == 'CO' and 'rang' in n
    is_com_ga   = category == 'CO' and ('gà' in n or 'ga' in n)

    veg_type  = _get_veg_type(name) if category == 'R' else None
    soup_type = _get_soup_type(name) if category == 'C' else None

    preferred      = _contains_any(name, _PREFERRED_KEYWORDS)
    less_preferred = _contains_any(name, _LESS_PREFERRED_KEYWORDS)

    score = -5 if less_preferred else (10 if preferred else 1)

    return Dish(
        code=code,
        name=name,
        category=category,
        is_fried=is_fried,
        is_vien=is_vien,
        has_fish=has_fish,
        has_shrimp=has_shrimp,
        has_egg=has_egg,
        has_beef=has_beef,
        has_basa=has_basa,
        has_milk=has_milk,
        is_com_rang=is_com_rang,
        is_com_ga=is_com_ga,
        veg_type=veg_type,
        soup_type=soup_type,
        preferred=preferred,
        less_preferred=less_preferred,
        preference_score=score,
    )


def load_dishes_from_excel(
    excel_path: str | Path,
    sample_size: int = 100,
) -> Dict[str, List[Dish]]:
    logger.info("Đang đọc %s ...", excel_path)
    wb = openpyxl.load_workbook(str(excel_path), read_only=True, data_only=True)
    ws = wb.active

    menu_map: Dict[str, dict] = {}  # code → {name, ingredient_codes}
    for row in ws.iter_rows(min_row=2, values_only=True):
        code, name, ing_code = row[0], row[1], row[2]
        if not code or not name:
            continue
        code = str(code).strip()
        name = str(name).strip()
        if code not in menu_map:
            menu_map[code] = {'name': name, 'ings': set()}
        if ing_code:
            menu_map[code]['ings'].add(str(ing_code).strip())

    wb.close()
    logger.info("Đọc xong. Tổng menu codes: %d", len(menu_map))

    # Pass 2: Phân loại và lọc
    buckets: Dict[str, List[Dish]] = {cat: [] for cat in _CATEGORY_LIMITS}

    for code, data in menu_map.items():
        name = data['name']
        ings = list(data['ings'])

        # Bỏ qua rau muống (theo quy định)
        if 'rau muống' in _norm(name):
            continue
        # Bỏ qua canh chua rau muống
        if 'chua rau muống' in _norm(name):
            continue

        cat = _classify_category(code, name)
        if cat is None or cat not in buckets:
            continue

        dish = _build_dish(code, name, ings, cat)
        buckets[cat].append(dish)

    # Pass 3: Chọn mẫu đại diện từ mỗi bucket
    result: Dict[str, List[Dish]] = {}
    total = 0
    for cat, limit in _CATEGORY_LIMITS.items():
        dishes = buckets[cat]
        dishes.sort(key=lambda d: (-d.preference_score, d.code))
        selected = dishes[:limit]
        result[cat] = selected
        total += len(selected)
        logger.info("  %s: %d món (từ %d)", cat, len(selected), len(dishes))

    logger.info("Tổng số món mẫu: %d", total)
    return result


def get_data_stats(dishes: Dict[str, List[Dish]]) -> dict:
    """Thống kê dữ liệu đã load."""
    total = sum(len(v) for v in dishes.values())
    return {
        'total': total,
        'by_category': {cat: len(lst) for cat, lst in dishes.items()},
        'preferred_count': sum(
            1 for lst in dishes.values() for d in lst if d.preferred
        ),
        'fried_count': sum(
            1 for lst in dishes.values() for d in lst if d.is_fried
        ),
        'vien_count': sum(
            1 for lst in dishes.values() for d in lst if d.is_vien
        ),
    }
