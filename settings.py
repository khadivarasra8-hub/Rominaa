# -*- coding: utf-8 -*-
"""
config/settings.py
-------------------
تنظیمات مرکزی سیستم بهره‌برداری شبکه مترو.
تمام پارامترهای پیش‌فرض، ثابت‌های عملیاتی و رنگ‌بندی نمودارها در این فایل
متمرکز شده‌اند تا ماژول‌های دیگر بدون هاردکد کردن مقادیر، از اینجا import کنند.
"""

from dataclasses import dataclass, field
from typing import Dict, List


# --------------------------------------------------------------------------
# اطلاعات کلی پروژه
# --------------------------------------------------------------------------
APP_NAME = "سیستم بهره‌برداری هوشمند شبکه مترو شهری"
APP_VERSION = "1.0.0"
RANDOM_SEED = 42


# --------------------------------------------------------------------------
# بازه‌های زمانی بهره‌برداری (Time Periods)
# --------------------------------------------------------------------------
TIME_PERIODS: List[str] = [
    "peak_morning",   # اوج صبح
    "off_peak_day",   # غیر اوج روز
    "peak_evening",   # اوج عصر
    "night",          # شب
]

TIME_PERIOD_LABELS_FA: Dict[str, str] = {
    "peak_morning": "اوج صبح",
    "off_peak_day": "غیر اوج روز",
    "peak_evening": "اوج عصر",
    "night": "شب",
}

# طول هر بازه زمانی به ساعت (برای تبدیل فرکانس ساعتی به تعداد سفر کل بازه)
TIME_PERIOD_DURATION_HOURS: Dict[str, float] = {
    "peak_morning": 3.0,
    "off_peak_day": 6.0,
    "peak_evening": 3.0,
    "night": 4.0,
}


# --------------------------------------------------------------------------
# پارامترهای پیش‌فرض ماژول برنامه‌ریزی خطی (LP/MILP) - فرکانس اعزام قطار
# --------------------------------------------------------------------------
@dataclass
class LineConfig:
    """پیکربندی یک خط مترو برای مدل فرکانس."""
    name: str
    demand_per_period: Dict[str, int]      # تقاضای مسافر در هر بازه (نفر)
    min_frequency: int                     # حداقل فرکانس مجاز (قطار/ساعت) - ایمنی/سرویس
    max_frequency: int                     # حداکثر فرکانس مجاز (قطار/ساعت) - ظرفیت خط/هدوی
    train_capacity: int = 1200             # ظرفیت هر رام قطار (نفر)
    energy_per_trip_kwh: float = 450.0     # مصرف انرژی هر سفر رفت (kWh)


DEFAULT_LINES: Dict[str, LineConfig] = {
    "line_1": LineConfig(
        name="خط ۱ (شمال-جنوب)",
        demand_per_period={
            "peak_morning": 42000,
            "off_peak_day": 14000,
            "peak_evening": 39000,
            "night": 6000,
        },
        min_frequency=4,
        max_frequency=18,
        train_capacity=1200,
        energy_per_trip_kwh=480.0,
    ),
    "line_2": LineConfig(
        name="خط ۲ (شرق-غرب)",
        demand_per_period={
            "peak_morning": 33000,
            "off_peak_day": 11000,
            "peak_evening": 30000,
            "night": 5000,
        },
        min_frequency=3,
        max_frequency=16,
        train_capacity=1000,
        energy_per_trip_kwh=420.0,
    ),
    "line_3": LineConfig(
        name="خط ۳ (حلقوی)",
        demand_per_period={
            "peak_morning": 21000,
            "off_peak_day": 9000,
            "peak_evening": 19000,
            "night": 4000,
        },
        min_frequency=3,
        max_frequency=14,
        train_capacity=900,
        energy_per_trip_kwh=380.0,
    ),
}

# مجموع ناوگان قابل بهره‌برداری هم‌زمان در کل شبکه (محدودیت سخت)
TOTAL_FLEET_AVAILABLE = 42

# وزن‌های تابع هدف (نرمال‌سازی‌شده): انرژی در برابر زمان انتظار مسافر
LP_OBJECTIVE_WEIGHTS = {
    "energy_weight": 0.45,          # اهمیت نسبی صرفه‌جویی انرژی
    "waiting_time_weight": 0.55,    # اهمیت نسبی کاهش زمان انتظار مسافر
}

# ارزش پولی تقریبی هر واحد (برای تبدیل به یک واحد سنجش مشترک - تومان)
COST_PER_KWH = 4200          # هزینه هر کیلووات ساعت برق (تومان)
COST_PER_PASSENGER_MINUTE = 850  # هزینه فرصت هر دقیقه انتظار مسافر (تومان)


# --------------------------------------------------------------------------
# پارامترهای پیش‌فرض ماژول الگوریتم ژنتیک (GA) - زمان‌بندی تعمیرات پیشگیرانه
# --------------------------------------------------------------------------
@dataclass
class GAConfig:
    """پیکربندی الگوریتم ژنتیک برای زمان‌بندی تعمیرات پیشگیرانه (PM)."""
    fleet_size: int = 42                 # تعداد کل رام‌های ناوگان
    planning_horizon_days: int = 14      # افق زمانی برنامه‌ریزی (روز)
    depot_slots_per_day: int = 5         # ظرفیت هم‌زمان پایانه تعمیراتی در هر روز
    maintenance_interval_days: int = 10  # حداکثر فاصله مجاز بین دو تعمیر متوالی
    min_gap_between_maintenance: int = 6 # حداقل فاصله مجاز (جلوگیری از تعمیر خیلی زودهنگام)

    population_size: int = 80
    generations: int = 150
    crossover_rate: float = 0.85
    mutation_rate: float = 0.12
    elitism_count: int = 4
    tournament_size: int = 3

    # جریمه‌های تابع برازش
    penalty_capacity_violation: float = 100.0     # تخلف از ظرفیت پایانه در یک روز
    penalty_interval_violation: float = 80.0      # تخلف از حداکثر فاصله مجاز تعمیر
    penalty_min_gap_violation: float = 60.0       # تخلف از حداقل فاصله مجاز
    penalty_peak_disruption: float = 40.0         # جریمه انجام تعمیر در روزهای پرتقاضا
    penalty_workload_imbalance: float = 0.5       # جریمه عدم توازن بار کاری روزانه


DEFAULT_GA_CONFIG = GAConfig()

# روزهای هفته با تقاضای بالا (برای جریمه اختلال سرویس، 0=شنبه ... 6=جمعه)
HIGH_DEMAND_WEEKDAYS = [0, 1, 2, 3, 6]   # شنبه تا سه‌شنبه و جمعه (نمونه فرضی)


# --------------------------------------------------------------------------
# رنگ‌بندی و ظاهر نمودارها
# --------------------------------------------------------------------------
CHART_COLORS = {
    "primary": "#0B5FFF",
    "secondary": "#00B8A9",
    "accent": "#FF6B6B",
    "warning": "#FFB020",
    "success": "#28C76F",
    "background": "#0E1117",
    "grid": "#2A2E39",
    "line_1": "#0B5FFF",
    "line_2": "#00B8A9",
    "line_3": "#FF6B6B",
}

PLOTLY_TEMPLATE = "plotly_dark"
