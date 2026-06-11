from __future__ import annotations
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
) -> Optional[Dict]:
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
        model.Add(y[day]["M1"] != y[day]["M2"])
        model.Add(B[day]["m1_fried"] + B[day]["m2_fried"] + B[day]["r_fried"] <= 1)
        model.AddImplication(B[day]["m1_fish"], B[day]["m2_egg"].Not())
        model.AddImplication(B[day]["m2_fish"], B[day]["m1_egg"].Not())
        
        for fish_src in [B[day]["m1_fish"], B[day]["m2_fish"], B[day]["c_seafood"]]:
            model.AddImplication(fish_src, B[day]["q_milk"].Not())
            
        model.AddImplication(B[day]["r_root"], B[day]["c_not_leaf"].Not())

    # ──────────────────────────────────────────────────────────────────────────
    # WEEKLY CONSTRAINTS
    # ──────────────────────────────────────────────────────────────────────────
    if not allow_repeat:
        # Bắt buộc kiểm tra độ dài mảng trước khi gán AllDifferent
        if len(valid_M) >= len(DAYS) * 2:
            model.AddAllDifferent([y[d]["M1"] for d in DAYS] + [y[d]["M2"] for d in DAYS])
        if nR >= len(DAYS):
            model.AddAllDifferent([y[d]["R"]  for d in DAYS])
        if nC >= len(DAYS):
            model.AddAllDifferent([y[d]["C"]  for d in DAYS])
            
    elif len(valid_M) >= len(DAYS):
        model.AddAllDifferent([y[d]["M1"] for d in DAYS])
        model.AddAllDifferent([y[d]["M2"] for d in DAYS])

    model.Add(sum(B[d]["m1_vien"] + B[d]["m2_vien"] for d in DAYS) <= 1)
    model.Add(sum(B[d]["m1_shrimp"] + B[d]["m2_shrimp"] + B[d]["c_seafood"] for d in DAYS) <= 1)
    model.Add(sum(B[d]["m1_beef"] + B[d]["m2_beef"] for d in DAYS) <= 1)
    model.Add(sum(B[d]["co_rang"] for d in DAYS) <= 1)
    model.Add(sum(B[d]["co_ga"] for d in DAYS) <= 1)

    # Tối ưu logic lặp lại món CO (Dùng OnlyEnforceIf thay vì Element)
    _free_CO = [i for i, d in enumerate(dCO) if not d.is_com_rang and not d.is_com_ga]
    _n_free = max(1, len(_free_CO))
    _co_max_repeat = max(2, -(-len(DAYS) // _n_free)) 

    if _co_max_repeat < len(DAYS):
        for i in range(nCO):
            days_used = []
            for d in DAYS:
                b_is_used = model.NewBoolVar(f"is_co_{i}_{d}")
                model.Add(y[d]["CO"] == i).OnlyEnforceIf(b_is_used)
                model.Add(y[d]["CO"] != i).OnlyEnforceIf(b_is_used.Not())
                days_used.append(b_is_used)
            model.Add(sum(days_used) <= _co_max_repeat)

    # ──────────────────────────────────────────────────────────────────────────
    # OBJECTIVE
    # ──────────────────────────────────────────────────────────────────────────
    score_terms = []
    for day in DAYS:
        for slot, cat in [("M1", "M"), ("M2", "M"), ("R", "R"), ("C", "C"), ("CO", "CO"), ("Q", "Q")]:
            score_terms.append(_score_var(model, f"sc_{slot}_{day}", y[day][slot], pref_scores[cat]))

    model.maximize(sum(score_terms))

    # ──────────────────────────────────────────────────────────────────────────
    # SEARCH STRATEGY
    # ──────────────────────────────────────────────────────────────────────────
    main_vars = []
    for d in DAYS:
        main_vars.extend([y[d]["M1"], y[d]["M2"], y[d]["R"], y[d]["C"], y[d]["CO"], y[d]["Q"]])
        
    model.AddDecisionStrategy(
        main_vars,
        cp_model.CHOOSE_FIRST,
        cp_model.SELECT_MIN_VALUE,  # Sửa: Thử index nhỏ nhất (món ngon nhất) đầu tiên
    )

    # ──────────────────────────────────────────────────────────────────────────
    # SOLVER (Chuẩn mực)
    # ──────────────────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_s
    solver.parameters.num_search_workers  = 1
    solver.parameters.search_branching    = cp_model.PORTFOLIO_SEARCH
    solver.parameters.cp_model_presolve   = True
    
    # Optional: Bật dòng dưới nếu bạn chỉ muốn lấy kết quả ĐẦU TIÊN thay vì kết quả TỐT NHẤT trong 8s
    # solver.parameters.stop_after_first_solution = True

    t0 = time.perf_counter()
    status = solver.solve(model)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    STATUS_MAP = {
        cp_model.OPTIMAL:       "optimal",
        cp_model.FEASIBLE:      "feasible",
        cp_model.INFEASIBLE:    "infeasible",
        cp_model.MODEL_INVALID: "invalid",
        cp_model.UNKNOWN:       "timeout",
    }
    status_name = STATUS_MAP.get(status, "unknown")

    logger.info(
        "Solver — status=%s elapsed=%dms",
        status_name, elapsed_ms
    )

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status":        status_name,
            "solve_time_ms": elapsed_ms,
            "score":         0,
            "menu":          WeekMenu(),
            "violations":    [],
        }

    best_score = int(solver.ObjectiveValue())
    
    dish_lists = {"M1": dM, "M2": dM, "R": dR, "C": dC, "CO": dCO, "Q": dQ}
    menu_raw: Dict[str, Dict[str, Dish]] = {}
    
    for d in DAYS:
        menu_raw[d] = {}
        for slot in SLOTS:
            idx = solver.Value(y[d][slot])
            menu_raw[d][slot] = dish_lists[slot][idx]

    # Cần định nghĩa hàm _to_week_menu trong module của bạn, hoặc import nó
    return {
        "status":        status_name,
        "solve_time_ms": elapsed_ms,
        "score":         best_score,
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