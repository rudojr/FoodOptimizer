"""
main.py – FastAPI application cho FoodOptimizer
Serve API tại /api/* và frontend tại /
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import data_loader
import local_repair
import optimizer
from models import (
    Dish,
    MonthMenu, WeekMenu,
    OptimizeRequest, OptimizeResponse,
    RepairRequest, RepairResponse,
    AutoRepairRequest, AutoRepairResponse,
    ValidateRequest, ValidateResponse,
    Violation,
)

# ─── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
ROOT_DIR     = BASE_DIR.parent
EXCEL_PATH   = ROOT_DIR / "raw.xlsx"
FRONTEND_DIR = ROOT_DIR / "frontend"

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DISHES: Dict[str, List[Dish]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global DISHES
    if not EXCEL_PATH.exists():
        logger.error("Không tìm thấy file: %s", EXCEL_PATH)
        raise RuntimeError(f"File không tồn tại: {EXCEL_PATH}")

    logger.info("Đang tải dữ liệu từ %s ...", EXCEL_PATH)
    DISHES = data_loader.load_dishes_from_excel(EXCEL_PATH)
    stats = data_loader.get_data_stats(DISHES)
    logger.info("Đã tải: %s", stats)
    yield
    logger.info("Server dừng.")


# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="FoodOptimizer API",
    description="Tối ưu hóa thực đơn tháng bằng OR-Tools CP-SAT",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)


@app.get("/health", tags=["system"])
def health_check():
    stats = data_loader.get_data_stats(DISHES) if DISHES else {}
    return {"status": "ok", "data_loaded": bool(DISHES), "stats": stats}


@app.get("/api/dishes", tags=["data"])
def get_dishes():
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")
    return {cat: [d.model_dump() for d in lst] for cat, lst in DISHES.items()}


@app.post("/api/optimize", response_model=OptimizeResponse, tags=["optimization"])
def optimize_menu(req: OptimizeRequest):
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")

    logger.info(
        "Optimize — timeout=%.1fs repeat=%s rule_set=%d n_weeks=%d",
        req.timeout_seconds, req.allow_dish_repeat, req.rule_set, req.n_weeks,
    )

    result = optimizer.build_and_solve(
        DISHES,
        timeout_s=req.timeout_seconds,
        allow_repeat=req.allow_dish_repeat,
        rule_set=req.rule_set,
        n_weeks=req.n_weeks,
    )

    if result is None:
        raise HTTPException(
            422,
            detail={
                "error": "infeasible",
                "message": (
                    "Không tìm được thực đơn thỏa mãn tất cả ràng buộc. "
                    "Thử bật allow_dish_repeat=true hoặc thêm dữ liệu."
                ),
            },
        )

    violations = _check_month_constraints(result["menu"])
    stats      = _compute_monthly_stats(result["menu"])

    return OptimizeResponse(
        status=result["status"],
        solve_time_ms=result["solve_time_ms"],
        score=result["score"],
        menu=result["menu"],
        violations=violations,
        stats=stats,
    )


@app.post("/api/repair", response_model=RepairResponse, tags=["repair"])
def repair_slot(req: RepairRequest):
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")
    if req.day not in optimizer.DAY_NAMES:
        raise HTTPException(400, f"day phải là một trong: {optimizer.DAY_NAMES}")
    if req.slot not in optimizer.SLOTS:
        raise HTTPException(400, f"slot phải là một trong: {optimizer.SLOTS}")

    day_menu = getattr(req.menu, req.day, None)
    current  = getattr(day_menu, req.slot, None) if day_menu else None
    alternatives = local_repair.get_alternatives(req.menu, req.day, req.slot, DISHES)

    return RepairResponse(current_dish=current, alternatives=alternatives[:20])


@app.post("/api/auto-repair", response_model=AutoRepairResponse, tags=["repair"])
def auto_repair_menu(req: AutoRepairRequest):
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")

    viols_before   = local_repair.check_constraints(req.menu)
    n_before       = len([v for v in viols_before if v.severity == "error"])
    repaired, changes = local_repair.auto_repair(req.menu, DISHES, req.max_iterations)
    viols_after    = local_repair.check_constraints(repaired)
    n_after        = len([v for v in viols_after if v.severity == "error"])

    return AutoRepairResponse(
        menu=repaired,
        violations_before=n_before,
        violations_after=n_after,
        changes=changes,
    )


@app.post("/api/validate", response_model=ValidateResponse, tags=["validation"])
def validate_menu(req: ValidateRequest):
    violations = local_repair.check_constraints(req.menu)
    score      = local_repair._compute_score(req.menu)
    return ValidateResponse(
        violations=violations,
        is_valid=all(v.severity != "error" for v in violations),
        score=score,
    )


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _check_month_constraints(menu: MonthMenu) -> List[Violation]:
    """Kiểm tra ràng buộc từng tuần + ràng buộc tháng."""
    violations: List[Violation] = []

    for wk_name in ["w1", "w2", "w3", "w4"]:
        wk_menu: Optional[WeekMenu] = getattr(menu, wk_name, None)
        if not wk_menu:
            continue
        for v in local_repair.check_constraints(wk_menu):
            # Đính thêm thông tin tuần vào violation
            violations.append(v.model_copy(update={"week": wk_name}))

    # Ràng buộc tháng: com_rang tổng
    rang_count = sum(
        1
        for wk_name in ["w1", "w2", "w3", "w4"]
        for d in optimizer.DAY_NAMES
        if (wk := getattr(menu, wk_name))
        and (dm := getattr(wk, d, None))
        and dm and dm.CO and dm.CO.is_com_rang
    )
    if rang_count > 1:
        violations.append(Violation(
            type="month_com_rang",
            severity="warning",
            message=f"Tháng có {rang_count} lần cơm rang (kiểm tra bộ rule)",
        ))

    return violations


def _compute_monthly_stats(menu: MonthMenu) -> dict:
    weeks = ["w1", "w2", "w3", "w4"]
    days  = optimizer.DAY_NAMES
    slots = optimizer.SLOTS

    vien_count = beef_used = shrimp_used = 0
    com_rang_weeks: List[str] = []
    com_ga_weeks:   List[str] = []
    preferred_count = 0
    fried_per_week: Dict[str, int] = {}

    for wk_name in weeks:
        wk_menu = getattr(menu, wk_name, None)
        if not wk_menu:
            continue
        raw = local_repair._menu_to_raw(wk_menu)
        week_fried = 0
        for d in days:
            for s in ["M1", "M2"]:
                dish = raw[d].get(s)
                if not dish:
                    continue
                if dish.is_vien:
                    vien_count += 1
                if dish.has_beef:
                    beef_used = True
                if dish.has_shrimp:
                    shrimp_used = True
                if dish.is_fried:
                    week_fried += 1
            co = raw[d].get("CO")
            if co:
                if co.is_com_rang and wk_name not in com_rang_weeks:
                    com_rang_weeks.append(wk_name)
                if co.is_com_ga and wk_name not in com_ga_weeks:
                    com_ga_weeks.append(wk_name)
            for s in slots:
                dish = raw[d].get(s)
                if dish and dish.preferred:
                    preferred_count += 1
        fried_per_week[wk_name] = week_fried

    return {
        "vien_count":       vien_count,
        "beef_used":        beef_used,
        "shrimp_used":      shrimp_used,
        "com_rang_weeks":   com_rang_weeks,
        "com_ga_weeks":     com_ga_weeks,
        "fried_per_week":   fried_per_week,
        "preferred_count":  preferred_count,
    }


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    logger.info("Serving frontend from %s", FRONTEND_DIR)
else:
    logger.warning("Frontend directory không tồn tại: %s", FRONTEND_DIR)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )
