"""
Energomonitor BESS - Energy Balance Core
PHASE 1: Baseline energy balance without battery
Handles kW vs kWh correctly: all financials use ENERGY
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict

@dataclass
class BaselineResults:
    df: pd.DataFrame  # enriched per timestamp
    totals: Dict
    ratios: Dict

def compute_baseline_balance(df_normalized: pd.DataFrame) -> BaselineResults:
    """
    df_normalized must have timestamp, consumption, pv_production (kW or kWh)
    Handles mixed power/energy columns via *_unit_type meta
    """
    df = df_normalized.copy()
    df = df.sort_values("timestamp")
    
    # Determine interval hours
    timestamps = df["timestamp"]
    diffs = timestamps.diff().shift(-1)
    median_diff = diffs.median()
    if pd.isna(median_diff):
        median_diff = pd.Timedelta(minutes=15)
    diffs = diffs.fillna(median_diff)
    interval_h = diffs.dt.total_seconds() / 3600.0
    df["_interval_h"] = interval_h

    def to_energy(col_name):
        """Convert column to energy kWh for this interval"""
        if col_name not in df.columns:
            return pd.Series(0.0, index=df.index)
        unit_type_col = f"{col_name}_unit_type"
        if unit_type_col in df.columns:
            # if marked as power -> energy = power * interval_h
            is_power = df[unit_type_col] == "power"
            # For energy-marked, value already is energy per interval
            energy = np.where(is_power, df[col_name].fillna(0) * df["_interval_h"], df[col_name].fillna(0))
            return pd.Series(energy, index=df.index)
        else:
            # Heuristic: if values look like power (avg > 1000) and interval small, assume power? For MVP assume power if consumption/pv
            if col_name in ["consumption","pv_production"]:
                # Assume power kW unless data clearly energy - we already converted in loader, but fallback
                return df[col_name].fillna(0) * df["_interval_h"]
            return df[col_name].fillna(0)

    # Energies
    df["consumption_kwh"] = to_energy("consumption")
    df["pv_generation_kwh"] = to_energy("pv_production")
    
    # Direct self-consumption: min(consumption, PV)
    df["direct_self_consumption_kwh"] = np.minimum(df["consumption_kwh"], df["pv_generation_kwh"])
    
    # Surplus and deficit
    df["net_balance_kwh"] = df["pv_generation_kwh"] - df["consumption_kwh"]
    df["surplus_kwh"] = np.where(df["net_balance_kwh"] > 0, df["net_balance_kwh"], 0.0)
    df["deficit_kwh"] = np.where(df["net_balance_kwh"] < 0, -df["net_balance_kwh"], 0.0)

    # Grid import/export - if provided use them, else infer from baseline (without battery)
    if "grid_import" in df.columns:
        df["grid_import_kwh"] = to_energy("grid_import")
    else:
        df["grid_import_kwh"] = df["deficit_kwh"]

    if "grid_export" in df.columns:
        df["grid_export_kwh"] = to_energy("grid_export")
    else:
        df["grid_export_kwh"] = df["surplus_kwh"]

    # Totals
    totals = {
        "total_consumption_kwh": float(df["consumption_kwh"].sum()),
        "total_pv_generation_kwh": float(df["pv_generation_kwh"].sum()),
        "total_direct_self_consumption_kwh": float(df["direct_self_consumption_kwh"].sum()),
        "total_grid_import_kwh": float(df["grid_import_kwh"].sum()),
        "total_grid_export_kwh": float(df["grid_export_kwh"].sum()),
        "total_surplus_kwh": float(df["surplus_kwh"].sum()),
        "total_deficit_kwh": float(df["deficit_kwh"].sum()),
        "interval_hours_avg": float(df["_interval_h"].mean()),
        "data_points": len(df),
        "start_date": df["timestamp"].min(),
        "end_date": df["timestamp"].max(),
    }

    # Ratios
    pv_gen = totals["total_pv_generation_kwh"]
    cons = totals["total_consumption_kwh"]
    direct = totals["total_direct_self_consumption_kwh"]

    self_consumption_ratio = (direct / pv_gen * 100) if pv_gen > 0 else 0.0
    self_sufficiency_ratio = (direct / cons * 100) if cons > 0 else 0.0

    ratios = {
        "self_consumption_ratio_pct": self_consumption_ratio,
        "self_sufficiency_ratio_pct": self_sufficiency_ratio,
    }

    return BaselineResults(df=df, totals=totals, ratios=ratios)
