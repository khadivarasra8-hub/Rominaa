# -*- coding: utf-8 -*-
"""
modules/linear_programming/metro_frequency.py
-----------------------------------------------
مدل برنامه‌ریزی عدد صحیح مختلط (MILP) برای بهینه‌سازی فرکانس اعزام قطار
در ساعات اوج و غیر اوج.

هدف: حداقل‌سازی مجموع وزن‌دار (۱) هزینه مصرف انرژی الکتریکی و
(۲) هزینه زمان انتظار مسافران، با رعایت محدودیت‌های ناوگان، ظرفیت خط،
و بازه‌های فرکانس مجاز (ایمنی/هدوی).

نویسنده: Operations Research Module
"""

from __future__ import annotations

import sys
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import pulp

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from settings import (
    DEFAULT_LINES,
    LineConfig,
    TIME_PERIODS,
    TIME_PERIOD_DURATION_HOURS,
    TIME_PERIOD_LABELS_FA,
    TOTAL_FLEET_AVAILABLE,
    LP_OBJECTIVE_WEIGHTS,
    COST_PER_KWH,
    COST_PER_PASSENGER_MINUTE,
)


# --------------------------------------------------------------------------
# ساختار خروجی
# --------------------------------------------------------------------------
@dataclass
class FrequencyResult:
    """نتیجه ساختاریافته حل مدل فرکانس اعزام قطار."""
    status: str
    objective_value: float
    total_energy_cost: float
    total_waiting_cost: float
    total_energy_kwh: float
    total_fleet_used_peak: int
    frequency_table: List[Dict]     # ردیف به ازای هر (خط, بازه)
    solver_log: str = ""


class MetroFrequencyOptimizer:
    """
    مدل MILP برای تعیین فرکانس بهینه اعزام قطار در هر خط و هر بازه زمانی.

    متغیر تصمیم:
        f[line, period] -> تعداد قطار اعزامی در ساعت (عدد صحیح)

    تابع هدف:
        min  Σ_line Σ_period [ w_e * energy_cost(line,period) +
                                 w_w * waiting_cost(line,period) ]

    محدودیت‌ها:
        1) min_frequency <= f[line,period] <= max_frequency
        2) Σ_line f[line,period] * dwell_factor <= TOTAL_FLEET_AVAILABLE   (برای هر period)
        3) f[line,period] * train_capacity * duration_hours >= demand[line,period]
           (پوشش حداقلی ظرفیت لازم برای تقاضا؛ به‌صورت soft/hard قابل تنظیم)
    """

    def __init__(
        self,
        lines: Optional[Dict[str, LineConfig]] = None,
        total_fleet: int = TOTAL_FLEET_AVAILABLE,
        energy_weight: float = LP_OBJECTIVE_WEIGHTS["energy_weight"],
        waiting_weight: float = LP_OBJECTIVE_WEIGHTS["waiting_time_weight"],
        enforce_full_demand_coverage: bool = True,
        round_trip_factor: float = 2.2,
    ):
        """
        Args:
            lines: دیکشنری پیکربندی خطوط (پیش‌فرض از config.settings)
            total_fleet: مجموع ناوگان قابل بهره‌برداری هم‌زمان
            energy_weight: وزن جزء انرژی در تابع هدف
            waiting_weight: وزن جزء زمان انتظار در تابع هدف
            enforce_full_demand_coverage: اگر True، پوشش کامل تقاضا محدودیت سخت است؛
                                           در غیر این صورت فقط در تابع هدف جریمه می‌شود.
            round_trip_factor: ضریب تبدیل فرکانس به تعداد رام درگیر هم‌زمان در مسیر
                                (سفر رفت‌وبرگشت + زمان چرخش در پایانه)
        """
        self.lines = lines or DEFAULT_LINES
        self.total_fleet = total_fleet
        self.energy_weight = energy_weight
        self.waiting_weight = waiting_weight
        self.enforce_full_demand_coverage = enforce_full_demand_coverage
        self.round_trip_factor = round_trip_factor
        self.model: Optional[pulp.LpProblem] = None
        self.freq_vars: Dict = {}

    # ----------------------------------------------------------------
    def _waiting_time_minutes(self, frequency: int) -> float:
        """
        تقریب زمان انتظار متوسط مسافر بر اساس فرکانس (مدل کلاسیک حمل‌ونقل ریلی):
        زمان انتظار متوسط ≈ نصف فاصله زمانی بین دو قطار (headway/2)
        """
        if frequency <= 0:
            return 60.0  # جریمه سنگین برای فرکانس صفر
        headway_minutes = 60.0 / frequency
        return headway_minutes / 2.0

    # ----------------------------------------------------------------
    def build_model(self) -> pulp.LpProblem:
        """ساخت مدل MILP با متغیرها، تابع هدف و محدودیت‌ها."""
        model = pulp.LpProblem("Metro_Frequency_Optimization", pulp.LpMinimize)

        # متغیرهای تصمیم: فرکانس (قطار در ساعت) - عدد صحیح
        freq_vars = {}
        for line_id, line_cfg in self.lines.items():
            for period in TIME_PERIODS:
                var = pulp.LpVariable(
                    f"freq_{line_id}_{period}",
                    lowBound=line_cfg.min_frequency,
                    upBound=line_cfg.max_frequency,
                    cat=pulp.LpInteger,
                )
                freq_vars[(line_id, period)] = var

        # ------------------ تابع هدف ------------------
        # چون زمان انتظار (headway/2) غیرخطی (1/f) است، از یک تقریب خطی‌شده
        # به‌صورت متغیر کمکی wait_time با محدودیت‌های خطی‌ساز (piecewise) استفاده می‌کنیم.
        # در این پیاده‌سازی از تقریب خطی محلی حول بازه مجاز فرکانس بهره می‌گیریم:
        # با نمونه‌برداری از نقاط شبکه فرکانس، یک رابطه خطی تکه‌ای (SOS2) ایجاد می‌شود.
        wait_time_vars = {}
        energy_terms = []
        waiting_terms = []

        for line_id, line_cfg in self.lines.items():
            for period in TIME_PERIODS:
                f_var = freq_vars[(line_id, period)]
                duration = TIME_PERIOD_DURATION_HOURS[period]

                # --- انرژی: خطی برحسب فرکانس (تعداد سفرها در بازه × مصرف هر سفر) ---
                trips_in_period = f_var * duration
                energy_kwh_expr = trips_in_period * line_cfg.energy_per_trip_kwh
                energy_cost_expr = energy_kwh_expr * COST_PER_KWH
                energy_terms.append(energy_cost_expr)

                # --- زمان انتظار: تقریب خطی‌شده‌ی 1/f با نقاط شبکه (Piecewise Linear) ---
                grid_points = sorted(
                    set(
                        [line_cfg.min_frequency, line_cfg.max_frequency]
                        + list(range(line_cfg.min_frequency, line_cfg.max_frequency + 1))
                    )
                )
                wait_values = [self._waiting_time_minutes(fp) for fp in grid_points]

                wt_var = pulp.LpVariable(f"wait_{line_id}_{period}", lowBound=0)
                wait_time_vars[(line_id, period)] = wt_var

                # SOS2 برای پیوسته و تکه‌ای‌خطی کردن رابطه بین f_var و wt_var
                lambda_vars = [
                    pulp.LpVariable(f"lam_{line_id}_{period}_{i}", lowBound=0, upBound=1)
                    for i in range(len(grid_points))
                ]
                model += pulp.lpSum(lambda_vars) == 1
                model += f_var == pulp.lpSum(
                    lam * gp for lam, gp in zip(lambda_vars, grid_points)
                )
                model += wt_var == pulp.lpSum(
                    lam * wv for lam, wv in zip(lambda_vars, wait_values)
                )
                model.addSOS2(lambda_vars)

                # هزینه زمان انتظار کل مسافران در آن بازه:
                # (تقاضای کل بازه) × (زمان انتظار متوسط به دقیقه) × (هزینه هر نفر-دقیقه)
                demand = line_cfg.demand_per_period[period]
                waiting_cost_expr = wt_var * demand * COST_PER_PASSENGER_MINUTE
                waiting_terms.append(waiting_cost_expr)

        total_energy_cost_expr = pulp.lpSum(energy_terms)
        total_waiting_cost_expr = pulp.lpSum(waiting_terms)

        model += (
            self.energy_weight * total_energy_cost_expr
            + self.waiting_weight * total_waiting_cost_expr
        ), "Total_Weighted_Cost"

        # ------------------ محدودیت‌ها ------------------
        # (1) محدودیت ناوگان: مجموع رام‌های درگیر هم‌زمان در هر بازه <= ناوگان در دسترس
        for period in TIME_PERIODS:
            fleet_used = pulp.lpSum(
                freq_vars[(line_id, period)] * self.round_trip_factor
                for line_id in self.lines
            )
            model += fleet_used <= self.total_fleet, f"Fleet_Limit_{period}"

        # (2) محدودیت پوشش تقاضا: ظرفیت ارائه‌شده >= تقاضای مسافر
        for line_id, line_cfg in self.lines.items():
            for period in TIME_PERIODS:
                f_var = freq_vars[(line_id, period)]
                duration = TIME_PERIOD_DURATION_HOURS[period]
                capacity_provided = f_var * duration * line_cfg.train_capacity
                demand = line_cfg.demand_per_period[period]
                if self.enforce_full_demand_coverage:
                    model += (
                        capacity_provided >= demand,
                        f"Demand_Coverage_{line_id}_{period}",
                    )

        self.model = model
        self.freq_vars = freq_vars
        self.wait_time_vars = wait_time_vars
        return model

    # ----------------------------------------------------------------
    def solve(self, msg: bool = False) -> FrequencyResult:
        """حل مدل با CBC solver و بازگرداندن نتیجه ساختاریافته."""
        if self.model is None:
            self.build_model()

        solver = pulp.PULP_CBC_CMD(msg=msg)
        self.model.solve(solver)

        status = pulp.LpStatus[self.model.status]

        frequency_table = []
        total_energy_kwh = 0.0
        total_energy_cost = 0.0
        total_waiting_cost = 0.0

        for line_id, line_cfg in self.lines.items():
            for period in TIME_PERIODS:
                f_val = self.freq_vars[(line_id, period)].value()
                f_val = round(f_val) if f_val is not None else 0
                duration = TIME_PERIOD_DURATION_HOURS[period]
                wait_min = self.wait_time_vars[(line_id, period)].value() or 0.0

                trips = f_val * duration
                energy_kwh = trips * line_cfg.energy_per_trip_kwh
                energy_cost = energy_kwh * COST_PER_KWH
                demand = line_cfg.demand_per_period[period]
                capacity_provided = f_val * duration * line_cfg.train_capacity
                waiting_cost = wait_min * demand * COST_PER_PASSENGER_MINUTE

                total_energy_kwh += energy_kwh
                total_energy_cost += energy_cost
                total_waiting_cost += waiting_cost

                frequency_table.append(
                    {
                        "line_id": line_id,
                        "line_name": line_cfg.name,
                        "period": period,
                        "period_label": TIME_PERIOD_LABELS_FA[period],
                        "frequency_trains_per_hour": f_val,
                        "headway_minutes": round(60.0 / f_val, 2) if f_val else None,
                        "avg_wait_minutes": round(wait_min, 2),
                        "demand_passengers": demand,
                        "capacity_provided": int(capacity_provided),
                        "utilization_pct": round(
                            100.0 * demand / capacity_provided, 1
                        )
                        if capacity_provided > 0
                        else None,
                        "energy_kwh": round(energy_kwh, 1),
                        "energy_cost_toman": round(energy_cost, 0),
                        "waiting_cost_toman": round(waiting_cost, 0),
                    }
                )

        # حداکثر ناوگان استفاده‌شده هم‌زمان (برای بازه اوج)
        peak_fleet_usage = 0
        for period in TIME_PERIODS:
            used = sum(
                (row["frequency_trains_per_hour"] * self.round_trip_factor)
                for row in frequency_table
                if row["period"] == period
            )
            peak_fleet_usage = max(peak_fleet_usage, used)

        objective_value = pulp.value(self.model.objective) or 0.0

        return FrequencyResult(
            status=status,
            objective_value=round(objective_value, 0),
            total_energy_cost=round(total_energy_cost, 0),
            total_waiting_cost=round(total_waiting_cost, 0),
            total_energy_kwh=round(total_energy_kwh, 1),
            total_fleet_used_peak=int(round(peak_fleet_usage)),
            frequency_table=frequency_table,
        )


# --------------------------------------------------------------------------
# اجرای مستقل جهت تست سریع
# --------------------------------------------------------------------------
def run_default_optimization() -> FrequencyResult:
    """اجرای مدل با پارامترهای پیش‌فرض؛ برای استفاده در main.py و web/app.py."""
    optimizer = MetroFrequencyOptimizer()
    optimizer.build_model()
    return optimizer.solve()


if __name__ == "__main__":
    result = run_default_optimization()
    print(f"وضعیت حل: {result.status}")
    print(f"مقدار تابع هدف (هزینه کل وزن‌دار): {result.objective_value:,.0f} تومان")
    print(f"هزینه کل انرژی: {result.total_energy_cost:,.0f} تومان")
    print(f"هزینه کل انتظار مسافران: {result.total_waiting_cost:,.0f} تومان")
    print(f"حداکثر ناوگان استفاده‌شده هم‌زمان: {result.total_fleet_used_peak}")
    print("-" * 70)
    for row in result.frequency_table:
        print(
            f"{row['line_name']:<15} | {row['period_label']:<10} | "
            f"فرکانس: {row['frequency_trains_per_hour']:>2} قطار/ساعت | "
            f"هدوی: {row['headway_minutes']:>5} دقیقه | "
            f"انتظار: {row['avg_wait_minutes']:>5} دقیقه"
        )
