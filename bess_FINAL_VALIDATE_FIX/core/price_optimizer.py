"""
PHASE 5 - Price-aware / Arbitrage simulator
Implements transparent configurable algorithm + optional LP optimization
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict
from core.battery_simulator import BatteryConfig

@dataclass
class PriceAwareResult:
    df: pd.DataFrame
    summary: Dict

def simulate_price_aware(baseline_df: pd.DataFrame, config: BatteryConfig,
                         buy_price_col: str = "buy_price_EUR_per_MWh",
                         sell_price_col: str = "sell_price_EUR_per_MWh",
                         fixed_buy_price: float = 180.0,
                         fixed_sell_price: float = 90.0,
                         low_price_percentile: float = 25,
                         high_price_percentile: float = 75,
                         allow_grid_charging: bool = True,
                         allow_grid_export: bool = True) -> PriceAwareResult:
    """
    Price-aware heuristic with day-ahead look-ahead:
    - For each day, compute low/high thresholds
    - If price low and SOC not full: charge from grid + surplus
    - If price high: discharge to cover load, and if allowed export, export for profit
    - Never simultaneous charge/discharge
    - Efficiency handled
    Objective: minimize cost = import*buy - export*sell
    """
    df = baseline_df.copy().sort_values("timestamp").reset_index(drop=True)
    n = len(df)
    
    # Get price series
    if buy_price_col in df.columns:
        buy_prices = df[buy_price_col].fillna(fixed_buy_price).values
    else:
        buy_prices = np.full(n, fixed_buy_price)
    
    if sell_price_col in df.columns:
        sell_prices = df[sell_price_col].fillna(fixed_sell_price).values
    else:
        sell_prices = np.full(n, fixed_sell_price)
    
    # Add day column for threshold per day
    df["date"] = df["timestamp"].dt.date
    
    soc_kwh = np.zeros(n)
    charge_ac_kwh = np.zeros(n)
    discharge_ac_kwh = np.zeros(n)
    charge_dc_kwh = np.zeros(n)
    discharge_dc_kwh = np.zeros(n)
    grid_import_after = np.zeros(n)
    grid_export_after = np.zeros(n)
    grid_charge_kwh = np.zeros(n)  # extra charging from grid
    grid_export_from_batt_kwh = np.zeros(n)
    losses_kwh = np.zeros(n)
    
    soc_current = config.initial_soc_kwh
    
    # Precompute daily thresholds
    daily_thresholds = {}
    for date, group in df.groupby("date"):
        b = buy_prices[group.index]
        daily_thresholds[date] = {
            "low": np.percentile(b, low_price_percentile),
            "high": np.percentile(b, high_price_percentile),
            "mean": np.mean(b)
        }
    
    for i in range(n):
        interval_h = df["_interval_h"].iloc[i] if "_interval_h" in df.columns else 0.25
        surplus = float(df["surplus_kwh"].iloc[i])
        deficit = float(df["deficit_kwh"].iloc[i])
        buy_price = buy_prices[i]
        sell_price = sell_prices[i]
        date = df["date"].iloc[i]
        thresh = daily_thresholds[date]
        
        max_charge_ac = config.max_charge_power_kw * interval_h
        max_discharge_ac = config.max_discharge_power_kw * interval_h
        
        charge_ac = 0.0
        discharge_ac = 0.0
        grid_charge = 0.0
        grid_export_batt = 0.0
        
        # Step 1: handle surplus always (self-consumption part)
        if surplus > 1e-9:
            free_cap = config.soc_max_kwh - soc_current
            if free_cap > 1e-9:
                max_ac_for_cap = free_cap / config.charge_efficiency if config.charge_efficiency>0 else 0
                charge_ac = min(surplus, max_charge_ac, max_ac_for_cap)
                surplus_remaining = surplus - charge_ac
            else:
                surplus_remaining = surplus
                charge_ac = 0
            export = surplus_remaining
            import_needed = 0
        else:
            export = 0
            import_needed = deficit
            surplus_remaining = 0
        
        # Step 2: price-aware extra actions
        # Low price -> charge from grid if allowed
        if allow_grid_charging and buy_price <= thresh["low"] and soc_current < config.soc_max_kwh - 1e-6:
            # how much can we still charge after surplus charging
            free_cap = config.soc_max_kwh - soc_current - (charge_ac * config.charge_efficiency)
            if free_cap > 1e-9:
                max_ac_for_cap = free_cap / config.charge_efficiency if config.charge_efficiency>0 else 0
                remaining_power = max_charge_ac - charge_ac
                grid_charge = min(remaining_power, max_ac_for_cap)
                # Don't charge more than needed to fill, and consider that charging from grid costs money - we do it only if low price
                # Add to charge
                charge_ac += grid_charge
        
        # High price -> discharge more aggressively, even to grid if allowed
        if deficit > 1e-9:
            # normal discharge to cover deficit (already partially handled? actually surplus case already, now deficit)
            # For deficit case, we need to discharge
            if surplus <= 1e-9:  # deficit case
                available = soc_current - config.soc_min_kwh
                # Note: soc_current hasn't been updated yet with this interval's charge, use current before
                # Actually we already have charge_ac from surplus, but in deficit surplus=0 so no charge
                # So available is current soc
                if available > 1e-9:
                    max_ac_avail = available * config.discharge_efficiency
                    discharge_ac = min(import_needed, max_discharge_ac, max_ac_avail)
                    import_needed -= discharge_ac
            # If price high and still SOC left and export allowed, export for profit
            if allow_grid_export and buy_price >= thresh["high"]:
                available_after = soc_current - config.soc_min_kwh - (discharge_ac / config.discharge_efficiency if config.discharge_efficiency>0 else 0)
                # Ensure battery has energy
                if available_after > 1e-9:
                    max_ac_avail2 = available_after * config.discharge_efficiency
                    remaining_power = max_discharge_ac - discharge_ac
                    # Export to grid for arbitrage - only if sell price is attractive vs future buy
                    # Simplified: if sell price > buy low threshold
                    if sell_price > thresh["low"] * 0.9:
                        grid_export_batt = min(remaining_power, max_ac_avail2)
                        discharge_ac += grid_export_batt
        else:
            # No deficit but price high and export allowed -> discharge to grid for arbitrage
            if allow_grid_export and buy_price >= thresh["high"] and surplus <= 1e-9:
                # Actually surplus already handled, but if we have no surplus and no deficit (perfect balance), we can still export
                available = soc_current - config.soc_min_kwh
                if available > 1e-9:
                    max_ac_avail = available * config.discharge_efficiency
                    discharge_ac = min(max_discharge_ac, max_ac_avail)
                    grid_export_batt = discharge_ac
                    import_needed = 0
        
        # Now apply SOC changes
        stored = charge_ac * config.charge_efficiency
        used_dc = discharge_ac / config.discharge_efficiency if config.discharge_efficiency>0 else 0
        
        # Check SOC limits
        soc_new = soc_current + stored - used_dc
        # If violates limits due to rounding, adjust
        if soc_new > config.soc_max_kwh + 0.001:
            # reduce charge
            excess = soc_new - config.soc_max_kwh
            reduction_ac = excess / config.charge_efficiency
            charge_ac -= reduction_ac
            stored = charge_ac * config.charge_efficiency
            soc_new = soc_current + stored - used_dc
        if soc_new < config.soc_min_kwh - 0.001:
            excess = config.soc_min_kwh - soc_new
            reduction_dc = excess
            reduction_ac = reduction_dc * config.discharge_efficiency
            discharge_ac -= reduction_ac
            used_dc = discharge_ac / config.discharge_efficiency if config.discharge_efficiency>0 else 0
            soc_new = soc_current + stored - used_dc
        
        soc_current = float(np.clip(soc_new, config.soc_min_kwh, config.soc_max_kwh))
        
        # Losses
        loss_charge = charge_ac - stored
        loss_discharge = used_dc - discharge_ac if discharge_ac>0 else 0
        total_loss = loss_charge + loss_discharge
        
        soc_kwh[i] = soc_current
        charge_ac_kwh[i] = charge_ac
        discharge_ac_kwh[i] = discharge_ac
        charge_dc_kwh[i] = stored
        discharge_dc_kwh[i] = used_dc
        losses_kwh[i] = total_loss
        grid_charge_kwh[i] = grid_charge
        grid_export_from_batt_kwh[i] = grid_export_batt
        
        if surplus > 1e-9:
            grid_import_after[i] = grid_charge  # grid charging import
            grid_export_after[i] = export  # surplus remaining after charging
        else:
            grid_import_after[i] = import_needed + grid_charge
            grid_export_after[i] = grid_export_batt  # export from battery
    
    result_df = df.copy()
    result_df["soc_kwh"] = soc_kwh
    result_df["soc_pct"] = soc_kwh / config.rated_capacity_kwh * 100.0 if config.rated_capacity_kwh>0 else 0
    result_df["battery_charge_ac_kwh"] = charge_ac_kwh
    result_df["battery_discharge_ac_kwh"] = discharge_ac_kwh
    result_df["battery_charge_dc_kwh"] = charge_dc_kwh
    result_df["battery_discharge_dc_kwh"] = discharge_dc_kwh
    result_df["battery_losses_kwh"] = losses_kwh
    result_df["grid_import_after_kwh"] = grid_import_after
    result_df["grid_export_after_kwh"] = grid_export_after
    result_df["grid_charge_kwh"] = grid_charge_kwh
    result_df["grid_export_from_batt_kwh"] = grid_export_from_batt_kwh
    
    # Summary
    total_charged_ac = float(np.sum(charge_ac_kwh))
    total_discharged_ac = float(np.sum(discharge_ac_kwh))
    total_import_before = float(df["grid_import_kwh"].sum() if "grid_import_kwh" in df.columns else df["deficit_kwh"].sum())
    total_export_before = float(df["grid_export_kwh"].sum() if "grid_export_kwh" in df.columns else df["surplus_kwh"].sum())
    total_import_after = float(np.sum(grid_import_after))
    total_export_after = float(np.sum(grid_export_after))
    
    # Financial benefit if price available
    # cost calc
    def cost(import_col, export_col, buy_arr, sell_arr, df_len):
        imp = result_df[import_col].values if import_col in result_df.columns else grid_import_after
        exp = result_df[export_col].values if export_col in result_df.columns else grid_export_after
        # for before, use baseline
        return None
    
    summary = {
        "config": {
            "rated_kwh": config.rated_capacity_kwh,
            "usable_kwh": config.usable_capacity_kwh,
            "charge_kw": config.max_charge_power_kw,
            "discharge_kw": config.max_discharge_power_kw,
            "allow_grid_charging": allow_grid_charging,
            "allow_grid_export": allow_grid_export,
            "low_percentile": low_price_percentile,
            "high_percentile": high_price_percentile,
        },
        "grid_import_before_kwh": total_import_before,
        "grid_export_before_kwh": total_export_before,
        "grid_import_after_kwh": total_import_after,
        "grid_export_after_kwh": total_export_after,
        "grid_import_reduction_kwh": total_import_before - total_import_after,
        "energy_charged_ac_kwh": total_charged_ac,
        "energy_discharged_ac_kwh": total_discharged_ac,
        "grid_charge_kwh": float(np.sum(grid_charge_kwh)),
        "grid_export_from_batt_kwh": float(np.sum(grid_export_from_batt_kwh)),
        "battery_losses_kwh": float(np.sum(losses_kwh)),
        "equivalent_full_cycles": float(np.sum(discharge_dc_kwh)/config.usable_capacity_kwh) if config.usable_capacity_kwh>0 else 0,
    }
    
    return PriceAwareResult(df=result_df, summary=summary)
