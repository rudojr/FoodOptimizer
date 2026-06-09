from __future__ import annotations
import copy
import logging
from typing import Dict, List, Optional, Any

from models import Dish, DayMenu, WeekMenu, Violation, AlternativeItem

logger = logging.getLogger(__name__)

DAYS = ['mon', 'tue', 'wed', 'thu', 'fri']
SLOTS = ['M1', 'M2', 'R', 'C', 'CO', 'Q']

DAY_LABELS = {
    'mon': 'Thứ 2', 'tue': 'Thứ 3', 'wed': 'Thứ 4',
    'thu': 'Thứ 5', 'fri': 'Thứ 6',
}


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _menu_to_raw(menu: WeekMenu) -> Dict[str, Dict[str, Optional[Dish]]]:
    """WeekMenu → dict dạng {day: {slot: Dish|None}}"""
    raw = {}
    for d in DAYS:
        dm: Optional[DayMenu] = getattr(menu, d, None)
        if dm:
            raw[d] = {s: getattr(dm, s, None) for s in SLOTS}
        else:
            raw[d] = {s: None for s in SLOTS}
    return raw


def _raw_to_menu(raw: Dict[str, Dict[str, Optional[Dish]]]) -> WeekMenu:
    kwargs = {}
    for d in DAYS:
        dm = raw.get(d, {})
        kwargs[d] = DayMenu(**{s: dm.get(s) for s in SLOTS})
    return WeekMenu(**kwargs)


def _compute_score(menu: WeekMenu) -> int:
    raw = _menu_to_raw(menu)
    total = 0
    for d in DAYS:
        for s in SLOTS:
            dish = raw[d].get(s)
            if dish:
                total += dish.preference_score
    return total


# ─── Constraint Checker ────────────────────────────────────────────────────────

def check_constraints(menu: WeekMenu) -> List[Violation]:
    """Kiểm tra tất cả ràng buộc. Trả về danh sách vi phạm."""
    raw = _menu_to_raw(menu)
    violations: List[Violation] = []

    # ── Ràng buộc theo ngày ────────────────────────────────────────────────────
    for d in DAYS:
        dm = raw[d]
        m1 = dm.get('M1')
        m2 = dm.get('M2')
        r  = dm.get('R')
        c  = dm.get('C')
        co = dm.get('CO')
        q  = dm.get('Q')
        label = DAY_LABELS[d]

        # R1: Không ≥2 món chiên/ngày (M1, M2, R)
        fried_slots = []
        for slot, dish in [('M1', m1), ('M2', m2), ('R', r)]:
            if dish and dish.is_fried:
                fried_slots.append(slot)
        if len(fried_slots) >= 2:
            violations.append(Violation(
                type='two_fried', day=d,
                slot=','.join(fried_slots), severity='error',
                message=f'{label}: Có {len(fried_slots)} món chiên cùng ngày ({", ".join(fried_slots)})',
            ))

        # R2: Cá/tôm + trứng không được kết hợp
        has_fish = ((m1 and (m1.has_fish or m1.has_shrimp)) or
                    (m2 and (m2.has_fish or m2.has_shrimp)))
        has_egg  = ((m1 and m1.has_egg) or (m2 and m2.has_egg))
        if has_fish and has_egg:
            violations.append(Violation(
                type='fish_egg', day=d, severity='error',
                message=f'{label}: Không kết hợp cá/tôm với trứng trong cùng bữa',
            ))

        # R3: Cá/tôm trong bữa → Q không được có sữa
        has_fish_c = c and (c.has_shrimp or c.has_fish)
        if (has_fish or has_fish_c) and (q and q.has_milk):
            violations.append(Violation(
                type='fish_milk', day=d, slot='Q', severity='error',
                message=f'{label}: Có cá/tôm trong bữa nhưng quà chiều có sữa',
            ))

        # R4: Rau củ quả → Canh phải là rau ăn lá
        if r and c and r.veg_type == 'root_fruit' and c.soup_type != 'leaf':
            violations.append(Violation(
                type='veg_soup_type', day=d, slot='C', severity='error',
                message=(f'{label}: Món rau là rau củ quả ({r.name}) '
                         f'nhưng canh không phải rau ăn lá ({c.name})'),
            ))

        # R5: Không dùng rau muống (kiểm tra phòng thủ)
        if r and 'muống' in r.name.lower():
            violations.append(Violation(
                type='rau_muong', day=d, slot='R', severity='error',
                message=f'{label}: Không sử dụng rau muống',
            ))

        # R11: Cá basa + chiên nhưng không phải viên
        for slot, dish in [('M1', m1), ('M2', m2)]:
            if dish and dish.has_basa and dish.is_fried and not dish.is_vien:
                violations.append(Violation(
                    type='basa_not_vien', day=d, slot=slot, severity='error',
                    message=f'{label}: Cá basa đông lạnh chỉ dùng cho món viên',
                ))

        # Warning: Cơm rang ưu tiên canh chua
        if co and co.is_com_rang:
            has_sour_soup = c and any(w in c.name.lower()
                                      for w in ['chua', 'dưa chua'])
            if not has_sour_soup:
                violations.append(Violation(
                    type='com_rang_soup', day=d, slot='C', severity='warning',
                    message=f'{label}: Cơm rang nên kết hợp với canh chua',
                ))

    # ── Ràng buộc theo tuần ────────────────────────────────────────────────────

    # R6: Tối đa 1 bữa món viên/tuần
    vien_days = [
        d for d in DAYS
        for slot in ['M1', 'M2']
        if (dish := raw[d].get(slot)) and dish.is_vien
    ]
    if len(vien_days) > 1:
        violations.append(Violation(
            type='too_many_vien', severity='error',
            message=f'Tuần có {len(vien_days)} bữa món viên (tối đa 1)',
        ))

    # R7: Cơm rang tối đa 1 lần/tuần
    rang_days = [d for d in DAYS if (dish := raw[d].get('CO')) and dish.is_com_rang]
    if len(rang_days) > 1:
        violations.append(Violation(
            type='too_many_com_rang', severity='error',
            message=f'Tuần có {len(rang_days)} lần cơm rang (tối đa 1)',
        ))

    # R8: Cơm gà tối đa 1 lần/tuần
    ga_days = [d for d in DAYS if (dish := raw[d].get('CO')) and dish.is_com_ga]
    if len(ga_days) > 1:
        violations.append(Violation(
            type='too_many_com_ga', severity='error',
            message=f'Tuần có {len(ga_days)} lần cơm gà (tối đa 1)',
        ))

    # R9: Thịt bò tối đa 1 bữa/tuần
    beef_days = [
        d for d in DAYS
        for slot in ['M1', 'M2']
        if (dish := raw[d].get(slot)) and dish.has_beef
    ]
    if len(beef_days) > 1:
        violations.append(Violation(
            type='too_many_beef', severity='error',
            message=f'Tuần có {len(beef_days)} bữa thịt bò (tối đa 1)',
        ))

    # R10: Tôm tối đa 1 bữa/tuần (bao gồm cả canh tôm)
    shrimp_count = sum(
        1 for d in DAYS
        for slot in ['M1', 'M2', 'C']
        if (dish := raw[d].get(slot)) and dish.has_shrimp
    )
    if shrimp_count > 1:
        violations.append(Violation(
            type='too_many_shrimp', severity='error',
            message=f'Tuần có {shrimp_count} bữa tôm (tối đa 1)',
        ))

    # Warning: Hạn chế dưa hấu trong Q
    duahau_days = [
        d for d in DAYS
        if (dish := raw[d].get('Q')) and 'dưa hấu' in dish.name.lower()
    ]
    if len(duahau_days) > 2:
        violations.append(Violation(
            type='too_much_duahau', severity='warning',
            message=f'Dùng dưa hấu {len(duahau_days)} ngày (nên hạn chế)',
        ))

    return violations


# ─── Local Repair (MPP) ────────────────────────────────────────────────────────

def get_alternatives(
    menu: WeekMenu,
    day: str,
    slot: str,
    dishes: Dict[str, List[Dish]],
) -> List[AlternativeItem]:
    """
    Tìm tất cả món thay thế hợp lệ cho slot (day, slot).
    Thuật toán MPP: chỉ thay đúng 1 ô, không làm thay đổi phần còn lại.

    Returns: Danh sách alternatives sắp xếp theo (violations asc, score desc)
    """
    raw = _menu_to_raw(menu)
    current_dish = raw[day].get(slot)

    # Chọn pool món đúng category
    cat = 'M' if slot in ['M1', 'M2'] else slot
    pool = dishes.get(cat, [])

    # Lấy các món đang dùng trong tuần (để check uniqueness)
    used_codes: set[str] = set()
    for d in DAYS:
        for s in SLOTS:
            dish = raw[d].get(s)
            if dish and not (d == day and s == slot):
                used_codes.add(dish.code)

    alternatives: List[AlternativeItem] = []
    for candidate in pool:
        # Không đề xuất món đang được dùng
        if candidate.code == (current_dish.code if current_dish else None):
            continue
        # Không đề xuất món đã dùng ở nơi khác trong tuần
        if candidate.code in used_codes:
            continue
        # M1 và M2 không được cùng món trong 1 ngày
        if slot == 'M1':
            other = raw[day].get('M2')
            if other and other.code == candidate.code:
                continue
        if slot == 'M2':
            other = raw[day].get('M1')
            if other and other.code == candidate.code:
                continue

        # Thử đặt candidate vào slot → kiểm tra ràng buộc
        temp_raw = copy.deepcopy(raw)
        temp_raw[day][slot] = candidate
        temp_menu = _raw_to_menu(temp_raw)
        viols = check_constraints(temp_menu)

        alternatives.append(AlternativeItem(
            dish=candidate,
            score=candidate.preference_score,
            remaining_violations=len(viols),
        ))

    # Sắp xếp: ít vi phạm nhất → preference_score cao nhất
    alternatives.sort(key=lambda a: (a.remaining_violations, -a.score))
    return alternatives


def auto_repair(
    menu: WeekMenu,
    dishes: Dict[str, List[Dish]],
    max_iterations: int = 20,
) -> tuple[WeekMenu, List[dict]]:
    """
    Tự động sửa tất cả vi phạm bằng Local Search (MPP).

    Mỗi iteration:
      1. Tìm vi phạm nghiêm trọng nhất
      2. Lấy slot bị ảnh hưởng
      3. Thay thế bằng alternative tốt nhất (ít vi phạm nhất)
      4. Lặp đến khi hết vi phạm hoặc đạt max_iterations

    Returns:
        (repaired_menu, changes_log)
    """
    raw = _menu_to_raw(menu)
    changes: List[dict] = []

    for iteration in range(max_iterations):
        current_menu = _raw_to_menu(raw)
        viols = check_constraints(current_menu)

        # Chỉ xử lý lỗi (error), bỏ qua warning
        errors = [v for v in viols if v.severity == 'error']
        if not errors:
            logger.info("Auto-repair: đã sửa xong sau %d vòng", iteration)
            break

        # Chọn vi phạm đầu tiên để sửa
        target_viol = errors[0]

        # Tìm slot cần sửa
        day, slot = _pick_slot_to_repair(target_viol, raw)
        if not day or not slot:
            logger.warning("Không tìm được slot để sửa vi phạm: %s", target_viol.type)
            break

        old_dish = raw[day].get(slot)
        alts = get_alternatives(current_menu, day, slot, dishes)

        # Chọn alternative tốt nhất
        best = next((a for a in alts if a.remaining_violations <= len(errors)), None)
        if not best:
            best = alts[0] if alts else None
        if not best:
            logger.warning("Không có alternative cho (%s, %s)", day, slot)
            break

        # Áp dụng thay thế
        raw[day][slot] = best.dish
        changes.append({
            'iteration': iteration + 1,
            'day': day,
            'slot': slot,
            'old_dish': old_dish.model_dump() if old_dish else None,
            'new_dish': best.dish.model_dump(),
            'violation_fixed': target_viol.type,
        })
        logger.info(
            "  Iter %d: [%s][%s] %s → %s",
            iteration + 1, day, slot,
            old_dish.name if old_dish else 'None',
            best.dish.name,
        )

    return _raw_to_menu(raw), changes


def _pick_slot_to_repair(
    viol: Violation,
    raw: Dict[str, Dict[str, Optional[Dish]]],
) -> tuple[Optional[str], Optional[str]]:
    """Chọn (day, slot) cần sửa từ thông tin vi phạm."""

    # Vi phạm có day và slot cụ thể
    if viol.day and viol.slot and ',' not in (viol.slot or ''):
        if viol.slot in SLOTS:
            return viol.day, viol.slot

    # Vi phạm có ngày nhưng slot phức hợp (vd: "M1,M2")
    if viol.day:
        # Ưu tiên sửa M2 (ít ảnh hưởng hơn M1)
        for slot in ['M2', 'M1', 'R', 'C', 'CO', 'Q']:
            if raw[viol.day].get(slot):
                return viol.day, slot

    # Vi phạm tuần → tìm ngày và slot phù hợp
    slot_by_type = {
        'too_many_vien':    ('M2', ['M2', 'M1']),
        'too_many_com_rang':('CO', ['CO']),
        'too_many_com_ga':  ('CO', ['CO']),
        'too_many_beef':    ('M2', ['M2', 'M1']),
        'too_many_shrimp':  ('M2', ['M2', 'M1', 'C']),
    }
    if viol.type in slot_by_type:
        _, pref_slots = slot_by_type[viol.type]
        # Tìm ngày sau nhất có dish ở slot này
        for day in reversed(DAYS):
            for slot in pref_slots:
                dish = raw[day].get(slot)
                if dish:
                    should_fix = False
                    if viol.type == 'too_many_vien'    and dish.is_vien:    should_fix = True
                    if viol.type == 'too_many_com_rang' and dish.is_com_rang: should_fix = True
                    if viol.type == 'too_many_com_ga'   and dish.is_com_ga:   should_fix = True
                    if viol.type == 'too_many_beef'    and dish.has_beef:   should_fix = True
                    if viol.type == 'too_many_shrimp'  and dish.has_shrimp: should_fix = True
                    if should_fix:
                        return day, slot

    return None, None
