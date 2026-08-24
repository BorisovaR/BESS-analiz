"""
Scenario engine - PHASE 3 but stubbed for PHASE 2 testing
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from typing import List, Dict
from core.battery_simulator import BatteryConfig, simulate_self_consumption

def suggest_capacity_range(baseline_df: pd.DataFrame) -> List[int]:
    """
    Auto suggest BESS capacities based on daily surplus
    """
    daily_surplus = baseline_df.groupby(baseline_df["timestamp"].dt.date)["surplus_kwh"].sum()
    avg_daily_surplus = daily_surplus.mean()
    p90_daily_surplus = daily_surplus.quantile(0.9)
    
    # Suggest capacities around avg and p90
    # e.g. if avg surplus 200 kWh, suggest 50,100,200,300,500
    base = max(20, int(avg_daily_surplus * 0.8))
    # Round to nice numbers
    def nice_round(x):
        if x < 100:
            return int(round(x/10)*10)
        elif x < 500:
            return int(round(x/50)*50)
        else:
            return int(round(x/100)*100)
    
    candidates = [
        nice_round(base*0.25),
        nice_round(base*0.5),
        nice_round(base*1.0),
        nice_round(base*1.5),
        nice_round(p90_daily_surplus),
        nice_round(p90_daily_surplus*1.5)
    ]
    # Unique, sorted, filter >10 kWh
    uniq = sorted(set([c for c in candidates if c>=10]))
    # Limit to 6
    return uniq[:6] if len(uniq)>=5 else [50,100,200,300,500]

def run_scenarios(baseline_df: pd.DataFrame, capacities_kwh: List[float], charge_power_ratio: float = 0.5) -> List[Dict]:
    """
    Run self-consumption simulation for each capacity
    charge_power_ratio: kW per kWh (e.g. 0.5 means 100kWh -> 50kW)
    """
    results = []
    for cap in capacities_kwh:
        config = BatteryConfig(
            rated_capacity_kwh=cap,
            max_charge_power_kw=cap*charge_power_ratio,
            max_discharge_power_kw=cap*charge_power_ratio,
            min_soc_pct=10,
            max_soc_pct=90,
            initial_soc_pct=50,
            round_trip_efficiency=0.90
        )
        sim = simulate_self_consumption(baseline_df, config)
        results.append({
            "capacity_kwh": cap,
            "config": config,
            "summary": sim.summary,
            "df": sim.df  # keep for detailed if needed, but for table we use summary
        })
    return results
