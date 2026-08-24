"""
Energomonitor BESS - Streamlit App PHASE 1
Focused on Import Wizard + Baseline Balance Dashboard
"""
import sys
sys.path.insert(0, "/mnt/data/bess_mvp")

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path
BASE_DIR = Path(__file__).parent

from core.data_loader import load_raw_file, auto_detect_columns, normalize_data
from core.energy_balance import compute_baseline_balance

st.set_page_config(
    page_title="Energomonitor BESS | Технико-икономическа оценка",
    layout="wide",
    page_icon="🔋"
)

# --- Styles ---
st.markdown("""
<style>
.main-header {font-size: 28px; font-weight: 700; color: #0B1F3A;}
.sub-header {font-size: 18px; color: #4A5568;}
.metric-card {background: #F7FAFC; border:1px solid #E2E8F0; padding:16px; border-radius:12px;}
.warning-box {background:#FFFBEB; border-left:4px solid #F59E0B; padding:12px; border-radius:6px;}
.info-box {background:#EBF8FF; border-left:4px solid #3182CE; padding:12px; border-radius:6px;}
</style>
""", unsafe_allow_html=True)

# Header
col_logo, col_title = st.columns([1,5])
with col_title:
    st.markdown('<div class="main-header">🔋 Energomonitor BESS - Технико-икономическа оценка</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Симулационен модел върху реални времеви данни - PHASE 1: Базов енергиен баланс</div>', unsafe_allow_html=True)

# Sidebar - Scenario
st.sidebar.title("Сценарий")
scenario = st.sidebar.radio(
    "Какъв е вашият обект?",
    ["A. Имам ФЕЦ и обмислям BESS (фокус MVP)",
     "B. Имам ФЕЦ + BESS и искам проверка",
     "C. Потребление и обмислям ФЕЦ/BESS",
     "D. Сравнявам оферта за BESS"],
    index=0
)

st.sidebar.divider()
st.sidebar.info("**Важно:** Суровите енергийни данни се използват само за изчисляване на анализа и не се изпращат към AI модели.")

# Session state
if "normalized_df" not in st.session_state:
    st.session_state.normalized_df = None
if "baseline" not in st.session_state:
    st.session_state.baseline = None
if "quality_report" not in st.session_state:
    st.session_state.quality_report = None

# --- STEP 1-2: Upload ---
st.header("STEP 1-2: Качване на данни")
st.write("Качете файл с потребление и ФЕЦ производство. Поддържат се CSV и XLSX. Предпочитана резолюция: 15 минути.")

demo_path = BASE_DIR / "data" / "demo_12m_15min.csv"
col_up1, col_up2 = st.columns([3,1])
with col_up1:
    uploaded = st.file_uploader("Изберете файл", type=["csv","xlsx","xls"])
with col_up2:
    use_demo = st.button("🔥 Зареди демо обект (12м, 15мин, 100kWp)")

df_raw = None
filename = None

if use_demo and demo_path.exists():
    df_raw = pd.read_csv(demo_path)
    filename = "demo_12m_15min.csv"
    st.success("Заредени са демонстрационни данни: индустриално предприятие, 100 kWp ФЕЦ, 12 месеца, 15-минутни интервали")
elif uploaded is not None:
    df_raw = load_raw_file(uploaded, uploaded.name)
    filename = uploaded.name
    st.success(f"Файл {filename} зареден: {len(df_raw)} реда, {len(df_raw.columns)} колони")

if df_raw is not None:
    st.subheader("Preview на данните")
    st.dataframe(df_raw.head(10), use_container_width=True)
    
    # Auto detection
    auto_map = auto_detect_columns(df_raw)
    st.subheader("STEP 3: Import Wizard - Съпоставяне на колони")
    
    st.markdown("Системата автоматично предлага колони. Моля потвърдете ръчно.")
    
    col_m1, col_m2, col_m3 = st.columns(3)
    mapping = {}
    units = {}
    
    # Define UI for mapping
    fields_to_map = [
        ("timestamp", "Време / timestamp *", True),
        ("consumption", "Потребление на предприятието *", True),
        ("pv_production", "Производство от ФЕЦ *", True),
        ("grid_import", "Покупка от мрежата (опционално)", False),
        ("grid_export", "Отдаване към мрежата (опционално)", False),
        ("buy_price", "Цена покупка (опц.)", False),
        ("sell_price", "Цена продажба (опц.)", False),
    ]
    
    unit_options = ["kW","kWh","MW","MWh","W","Wh"]
    
    for i, (canon, label, required) in enumerate(fields_to_map):
        col = [col_m1, col_m2, col_m3][i % 3]
        with col:
            options = ["-- не използвай --"] + list(df_raw.columns)
            auto_guess = auto_map.get(canon)
            default_idx = 0
            if auto_guess and auto_guess in df_raw.columns:
                default_idx = options.index(auto_guess)
                if required:
                    st.markdown(f"**{label}** - авто: `{auto_guess}`")
                else:
                    st.markdown(f"{label} - авто: `{auto_guess}`")
            else:
                st.markdown(f"{label}" + (" *" if required else ""))
            sel = st.selectbox(f"Колона за {canon}", options, index=default_idx, key=f"map_{canon}", label_visibility="collapsed")
            if sel != "-- не използвай --":
                mapping[canon] = sel
                # unit
                default_unit = "kW" if canon in ["consumption","pv_production","grid_import","grid_export"] else ("EUR/MWh" if "price" in canon else "kW")
                if "price" not in canon:
                    u = st.selectbox(f"Единица {canon}", unit_options, index=unit_options.index("kW") if canon in ["consumption","pv_production"] else 0, key=f"unit_{canon}")
                    units[canon] = u
                else:
                    units[canon] = "EUR/MWh"
    
    # Validate required
    missing_req = [c for c in ["timestamp","consumption","pv_production"] if c not in mapping]
    if missing_req:
        st.warning(f"Моля изберете задължителните колони: {', '.join(missing_req)}")
    else:
        if st.button("▶️ Валидирай и изчисли базов баланс", type="primary"):
            try:
                normalized, q_report, warnings = normalize_data(df_raw, mapping, units)
                st.session_state.normalized_df = normalized
                st.session_state.quality_report = q_report
                
                # baseline
                baseline = compute_baseline_balance(normalized)
                st.session_state.baseline = baseline
                
                st.success("Данните са нормализирани успешно")
            except Exception as e:
                st.error(f"Грешка при нормализация: {e}")
                st.exception(e)

# --- STEP 4: Data Quality ---
if st.session_state.quality_report is not None:
    qr = st.session_state.quality_report
    st.header("STEP 4: Качество на данните")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Data Quality Score", f"{qr.score}/100")
    c2.metric("Интервали", qr.total_rows)
    c3.metric("Резолюция", f"{qr.resolution_minutes:.0f} мин" if qr.resolution_minutes else "N/A")
    c4.metric("Липсващи интервала", qr.missing_intervals)
    
    if qr.warnings:
        for w in qr.warnings:
            st.markdown(f'<div class="warning-box">⚠️ {w}</div>', unsafe_allow_html=True)
    
    if qr.anomalies:
        with st.expander("Открити аномалии"):
            for a in qr.anomalies:
                st.write(f"- {a}")
    
    if qr.duplicate_timestamps:
        st.warning(f"Открити дублирани timestamps: {qr.duplicate_timestamps} (запазен е първият)")
    
    if qr.negative_values:
        st.warning(f"Отрицателни стойности: {qr.negative_values} - проверете дали не са грешка в измерването")

# --- STEP 5-6: Dashboard Baseline ---
if st.session_state.baseline is not None:
    baseline = st.session_state.baseline
    totals = baseline.totals
    ratios = baseline.ratios
    df_enriched = baseline.df
    
    st.header("STEP 5-6: Как работи обектът ви днес (без BESS)")
    st.write("Всички финансови изчисления използват ЕНЕРГИЯ (kWh), не моментна мощност (kW).")
    
    # Metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Общо потребление", f"{totals['total_consumption_kwh']/1000:.1f} MWh")
    m2.metric("Общо ФЕЦ производство", f"{totals['total_pv_generation_kwh']/1000:.1f} MWh")
    m3.metric("Директно собствено потребление", f"{totals['total_direct_self_consumption_kwh']/1000:.1f} MWh")
    m4.metric("Покупка от мрежата", f"{totals['total_grid_import_kwh']/1000:.1f} MWh")
    m5.metric("Отдаване към мрежата", f"{totals['total_grid_export_kwh']/1000:.1f} MWh")
    
    r1, r2, r3 = st.columns(3)
    r1.metric("PV Self-Consumption Ratio", f"{ratios['self_consumption_ratio_pct']:.1f}%",
              help="Директно използвано ФЕЦ / Общо ФЕЦ производство. Колко от произведеното от ФЕЦ се използва директно на обекта без батерия.")
    r2.metric("Self-Sufficiency Ratio", f"{ratios['self_sufficiency_ratio_pct']:.1f}%",
              help="ФЕЦ енергия използвана на обекта / Общо потребление. Колко % от потреблението се покрива от собствен ФЕЦ.")
    r3.metric("Средна резолюция", f"{totals['interval_hours_avg']*60:.0f} мин")
    
    # Charts
    st.subheader("1. Производство и потребление във времето (първите 7 дни)")
    df_7d = df_enriched.head(int(7*24 / totals['interval_hours_avg'])).copy()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_7d["timestamp"], y=df_7d["consumption_kwh"]/df_7d["_interval_h"], name="Потребление kW", line=dict(color="#E53E3E")))
    fig.add_trace(go.Scatter(x=df_7d["timestamp"], y=df_7d["pv_generation_kwh"]/df_7d["_interval_h"], name="ФЕЦ kW", line=dict(color="#38A169")))
    fig.update_layout(height=400, hovermode="x unified", legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)
    
    st.subheader("2. Излишък / Недостиг (около нулева линия)")
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=df_7d["timestamp"], y=df_7d["net_balance_kwh"]/df_7d["_interval_h"], name="Net (PV-Load) kW", fill='tozeroy', line=dict(color="#3182CE")))
    fig2.add_hline(y=0, line_dash="dash")
    fig2.update_layout(height=350, yaxis_title="kW (+ излишък, - недостиг)")
    st.plotly_chart(fig2, use_container_width=True)
    st.caption("Ето тук произвеждате повече, отколкото ви трябва (над нулата) и ето тук купувате от мрежата (под нулата).")
    
    st.subheader("3. Среднодневен профил по час")
    df_enriched["hour"] = df_enriched["timestamp"].dt.hour
    hourly = df_enriched.groupby("hour").agg(
        avg_consumption_kW=("consumption_kwh", lambda x: (x / df_enriched.loc[x.index, "_interval_h"]).mean()),
        avg_pv_kW=("pv_generation_kwh", lambda x: (x / df_enriched.loc[x.index, "_interval_h"]).mean()),
        avg_surplus_kW=("surplus_kwh", lambda x: (x / df_enriched.loc[x.index, "_interval_h"]).mean()),
    ).reset_index()
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(x=hourly["hour"], y=hourly["avg_consumption_kW"], name="Ср. потребление kW", marker_color="#FC8181"))
    fig3.add_trace(go.Bar(x=hourly["hour"], y=hourly["avg_pv_kW"], name="Ср. ФЕЦ kW", marker_color="#68D391"))
    fig3.update_layout(barmode="group", height=350)
    st.plotly_chart(fig3, use_container_width=True)
    
    st.subheader("4. Месечно сравнение")
    df_enriched["month"] = df_enriched["timestamp"].dt.to_period("M").astype(str)
    monthly = df_enriched.groupby("month").agg(
        consumption_MWh=("consumption_kwh","sum"),
        pv_MWh=("pv_generation_kwh","sum"),
        import_MWh=("grid_import_kwh","sum"),
        export_MWh=("grid_export_kwh","sum"),
    ).reset_index()
    monthly["consumption_MWh"] /=1000
    monthly["pv_MWh"] /=1000
    monthly["import_MWh"] /=1000
    monthly["export_MWh"] /=1000
    
    fig4 = go.Figure()
    for col, name, color in [("consumption_MWh","Потребление","#E53E3E"),("pv_MWh","ФЕЦ","#38A169"),("import_MWh","Внос","#DD6B20"),("export_MWh","Износ","#3182CE")]:
        fig4.add_trace(go.Bar(x=monthly["month"], y=monthly[col], name=name, marker_color=color))
    fig4.update_layout(barmode="group", height=400)
    st.plotly_chart(fig4, use_container_width=True)
    
    # Key insights text
    st.subheader("Ключови изводи (без BESS)")
    surplus_hours = (df_enriched["surplus_kwh"]>0).sum()
    deficit_hours = (df_enriched["deficit_kwh"]>0).sum()
    st.markdown(f"""
    - **Обектът има значителен PV surplus в периода 11:00–15:00**: среден излишък {hourly.loc[hourly['hour'].between(11,15), 'avg_surplus_kW'].mean():.1f} kW в този интервал.
    - **Системен недостиг след 17:00**: {hourly.loc[hourly['hour']>=17, 'avg_consumption_kW'].mean():.0f} kW средно потребление при почти нулево ФЕЦ производство.
    - **Собствено потребление**: {ratios['self_consumption_ratio_pct']:.1f}% от ФЕЦ се използва директно, останалите {100-ratios['self_consumption_ratio_pct']:.1f}% се изнасят.
    - **Самодостатъчност**: {ratios['self_sufficiency_ratio_pct']:.1f}% от потреблението се покрива от ФЕЦ.
    - Данни: {totals['data_points']} интервала от {totals['start_date'].date()} до {totals['end_date'].date()}, резолюция {totals['interval_hours_avg']*60:.0f} мин.
    """)
    
    st.info("PHASE 1 завършена ✅ - Базовият енергиен баланс работи. В PHASE 2 ще добавим Virtual BESS Engine за симулация на self-consumption стратегия.")
    
    # Export baseline
    csv = df_enriched.to_csv(index=False).encode('utf-8')
    st.download_button("⬇️ Свали обогатените данни (baseline)", csv, "baseline_enriched.csv", "text/csv")
