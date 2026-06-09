# pyrefly: ignore [invalid-syntax]
from __future__ import annotations
# pyright: ignore[reportMissingImports]
import models
import time
import logging
from typing import Dict, List, Optional
from ortools.sat.python import cp_model
from models import Dish, DayMenu, WeekMenu

logger = logging.getLogger(__name__)

DAYS = ["mon", "tue", "wed", "thu", "fri"]
SLOTS = ["M1", "M2", "R", "C", "CO", "Q"]


def _flags(dishes: List[Dish], attr: str) -> List[int]:
    return [int(getattr(d, attr, False)) for d in dishes]


def _elem(model, name: str, idx_var, values: List[int]):
    """Bool var = values[idx_var]."""
    b = model.NewBoolVar(name)
    model.AddElement(idx_var, values, b)
    return b


def _score_var(model, name: str, idx_var, scores: List[int]):
    """IntVar = scores[idx_var], used in objective."""
    sc = model.NewIntVar(min(scores), max(scores), name)
    model.AddElement(idx_var, scores, sc)
    return sc


def build_and_solve(
    dishes: Dict[str, List[Dish]],
    timeout_s: float = 8.0,
    allow_repeat: bool = True,
):
    model = cp_model.CpModel()

    dM  = dishes["M"]
    dR  = dishes["R"]
    dC  = dishes["C"]
    dCO = dishes["CO"]
    dQ  = dishes["Q"]

    nM, nR, nC, nCO, nQ = len(dM), len(dR), len(dC), len(dCO), len(dQ)

    # ──────────────────────────────────────────────────────────────────────────
    # PREPROCESS — feature flag arrays
    # ──────────────────────────────────────────────────────────────────────────

    meta = {
        "M": {
            "fried":  _flags(dM, "is_fried"),
            "vien":   _flags(dM, "is_vien"),
            "fish":   [int(d.has_fish or d.has_shrimp) for d in dM],
            "egg":    _flags(dM, "has_egg"),
            "shrimp": _flags(dM, "has_shrimp"),
            "beef":   _flags(dM, "has_beef"),
        },
        "R": {
            "fried": _flags(dR, "is_fried"),
            "root":  [int(d.veg_type == "root_fruit") for d in dR],
        },
        "C": {
            "not_leaf": [int(d.soup_type == "root_fruit") for d in dC],
            "seafood":  [int(d.has_shrimp or d.has_fish) for d in dC],
        },
        "CO": {
            "rang": _flags(dCO, "is_com_rang"),
            "ga":   _flags(dCO, "is_com_ga"),
        },
        "Q": {
            "milk": _flags(dQ, "has_milk"),
        },
    }

    pref_scores = {
        "M":  [d.preference_score for d in dM],
        "R":  [d.preference_score for d in dR],
        "C":  [d.preference_score for d in dC],
        "CO": [d.preference_score for d in dCO],
        "Q":  [d.preference_score for d in dQ],
    }

    # ──────────────────────────────────────────────────────────────────────────
    # DOMAIN REDUCTION
    # Rule 1.1: Basa chỉ dùng cho món viên, không dùng chiên bột
    # ──────────────────────────────────────────────────────────────────────────

    valid_M = [
        i for i, d in enumerate(dM)
        if not (d.has_basa and d.is_fried and not d.is_vien)
    ]

    if not valid_M:
        logger.error("Không có món mặn hợp lệ sau domain reduction.")
        return None

    domain_M = cp_model.Domain.FromValues(valid_M)

    # ──────────────────────────────────────────────────────────────────────────
    # DECISION VARIABLES
    # ──────────────────────────────────────────────────────────────────────────

    y = {}
    for day in DAYS:
        y[day] = {
            "M1": model.NewIntVarFromDomain(domain_M, f"M1_{day}"),
            "M2": model.NewIntVarFromDomain(domain_M, f"M2_{day}"),
            "R":  model.NewIntVar(0, nR  - 1, f"R_{day}"),
            "C":  model.NewIntVar(0, nC  - 1, f"C_{day}"),
            "CO": model.NewIntVar(0, nCO - 1, f"CO_{day}"),
            "Q":  model.NewIntVar(0, nQ  - 1, f"Q_{day}"),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # BOOLEAN FEATURE VARIABLES
    # ──────────────────────────────────────────────────────────────────────────

    B = {}
    for day in DAYS:
        B[day] = {}

        for slot in ["M1", "M2"]:
            key = slot.lower()
            for feat in ["fried", "fish", "egg", "shrimp", "beef", "vien"]:
                B[day][f"{key}_{feat}"] = _elem(
                    model, f"{key}_{feat}_{day}",
                    y[day][slot], meta["M"][feat],
                )

        B[day]["r_fried"]   = _elem(model, f"r_fried_{day}",    y[day]["R"],  meta["R"]["fried"])
        B[day]["r_root"]    = _elem(model, f"r_root_{day}",     y[day]["R"],  meta["R"]["root"])
        B[day]["c_not_leaf"]= _elem(model, f"c_not_leaf_{day}", y[day]["C"],  meta["C"]["not_leaf"])
        B[day]["c_seafood"] = _elem(model, f"c_seafood_{day}",  y[day]["C"],  meta["C"]["seafood"])
        B[day]["q_milk"]    = _elem(model, f"q_milk_{day}",     y[day]["Q"],  meta["Q"]["milk"])
        B[day]["co_rang"]   = _elem(model, f"co_rang_{day}",    y[day]["CO"], meta["CO"]["rang"])
        B[day]["co_ga"]     = _elem(model, f"co_ga_{day}",      y[day]["CO"], meta["CO"]["ga"])

    # ──────────────────────────────────────────────────────────────────────────
    # DAILY CONSTRAINTS
    # ──────────────────────────────────────────────────────────────────────────

    for day in DAYS:
        # M1 và M2 phải khác nhau trong ngày
        model.Add(y[day]["M1"] != y[day]["M2"])

        # Rule 3.1: Không quá 1 món chiên/ngày (M1 + M2 + rau)
        model.Add(
            B[day]["m1_fried"] + B[day]["m2_fried"] + B[day]["r_fried"] <= 1
        )

        # Rule 1.2: Không kết hợp cá/tôm với trứng
        model.AddImplication(B[day]["m1_fish"], B[day]["m2_egg"].Not())
        model.AddImplication(B[day]["m2_fish"], B[day]["m1_egg"].Not())

        # Rule 1.3: Cá/tôm không kết hợp với sữa (quà chiều)
        for fish_src in [
            B[day]["m1_fish"],
            B[day]["m2_fish"],
            B[day]["c_seafood"],
        ]:
            model.AddImplication(fish_src, B[day]["q_milk"].Not())

        # Rule 1.4: Rau củ quả → canh phải thuộc nhóm rau ăn lá
        model.AddImplication(B[day]["r_root"], B[day]["c_not_leaf"].Not())

    # ──────────────────────────────────────────────────────────────────────────
    # WEEKLY CONSTRAINTS
    # ──────────────────────────────────────────────────────────────────────────

    if not allow_repeat:
        model.AddAllDifferent(
            [y[d]["M1"] for d in DAYS] + [y[d]["M2"] for d in DAYS]
        )
        model.AddAllDifferent([y[d]["R"]  for d in DAYS])
        model.AddAllDifferent([y[d]["C"]  for d in DAYS])
        # CO: không dùng AllDifferent vì co_ga<=1 và co_rang<=1 đã giới hạn tần suất.
        # Với ít món CO (5 món, 2 co_ga), AllDifferent + co_ga<=1 luôn vô nghiệm.
    elif nM >= len(DAYS):
        # allow_repeat=True nhưng vẫn ngăn cùng món M chiếm toàn bộ slot M1 hoặc M2
        model.AddAllDifferent([y[d]["M1"] for d in DAYS])
        model.AddAllDifferent([y[d]["M2"] for d in DAYS])

    # Rule 2.1: Tối đa 1 món viên/tuần
    model.Add(
        sum(B[d]["m1_vien"] + B[d]["m2_vien"] for d in DAYS) <= 1
    )

    # Rule 2.4: Tôm tối đa 1 bữa/tuần
    model.Add(
        sum(
            B[d]["m1_shrimp"] + B[d]["m2_shrimp"] + B[d]["c_seafood"]
            for d in DAYS
        ) <= 1
    )

    # Rule 2.5: Thịt bò tối đa 1 bữa/tuần
    model.Add(
        sum(B[d]["m1_beef"] + B[d]["m2_beef"] for d in DAYS) <= 1
    )

    # Rule 2.2: Cơm rang tối đa 1 lần/tuần
    model.Add(sum(B[d]["co_rang"] for d in DAYS) <= 1)

    # Rule 2.3: Cơm gà tối đa 1 lần/tuần
    model.Add(sum(B[d]["co_ga"] for d in DAYS) <= 1)

    # CO variety: mỗi món CO xuất hiện tối đa N lần/tuần.
    # N tối thiểu = ceil(len(DAYS) / số món CO "tự do" (không bị rang/ga giới hạn))
    # Đảm bảo luôn feasible ngay cả khi không dùng co_rang / co_ga.
    _free_CO = [i for i, d in enumerate(dCO) if not d.is_com_rang and not d.is_com_ga]
    _n_free = max(1, len(_free_CO))
    _co_max_repeat = max(2, -(-len(DAYS) // _n_free))  # ceiling division
    for i in range(nCO):
        is_i = [1 if j == i else 0 for j in range(nCO)]
        model.Add(
            sum(_elem(model, f"co_{i}_{d}", y[d]["CO"], is_i) for d in DAYS)
            <= _co_max_repeat
        )

    # ──────────────────────────────────────────────────────────────────────────
    # OBJECTIVE — maximize preference_score tổng hợp
    # Hướng solver ưu tiên món ưa thích, hạn chế món ít được ưa thích
    # ──────────────────────────────────────────────────────────────────────────

    score_terms = []
    for day in DAYS:
        for slot, cat in [
            ("M1", "M"), ("M2", "M"),
            ("R", "R"), ("C", "C"), ("CO", "CO"), ("Q", "Q"),
        ]:
            score_terms.append(
                _score_var(model, f"sc_{slot}_{day}", y[day][slot], pref_scores[cat])
            )

    model.maximize(sum(score_terms))

    # ──────────────────────────────────────────────────────────────────────────
    # DIAGNOSTICS
    # ──────────────────────────────────────────────────────────────────────────

    logger.info(
        "Dishes — M=%d R=%d C=%d CO=%d Q=%d",
        nM, nR, nC, nCO, nQ,
    )
    logger.info(
        "M flags — vien=%d beef=%d shrimp=%d fried=%d",
        sum(d.is_vien   for d in dM),
        sum(d.has_beef  for d in dM),
        sum(d.has_shrimp for d in dM),
        sum(d.is_fried  for d in dM),
    )
    logger.info(
        "C soup_type — leaf=%d root=%d unknown=%d",
        sum(1 for d in dC if d.soup_type == "leaf"),
        sum(1 for d in dC if d.soup_type == "root_fruit"),
        sum(1 for d in dC if d.soup_type is None),
    )
    logger.info(
        "R veg_type — leaf=%d root=%d",
        sum(1 for d in dR if d.veg_type == "leaf"),
        sum(1 for d in dR if d.veg_type == "root_fruit"),
    )
    logger.info(
        "CO — rang=%d ga=%d",
        sum(d.is_com_rang for d in dCO),
        sum(d.is_com_ga   for d in dCO),
    )

    # ──────────────────────────────────────────────────────────────────────────
    # SOLVER
    # ──────────────────────────────────────────────────────────────────────────

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds  = timeout_s
    solver.parameters.num_search_workers   = 8
    solver.parameters.search_branching     = cp_model.PORTFOLIO_SEARCH
    solver.parameters.cp_model_presolve    = True
    solver.parameters.log_search_progress  = True

    t0     = time.perf_counter()
    status = solver.solve(model)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    STATUS_MAP = {
        cp_model.OPTIMAL:       "optimal",
        cp_model.FEASIBLE:      "feasible",
        cp_model.INFEASIBLE:    "infeasible",
        cp_model.MODEL_INVALID: "invalid",
        cp_model.UNKNOWN:       "timeout",
    }

    logger.info(
        "Solver — status=%s elapsed=%dms objective=%.0f",
        solver.status_name(status), elapsed_ms, solver.objective_value,
    )

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status":        STATUS_MAP.get(status, "unknown"),
            "solve_time_ms": elapsed_ms,
            "score":         0,
            "menu":          WeekMenu(),
            "violations":    [],
        }

    dish_lists = {
        "M1": dM, "M2": dM,
        "R":  dR, "C":  dC,
        "CO": dCO, "Q": dQ,
    }
    menu_raw: Dict[str, Dict[str, Dish]] = {}
    for d in DAYS:
        menu_raw[d] = {}
        for slot in SLOTS:
            idx = solver.value(y[d][slot])
            if idx < 0 or idx >= len(dish_lists[slot]):
                logger.error("Invalid index %s for %s/%s", idx, d, slot)
                return None
            menu_raw[d][slot] = dish_lists[slot][idx]

    return {
        "status":        STATUS_MAP.get(status, "unknown"),
        "solve_time_ms": elapsed_ms,
        "score":         int(solver.objective_value),
        "menu":          _to_week_menu(menu_raw),
        "violations":    [],
    }


def _to_week_menu(raw: Dict[str, Dict[str, Dish]]) -> WeekMenu:
    return WeekMenu(**{
        d: DayMenu(
            M1=raw[d].get("M1"), M2=raw[d].get("M2"),
            R=raw[d].get("R"),   C=raw[d].get("C"),
            CO=raw[d].get("CO"), Q=raw[d].get("Q"),
        )
        for d in DAYS
    })
