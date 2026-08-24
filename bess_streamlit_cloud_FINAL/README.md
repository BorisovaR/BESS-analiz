# Energomonitor BESS - Технико-икономическа оценка MVP

## PHASE 1 - DONE
- Import wizard: CSV/XLSX, auto-detection на колони (timestamp, consumption, PV, grid, цени), ръчно mapping, избор на единица (kW/kWh/MW/MWh)
- Валидация: липсващи интервали, дублирани timestamps, отрицателни стойности, аномалии, Data Quality Score 0-100
- Не се измислят данни - показва предупреждения за резолюция !=15min и сезонност <12м
- Базов енергиен баланс: energy = power * interval_h, всички финанси използват ENERGY
- Metrics: total consumption, PV generation, direct self-consumption, grid import/export, surplus/deficit, self-consumption ratio, self-sufficiency ratio
- Визуализации: PV vs Load (7дни), Surplus/Deficit около нула, среднодневен профил по час, месечно сравнение
- Demo dataset: 12 месеца 15-мин индустриално предприятие 100kWp + синтетични IBEX цени (маркирани като демо)

## Структура (calculation engine отделен от UI)
```
/core/data_loader.py - import wizard + normalization + quality
/core/energy_balance.py - baseline баланс
/core/battery_simulator.py - PHASE 2 placeholder
/core/economics.py - PHASE 4
/core/recommendations.py - PHASE 6
/core/scenario_engine.py - PHASE 3
/utils/demo_generator.py
/app.py - Streamlit UI (само визуализация)
/tests/
```

## Стартиране
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Следващи фази
PHASE 2: Virtual BESS self-consumption simulator с SOC tracking, efficiency, power limits, no simultaneous charge/discharge, unit tests
PHASE 3: Scenario engine за различни капацитети + diminishing returns графика
PHASE 4: CAPEX/OPEX/NPV/IRR
PHASE 5: Price-aware optimization (scipy.optimize linprog)
PHASE 6: AI recommendations (aggregated JSON only, no raw data to LLM)
PHASE 7: PDF report
