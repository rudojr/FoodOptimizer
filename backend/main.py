"""
main.py – FastAPI application cho FoodOptimizer
Serve API tại /api/* và frontend tại /
"""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import data_loader
import local_repair
import optimizer
from models import (
    Dish,
    OptimizeRequest, OptimizeResponse,
    RepairRequest, RepairResponse,
    AutoRepairRequest, AutoRepairResponse,
    ValidateRequest, ValidateResponse,
    WeekMenu,
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

# ─── Global state ──────────────────────────────────────────────────────────────
DISHES: Dict[str, List[Dish]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load data từ Excel khi khởi động."""
    global DISHES
    if not EXCEL_PATH.exists():
        logger.error("Không tìm thấy file: %s", EXCEL_PATH)
        raise RuntimeError(f"File không tồn tại: {EXCEL_PATH}")

    logger.info("Đang tải dữ liệu từ %s ...", EXCEL_PATH)
    DISHES = data_loader.load_dishes_from_excel(EXCEL_PATH, sample_size=100)
    stats = data_loader.get_data_stats(DISHES)
    logger.info("Đã tải: %s", stats)
    yield
    logger.info("Server dừng.")


# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="FoodOptimizer API",
    description="Tối ưu hóa thực đơn tuần bằng OR-Tools CP-SAT + Local Search",
    version="1.0.0",
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
    return {
        "status": "ok",
        "data_loaded": bool(DISHES),
        "stats": stats,
    }


@app.get("/api/dishes", tags=["data"])
def get_dishes():
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")
    return {
        cat: [d.model_dump() for d in lst]
        for cat, lst in DISHES.items()
    }


@app.post("/api/optimize", response_model=OptimizeResponse, tags=["optimization"])
def optimize_menu(req: OptimizeRequest):
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")

    logger.info("Bắt đầu optimize (timeout=%.1fs, allow_repeat=%s)",
                req.timeout_seconds, req.allow_dish_repeat)

    result = optimizer.build_and_solve(
        DISHES,
        timeout_s=req.timeout_seconds,
        allow_repeat=req.allow_dish_repeat,
    )

    if result is None:
        raise HTTPException(
            422,
            detail={
                "error": "infeasible",
                "message": ("Không tìm được thực đơn thỏa mãn tất cả ràng buộc. "
                            "Thử bật allow_dish_repeat=true hoặc thêm dữ liệu."),
            },
        )

    # Kiểm tra ràng buộc trên kết quả (double-check)
    violations = local_repair.check_constraints(result['menu'])

    # Thống kê
    stats = _compute_weekly_stats(result['menu'])

    return OptimizeResponse(
        status=result['status'],
        solve_time_ms=result['solve_time_ms'],
        score=result['score'],
        menu=result['menu'],
        violations=violations,
        stats=stats,
    )


@app.post("/api/repair", response_model=RepairResponse, tags=["repair"])
def repair_slot(req: RepairRequest):
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")

    if req.day not in optimizer.DAYS:
        raise HTTPException(400, f"day phải là một trong: {optimizer.DAYS}")
    if req.slot not in optimizer.SLOTS:
        raise HTTPException(400, f"slot phải là một trong: {optimizer.SLOTS}")

    # Lấy món hiện tại
    day_menu = getattr(req.menu, req.day, None)
    current = getattr(day_menu, req.slot, None) if day_menu else None

    alternatives = local_repair.get_alternatives(req.menu, req.day, req.slot, DISHES)

    return RepairResponse(
        current_dish=current,
        alternatives=alternatives[:20],  # Trả về tối đa 20 lựa chọn
    )


@app.post("/api/auto-repair", response_model=AutoRepairResponse, tags=["repair"])
def auto_repair_menu(req: AutoRepairRequest):
    if not DISHES:
        raise HTTPException(503, "Dữ liệu chưa được tải")

    viols_before = local_repair.check_constraints(req.menu)
    n_before = len([v for v in viols_before if v.severity == 'error'])

    repaired_menu, changes = local_repair.auto_repair(
        req.menu, DISHES, max_iterations=req.max_iterations
    )

    viols_after = local_repair.check_constraints(repaired_menu)
    n_after = len([v for v in viols_after if v.severity == 'error'])

    return AutoRepairResponse(
        menu=repaired_menu,
        violations_before=n_before,
        violations_after=n_after,
        changes=changes,
    )


@app.post("/api/validate", response_model=ValidateResponse, tags=["validation"])
def validate_menu(req: ValidateRequest):
    """
    Kiểm tra tất cả ràng buộc cho một thực đơn bất kỳ.
    Dùng để validate sau khi người dùng chỉnh sửa thủ công.
    """
    violations = local_repair.check_constraints(req.menu)
    score = local_repair._compute_score(req.menu)
    return ValidateResponse(
        violations=violations,
        is_valid=all(v.severity != 'error' for v in violations),
        score=score,
    )


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _compute_weekly_stats(menu: WeekMenu) -> dict:
    raw = local_repair._menu_to_raw(menu)
    days = optimizer.DAYS

    vien_count = sum(
        1 for d in days for s in ['M1', 'M2']
        if (dish := raw[d].get(s)) and dish.is_vien
    )
    beef_used = any(
        raw[d].get(s) and raw[d][s].has_beef
        for d in days for s in ['M1', 'M2']
    )
    shrimp_used = any(
        raw[d].get(s) and raw[d][s].has_shrimp
        for d in days for s in ['M1', 'M2', 'C']
    )
    com_rang_days = [d for d in days if (dish := raw[d].get('CO')) and dish.is_com_rang]
    com_ga_days   = [d for d in days if (dish := raw[d].get('CO')) and dish.is_com_ga]
    fried_per_day = {
        d: sum(1 for s in ['M1', 'M2', 'R'] if (dish := raw[d].get(s)) and dish.is_fried)
        for d in days
    }
    preferred_count = sum(
        1 for d in days for s in optimizer.SLOTS
        if (dish := raw[d].get(s)) and dish.preferred
    )

    return {
        'vien_count':      vien_count,
        'beef_used':       beef_used,
        'shrimp_used':     shrimp_used,
        'com_rang_days':   com_rang_days,
        'com_ga_days':     com_ga_days,
        'fried_per_day':   fried_per_day,
        'preferred_count': preferred_count,
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
