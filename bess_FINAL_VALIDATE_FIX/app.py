"""
Energomonitor BESS - FINAL FIXED - session_state persistence
"""
import sys
from pathlib import Path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from core.data_loader import load_raw_file, auto_detect_columns, normalize_data
from core.energy_balance import compute_baseline_balance
from core.battery_simulator import BatteryConfig, simulate_self_consumption
from core.economics import EconomicConfig, calculate_financials
from core.price_optimizer import simulate_price_aware
from core.offer_comparator import compare_offer
from core.ibex_provider import get_price_data_for_simulation, generate_synthetic_ibex_prices, resample_hourly_to_15min
from core.recommendations import build_summary_json, generate_expert_report
from core.report_generator import create_pdf_report

st.set_page_config(page_title="Energomonitor BESS", layout="wide", page_icon="🔋")

st.markdown("""
<style>
.main-header{font-size:32px;font-weight:800;color:#0B1F3A}
.card{background:#FFF;border:1px solid #E2E8F0;border-radius:16px;padding:16px}
.bad{background:#FFF5F5;border-left:4px solid #E53E3E;padding:12px;border-radius:8px}
.ok{background:#F0FFF4;border-left:4px solid #38A169;padding:12px;border-radius:8px}
.entsoe{background:#EBF8FF;border-left:4px solid #3182CE;padding:12px;border-radius:8px}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">🔋 Energomonitor BESS - с реални IBEX цени от ENTSO-E</div>', unsafe_allow_html=True)

# init session
for k in ["baseline_df","baseline_obj","quality","df_raw","df_raw_name","sim_self","sim_price","price_label"]:
    if k not in st.session_state:
        st.session_state[k]=None

st.sidebar.title("⚡ Ценови данни - IBEX")
price_source = st.sidebar.radio("Източник", ["ENTSO-E Transparency (реални IBEX цени)", "Качи файл с IBEX цени", "Демо синтетични цени (IBEX-like)"], index=2, key="ps")

entsoe_token = st.sidebar.text_input("ENTSO-E Token", type="password", key="token") if "ENTSO-E" in price_source else None
uploaded_price_file = st.sidebar.file_uploader("Файл с цени", type=["csv","xlsx","xls"], key="price_file") if "Качи файл" in price_source else None
uploaded_price_filename = uploaded_price_file.name if uploaded_price_file else None

demo_path = BASE_DIR / "data" / "demo_12m_15min.csv"

c1,c2 = st.columns([3,1])
with c1:
    uploaded = st.file_uploader("Качи CSV/XLSX с потребление + ФЕЦ", type=["csv","xlsx","xls"], key="main_upload")
with c2:
    use_demo = st.button("🔥 Зареди демо обект (12м, 15мин, 100kWp)", use_container_width=True, key="demo_btn_final")

# PERSISTENT storage
if use_demo:
    if demo_path.exists():
        st.session_state.df_raw = pd.read_csv(demo_path)
        st.session_state.df_raw_name = demo_path.name
        st.success(f"Демо заредено: {len(st.session_state.df_raw)} реда")
    else:
        st.error(f"Липсва {demo_path}")

if uploaded is not None:
    st.session_state.df_raw = load_raw_file(uploaded, uploaded.name)
    st.session_state.df_raw_name = uploaded.name
    st.success(f"{uploaded.name}: {len(st.session_state.df_raw)} реда")

df_raw = st.session_state.df_raw

if df_raw is not None:
    st.info(f"Активни данни: {st.session_state.df_raw_name} | {len(df_raw)} реда | {len(df_raw.columns)} колони")
    st.dataframe(df_raw.head(6), use_container_width=True)
    auto_map = auto_detect_columns(df_raw)
    cols = list(df_raw.columns)
    mapping={}; units={}
    col_a,col_b,col_c = st.columns(3)
    def sel(can,label,cont):
        with cont:
            opts=["--"]+cols
            guess=auto_map.get(can)
            idx=opts.index(guess) if guess in opts else 0
            st.write(f"**{label}** (авто:{guess})")
            s=st.selectbox(can, opts, index=idx, key=f"map_{can}_final", label_visibility="collapsed")
            if s!="--":
                mapping[can]=s
                u=st.selectbox(f"unit_{can}",["kW","kWh","MW","MWh"], key=f"unit_{can}_final")
                units[can]=u
    sel("timestamp","Време / timestamp",col_a)
    sel("consumption","Потребление",col_b)
    sel("pv_production","ФЕЦ производство",col_c)
    
    # optional price columns
    with st.expander("Опционално: цени от файла", expanded=False):
        col_p1,col_p2 = st.columns(2)
        sel("buy_price","Покупка от мрежата (опц.)",col_p1)
        sel("sell_price","Отдаване към мрежата (опц.)",col_p2)
    
    st.divider()
    if all(k in mapping for k in ["timestamp","consumption","pv_production"]):
        if st.button("▶ Валидирай и изчисли базов баланс", type="primary", use_container_width=True, key="validate_final_btn"):
            try:
                with st.spinner("Изчислявам базов баланс..."):
                    norm, qr, _ = normalize_data(df_raw, mapping, units)
                    base = compute_baseline_balance(norm)
                    baseline_df = base.df
                    
                    # price handling
                    src_key = "entsoe" if "ENTSO-E" in price_source else "uploaded" if "Качи файл" in price_source else "demo"
                    try:
                        price_15, price_label = get_price_data_for_simulation(baseline_df, price_source=src_key, uploaded_file=uploaded_price_file, filename=uploaded_price_filename, entsoe_token=entsoe_token)
                        baseline_df["buy_price_EUR_per_MWh"] = price_15["buy_price_EUR_per_MWh"].values[:len(baseline_df)] if "buy_price_EUR_per_MWh" in price_15.columns else 180
                        baseline_df["sell_price_EUR_per_MWh"] = price_15["sell_price_EUR_per_MWh"].values[:len(baseline_df)] if "sell_price_EUR_per_MWh" in price_15.columns else 90
                    except Exception as e:
                        st.warning(f"Цени fallback: {e}")
                        synth = generate_synthetic_ibex_prices(baseline_df["timestamp"].min(), baseline_df["timestamp"].max())
                        price_15 = resample_hourly_to_15min(synth)
                        baseline_df["buy_price_EUR_per_MWh"] = price_15["buy_price_EUR_per_MWh"].values[:len(baseline_df)]
                        baseline_df["sell_price_EUR_per_MWh"] = price_15["sell_price_EUR_per_MWh"].values[:len(baseline_df)]
                        price_label = "Демо цени"
                    
                    st.session_state.baseline_df = baseline_df
                    st.session_state.baseline_obj = base
                    st.session_state.quality = qr
                    st.session_state.price_label = price_label
                    st.success(f"✅ Готово! Базов баланс: {len(baseline_df)} интервала, {base.totals['total_consumption_kwh']/1000:.0f} MWh товар")
                    st.balloons()
            except Exception as e:
                st.error(f"Грешка при валидация: {e}")
                import traceback
                st.code(traceback.format_exc())
    else:
        st.warning("Изберете колони за време, потребление и ФЕЦ")

if st.session_state.baseline_df is not None:
    base=st.session_state.baseline_obj
    df=st.session_state.baseline_df
    qr=st.session_state.quality
    totals=base.totals
    ratios=base.ratios
    price_label = st.session_state.get("price_label","Демо цени")
    st.divider()
    st.header("📊 Без BESS")
    st.markdown(f'<div class="entsoe">💰 Ценови източник: <b>{price_label}</b></div>', unsafe_allow_html=True)
    k1,k2,k3,k4=st.columns(4)
    k1.markdown(f'<div class="card">Потребление<br><b>{totals["total_consumption_kwh"]/1000:.0f} MWh</b></div>', unsafe_allow_html=True)
    k2.markdown(f'<div class="card">ФЕЦ<br><b style="color:#38A169">{totals["total_pv_generation_kwh"]/1000:.1f} MWh</b></div>', unsafe_allow_html=True)
    k3.markdown(f'<div class="card">Self-Consumption<br><b>{ratios["self_consumption_ratio_pct"]:.1f}%</b></div>', unsafe_allow_html=True)
    k4.markdown(f'<div class="card">Износ<br><b>{totals["total_grid_export_kwh"]:.0f} kWh</b></div>', unsafe_allow_html=True)
    
    sample=df.head(int(7*24/totals["interval_hours_avg"]))
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=sample["timestamp"], y=sample["consumption_kwh"]/sample["_interval_h"], name="Товар", fill='tozeroy'))
    fig.add_trace(go.Scatter(x=sample["timestamp"], y=sample["pv_generation_kwh"]/sample["_interval_h"], name="ФЕЦ"))
    fig.update_layout(height=350, hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True, key="main_chart_final")
    
    st.divider()
    st.header("🔋 BESS симулация")
    rated=st.sidebar.slider("Капацитет kWh", 20, 1000, 100, step=10, key="rated_final")
    c_ratio=st.sidebar.slider("C-rate", 0.2, 1.0, 0.5, key="crate_final")
    min_soc=st.sidebar.slider("Min SOC %", 0, 30, 10, key="min_final")
    max_soc=st.sidebar.slider("Max SOC %", 70, 100, 90, key="max_final")
    init_soc=st.sidebar.slider("Init SOC %", 0, 100, 50, key="init_final")
    rt_eff=st.sidebar.slider("Round-trip %", 80, 98, 90, key="rteff_final")
    capex_kwh=st.sidebar.number_input("€/kWh", value=350, key="ckwh_final")
    capex_kw=st.sidebar.number_input("€/kW", value=150, key="ckw_final")
    install=st.sidebar.number_input("Инсталация €", value=5000, key="inst_final")
    discount=st.sidebar.slider("Дисконт %", 0, 12, 6, key="disc_final")
    lifetime=st.sidebar.slider("Живот години", 5, 25, 15, key="life_final")
    buy_fixed=st.sidebar.number_input("Фикс покупка €/MWh", value=180, key="buyf_final")
    sell_fixed=st.sidebar.number_input("Фикс продажба €/MWh", value=90, key="sellf_final")
    
    config=BatteryConfig(rated_capacity_kwh=rated, max_charge_power_kw=rated*c_ratio, max_discharge_power_kw=rated*c_ratio, min_soc_pct=min_soc, max_soc_pct=max_soc, initial_soc_pct=init_soc, round_trip_efficiency=rt_eff/100.0)
    econ_conf=EconomicConfig(capex_per_kwh_eur=capex_kwh, capex_per_kw_eur=capex_kw, installation_eur=install, discount_rate_pct=discount, battery_lifetime_years=lifetime)
    
    if st.button("▶ Симулирай BESS", type="primary", key="sim_final_btn"):
        with st.spinner("Симулирам..."):
            sim_self=simulate_self_consumption(df, config)
            sim_price=simulate_price_aware(df, config, buy_price_col="buy_price_EUR_per_MWh", sell_price_col="sell_price_EUR_per_MWh", fixed_buy_price=buy_fixed, fixed_sell_price=sell_fixed, allow_grid_charging=True, allow_grid_export=True)
            st.session_state.sim_self=sim_self
            st.session_state.sim_price=sim_price
            st.success("Симулация готова!")
    
    if st.session_state.sim_self is not None:
        sim_self=st.session_state.sim_self
        sim_price=st.session_state.sim_price
        s=sim_self.summary
        fin_self=calculate_financials(df, sim_self.df, rated, rated*c_ratio, econ_conf, buy_price_col="buy_price_EUR_per_MWh", sell_price_col="sell_price_EUR_per_MWh", fixed_buy_price=buy_fixed, fixed_sell_price=sell_fixed)
        fin_price=calculate_financials(df, sim_price.df, rated, rated*c_ratio, econ_conf, buy_price_col="buy_price_EUR_per_MWh", sell_price_col="sell_price_EUR_per_MWh", fixed_buy_price=buy_fixed, fixed_sell_price=sell_fixed)
        st.subheader(f"Резултат с {price_label}")
        c1,c2,c3=st.columns(3)
        c1.metric("CAPEX", f"{fin_self.capex_eur:,.0f} €")
        c2.metric("Полза self", f"{fin_self.annual_gross_benefit_eur:.0f} €/год")
        c3.metric("Полза price-aware", f"{fin_price.annual_gross_benefit_eur:.0f} €/год")
        
        sample_sim=sim_price.df.head(int(7*24/totals["interval_hours_avg"]))
        fig_soc=go.Figure()
        fig_soc.add_trace(go.Scatter(x=sample_sim["timestamp"], y=sample_sim["soc_pct"], name="SOC %", fill='tozeroy'))
        fig_soc.add_trace(go.Scatter(x=sample_sim["timestamp"], y=sample_sim["buy_price_EUR_per_MWh"], name="IBEX €/MWh", yaxis="y2"))
        fig_soc.update_layout(height=400, yaxis=dict(range=[0,100]), yaxis2=dict(overlaying="y", side="right"), hovermode="x unified")
        st.plotly_chart(fig_soc, use_container_width=True, key="soc_final")
        
        if fin_self.simple_payback_years>15:
            st.markdown(f'<div class="bad"><b>Извод:</b> {rated}kWh за {fin_self.capex_eur:,.0f}€ дава {fin_self.annual_gross_benefit_eur:.0f}€/год. Payback {fin_self.simple_payback_years:.1f}г, NPV {fin_self.npv_eur:,.0f}€. <b>Няма икономическо основание.</b></div>', unsafe_allow_html=True)
