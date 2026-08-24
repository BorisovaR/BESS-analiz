"""
Energomonitor BESS - COMPLETE MVP PHASE 1-8 with ENTSO-E IBEX integration
Fixed for Streamlit Cloud deployment - uses relative paths
"""
import sys
from pathlib import Path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from core.data_loader import load_raw_file, auto_detect_columns, normalize_data
from core.energy_balance import compute_baseline_balance
from core.battery_simulator import BatteryConfig, simulate_self_consumption
from core.economics import EconomicConfig, calculate_financials
from core.price_optimizer import simulate_price_aware
from core.price_optimizer_lp import simulate_lp_optimal
from core.offer_comparator import compare_offer
from core.ibex_provider import get_price_data_for_simulation, generate_synthetic_ibex_prices, resample_hourly_to_15min
from core.recommendations import build_summary_json, generate_expert_report
from core.report_generator import create_pdf_report

st.set_page_config(page_title="Energomonitor BESS - ENTSO-E", layout="wide", page_icon="🔋")

st.markdown("""
<style>
.main-header{font-size:32px;font-weight:800;color:#0B1F3A}
.sub-header{font-size:15px;color:#718096}
.kpi{font-size:26px;font-weight:700}
.card{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:16px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,0.05)}
.bad{background:#FFF5F5;border-left:4px solid #E53E3E;padding:12px;border-radius:8px}
.ok{background:#F0FFF4;border-left:4px solid #38A169;padding:12px;border-radius:8px}
.entsoe{background:#EBF8FF;border-left:4px solid #3182CE;padding:12px;border-radius:8px}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">🔋 Energomonitor BESS - с реални IBEX цени от ENTSO-E</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Технико-икономическа оценка • Симулация timestamp по timestamp • ENTSO-E Day-Ahead Bulgaria BZN</div>', unsafe_allow_html=True)

if "baseline_df" not in st.session_state: st.session_state.baseline_df=None
if "baseline_obj" not in st.session_state: st.session_state.baseline_obj=None
if "quality" not in st.session_state: st.session_state.quality=None

st.sidebar.title("⚡ Ценови данни - IBEX")
price_source = st.sidebar.radio("Източник", ["ENTSO-E Transparency (реални IBEX цени)", "Качи файл с IBEX цени (CSV/XLSX)", "Демо синтетични цени (IBEX-like)"], index=2)

entsoe_token = None
uploaded_price_file = None
uploaded_price_filename = None

if "ENTSO-E" in price_source:
    st.sidebar.markdown('<div class="entsoe">Реални Day-Ahead цени за България (BZN) от ENTSO-E. Изисква безплатен токен от transparency.entsoe.eu</div>', unsafe_allow_html=True)
    entsoe_token = st.sidebar.text_input("ENTSO-E Security Token", type="password")
    if not entsoe_token:
        st.sidebar.warning("Без токен ще се ползват синтетични цени.")
elif "Качи файл" in price_source:
    uploaded_price_file = st.sidebar.file_uploader("Качи файл с цени", type=["csv","xlsx","xls"], key="price_upload")
    if uploaded_price_file:
        uploaded_price_filename = uploaded_price_file.name

# FIXED PATH - relative for Streamlit Cloud
demo_path = BASE_DIR / "data" / "demo_12m_15min.csv"

c1,c2=st.columns([3,1])
with c1:
    uploaded=st.file_uploader("Качи CSV/XLSX с потребление + ФЕЦ", type=["csv","xlsx","xls"], key="main_upload")
with c2:
    use_demo=st.button("🔥 Демо - малък излишък", use_container_width=True)

df_raw=None
if use_demo:
    if demo_path.exists():
        df_raw=pd.read_csv(demo_path)
        st.success(f"Демо: {demo_path.name} - 100kWp, 1184 MWh товар, 91 MWh ФЕЦ, износ 0.6 MWh/год")
    else:
        st.error(f"Демо файлът не е намерен на {demo_path}. Проверете структурата на repo-то: data/demo_12m_15min.csv трябва да е в root/data/")
elif uploaded:
    df_raw=load_raw_file(uploaded, uploaded.name)
    st.success(f"{uploaded.name}: {len(df_raw)} реда")

if df_raw is not None:
    with st.expander("Import Wizard + ENTSO-E", expanded=True):
        st.dataframe(df_raw.head(6), use_container_width=True)
        auto_map=auto_detect_columns(df_raw)
        cols=list(df_raw.columns)
        mapping={}; units={}
        col_a,col_b,col_c=st.columns(3)
        def sel(can,label,cont,req=False):
            with cont:
                opts=["--"]+cols
                guess=auto_map.get(can)
                idx=opts.index(guess) if guess in opts else 0
                st.write(f"**{label}**{'*' if req else ''} (авто:{guess})")
                s=st.selectbox(can,opts,index=idx,key=f"m_{can}",label_visibility="collapsed")
                if s!="--":
                    mapping[can]=s
                    if "price" not in can:
                        u=st.selectbox(f"u_{can}",["kW","kWh","MW","MWh"],key=f"u_{can}")
                        units[can]=u
        sel("timestamp","Време",col_a,True)
        sel("consumption","Потребление",col_b,True)
        sel("pv_production","ФЕЦ",col_c,True)
        if all(k in mapping for k in ["timestamp","consumption","pv_production"]):
            if st.button("▶ Изчисли базов баланс + зареди IBEX цени", type="primary"):
                with st.spinner("Зареждам данни и IBEX цени..."):
                    norm,qr,_=normalize_data(df_raw,mapping,units)
                    base=compute_baseline_balance(norm)
                    baseline_df = base.df
                    src_key = "entsoe" if "ENTSO-E" in price_source else "uploaded" if "Качи файл" in price_source else "demo"
                    try:
                        price_15, price_label = get_price_data_for_simulation(baseline_df, price_source=src_key, uploaded_file=uploaded_price_file, filename=uploaded_price_filename, entsoe_token=entsoe_token)
                        baseline_df["buy_price_EUR_per_MWh"] = price_15["buy_price_EUR_per_MWh"].values[:len(baseline_df)] if "buy_price_EUR_per_MWh" in price_15.columns else 180
                        baseline_df["sell_price_EUR_per_MWh"] = price_15["sell_price_EUR_per_MWh"].values[:len(baseline_df)] if "sell_price_EUR_per_MWh" in price_15.columns else 90
                        st.session_state.price_label = price_label
                    except Exception as e:
                        st.warning(f"Грешка при цени: {e}. Ползват се демо цени.")
                        synth = generate_synthetic_ibex_prices(baseline_df["timestamp"].min(), baseline_df["timestamp"].max())
                        price_15 = resample_hourly_to_15min(synth)
                        baseline_df["buy_price_EUR_per_MWh"] = price_15["buy_price_EUR_per_MWh"].values[:len(baseline_df)]
                        baseline_df["sell_price_EUR_per_MWh"] = price_15["sell_price_EUR_per_MWh"].values[:len(baseline_df)]
                        st.session_state.price_label = f"Демо цени (fallback): {e}"
                    st.session_state.baseline_df=baseline_df
                    st.session_state.baseline_obj=base
                    st.session_state.quality=qr
                st.rerun()

if st.session_state.baseline_df is not None:
    base=st.session_state.baseline_obj
    df=st.session_state.baseline_df
    qr=st.session_state.quality
    totals=base.totals
    ratios=base.ratios
    price_label = st.session_state.get("price_label", "Демо цени")
    st.divider()
    st.header("📊 Без BESS")
    st.markdown(f'<div class="entsoe">💰 Ценови източник: <b>{price_label}</b></div>', unsafe_allow_html=True)
    k1,k2,k3,k4=st.columns(4)
    k1.markdown(f'<div class="card">Потребление<br><span class="kpi">{totals["total_consumption_kwh"]/1000:.0f} MWh</span></div>', unsafe_allow_html=True)
    k2.markdown(f'<div class="card">ФЕЦ<br><span class="kpi" style="color:#38A169">{totals["total_pv_generation_kwh"]/1000:.1f} MWh</span></div>', unsafe_allow_html=True)
    k3.markdown(f'<div class="card">Self-Consumption<br><span class="kpi">{ratios["self_consumption_ratio_pct"]:.1f}%</span></div>', unsafe_allow_html=True)
    k4.markdown(f'<div class="card">Износ<br><span class="kpi">{totals["total_grid_export_kwh"]:.0f} kWh</span></div>', unsafe_allow_html=True)
    
    if "buy_price_EUR_per_MWh" in df.columns:
        sample_price = df.head(int(7*24/totals["interval_hours_avg"]))
        fig_price = go.Figure()
        fig_price.add_trace(go.Scatter(x=sample_price["timestamp"], y=sample_price["buy_price_EUR_per_MWh"], name="Покупка €/MWh", line=dict(color="#DD6B20", width=2)))
        fig_price.add_trace(go.Scatter(x=sample_price["timestamp"], y=sample_price["sell_price_EUR_per_MWh"], name="Продажба €/MWh", line=dict(color="#3182CE", dash="dot")))
        fig_price.update_layout(height=300, hovermode="x unified")
        st.plotly_chart(fig_price, use_container_width=True)
    
    sample=df.head(int(7*24/totals["interval_hours_avg"]))
    ca,cb=st.columns([2,1])
    with ca:
        fig=go.Figure()
        fig.add_trace(go.Scatter(x=sample["timestamp"], y=sample["consumption_kwh"]/sample["_interval_h"], fill='tozeroy', name="Товар", line=dict(color="#E53E3E"), fillcolor="rgba(229,62,62,0.15)"))
        fig.add_trace(go.Scatter(x=sample["timestamp"], y=sample["pv_generation_kwh"]/sample["_interval_h"], fill='tozeroy', name="ФЕЦ", line=dict(color="#38A169", width=2), fillcolor="rgba(56,161,105,0.25)"))
        fig.update_layout(height=350, hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
    with cb:
        hourly=df.groupby(df["timestamp"].dt.hour).agg(cons=("consumption_kwh",lambda x:(x/df.loc[x.index,"_interval_h"]).mean()), pv=("pv_generation_kwh",lambda x:(x/df.loc[x.index,"_interval_h"]).mean())).reset_index()
        fig2=go.Figure()
        fig2.add_trace(go.Bar(x=hourly["timestamp"], y=hourly["cons"], name="Товар", marker_color="#FEB2B2"))
        fig2.add_trace(go.Bar(x=hourly["timestamp"], y=hourly["pv"], name="ФЕЦ", marker_color="#9AE6B4"))
        fig2.update_layout(height=350, barmode="group")
        st.plotly_chart(fig2, use_container_width=True)
    
    st.divider()
    st.header("🔋 BESS симулация")
    with st.sidebar:
        st.divider()
        st.subheader("BESS")
        rated=st.slider("Капацитет kWh", 20, 1000, 100, step=10)
        c_ratio=st.slider("C-rate", 0.2, 1.0, 0.5)
        min_soc=st.slider("Min SOC %", 0, 30, 10)
        max_soc=st.slider("Max SOC %", 70, 100, 90)
        init_soc=st.slider("Init SOC %", 0, 100, 50)
        rt_eff=st.slider("Round-trip %", 80, 98, 90)
        st.subheader("CAPEX")
        capex_kwh=st.number_input("€/kWh", value=350)
        capex_kw=st.number_input("€/kW", value=150)
        install=st.number_input("Инсталация €", value=5000)
        st.subheader("Икономика")
        discount=st.slider("Дисконт %", 0, 12, 6)
        lifetime=st.slider("Живот години", 5, 25, 15)
        buy_price_fixed=st.number_input("Фикс покупка €/MWh", value=180)
        sell_price_fixed=st.number_input("Фикс продажба €/MWh", value=90)
    
    config=BatteryConfig(rated_capacity_kwh=rated, max_charge_power_kw=rated*c_ratio, max_discharge_power_kw=rated*c_ratio, min_soc_pct=min_soc, max_soc_pct=max_soc, initial_soc_pct=init_soc, round_trip_efficiency=rt_eff/100.0)
    econ_conf=EconomicConfig(capex_per_kwh_eur=capex_kwh, capex_per_kw_eur=capex_kw, installation_eur=install, discount_rate_pct=discount, battery_lifetime_years=lifetime)
    
    if st.button("▶ Симулирай", type="primary"):
        with st.spinner("Симулирам..."):
            sim_self=simulate_self_consumption(df, config)
            sim_price=simulate_price_aware(df, config, buy_price_col="buy_price_EUR_per_MWh", sell_price_col="sell_price_EUR_per_MWh", fixed_buy_price=buy_price_fixed, fixed_sell_price=sell_price_fixed, allow_grid_charging=True, allow_grid_export=True)
            st.session_state.sim_self=sim_self
            st.session_state.sim_price=sim_price
        st.rerun()
    
    if "sim_self" in st.session_state:
        sim_self=st.session_state.sim_self
        sim_price=st.session_state.sim_price
        s=sim_self.summary
        fin_self=calculate_financials(df, sim_self.df, rated, rated*c_ratio, econ_conf, buy_price_col="buy_price_EUR_per_MWh", sell_price_col="sell_price_EUR_per_MWh", fixed_buy_price=buy_price_fixed, fixed_sell_price=sell_price_fixed)
        fin_price=calculate_financials(df, sim_price.df, rated, rated*c_ratio, econ_conf, buy_price_col="buy_price_EUR_per_MWh", sell_price_col="sell_price_EUR_per_MWh", fixed_buy_price=buy_price_fixed, fixed_sell_price=sell_price_fixed)
        st.subheader(f"Резултат с {price_label}")
        c1,c2,c3=st.columns(3)
        c1.markdown(f'<div class="card">CAPEX<br><span class="kpi">{fin_self.capex_eur:,.0f} €</span></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="card">Полза self<br><span class="kpi">{fin_self.annual_gross_benefit_eur:.0f} €/год</span></div>', unsafe_allow_html=True)
        c3.markdown(f'<div class="card">Полза price-aware<br><span class="kpi" style="color:#553C9A">{fin_price.annual_gross_benefit_eur:.0f} €/год</span></div>', unsafe_allow_html=True)
        sample_sim=sim_price.df.head(int(7*24/totals["interval_hours_avg"]))
        fig_soc=go.Figure()
        fig_soc.add_trace(go.Scatter(x=sample_sim["timestamp"], y=sample_sim["soc_pct"], name="SOC %", line=dict(color="#805AD5", width=2.5), fill='tozeroy', fillcolor="rgba(128,90,213,0.15)"))
        fig_soc.add_trace(go.Scatter(x=sample_sim["timestamp"], y=sample_sim["buy_price_EUR_per_MWh"], name="IBEX €/MWh", line=dict(color="#DD6B20", dash="dot"), yaxis="y2"))
        fig_soc.update_layout(height=420, yaxis=dict(title="SOC %", range=[0,100]), yaxis2=dict(title="€/MWh", overlaying="y", side="right"), hovermode="x unified")
        st.plotly_chart(fig_soc, use_container_width=True)
        if fin_self.simple_payback_years>15:
            st.markdown(f'<div class="bad"><b>Извод:</b> {rated}kWh за {fin_self.capex_eur:,.0f}€ дава {fin_self.annual_gross_benefit_eur:.0f}€/год. Payback {fin_self.simple_payback_years:.1f}г, NPV {fin_self.npv_eur:,.0f}€. <b>Няма икономическо основание.</b></div>', unsafe_allow_html=True)
        
        st.divider()
        st.header("📋 Сравни оферта (D)")
        col_o1,col_o2,col_o3=st.columns(3)
        with col_o1:
            offer_cap=st.number_input("Оферта kWh", value=200)
            offer_power=st.number_input("Оферта kW", value=100)
        with col_o2:
            offer_capex=st.number_input("CAPEX €", value=80000)
            offer_promised=st.number_input("Обещано MWh/год", value=15.0)
        with col_o3:
            offer_eff=st.slider("Ефективност %", 80, 98, 90)
        if st.button("📊 Сравни оферта"):
            offer_dict={"capacity_kwh":offer_cap,"power_kw":offer_power,"capex_eur":offer_capex,"charge_eff":(offer_eff/100)**0.5,"discharge_eff":(offer_eff/100)**0.5,"promised_annual_saving_mwh":offer_promised}
            econ_for_offer=EconomicConfig(capex_total_eur=offer_capex, battery_lifetime_years=lifetime, discount_rate_pct=discount)
            comp=compare_offer(df, offer_dict, econ_for_offer, buy_price_col="buy_price_EUR_per_MWh", sell_price_col="sell_price_EUR_per_MWh", fixed_buy=buy_price_fixed, fixed_sell=sell_price_fixed)
            st.session_state.offer_comp=comp
            st.rerun()
        if "offer_comp" in st.session_state:
            comp=st.session_state.offer_comp
            promised=comp["promised_vs_simulated"]["promised_mwh"]
            simulated=comp["promised_vs_simulated"]["simulated_mwh"]
            ratio=comp["promised_vs_simulated"]["ratio_pct"]
            fig_offer=go.Figure()
            fig_offer.add_trace(go.Bar(x=["Обещано","Симулирано"], y=[promised, simulated], marker_color=["#A0AEC0","#38A169" if ratio>70 else "#E53E3E"], text=[f"{promised} MWh", f"{simulated:.2f} MWh"], textposition="auto"))
            fig_offer.update_layout(height=350, title=f"Обещано {promised} MWh vs реалност {simulated:.2f} MWh ({ratio:.0f}%)")
            st.plotly_chart(fig_offer, use_container_width=True)
        
        st.divider()
        st.header("🤖 Експертен доклад + PDF")
        if st.button("📄 Генерирай доклад"):
            summary_json = build_summary_json(site_info={"name":"Индустриално","price_source":price_label}, baseline_totals=totals, baseline_ratios=ratios, data_quality={"score":qr.score, "resolution_minutes":qr.resolution_minutes, "total_rows":qr.total_rows, "warnings":qr.warnings}, bess_scenarios=[{"capacity_kwh":rated,"import_reduction_mwh":s["grid_import_reduction_kwh"]/1000,"self_consumption_after_pct":s["self_consumption_after_pct"]}], financials=[{"capacity_kwh":rated,"capex_eur":fin_self.capex_eur,"annual_gross_benefit_eur":fin_self.annual_gross_benefit_eur,"simple_payback_years":fin_self.simple_payback_years,"npv_eur":fin_self.npv_eur,"buy_price_source":price_label}], price_aware_comparison={"extra_benefit_eur":fin_price.annual_gross_benefit_eur-fin_self.annual_gross_benefit_eur})
            report_text = generate_expert_report(summary_json, use_api=True)
            st.session_state.summary_json=summary_json
            st.session_state.expert_report=report_text
            st.rerun()
        if "expert_report" in st.session_state:
            st.markdown(st.session_state.expert_report)
            if st.button("⬇️ Генерирай PDF"):
                pdf_bytes = create_pdf_report("ТЕХНИКО-ИКОНОМИЧЕСКА ОЦЕНКА НА BESS - с реални IBEX цени от ENTSO-E", st.session_state.expert_report, st.session_state.summary_json)
                st.session_state.pdf_bytes=pdf_bytes
                st.rerun()
            if "pdf_bytes" in st.session_state:
                st.download_button("📥 Свали PDF", data=st.session_state.pdf_bytes, file_name="BESS_IBEX_ENTSOE.pdf", mime="application/pdf")
