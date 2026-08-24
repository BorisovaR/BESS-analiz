"""
Energomonitor BESS - Virtual Battery Simulator
PHASE 2: Self-consumption strategy with strict energy conservation
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional

@dataclass
class BatteryConfig:
    rated_capacity_kwh: float = 200.0
    usable_capacity_kwh: Optional[float] = None
    max_charge_power_kw: float = 100.0
    max_discharge_power_kw: float = 100.0
    min_soc_pct: float = 10.0
    max_soc_pct: float = 90.0
    initial_soc_pct: float = 50.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    round_trip_efficiency: Optional[float] = None
    allowed_grid_charging: bool = False
    allowed_grid_export_from_battery: bool = False
    
    def __post_init__(self):
        if self.round_trip_efficiency is not None:
            eff = np.sqrt(self.round_trip_efficiency)
            self.charge_efficiency = eff
            self.discharge_efficiency = eff
        if self.usable_capacity_kwh is None:
            self.usable_capacity_kwh = self.rated_capacity_kwh * (self.max_soc_pct - self.min_soc_pct) / 100.0
        self.charge_efficiency = float(np.clip(self.charge_efficiency, 0.7, 1.0))
        self.discharge_efficiency = float(np.clip(self.discharge_efficiency, 0.7, 1.0))
    
    @property
    def soc_min_kwh(self) -> float:
        return self.rated_capacity_kwh * self.min_soc_pct / 100.0
    
    @property
    def soc_max_kwh(self) -> float:
        return self.rated_capacity_kwh * self.max_soc_pct / 100.0
    
    @property
    def initial_soc_kwh(self) -> float:
        target = self.rated_capacity_kwh * self.initial_soc_pct / 100.0
        return float(np.clip(target, self.soc_min_kwh, self.soc_max_kwh))

@dataclass
class BatterySimulationResult:
    df: pd.DataFrame
    summary: Dict

def simulate_self_consumption(baseline_df: pd.DataFrame, config: BatteryConfig) -> BatterySimulationResult:
    df = baseline_df.copy().sort_values("timestamp").reset_index(drop=True)
    n = len(df)
    soc_kwh = np.zeros(n)
    charge_ac_kwh = np.zeros(n)
    discharge_ac_kwh = np.zeros(n)
    charge_dc_kwh = np.zeros(n)
    discharge_dc_kwh = np.zeros(n)
    grid_import_after = np.zeros(n)
    grid_export_after = np.zeros(n)
    losses_kwh = np.zeros(n)
    soc_current = config.initial_soc_kwh
    
    for i in range(n):
        interval_h = df["_interval_h"].iloc[i] if "_interval_h" in df.columns else 0.25
        surplus = float(df["surplus_kwh"].iloc[i])
        deficit = float(df["deficit_kwh"].iloc[i])
        max_charge_energy_ac = config.max_charge_power_kw * interval_h
        max_discharge_energy_ac = config.max_discharge_power_kw * interval_h
        
        if surplus > 1e-9:
            free_capacity_kwh = config.soc_max_kwh - soc_current
            if free_capacity_kwh < 1e-9:
                charge_ac = 0.0
            else:
                max_ac_for_free_cap = free_capacity_kwh / config.charge_efficiency if config.charge_efficiency>0 else 0
                charge_ac = min(surplus, max_charge_energy_ac, max_ac_for_free_cap)
            stored = charge_ac * config.charge_efficiency
            soc_current += stored
            charge_ac_kwh[i] = charge_ac
            charge_dc_kwh[i] = stored
            losses_kwh[i] = charge_ac - stored
            grid_import_after[i] = 0.0
            grid_export_after[i] = surplus - charge_ac
        elif deficit > 1e-9:
            available_kwh = soc_current - config.soc_min_kwh
            if available_kwh < 1e-9:
                discharge_ac = 0.0
                battery_used_dc = 0.0
            else:
                max_ac_from_available = available_kwh * config.discharge_efficiency
                discharge_ac = min(deficit, max_discharge_energy_ac, max_ac_from_available)
                battery_used_dc = discharge_ac / config.discharge_efficiency if config.discharge_efficiency>0 else 0
            soc_current -= battery_used_dc
            discharge_ac_kwh[i] = discharge_ac
            discharge_dc_kwh[i] = battery_used_dc
            losses_kwh[i] = battery_used_dc - discharge_ac
            grid_import_after[i] = deficit - discharge_ac
            grid_export_after[i] = 0.0
        else:
            grid_import_after[i] = 0
            grid_export_after[i] = 0
        soc_current = float(np.clip(soc_current, config.soc_min_kwh, config.soc_max_kwh))
        soc_kwh[i] = soc_current
    
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
    
    total_charged_ac = float(np.sum(charge_ac_kwh))
    total_discharged_ac = float(np.sum(discharge_ac_kwh))
    total_charged_dc = float(np.sum(charge_dc_kwh))
    total_discharged_dc = float(np.sum(discharge_dc_kwh))
    total_losses = float(np.sum(losses_kwh))
    total_import_before = float(df["grid_import_kwh"].sum() if "grid_import_kwh" in df.columns else df["deficit_kwh"].sum())
    total_export_before = float(df["grid_export_kwh"].sum() if "grid_export_kwh" in df.columns else df["surplus_kwh"].sum())
    total_import_after = float(np.sum(grid_import_after))
    total_export_after = float(np.sum(grid_export_after))
    equivalent_cycles = total_discharged_dc / config.usable_capacity_kwh if config.usable_capacity_kwh>0 else 0
    total_direct = float(df["direct_self_consumption_kwh"].sum() if "direct_self_consumption_kwh" in df.columns else 0)
    total_pv = float(df["pv_generation_kwh"].sum())
    total_consumption = float(df["consumption_kwh"].sum())
    total_pv_used_after = total_direct + total_discharged_ac
    self_consumption_after_pct = (total_pv_used_after / total_pv * 100) if total_pv>0 else 0
    self_sufficiency_after_pct = (total_pv_used_after / total_consumption * 100) if total_consumption>0 else 0
    
    summary = {
        "config": {
            "rated_kwh": config.rated_capacity_kwh,
            "usable_kwh": config.usable_capacity_kwh,
            "charge_kw": config.max_charge_power_kw,
            "discharge_kw": config.max_discharge_power_kw,
            "min_soc_pct": config.min_soc_pct,
            "max_soc_pct": config.max_soc_pct,
            "initial_soc_pct": config.initial_soc_pct,
            "charge_eff": config.charge_efficiency,
            "discharge_eff": config.discharge_efficiency,
            "round_trip_eff": config.charge_efficiency * config.discharge_efficiency,
        },
        "grid_import_before_kwh": total_import_before,
        "grid_export_before_kwh": total_export_before,
        "grid_import_after_kwh": total_import_after,
        "grid_export_after_kwh": total_export_after,
        "grid_import_reduction_kwh": total_import_before - total_import_after,
        "grid_export_reduction_kwh": total_export_before - total_export_after,
        "energy_charged_ac_kwh": total_charged_ac,
        "energy_discharged_ac_kwh": total_discharged_ac,
        "energy_charged_dc_kwh": total_charged_dc,
        "energy_discharged_dc_kwh": total_discharged_dc,
        "battery_losses_kwh": total_losses,
        "equivalent_full_cycles": equivalent_cycles,
        "self_consumption_before_pct": (total_direct/total_pv*100) if total_pv>0 else 0,
        "self_consumption_after_pct": self_consumption_after_pct,
        "self_sufficiency_before_pct": (total_direct/total_consumption*100) if total_consumption>0 else 0,
        "self_sufficiency_after_pct": self_sufficiency_after_pct,
    }
    return BatterySimulationResult(df=result_df, summary=summary)
