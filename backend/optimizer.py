from __future__ import annotations
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from ortools.sat.python import cp_model
from models import Dish, DayMenu, WeekMenu, MonthMenu

logger = logging.getLogger(__name__)

WEEKS     = ["w1", "w2", "w3", "w4"]
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri"]
SLOTS     = ["M1", "M2", "R", "C", "CO", "Q"]


# ──────────────────────────────────────────────────────────────────────────────
# RuleConfig – Cấu hình bộ rule cho từng điểm
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RuleConfig:
    # Giới hạn CO theo tháng
    max_com_rang_per_month: int = 99      # 99 = không giới hạn
    max_com_ga_per_month:   int = 99
    com_rang_alternate_week: bool = False  # rule 03: cơm rang cách tuần

    # Ràng buộc ngày cải thiện
    cai_thien_only_on_friday: bool = True   # cải thiện chỉ được vào thứ Sáu (nguyên tắc chung)
    friday_must_cai_thien:    bool = False  # thứ Sáu BẮT BUỘC có cải thiện (rule 07)
    no_service_on_friday:     bool = False  # rule 06: không phục vụ thứ 6

    # Giới hạn vien theo tuần
    max_vien_per_week: int = 1

    # Giới hạn protein theo tháng
    max_beef_per_month:   int = 99
    max_shrimp_per_month: int = 99

    # Cơ cấu Quà chiều (Q) theo tuần — None = không ràng buộc
    q_fruit_per_week: Optional[int] = None
    q_cake_per_week:  Optional[int] = None
    q_milk_per_week:  Optional[int] = None

    # Hạn chế loại Q
    q_only_fruit:     bool = False  # rule 05: chỉ dùng chuối
    q_only_cake_milk: bool = False  # rule 04: chỉ bánh và sữa

    # Tên/từ khóa cấm (domain reduction — áp dụng trên tên món)
    no_dish_keywords: List[str] = field(default_factory=list)

    # Ưu tiên thêm (tên/từ khóa → tăng score)
    extra_priority: List[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# 10 bộ rule preset
# ──────────────────────────────────────────────────────────────────────────────

RULE_CONFIGS: Dict[int, RuleConfig] = {
    1: RuleConfig(
        max_com_rang_per_month=1,
        max_com_ga_per_month=1,
        q_fruit_per_week=1, q_cake_per_week=2, q_milk_per_week=2,
        max_vien_per_week=1,
        no_dish_keywords=['chân giò giả cầy', 'chân giò hấp', 'solite'],
        extra_priority=['xá xíu', 'gà viên', 'chiên lắc phô mai', 'chiên lắc phomai'],
    ),
    2: RuleConfig(
        max_beef_per_month=1,
        max_shrimp_per_month=1,
        q_fruit_per_week=1, q_cake_per_week=1, q_milk_per_week=1,
        max_vien_per_week=1,
        no_dish_keywords=['cải cúc'],
        extra_priority=['nem lụi', 'sốt chua ngọt', 'xúc xích'],
    ),
    3: RuleConfig(
        com_rang_alternate_week=True,
        max_vien_per_week=1,
        no_dish_keywords=['thịt rang nấm hương', 'mộc nhĩ', 'gà sốt chua ngọt',
                          'rang cháy cạnh'],
    ),
    4: RuleConfig(
        max_com_rang_per_month=1,
        q_only_cake_milk=True,
        max_vien_per_week=1,
    ),
    5: RuleConfig(
        max_vien_per_week=1,
        q_only_fruit=True,
        no_dish_keywords=['dưa hấu', 'solite', 'bông lan cuộn kem'],
    ),
    6: RuleConfig(
        no_service_on_friday=True,
        cai_thien_only_on_friday=False,  # không có thứ 6 nên không áp
        max_vien_per_week=1,
    ),
    7: RuleConfig(
        friday_must_cai_thien=True,      # mỗi tuần 1 bữa cải thiện vào thứ 6
        max_vien_per_week=1,
        no_dish_keywords=['bánh vị cốm'],
        extra_priority=['trứng ốp', 'trứng kho'],
    ),
    8: RuleConfig(
        max_shrimp_per_month=1,
        max_vien_per_week=1,
        no_dish_keywords=['giò', 'chả'],
    ),
    9: RuleConfig(
        max_vien_per_week=1,
        no_dish_keywords=['su su xào'],
    ),
    10: RuleConfig(
        max_vien_per_week=1,
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# CP-SAT helpers
# ──────────────────────────────────────────────────────────────────────────────

def _flags(dishes: List[Dish], attr: str) -> List[int]:
    return [int(getattr(d, attr, False)) for d in dishes]


def _elem(model, name: str, idx_var, values: List[int]):
    """BoolVar = values[idx_var]."""
    b = model.NewBoolVar(name)
    model.AddElement(idx_var, values, b)
    return b


def _score_var(model, name: str, idx_var, scores: List[int]):
    """IntVar = scores[idx_var], dùng trong objective."""
    lo, hi = min(scores), max(scores)
    if lo == hi:
        hi += 1
    sc = model.NewIntVar(lo, hi, name)
    model.AddElement(idx_var, scores, sc)
    return sc


def _bool_or(model, name: str, bools: List):
    """Auxiliary var = OR(bools)."""
    b = model.NewBoolVar(name)
    model.AddBoolOr(bools).OnlyEnforceIf(b)
    model.AddBoolAnd([x.Not() for x in bools]).OnlyEnforceIf(b.Not())
    return b


# ──────────────────────────────────────────────────────────────────────────────
# Domain reduction theo rule config
# ──────────────────────────────────────────────────────────────────────────────

def _filter_dishes(dishes: List[Dish], cfg: RuleConfig) -> List[Dish]:
    """Loại bỏ món bị cấm bởi bộ rule."""
    if not cfg.no_dish_keywords:
        return dishes
    result = []
    for d in dishes:
        n = d.name.lower()
        if any(kw in n for kw in cfg.no_dish_keywords):
            continue
        result.append(d)
    return result


def _boost_scores(dishes: List[Dish], extra_priority: List[str]) -> List[Dish]:
    """Tăng score cho món ưu tiên theo bộ rule."""
    if not extra_priority:
        return dishes
    updated = []
    for d in dishes:
        n = d.name.lower()
        if any(kw in n for kw in extra_priority) and d.preference_score < 10:
            d = d.model_copy(update={"preference_score": 10, "preferred": True})
        updated.append(d)
    return updated


# ──────────────────────────────────────────────────────────────────────────────
# Build & Solve
# ──────────────────────────────────────────────────────────────────────────────

def build_and_solve(
    dishes: Dict[str, List[Dish]],
    timeout_s: float = 20.0,
    allow_repeat: bool = True,
    rule_set: int = 1,
    n_weeks: int = 4,
) -> Optional[Dict]:

    cfg   = RULE_CONFIGS.get(rule_set, RULE_CONFIGS[1])
    weeks = WEEKS[:n_weeks]

    # ── Domain reduction toàn cục ──────────────────────────────────────────────
    # Rule I.3: Không dùng giò, chả, xúc xích (trừ khi bộ rule riêng cho phép)
    # (Các bộ rule 02, 08 hạn chế nhưng không cấm hoàn toàn — chỉ loại nếu rõ ràng)
    _no_gio_cha_general = rule_set not in (2, 8, 9)  # rule 02/08/09 còn cho phép

    dM_raw  = dishes["M"]
    dR_raw  = dishes["R"]
    dC_raw  = dishes["C"]
    dCO_raw = dishes["CO"]
    dQ_raw  = dishes["Q"]

    # Áp dụng domain reduction: basa + chiên nhưng không phải viên
    dM = [d for d in dM_raw if not (d.has_basa and d.is_fried and not d.is_vien)]
    if _no_gio_cha_general:
        dM = [d for d in dM if not d.is_gio and not d.is_cha and not d.is_xuc_xich]

    dM  = _filter_dishes(dM,  cfg)
    dR  = _filter_dishes(dR_raw, cfg)
    dC  = _filter_dishes(dC_raw, cfg)
    dCO = _filter_dishes(dCO_raw, cfg)

    # Q: lọc theo loại nếu bộ rule hạn chế
    dQ = dQ_raw
    if cfg.q_only_fruit:
        dQ = [d for d in dQ if d.is_fruit_snack]
    elif cfg.q_only_cake_milk:
        dQ = [d for d in dQ if d.is_cake_snack or d.is_milk_snack]
    dQ = _filter_dishes(dQ, cfg)

    # Boost score theo extra_priority của bộ rule
    dM  = _boost_scores(dM,  cfg.extra_priority)
    dCO = _boost_scores(dCO, cfg.extra_priority)

    nM, nR, nC, nCO, nQ = len(dM), len(dR), len(dC), len(dCO), len(dQ)

    if not dM:
        logger.error("Không có món mặn hợp lệ sau domain reduction.")
        return None
    if not dQ and not cfg.q_only_fruit:
        logger.warning("Danh sách Q rỗng sau domain reduction.")

    valid_M = list(range(nM))
    domain_M = cp_model.Domain.FromValues(valid_M)

    # ── Preprocess flags ───────────────────────────────────────────────────────
    meta: Dict[str, Dict[str, List[int]]] = {
        "M": {
            "fried":   _flags(dM, "is_fried"),
            "vien":    _flags(dM, "is_vien"),
            "fish":    [int(d.has_fish or d.has_shrimp) for d in dM],
            "fish_only": _flags(dM, "has_fish"),
            "shrimp":  _flags(dM, "has_shrimp"),
            "egg":     _flags(dM, "has_egg"),
            "beef":    _flags(dM, "has_beef"),
            "pork":    _flags(dM, "has_pork"),
            "chicken": _flags(dM, "has_chicken"),
            "tofu":    _flags(dM, "has_tofu"),
        },
        "R": {
            "fried":      _flags(dR, "is_fried"),
            "root":       [int(d.veg_type == "root_fruit") for d in dR],
            "boil":       [int(d.veg_cooking_method == "boil") for d in dR],
            "heavy_prep": _flags(dR, "needs_heavy_prep"),
            "cabbage":    _flags(dR, "is_cabbage"),
        },
        "C": {
            "not_leaf":   [int(d.soup_type == "root_fruit") for d in dC],
            "seafood":    [int(d.has_shrimp or d.has_fish) for d in dC],
            "bone_soup":  _flags(dC, "is_bone_soup"),
        },
        "CO": {
            "rang":       _flags(dCO, "is_com_rang"),
            "ga":         _flags(dCO, "is_com_ga"),
            "cai_thien":  _flags(dCO, "is_com_cai_thien"),
        },
        "Q": {
            "milk":       _flags(dQ, "has_milk") if dQ else [],
            "fruit":      _flags(dQ, "is_fruit_snack") if dQ else [],
            "cake":       _flags(dQ, "is_cake_snack") if dQ else [],
            "milk_snack": _flags(dQ, "is_milk_snack") if dQ else [],
        },
    }

    pref_scores = {
        "M":  [d.preference_score for d in dM],
        "R":  [d.preference_score for d in dR],
        "C":  [d.preference_score for d in dC],
        "CO": [d.preference_score for d in dCO],
        "Q":  [d.preference_score for d in dQ] if dQ else [1],
    }

    # ── Model ──────────────────────────────────────────────────────────────────
    model = cp_model.CpModel()

    # ── Decision variables: y[wk][d][slot] ────────────────────────────────────
    y: Dict[str, Dict[str, Dict[str, any]]] = {}
    for wk in weeks:
        y[wk] = {}
        for d in DAY_NAMES:
            if cfg.no_service_on_friday and d == "fri":
                continue
            y[wk][d] = {
                "M1": model.NewIntVarFromDomain(domain_M,        f"M1_{wk}_{d}"),
                "M2": model.NewIntVarFromDomain(domain_M,        f"M2_{wk}_{d}"),
                "R":  model.NewIntVar(0, nR  - 1,                f"R_{wk}_{d}"),
                "C":  model.NewIntVar(0, nC  - 1,                f"C_{wk}_{d}"),
                "CO": model.NewIntVar(0, nCO - 1,                f"CO_{wk}_{d}"),
                "Q":  model.NewIntVar(0, max(nQ - 1, 0),         f"Q_{wk}_{d}"),
            }

    # ── Boolean feature variables: B[wk][d][feat] ─────────────────────────────
    B: Dict[str, Dict[str, Dict[str, any]]] = {}
    for wk in weeks:
        B[wk] = {}
        for d in DAY_NAMES:
            if d not in y[wk]:
                continue
            B[wk][d] = {}
            yv = y[wk][d]
            pfx = f"{wk}_{d}"

            for slot_key, slot_name in [("m1", "M1"), ("m2", "M2")]:
                for feat in ["fried", "vien", "fish", "fish_only", "shrimp",
                             "egg", "beef", "pork", "chicken", "tofu"]:
                    B[wk][d][f"{slot_key}_{feat}"] = _elem(
                        model, f"{slot_key}_{feat}_{pfx}",
                        yv[slot_name], meta["M"][feat],
                    )

            B[wk][d]["r_fried"]       = _elem(model, f"r_fried_{pfx}",       yv["R"],  meta["R"]["fried"])
            B[wk][d]["r_root"]        = _elem(model, f"r_root_{pfx}",        yv["R"],  meta["R"]["root"])
            B[wk][d]["r_boil"]        = _elem(model, f"r_boil_{pfx}",        yv["R"],  meta["R"]["boil"])
            B[wk][d]["r_heavy_prep"]  = _elem(model, f"r_heavy_{pfx}",       yv["R"],  meta["R"]["heavy_prep"])
            B[wk][d]["r_cabbage"]     = _elem(model, f"r_cabbage_{pfx}",     yv["R"],  meta["R"]["cabbage"])
            B[wk][d]["c_not_leaf"]    = _elem(model, f"c_not_leaf_{pfx}",    yv["C"],  meta["C"]["not_leaf"])
            B[wk][d]["c_seafood"]     = _elem(model, f"c_seafood_{pfx}",     yv["C"],  meta["C"]["seafood"])
            B[wk][d]["c_bone_soup"]   = _elem(model, f"c_bone_{pfx}",        yv["C"],  meta["C"]["bone_soup"])
            B[wk][d]["co_rang"]       = _elem(model, f"co_rang_{pfx}",       yv["CO"], meta["CO"]["rang"])
            B[wk][d]["co_ga"]         = _elem(model, f"co_ga_{pfx}",         yv["CO"], meta["CO"]["ga"])
            B[wk][d]["co_cai_thien"]  = _elem(model, f"co_ct_{pfx}",         yv["CO"], meta["CO"]["cai_thien"])

            if dQ:
                B[wk][d]["q_milk"]      = _elem(model, f"q_milk_{pfx}",      yv["Q"],  meta["Q"]["milk"])
                B[wk][d]["q_fruit"]     = _elem(model, f"q_fruit_{pfx}",     yv["Q"],  meta["Q"]["fruit"])
                B[wk][d]["q_cake"]      = _elem(model, f"q_cake_{pfx}",      yv["Q"],  meta["Q"]["cake"])
                B[wk][d]["q_milk_snack"]= _elem(model, f"q_msnack_{pfx}",    yv["Q"],  meta["Q"]["milk_snack"])
            else:
                for k in ["q_milk", "q_fruit", "q_cake", "q_milk_snack"]:
                    B[wk][d][k] = model.NewConstant(0)

    # ═════════════════════════════════════════════════════════════════════════
    # I. NGUYÊN TẮC KẾT HỢP THỰC PHẨM
    # ═════════════════════════════════════════════════════════════════════════
    for wk in weeks:
        for d in DAY_NAMES:
            if d not in y[wk]:
                continue
            bd = B[wk][d]

            # I.1a: M1 != M2 (món mặn không trùng trong ngày)
            model.Add(y[wk][d]["M1"] != y[wk][d]["M2"])

            # I.1b: Cá/tôm + trứng kỵ nhau
            model.AddImplication(bd["m1_fish"], bd["m2_egg"].Not())
            model.AddImplication(bd["m2_fish"], bd["m1_egg"].Not())
            model.AddImplication(bd["m1_fish"], bd["c_seafood"].Not())  # cá món mặn + tôm canh

            # I.1c: Cá/tôm + sữa kỵ nhau (Q)
            for fish_src in [bd["m1_fish"], bd["m2_fish"], bd["c_seafood"]]:
                model.AddImplication(fish_src, bd["q_milk"].Not())

            # I.1d: Trứng không dùng vào thứ Hai
            if d == "mon":
                model.Add(bd["m1_egg"] == 0)
                model.Add(bd["m2_egg"] == 0)

            # I.2: M1 và M2 không cùng nhóm protein chính
            for prot in ["pork", "chicken", "beef", "fish_only", "shrimp", "egg", "tofu"]:
                model.AddImplication(bd[f"m1_{prot}"], bd[f"m2_{prot}"].Not())

    # ═════════════════════════════════════════════════════════════════════════
    # II. QUY ĐỊNH CHẾ BIẾN
    # ═════════════════════════════════════════════════════════════════════════
    for wk in weeks:
        for d in DAY_NAMES:
            if d not in y[wk]:
                continue
            bd = B[wk][d]

            # II.4: Tối đa 1 món chiên/ngày (M1 + M2 + R)
            model.Add(bd["m1_fried"] + bd["m2_fried"] + bd["r_fried"] <= 1)

            # II.5: Ngày có món chiên → không dùng rau củ sơ chế nhiều
            b_has_fried = _bool_or(
                model, f"has_fried_{wk}_{d}",
                [bd["m1_fried"], bd["m2_fried"], bd["r_fried"]],
            )
            model.AddImplication(b_has_fried, bd["r_heavy_prep"].Not())

    # ═════════════════════════════════════════════════════════════════════════
    # III. QUY ĐỊNH RAU VÀ CANH
    # ═════════════════════════════════════════════════════════════════════════
    has_boil_R = sum(meta["R"]["boil"]) > 0
    has_stir_R = sum(1 for v in meta["R"]["boil"] if v == 0) > 0

    for wk in weeks:
        # III.8: Rau luộc và rau xào xen kẽ trong tuần
        if has_boil_R and has_stir_R:
            active_days = [d for d in DAY_NAMES if d in y[wk]]
            for i in range(len(active_days) - 1):
                d1, d2 = active_days[i], active_days[i + 1]
                # Consecutive days must differ (boil XOR stir_fry)
                model.Add(B[wk][d1]["r_boil"] + B[wk][d2]["r_boil"] == 1)

        for d in DAY_NAMES:
            if d not in y[wk]:
                continue
            bd = B[wk][d]

            # III.9: Rau củ quả → Canh phải là rau ăn lá
            model.AddImplication(bd["r_root"], bd["c_not_leaf"].Not())

            # III.10: Ngày có thịt gà → Canh nấu từ xương, không canh lá
            # Guard: chỉ áp nếu pool C có đủ bone soup + not_leaf để thỏa mãn
            b_has_chicken = _bool_or(
                model, f"has_chicken_{wk}_{d}",
                [bd["m1_chicken"], bd["m2_chicken"]],
            )
            if sum(meta["C"]["bone_soup"]) > 0:
                model.AddImplication(b_has_chicken, bd["c_bone_soup"])
            if sum(meta["C"]["not_leaf"]) > 0:
                model.AddImplication(b_has_chicken, bd["c_not_leaf"])

            # Gà + cải họ kỵ nhau
            model.AddImplication(bd["m1_chicken"], bd["r_cabbage"].Not())
            model.AddImplication(bd["m2_chicken"], bd["r_cabbage"].Not())

            # Cá + đậu phụ kỵ nhau (rule IV.11)
            for fish_src in ["m1_fish_only", "m2_fish_only"]:
                model.AddImplication(bd[fish_src], bd["m1_tofu"].Not())
                model.AddImplication(bd[fish_src], bd["m2_tofu"].Not())

    # ═════════════════════════════════════════════════════════════════════════
    # IV. NGÀY CẢI THIỆN (Thứ Sáu)
    # ═════════════════════════════════════════════════════════════════════════
    has_cai_thien     = sum(meta["CO"]["cai_thien"]) > 0
    has_non_cai_thien = sum(1 for v in meta["CO"]["cai_thien"] if v == 0) > 0

    # Cải thiện chỉ được vào thứ Sáu (nguyên tắc chung IV.12)
    if cfg.cai_thien_only_on_friday and has_cai_thien and has_non_cai_thien:
        for wk in weeks:
            for d in ["mon", "tue", "wed", "thu"]:
                if d in y[wk]:
                    model.Add(B[wk][d]["co_cai_thien"] == 0)

    # Thứ Sáu bắt buộc cải thiện (rule 07: mỗi tuần 1 bữa cải thiện)
    if cfg.friday_must_cai_thien and has_cai_thien:
        for wk in weeks:
            if "fri" in y[wk]:
                model.Add(B[wk]["fri"]["co_cai_thien"] == 1)

    # ═════════════════════════════════════════════════════════════════════════
    # V. RÀNG BUỘC NỘI TUẦN
    # ═════════════════════════════════════════════════════════════════════════
    for wk in weeks:
        active_days = [d for d in DAY_NAMES if d in y[wk]]
        n_active    = len(active_days)

        # Giới hạn vien/tuần
        model.Add(
            sum(B[wk][d]["m1_vien"] + B[wk][d]["m2_vien"] for d in active_days)
            <= cfg.max_vien_per_week
        )

        # AllDifferent cho M1 và M2 nội tuần
        if len(valid_M) >= n_active:
            model.AddAllDifferent([y[wk][d]["M1"] for d in active_days])
            model.AddAllDifferent([y[wk][d]["M2"] for d in active_days])
        if nR >= n_active:
            model.AddAllDifferent([y[wk][d]["R"] for d in active_days])
        if nC >= n_active:
            model.AddAllDifferent([y[wk][d]["C"] for d in active_days])

        # Cơ cấu Quà chiều theo tuần (bộ rule riêng)
        # Guard: chỉ add constraint nếu pool Q thực sự có đủ món thuộc loại yêu cầu
        if dQ and cfg.q_fruit_per_week is not None:
            if sum(meta["Q"]["fruit"]) >= cfg.q_fruit_per_week:
                model.Add(sum(B[wk][d]["q_fruit"] for d in active_days) >= cfg.q_fruit_per_week)
            else:
                logger.warning("rule_set=%d: Q pool thiếu is_fruit_snack (%d món), bỏ q_fruit constraint",
                               rule_set, sum(meta["Q"]["fruit"]))
        if dQ and cfg.q_cake_per_week is not None:
            if sum(meta["Q"]["cake"]) >= cfg.q_cake_per_week:
                model.Add(sum(B[wk][d]["q_cake"] for d in active_days) >= cfg.q_cake_per_week)
            else:
                logger.warning("rule_set=%d: Q pool thiếu is_cake_snack (%d món), bỏ q_cake constraint",
                               rule_set, sum(meta["Q"]["cake"]))
        if dQ and cfg.q_milk_per_week is not None:
            if sum(meta["Q"]["milk_snack"]) >= cfg.q_milk_per_week:
                model.Add(sum(B[wk][d]["q_milk_snack"] for d in active_days) >= cfg.q_milk_per_week)
            else:
                logger.warning("rule_set=%d: Q pool thiếu is_milk_snack (%d món), bỏ q_milk constraint",
                               rule_set, sum(meta["Q"]["milk_snack"]))

        # CO: tối ưu lặp lại (giữ nguyên logic cũ, áp cho mỗi tuần)
        _free_CO = [i for i, d in enumerate(dCO) if not d.is_com_rang and not d.is_com_ga]
        _n_free  = max(1, len(_free_CO))
        _co_max  = max(2, -(-n_active // _n_free))
        if _co_max < n_active:
            for i in range(nCO):
                days_used = []
                for d in active_days:
                    b = model.NewBoolVar(f"co_{i}_{wk}_{d}")
                    model.Add(y[wk][d]["CO"] == i).OnlyEnforceIf(b)
                    model.Add(y[wk][d]["CO"] != i).OnlyEnforceIf(b.Not())
                    days_used.append(b)
                model.Add(sum(days_used) <= _co_max)

    # ═════════════════════════════════════════════════════════════════════════
    # VI. RÀNG BUỘC TOÀN THÁNG
    # ═════════════════════════════════════════════════════════════════════════
    all_active: List[Tuple[str, str]] = [
        (wk, d) for wk in weeks for d in DAY_NAMES if d in y[wk]
    ]

    # Com rang / gà theo tháng
    if cfg.max_com_rang_per_month < 99:
        model.Add(
            sum(B[wk][d]["co_rang"] for wk, d in all_active)
            <= cfg.max_com_rang_per_month
        )
    if cfg.max_com_ga_per_month < 99:
        model.Add(
            sum(B[wk][d]["co_ga"] for wk, d in all_active)
            <= cfg.max_com_ga_per_month
        )

    # Cơm rang cách tuần (rule 03)
    if cfg.com_rang_alternate_week:
        for i in range(len(weeks) - 1):
            wk1, wk2 = weeks[i], weeks[i + 1]
            ad1 = [d for d in DAY_NAMES if d in y[wk1]]
            ad2 = [d for d in DAY_NAMES if d in y[wk2]]
            rang1 = sum(B[wk1][d]["co_rang"] for d in ad1)
            rang2 = sum(B[wk2][d]["co_rang"] for d in ad2)
            b_rang1 = model.NewBoolVar(f"rang_w{i+1}")
            b_rang2 = model.NewBoolVar(f"rang_w{i+2}")
            model.Add(rang1 >= 1).OnlyEnforceIf(b_rang1)
            model.Add(rang1 == 0).OnlyEnforceIf(b_rang1.Not())
            model.Add(rang2 >= 1).OnlyEnforceIf(b_rang2)
            model.Add(rang2 == 0).OnlyEnforceIf(b_rang2.Not())
            # Không có 2 tuần liên tiếp đều có cơm rang
            model.Add(b_rang1 + b_rang2 <= 1)

    # Thịt bò / tôm theo tháng
    if cfg.max_beef_per_month < 99:
        model.Add(
            sum(B[wk][d]["m1_beef"] + B[wk][d]["m2_beef"] for wk, d in all_active)
            <= cfg.max_beef_per_month
        )
    if cfg.max_shrimp_per_month < 99:
        model.Add(
            sum(B[wk][d]["m1_shrimp"] + B[wk][d]["m2_shrimp"] + B[wk][d]["c_seafood"]
                for wk, d in all_active)
            <= cfg.max_shrimp_per_month
        )

    # AllDifferent xuyên tháng (khi allow_repeat=False)
    if not allow_repeat:
        total_days = len(all_active)
        if len(valid_M) >= total_days * 2:
            all_m1 = [y[wk][d]["M1"] for wk, d in all_active]
            all_m2 = [y[wk][d]["M2"] for wk, d in all_active]
            model.AddAllDifferent(all_m1 + all_m2)
        if nR >= total_days:
            model.AddAllDifferent([y[wk][d]["R"] for wk, d in all_active])
        if nC >= total_days:
            model.AddAllDifferent([y[wk][d]["C"] for wk, d in all_active])

    # ═════════════════════════════════════════════════════════════════════════
    # OBJECTIVE
    # ═════════════════════════════════════════════════════════════════════════
    score_terms = []
    for wk, d in all_active:
        for slot, cat in [("M1", "M"), ("M2", "M"), ("R", "R"),
                          ("C", "C"), ("CO", "CO"), ("Q", "Q")]:
            score_terms.append(
                _score_var(model, f"sc_{slot}_{wk}_{d}", y[wk][d][slot], pref_scores[cat])
            )
    model.maximize(sum(score_terms))

    # ═════════════════════════════════════════════════════════════════════════
    # SEARCH STRATEGY
    # ═════════════════════════════════════════════════════════════════════════
    main_vars = []
    for wk, d in all_active:
        main_vars.extend([y[wk][d][s] for s in SLOTS])

    model.AddDecisionStrategy(
        main_vars,
        cp_model.CHOOSE_FIRST,
        cp_model.SELECT_MIN_VALUE,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # SOLVER
    # ═════════════════════════════════════════════════════════════════════════
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_s
    solver.parameters.num_search_workers  = 1
    solver.parameters.search_branching    = cp_model.PORTFOLIO_SEARCH
    solver.parameters.cp_model_presolve   = True

    t0      = time.perf_counter()
    status  = solver.solve(model)
    elapsed = int((time.perf_counter() - t0) * 1000)

    STATUS_MAP = {
        cp_model.OPTIMAL:       "optimal",
        cp_model.FEASIBLE:      "feasible",
        cp_model.INFEASIBLE:    "infeasible",
        cp_model.MODEL_INVALID: "invalid",
        cp_model.UNKNOWN:       "timeout",
    }
    status_name = STATUS_MAP.get(status, "unknown")
    logger.info("Solver — status=%s elapsed=%dms rule_set=%d n_weeks=%d",
                status_name, elapsed, rule_set, n_weeks)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status":        status_name,
            "solve_time_ms": elapsed,
            "score":         0,
            "menu":          MonthMenu(),
            "violations":    [],
        }

    best_score = int(solver.ObjectiveValue())
    dish_lists = {"M1": dM, "M2": dM, "R": dR, "C": dC, "CO": dCO, "Q": dQ}

    raw: Dict[str, Dict[str, Dict[str, Dish]]] = {}
    for wk in weeks:
        raw[wk] = {}
        for d in DAY_NAMES:
            if d not in y[wk]:
                continue
            raw[wk][d] = {
                slot: dish_lists[slot][solver.Value(y[wk][d][slot])]
                for slot in SLOTS
            }

    return {
        "status":        status_name,
        "solve_time_ms": elapsed,
        "score":         best_score,
        "menu":          _to_month_menu(raw, weeks),
        "violations":    [],
    }


def _to_month_menu(
    raw: Dict[str, Dict[str, Dict[str, Dish]]],
    weeks: List[str],
) -> MonthMenu:
    week_menus = {}
    for wk in weeks:
        week_days = raw.get(wk, {})
        week_menus[wk] = WeekMenu(**{
            d: DayMenu(
                M1=week_days[d].get("M1"), M2=week_days[d].get("M2"),
                R=week_days[d].get("R"),   C=week_days[d].get("C"),
                CO=week_days[d].get("CO"), Q=week_days[d].get("Q"),
            )
            for d in DAY_NAMES if d in week_days
        })
    return MonthMenu(**week_menus)