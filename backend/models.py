"""
models.py – Pydantic schemas cho FoodOptimizer API
"""
from __future__ import annotations
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ─── Dish ──────────────────────────────────────────────────────────────────────

class Dish(BaseModel):
    code: str
    name: str
    category: str                    # 'M' | 'R' | 'C' | 'CO' | 'Q'
    is_fried: bool = False
    is_vien: bool = False
    has_fish: bool = False
    has_shrimp: bool = False
    has_egg: bool = False
    has_beef: bool = False
    has_basa: bool = False
    has_milk: bool = False
    is_com_rang: bool = False
    is_com_ga: bool = False
    veg_type: Optional[str] = None   # 'leaf' | 'root_fruit' (R category)
    soup_type: Optional[str] = None  # 'leaf' | 'root_fruit' (C category)
    preferred: bool = False
    less_preferred: bool = False
    preference_score: int = 1


# ─── Menu ──────────────────────────────────────────────────────────────────────

class DayMenu(BaseModel):
    M1: Optional[Dish] = None
    M2: Optional[Dish] = None
    R:  Optional[Dish] = None
    C:  Optional[Dish] = None
    CO: Optional[Dish] = None
    Q:  Optional[Dish] = None


class WeekMenu(BaseModel):
    mon: Optional[DayMenu] = None
    tue: Optional[DayMenu] = None
    wed: Optional[DayMenu] = None
    thu: Optional[DayMenu] = None
    fri: Optional[DayMenu] = None


# ─── Violations ────────────────────────────────────────────────────────────────

class Violation(BaseModel):
    type: str
    day: Optional[str] = None   # 'mon'|'tue'|… hoặc None nếu là ràng buộc tuần
    slot: Optional[str] = None  # 'M1'|'M2'|'R'|'C'|'CO'|'Q'
    severity: str = "error"     # 'error' | 'warning'
    message: str


# ─── API Requests ──────────────────────────────────────────────────────────────

class OptimizeRequest(BaseModel):
    week_start: Optional[str] = Field(None, description="YYYY-MM-DD")
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    allow_dish_repeat: bool = Field(
        default=False,
        description="Cho phép lặp món trong tuần (dùng khi dữ liệu ít)"
    )


class RepairRequest(BaseModel):
    menu: WeekMenu
    day: str   # 'mon' | 'tue' | 'wed' | 'thu' | 'fri'
    slot: str  # 'M1' | 'M2' | 'R' | 'C' | 'CO' | 'Q'


class AutoRepairRequest(BaseModel):
    menu: WeekMenu
    max_iterations: int = Field(default=20, ge=1, le=50)


class ValidateRequest(BaseModel):
    menu: WeekMenu


# ─── API Responses ─────────────────────────────────────────────────────────────

class DataStats(BaseModel):
    total: int
    by_category: Dict[str, int]


class OptimizeResponse(BaseModel):
    status: str          
    solve_time_ms: int
    score: int
    menu: WeekMenu
    violations: List[Violation]
    stats: Dict[str, Any]


class AlternativeItem(BaseModel):
    dish: Dish
    score: int
    remaining_violations: int


class RepairResponse(BaseModel):
    current_dish: Optional[Dish]
    alternatives: List[AlternativeItem]


class AutoRepairResponse(BaseModel):
    menu: WeekMenu
    violations_before: int
    violations_after: int
    changes: List[Dict[str, Any]]  # [{day, slot, old_dish, new_dish}]


class ValidateResponse(BaseModel):
    violations: List[Violation]
    is_valid: bool
    score: int
