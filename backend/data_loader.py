"""
data_loader.py – Đọc raw.xlsx và trích xuất món ăn có phân loại đầy đủ
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Dict, List, Optional
import openpyxl

from models import Dish

logger = logging.getLogger(__name__)

# ─── Từ khóa phân loại rau ─────────────────────────────────────────────────────

_LEAF_KEYWORDS = [
    'bắp cải', 'cải thảo', 'cải chíp', 'cải xanh',
    'mồng tơi', 'rau dền', 'rau cải', 'cải cúc',
    'rau ngót', 'rau muống', 'dưa cải', 'dưa chua',
]

_ROOT_FRUIT_KEYWORDS = [
    'bầu', 'bí xanh', 'bí đỏ', 'củ quả', 'khoai tây',
    'su hào', 'su su', 'cà rốt', 'củ cải', 'ngô',
    'đỗ quả', 'đậu', 'mướp', 'cà chua',
]

# Rau củ cần gọt vỏ nhiều (rule II.5)
_HEAVY_PREP_KEYWORDS = ['khoai tây', 'su hào', 'củ cải', 'cà rốt', 'su su', 'củ quả', 'ngô']

# Rau cải họ (kỵ thịt gà)
_CABBAGE_KEYWORDS = ['bắp cải', 'cải thảo', 'cải chíp', 'cải xanh', 'cải ngọt', 'cải cúc']

_PREFERRED_KEYWORDS = [
    'xá xíu', 'gà viên', 'chiên lắc phomai', 'chiên lắc phô mai',
    'nem lụi', 'sốt chua ngọt', 'viên ngũ sắc', 'chuối',
]

_LESS_PREFERRED_KEYWORDS = [
    'rang nấm hương', 'mộc nhĩ',
    'gà sốt chua ngọt', 'rang cháy cạnh',
    'chân giò hấp', 'nấu giả cầy', 'rau muống',
    'dưa hấu', 'solite', 'cải cúc',
]

_CATEGORY_LIMITS: Dict[str, int] = {
    'CO': 10,
    'C':  36,
    'R':  44,
    'M':  90,
    'Q':  20,
}


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return s.lower().strip() if s else ''


def _contains_any(text: str, keywords: List[str]) -> bool:
    t = _norm(text)
    return any(k in t for k in keywords)


def _classify_category(code: str, name: str) -> Optional[str]:
    n = _norm(name)

    if n.startswith('as ') or name.upper().startswith('AS '):
        return None

    if n.startswith('cơm '):
        return 'CO'

    if 'canh' in n:
        return 'C'

    if (name.upper().startswith('QC ') or
            n.startswith('qc ') or
            'sữa' in n or
            'bánh + sữa' in n or
            'xôi' in n or
            'chè ' in n or
            'phồng tôm' in n):
        return 'Q'

    _M_PATTERNS = [
        'thịt ', 'gà ', 'cá ', 'cánh gà', 'đùi gà',
        'tôm ', 'trứng', 'giò ', 'chả', 'xúc xích',
        'sườn', ' bò ', 'mọc', 'ruốc', 'nem ', 'lươn',
        'viên ', 'bò,', 'cá,',
    ]
    if any(p in n for p in _M_PATTERNS):
        return 'M'
    _M_SUFFIXES = ['thịt hs', 'gà hs', 'trứng hs']
    if any(n.endswith(s) for s in _M_SUFFIXES):
        return 'M'

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
    return 'root_fruit'


def _get_soup_type(name: str) -> Optional[str]:
    n = _norm(name)
    if _contains_any(n, _LEAF_KEYWORDS) or 'chua' in n:
        return 'leaf'
    if _contains_any(n, _ROOT_FRUIT_KEYWORDS):
        return 'root_fruit'
    return None


def _build_dish(code: str, name: str,
                ingredient_codes: List[str],
                category: str) -> Dish:
    n = _norm(name)
    ing = [c.upper() for c in ingredient_codes]

    # ── Cách chế biến ──────────────────────────────────────────────────────────
    is_fried = any(w in n for w in ['chiên', 'rán', ' xù', 'chiên xù', 'rán giòn'])
    is_vien  = 'viên' in n

    # ── Protein / nguyên liệu chính ────────────────────────────────────────────
    has_fish   = ('cá ' in n or 'cá\n' in n or n.startswith('cá')
                  or any(c.startswith('CA0') or c.startswith('CA1') for c in ing))
    has_shrimp = ('tôm' in n or any(c.startswith('TOM') for c in ing))
    has_egg    = ('trứng' in n or any(c.startswith('TRUNG') for c in ing))
    has_beef   = ('bò' in n or any(c.startswith('BO') and not c.startswith('BONG') for c in ing))
    has_chicken= (category == 'M' and (
                  'gà ' in n or 'gà,' in n or n.startswith('gà')
                  or 'cánh gà' in n or 'đùi gà' in n))
    has_pork   = (category == 'M' and
                  not has_chicken and not has_beef and not has_fish and not has_shrimp and
                  ('thịt' in n or 'sườn' in n or 'ruốc' in n or 'giò' in n or 'chả' in n))
    has_tofu   = 'đậu phụ' in n
    has_basa   = 'basa' in n
    has_milk   = any(c.startswith('SUA') for c in ing) or 'sữa' in n

    # ── Giò / chả / xúc xích / chân giò ───────────────────────────────────────
    is_gio     = 'giò' in n and not 'chân giò' in n
    is_cha     = 'chả' in n
    is_xuc_xich= 'xúc xích' in n
    is_chan_gio = 'chân giò' in n

    # ── CO ─────────────────────────────────────────────────────────────────────
    is_com_rang      = category == 'CO' and 'rang' in n
    is_com_ga        = category == 'CO' and ('gà' in n or 'ga' in n)
    is_com_cai_thien = is_com_rang or is_com_ga

    # ── Rau (R) ────────────────────────────────────────────────────────────────
    veg_type = _get_veg_type(name) if category == 'R' else None

    veg_cooking_method: Optional[str] = None
    if category == 'R':
        if 'xào' in n:
            veg_cooking_method = 'stir_fry'
        elif 'luộc' in n or 'rán' in n:
            veg_cooking_method = 'boil'

    needs_heavy_prep = (category == 'R' and _contains_any(n, _HEAVY_PREP_KEYWORDS))
    is_cabbage       = (category == 'R' and _contains_any(n, _CABBAGE_KEYWORDS))

    # ── Canh (C) ───────────────────────────────────────────────────────────────
    soup_type     = _get_soup_type(name) if category == 'C' else None
    is_bone_soup  = category == 'C' and 'xương' in n

    # ── Quà chiều (Q) ──────────────────────────────────────────────────────────
    _FRUIT_KW  = ['hoa quả', 'chuối', 'cam ', 'táo', 'bưởi', 'xoài', 'thanh long', 'ổi', 'dưa']
    _CAKE_KW   = ['bánh']
    _MILK_KW   = ['sữa', 'yaourt', 'sữa chua', 'sữa tươi']

    is_fruit_snack = category == 'Q' and _contains_any(n, _FRUIT_KW)
    is_cake_snack  = category == 'Q' and _contains_any(n, _CAKE_KW)
    is_milk_snack  = category == 'Q' and _contains_any(n, _MILK_KW)
    is_banana      = 'chuối' in n
    is_solite      = 'solite' in n
    has_watermelon = 'dưa hấu' in n

    # ── Độ ưa thích ────────────────────────────────────────────────────────────
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
        has_pork=has_pork,
        has_chicken=has_chicken,
        has_tofu=has_tofu,
        has_basa=has_basa,
        has_milk=has_milk,
        is_gio=is_gio,
        is_cha=is_cha,
        is_xuc_xich=is_xuc_xich,
        is_chan_gio=is_chan_gio,
        is_com_rang=is_com_rang,
        is_com_ga=is_com_ga,
        is_com_cai_thien=is_com_cai_thien,
        veg_type=veg_type,
        veg_cooking_method=veg_cooking_method,
        needs_heavy_prep=needs_heavy_prep,
        is_cabbage=is_cabbage,
        soup_type=soup_type,
        is_bone_soup=is_bone_soup,
        is_fruit_snack=is_fruit_snack,
        is_cake_snack=is_cake_snack,
        is_milk_snack=is_milk_snack,
        is_banana=is_banana,
        is_solite=is_solite,
        has_watermelon=has_watermelon,
        preferred=preferred,
        less_preferred=less_preferred,
        preference_score=score,
    )


def load_dishes_from_excel(excel_path: str | Path) -> Dict[str, List[Dish]]:
    logger.info("Đang đọc %s ...", excel_path)
    wb = openpyxl.load_workbook(str(excel_path), read_only=True, data_only=True)
    ws = wb.active

    menu_map: Dict[str, dict] = {}
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

    buckets: Dict[str, List[Dish]] = {cat: [] for cat in _CATEGORY_LIMITS}

    for code, data in menu_map.items():
        name = data['name']
        ings = list(data['ings'])

        # Loại bỏ rau muống theo nguyên tắc chung
        if 'rau muống' in _norm(name) or 'chua rau muống' in _norm(name):
            continue

        cat = _classify_category(code, name)
        if cat is None or cat not in buckets:
            continue

        dish = _build_dish(code, name, ings, cat)
        buckets[cat].append(dish)

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
    total = sum(len(v) for v in dishes.values())
    return {
        'total': total,
        'by_category': {cat: len(lst) for cat, lst in dishes.items()},
        'preferred_count': sum(1 for lst in dishes.values() for d in lst if d.preferred),
        'fried_count': sum(1 for lst in dishes.values() for d in lst if d.is_fried),
        'vien_count': sum(1 for lst in dishes.values() for d in lst if d.is_vien),
    }
