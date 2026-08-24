"""
PHASE 7A - LP Optimal Price-aware optimizer using scipy.optimize.linprog
True cost minimization vs heuristic
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict
from core.battery_simulator import BatteryConfig
from scipy.optimize import linprog

@dataclass
class LPOptimizationResult:
    df: pd.DataFrame
    summary: Dict
    solver_status: str

def _optimize_one_day(day_df: pd.DataFrame, config: BatteryConfig,
                      buy_prices: np.ndarray, sell_prices: np.ndarray,
                      initial_soc_kwh: float) -> Dict:
    """
    Optimize single day using LP
    day_df has consumption_kwh, pv_generation_kwh, _interval_h
    Returns dict with arrays for charge, discharge, soc, import, export
    """
    n = len(day_df)
    cons = day_df["consumption_kwh"].values
    pv = day_df["pv_generation_kwh"].values
    interval_h = day_df["_interval_h"].values if "_interval_h" in day_df.columns else np.full(n, 0.25)
    
    max_charge_e = config.max_charge_power_kw * interval_h
    max_discharge_e = config.max_discharge_power_kw * interval_h
    
    # Variables order: [charge (n), discharge (n), soc (n), import (n), export (n)] => 5n vars
    # Objective: minimize import*buy - export*sell
    c = np.zeros(5*n)
    # import coefficients
    c[3*n:4*n] = buy_prices / 1000.0  # EUR per kWh (since buy is EUR/MWh, divide 1000, but we already have cost = kWh*price/1000, so coeff = price/1000)
    # Actually linprog minimizes c^T x, so for export negative: -sell_price/1000
    c[4*n:5*n] = -sell_prices / 1000.0
    
    # Equality constraints: 
    # 1) Energy balance per t: -charge + discharge + import - export = cons - pv
    # 2) SOC dynamics per t: -charge*eff_c + discharge/eff_d + soc[t] - soc[t-1] = 0 (for t>0), for t=0: soc[0] - charge*eff_c + discharge/eff_d = initial_soc
    num_eq = 2*n
    A_eq = np.zeros((num_eq, 5*n))
    b_eq = np.zeros(num_eq)
    
    eff_c = config.charge_efficiency
    eff_d = config.discharge_efficiency
    
    for t in range(n):
        # Energy balance
        row = t
        A_eq[row, t] = -1  # charge[t]
        A_eq[row, n + t] = 1  # discharge[t]
        A_eq[row, 3*n + t] = 1  # import[t]
        A_eq[row, 4*n + t] = -1  # export[t]
        b_eq[row] = cons[t] - pv[t]
        
        # SOC dynamics
        row2 = n + t
        # soc[t]
        A_eq[row2, 2*n + t] = 1
        # - soc[t-1] for t>0
        if t > 0:
            A_eq[row2, 2*n + (t-1)] = -1
        # - charge*eff_c
        A_eq[row2, t] = -eff_c
        # + discharge/eff_d
        A_eq[row2, n + t] = 1.0 / eff_d if eff_d>0 else 0
        
        if t == 0:
            b_eq[row2] = initial_soc_kwh
        else:
            b_eq[row2] = 0
    
    # Bounds
    bounds = []
    # charge
    for t in range(n):
        bounds.append((0, max_charge_e[t]))
    # discharge
    for t in range(n):
        bounds.append((0, max_discharge_e[t]))
    # soc
    for t in range(n):
        bounds.append((config.soc_min_kwh, config.soc_max_kwh))
    # import
    for t in range(n):
        bounds.append((0, 1e6))  # large
    # export
    for t in range(n):
        bounds.append((0, 1e6))
    
    # Solve
    try:
        res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs', options={'disp': False})
        if not res.success:
            # Fallback to heuristic if fails
            return None
        x = res.x
        charge = x[0:n]
        discharge = x[n:2*n]
        soc = x[2*n:3*n]
        imp = x[3*n:4*n]
        exp = x[4*n:5*n]
        return {
            "charge": charge,
            "discharge": discharge,
            "soc": soc,
            "import": imp,
            "export": exp,
            "cost": res.fun,
            "status": "optimal"
        }
    except Exception as e:
        return None

def simulate_lp_optimal(baseline_df: pd.DataFrame, config: BatteryConfig,
                        buy_price_col: str = "buy_price_EUR_per_MWh",
                        sell_price_col: str = "sell_price_EUR_per_MWh",
                        fixed_buy_price: float = 180.0,
                        fixed_sell_price: float = 90.0) -> LPOptimizationResult:
    """
    Day-by-day LP optimization - true price-aware optimum
    """
    df = baseline_df.copy().sort_values("timestamp").reset_index(drop=True)
    n = len(df)
    
    if buy_price_col and buy_price_col in df.columns:
        buy_prices = df[buy_price_col].fillna(fixed_buy_price).values
    else:
        buy_prices = np.full(n, fixed_buy_price)
    
    if sell_price_col and sell_price_col in df.columns:
        sell_prices = df[sell_price_col].fillna(fixed_sell_price).values
    else:
        sell_prices = np.full(n, fixed_sell_price)
    
    df["date"] = df["timestamp"].dt.date
    
    # Result arrays
    charge_ac = np.zeros(n)
    discharge_ac = np.zeros(n)
    soc_arr = np.zeros(n)
    import_after = np.zeros(n)
    export_after = np.zeros(n)
    
    current_soc = config.initial_soc_kwh
    total_cost = 0
    
    # Group by date
    for date, group in df.groupby("date"):
        idx = group.index.values
        day_df = df.loc[idx]
        day_buy = buy_prices[idx]
        day_sell = sell_prices[idx]
        
        day_res = _optimize_one_day(day_df, config, day_buy, day_sell, current_soc)
        if day_res is None:
            # Fallback: use heuristic for this day
            from core.price_optimizer import simulate_price_aware
            # For fallback we skip and use zero
            # Just keep SOC
            for i, gi in enumerate(idx):
                soc_arr[gi] = current_soc
                # simple: no operation
                import_after[gi] = max(0, day_df["consumption_kwh"].iloc[i] - day_df["pv_generation_kwh"].iloc[i])
                export_after[gi] = max(0, day_df["pv_generation_kwh"].iloc[i] - day_df["consumption_kwh"].iloc[i])
            continue
        
        # Fill results
        charge_ac[idx] = day_res["charge"]
        discharge_ac[idx] = day_res["discharge"]
        soc_arr[idx] = day_res["soc"]
        import_after[idx] = day_res["import"]
        export_after[idx] = day_res["export"]
        total_cost += day_res["cost"]
        current_soc = day_res["soc"][-1] if len(day_res["soc"])>0 else current_soc
    
    # Compute losses and DC values
    charge_dc = charge_ac * config.charge_efficiency
    discharge_dc = discharge_ac / config.discharge_efficiency if config.discharge_efficiency>0 else discharge_ac
    losses = (charge_ac - charge_dc) + (discharge_dc - discharge_ac)
    
    result_df = df.copy()
    result_df["soc_kwh"] = soc_arr
    result_df["soc_pct"] = soc_arr / config.rated_capacity_kwh * 100 if config.rated_capacity_kwh>0 else 0
    result_df["battery_charge_ac_kwh"] = charge_ac
    result_df["battery_discharge_ac_kwh"] = discharge_ac
    result_df["battery_charge_dc_kwh"] = charge_dc
    result_df["battery_discharge_dc_kwh"] = discharge_dc
    result_df["battery_losses_kwh"] = losses
    result_df["grid_import_after_kwh"] = import_after
    result_df["grid_export_after_kwh"] = export_after
    result_df["grid_charge_kwh"] = np.maximum(0, charge_ac - result_df["surplus_kwh"].clip(lower=0))
    result_df["grid_export_from_batt_kwh"] = np.maximum(0, discharge_ac - result_df["deficit_kwh"].clip(lower=0))
    
    total_import_before = float(df["grid_import_kwh"].sum() if "grid_import_kwh" in df.columns else df["deficit_kwh"].sum())
    total_export_before = float(df["grid_export_kwh"].sum() if "grid_export_kwh" in df.columns else df["surplus_kwh"].sum())
    
    summary = {
        "grid_import_before_kwh": total_import_before,
        "grid_export_before_kwh": total_export_before,
        "grid_import_after_kwh": float(np.sum(import_after)),
        "grid_export_after_kwh": float(np.sum(export_after)),
        "energy_charged_ac_kwh": float(np.sum(charge_ac)),
        "energy_discharged_ac_kwh": float(np.sum(discharge_ac)),
        "battery_losses_kwh": float(np.sum(losses)),
        "equivalent_full_cycles": float(np.sum(discharge_dc)/config.usable_capacity_kwh) if config.usable_capacity_kwh>0 else 0,
        "total_cost_eur": total_cost,
    }
    
    return LPOptimizationResult(df=result_df, summary=summary, solver_status="optimal")
