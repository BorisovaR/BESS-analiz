"""
PHASE 7B - Offer Comparator (Сценарий D) + Existing BESS audit (Сценарий B)
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Dict
from core.battery_simulator import BatteryConfig, simulate_self_consumption
from core.economics import EconomicConfig, calculate_financials
from core.price_optimizer import simulate_price_aware

def compare_offer(baseline_df: pd.DataFrame, offer: Dict, econ_config: EconomicConfig,
                  buy_price_col: str = "buy_price_EUR_per_MWh", sell_price_col: str = "sell_price_EUR_per_MWh",
                  fixed_buy: float = 180, fixed_sell: float = 90) -> Dict:
    """
    offer = {
      capacity_kwh, power_kw, capex_eur, charge_eff, discharge_eff,
      promised_annual_saving_mwh, promised_payback_years
    }
    Returns comparison promised vs simulated
    """
    config = BatteryConfig(
        rated_capacity_kwh=offer["capacity_kwh"],
        max_charge_power_kw=offer.get("power_kw", offer["capacity_kwh"]*0.5),
        max_discharge_power_kw=offer.get("power_kw", offer["capacity_kwh"]*0.5),
        charge_efficiency=offer.get("charge_eff", 0.95),
        discharge_efficiency=offer.get("discharge_eff", 0.95),
        min_soc_pct=offer.get("min_soc_pct", 10),
        max_soc_pct=offer.get("max_soc_pct", 90),
    )
    
    sim_self = simulate_self_consumption(baseline_df, config)
    sim_price = simulate_price_aware(baseline_df, config, buy_price_col=buy_price_col, sell_price_col=sell_price_col,
                                     fixed_buy_price=fixed_buy, fixed_sell_price=fixed_sell,
                                     allow_grid_charging=True, allow_grid_export=True)
    
    fin_self = calculate_financials(baseline_df, sim_self.df, offer["capacity_kwh"], offer.get("power_kw", offer["capacity_kwh"]*0.5),
                                    econ_config, buy_price_col, sell_price_col, fixed_buy, fixed_sell)
    fin_price = calculate_financials(baseline_df, sim_price.df, offer["capacity_kwh"], offer.get("power_kw", offer["capacity_kwh"]*0.5),
                                     econ_config, buy_price_col, sell_price_col, fixed_buy, fixed_sell)
    
    promised_mwh = offer.get("promised_annual_saving_mwh", 0)
    simulated_mwh = sim_self.summary["grid_import_reduction_kwh"] / 1000.0
    ratio = (simulated_mwh / promised_mwh * 100) if promised_mwh>0 else 0
    
    return {
        "offer": offer,
        "simulated_self": sim_self.summary,
        "simulated_price": sim_price.summary,
        "financial_self": {
            "annual_benefit_eur": fin_self.annual_gross_benefit_eur,
            "payback": fin_self.simple_payback_years,
            "npv": fin_self.npv_eur,
            "capex": fin_self.capex_eur
        },
        "financial_price": {
            "annual_benefit_eur": fin_price.annual_gross_benefit_eur,
            "payback": fin_price.simple_payback_years,
            "npv": fin_price.npv_eur,
        },
        "promised_vs_simulated": {
            "promised_mwh": promised_mwh,
            "simulated_mwh": simulated_mwh,
            "ratio_pct": ratio,
            "difference_mwh": simulated_mwh - promised_mwh,
            "is_overpromised": ratio < 70
        },
        "sim_dfs": {
            "self": sim_self.df,
            "price": sim_price.df
        }
    }

def audit_existing_bess(baseline_df: pd.DataFrame, actual_bess_df: pd.DataFrame, config: BatteryConfig) -> Dict:
    """
    actual_bess_df must have timestamp, battery_charge_ac_kwh, battery_discharge_ac_kwh, soc_kwh (from actual operation)
    Compares ACTUAL vs SIMULATED OPTIMAL
    """
    # Actual operation metrics
    actual_charge = actual_bess_df["battery_charge_ac_kwh"].sum() if "battery_charge_ac_kwh" in actual_bess_df.columns else 0
    actual_discharge = actual_bess_df["battery_discharge_ac_kwh"].sum() if "battery_discharge_ac_kwh" in actual_bess_df.columns else 0
    actual_import = actual_bess_df["grid_import_after_kwh"].sum() if "grid_import_after_kwh" in actual_bess_df.columns else baseline_df["grid_import_kwh"].sum()
    
    # Simulated optimal
    sim_opt = simulate_self_consumption(baseline_df, config)
    opt_discharge = sim_opt.summary["energy_discharged_ac_kwh"]
    opt_import = sim_opt.summary["grid_import_after_kwh"]
    
    utilization = (actual_discharge / opt_discharge * 100) if opt_discharge>0 else 0
    missed_saving_kwh = opt_import - actual_import  # negative means actual worse
    
    return {
        "actual": {
            "charged_kwh": float(actual_charge),
            "discharged_kwh": float(actual_discharge),
            "import_after_kwh": float(actual_import),
            "equivalent_cycles": float(actual_discharge / config.usable_capacity_kwh) if config.usable_capacity_kwh>0 else 0
        },
        "optimal_self": sim_opt.summary,
        "audit": {
            "utilization_pct": utilization,
            "unused_potential_pct": 100 - utilization,
            "missed_import_reduction_kwh": float(actual_import - opt_import),
            "is_underutilized": utilization < 70
        },
        "sim_df": sim_opt.df
    }
