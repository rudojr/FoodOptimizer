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
    # Cách chế biến
    is_fried: bool = False
    is_vien: bool = False
    # Nhóm protein / nguyên liệu chính
    has_fish: bool = False
    has_shrimp: bool = False
    has_egg: bool = False
    has_beef: bool = False
    has_pork: bool = False           # thịt lợn
    has_chicken: bool = False        # thịt gà
    has_tofu: bool = False           # đậu phụ
    has_basa: bool = False
    has_milk: bool = False           # chứa sữa (nguyên liệu)
    # Phân loại giò/chả/xúc xích
    is_gio: bool = False
    is_cha: bool = False
    is_xuc_xich: bool = False
    is_chan_gio: bool = False
    # CO – loại cơm
    is_com_rang: bool = False
    is_com_ga: bool = False
    is_com_cai_thien: bool = False   # cơm rang/gà = bữa cải thiện
    # Phân loại rau (R)
    veg_type: Optional[str] = None           # 'leaf' | 'root_fruit'
    veg_cooking_method: Optional[str] = None  # 'boil' | 'stir_fry'
    needs_heavy_prep: bool = False            # rau củ cần gọt vỏ nhiều
    # Phân loại canh (C)
    soup_type: Optional[str] = None  # 'leaf' | 'root_fruit'
    is_bone_soup: bool = False       # canh nấu từ xương
    # Phân loại quà chiều (Q)
    is_fruit_snack: bool = False     # hoa quả
    is_cake_snack: bool = False      # bánh
    is_milk_snack: bool = False      # sữa / sữa chua
    is_cabbage: bool = False          # R: thuộc họ cải (kỵ thịt gà)
    is_banana: bool = False
    is_solite: bool = False
    has_watermelon: bool = False
    # Độ ưa thích
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


class MonthMenu(BaseModel):
    w1: Optional[WeekMenu] = None
    w2: Optional[WeekMenu] = None
    w3: Optional[WeekMenu] = None
    w4: Optional[WeekMenu] = None


# ─── Violations ────────────────────────────────────────────────────────────────

class Violation(BaseModel):
    type: str
    week: Optional[str] = None   # 'w1'|'w2'|'w3'|'w4' hoặc None nếu ràng buộc tháng
    day: Optional[str] = None    # 'mon'|'tue'|… hoặc None nếu là ràng buộc tuần
    slot: Optional[str] = None   # 'M1'|'M2'|'R'|'C'|'CO'|'Q'
    severity: str = "error"      # 'error' | 'warning'
    message: str


# ─── API Requests ──────────────────────────────────────────────────────────────

class OptimizeRequest(BaseModel):
    month_start: Optional[str] = Field(None, description="YYYY-MM-DD (ngày đầu tháng)")
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    allow_dish_repeat: bool = Field(
        default=True,
        description="Cho phép lặp món giữa các tuần (luôn bật nội tuần không lặp)"
    )
    rule_set: int = Field(
        default=1, ge=1, le=10,
        description="Bộ rule riêng cho từng điểm (1-10)"
    )
    n_weeks: int = Field(
        default=4, ge=1, le=5,
        description="Số tuần trong tháng"
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
    menu: MonthMenu
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
    changes: List[Dict[str, Any]]


class ValidateResponse(BaseModel):
    violations: List[Violation]
    is_valid: bool
    score: int
