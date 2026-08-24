"""
PHASE 3 - CAPEX and Financial model
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional

@dataclass
class EconomicConfig:
    # CAPEX input modes
    capex_total_eur: Optional[float] = None  # if provided, overrides per kWh/kW
    capex_per_kwh_eur: float = 350.0  # EUR/kWh usable? We'll use rated
    capex_per_kw_eur: float = 150.0   # PCS
    installation_eur: float = 5000.0
    other_capex_eur: float = 0.0
    
    opex_annual_pct: float = 2.0  # % of CAPEX per year
    opex_fixed_eur: float = 0.0
    
    battery_lifetime_years: int = 15
    discount_rate_pct: float = 6.0
    degradation_pct_per_year: float = 1.5
    # Price escalation
    electricity_price_escalation_pct: float = 3.0

    def total_capex(self, rated_kwh: float, power_kw: float) -> float:
        if self.capex_total_eur is not None:
            return self.capex_total_eur
        return rated_kwh * self.capex_per_kwh_eur + power_kw * self.capex_per_kw_eur + self.installation_eur + self.other_capex_eur

@dataclass
class FinancialResult:
    capex_eur: float
    annual_opex_eur: float
    annual_gross_benefit_eur: float
    annual_net_benefit_eur: float
    simple_payback_years: float
    npv_eur: float
    irr_pct: Optional[float]
    roi_pct: float
    cash_flows: list
    assumptions: Dict

def compute_electricity_cost(df: pd.DataFrame, import_col: str, export_col: str,
                             buy_price_col: str = None, sell_price_col: str = None,
                             fixed_buy_price_eur_per_mwh: float = 180.0,
                             fixed_sell_price_eur_per_mwh: float = 90.0) -> float:
    """
    Compute total electricity cost for period in EUR
    cost = import * buy_price - export * sell_price
    Prices expected in EUR/MWh, energy in kWh -> convert
    """
    if buy_price_col and buy_price_col in df.columns:
        buy_price = df[buy_price_col].fillna(fixed_buy_price_eur_per_mwh)  # EUR/MWh
    else:
        buy_price = pd.Series(fixed_buy_price_eur_per_mwh, index=df.index)
    
    if sell_price_col and sell_price_col in df.columns:
        sell_price = df[sell_price_col].fillna(fixed_sell_price_eur_per_mwh)
    else:
        sell_price = pd.Series(fixed_sell_price_eur_per_mwh, index=df.index)
    
    import_kwh = df[import_col].fillna(0)
    export_kwh = df[export_col].fillna(0)
    
    # cost = kWh * EUR/MWh /1000
    cost = (import_kwh * buy_price / 1000.0) - (export_kwh * sell_price / 1000.0)
    return float(cost.sum()), buy_price, sell_price

def calculate_financials(baseline_df: pd.DataFrame,
                         bess_df: pd.DataFrame,
                         battery_rated_kwh: float,
                         battery_power_kw: float,
                         econ_config: EconomicConfig,
                         buy_price_col: str = "buy_price_EUR_per_MWh",
                         sell_price_col: str = "sell_price_EUR_per_MWh",
                         fixed_buy_price: float = 180.0,
                         fixed_sell_price: float = 90.0,
                         period_years: float = None) -> FinancialResult:
    """
    Calculates annual benefits extrapolating from simulated period
    """
    # Determine period length in years
    if period_years is None:
        start = baseline_df["timestamp"].min()
        end = baseline_df["timestamp"].max()
        days = (end - start).days
        period_years = max(0.1, days / 365.0)
    
    # Costs
    cost_before, _, _ = compute_electricity_cost(
        baseline_df, "grid_import_kwh", "grid_export_kwh",
        buy_price_col, sell_price_col, fixed_buy_price, fixed_sell_price
    )
    cost_after, _, _ = compute_electricity_cost(
        bess_df, "grid_import_after_kwh", "grid_export_after_kwh",
        buy_price_col, sell_price_col, fixed_buy_price, fixed_sell_price
    )
    
    gross_benefit_period = cost_before - cost_after  # EUR for simulated period
    # Annualize
    annual_gross = gross_benefit_period / period_years
    
    capex = econ_config.total_capex(battery_rated_kwh, battery_power_kw)
    annual_opex = capex * econ_config.opex_annual_pct / 100.0 + econ_config.opex_fixed_eur
    annual_net = annual_gross - annual_opex
    
    # Simple payback
    simple_payback = capex / annual_net if annual_net > 0 else float('inf')
    
    # NPV and IRR - cash flow model with degradation and price escalation
    cash_flows = [-capex]
    npv = -capex
    for year in range(1, econ_config.battery_lifetime_years + 1):
        # Degradation reduces benefit, escalation increases price
        degradation_factor = (1 - econ_config.degradation_pct_per_year/100.0) ** (year-1)
        escalation_factor = (1 + econ_config.electricity_price_escalation_pct/100.0) ** (year-1)
        cf = annual_net * degradation_factor * escalation_factor
        # discount
        discounted = cf / ((1 + econ_config.discount_rate_pct/100.0) ** year)
        npv += discounted
        cash_flows.append(cf)
    
    # IRR approximation - find rate where NPV=0 using numpy
    irr = None
    try:
        # Use numpy irr if cash flows not all negative
        if annual_net > 0:
            # simple bisection
            def npv_at_rate(rate):
                s = -capex
                for yr in range(1, econ_config.battery_lifetime_years+1):
                    deg = (1 - econ_config.degradation_pct_per_year/100.0) ** (yr-1)
                    esc = (1 + econ_config.electricity_price_escalation_pct/100.0) ** (yr-1)
                    cf = annual_net * deg * esc
                    s += cf / ((1+rate)**yr)
                return s
            # search
            low, high = -0.5, 2.0
            for _ in range(50):
                mid = (low+high)/2
                if npv_at_rate(mid) > 0:
                    low = mid
                else:
                    high = mid
                if abs(high-low) < 0.0001:
                    break
            irr_candidate = (low+high)/2
            if irr_candidate > -0.9 and irr_candidate < 5:
                irr = irr_candidate * 100
            else:
                irr = None
    except:
        irr = None
    
    roi = (annual_net * econ_config.battery_lifetime_years / capex * 100) if capex>0 else 0
    
    assumptions = {
        "period_days": (baseline_df["timestamp"].max() - baseline_df["timestamp"].min()).days,
        "period_years": period_years,
        "buy_price_source": buy_price_col if buy_price_col in baseline_df.columns else f"fixed {fixed_buy_price} EUR/MWh",
        "sell_price_source": sell_price_col if sell_price_col in baseline_df.columns else f"fixed {fixed_sell_price} EUR/MWh",
        "capex_breakdown": f"{battery_rated_kwh}kWh*{econ_config.capex_per_kwh_eur}+{battery_power_kw}kW*{econ_config.capex_per_kw_eur}+{econ_config.installation_eur}",
        "discount_rate": econ_config.discount_rate_pct,
        "lifetime": econ_config.battery_lifetime_years,
        "degradation": econ_config.degradation_pct_per_year,
        "price_escalation": econ_config.electricity_price_escalation_pct,
        "opex_pct": econ_config.opex_annual_pct,
    }
    
    return FinancialResult(
        capex_eur=capex,
        annual_opex_eur=annual_opex,
        annual_gross_benefit_eur=annual_gross,
        annual_net_benefit_eur=annual_net,
        simple_payback_years=simple_payback,
        npv_eur=npv,
        irr_pct=irr,
        roi_pct=roi,
        cash_flows=cash_flows,
        assumptions=assumptions
    )
