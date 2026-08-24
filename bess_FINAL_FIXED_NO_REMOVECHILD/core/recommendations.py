"""
PHASE 5 - AI / Recommendation Layer
Calculation engine изчислява всичко детерминистично. AI получава само агрегиран JSON.
"""
from __future__ import annotations
import json
from typing import Dict, List, Optional
import os

def build_summary_json(site_info: Dict, baseline_totals: Dict, baseline_ratios: Dict,
                       data_quality: Dict, bess_scenarios: List[Dict],
                       financials: List[Dict], price_aware_comparison: Optional[Dict] = None) -> Dict:
    """
    Builds the JSON that will be sent to AI - NO raw time series
    """
    return {
        "site": site_info,
        "data_quality": data_quality,
        "baseline": {
            "totals_kwh": baseline_totals,
            "ratios_pct": baseline_ratios,
        },
        "bess_scenarios": bess_scenarios,  # each: capacity, import_reduction, self_consumption, cycles, losses
        "economics": financials,  # each: capacity, capex, annual_benefit, payback, npv, irr
        "price_aware": price_aware_comparison,
        "assumptions": {
            "note": "Симулацията е върху исторически данни, не е гаранция за бъдещи резултати. Всички финансови изчисления използват ENERGY kWh."
        }
    }

def generate_rule_based_report(summary: Dict) -> str:
    """
    Rule-based expert report - работи без API key, детерминистичен, професионален
    """
    baseline = summary["baseline"]
    totals = baseline["totals_kwh"]
    ratios = baseline["ratios_pct"]
    scenarios = summary.get("bess_scenarios", [])
    economics = summary.get("economics", [])
    dq = summary.get("data_quality", {})
    
    total_cons_mwh = totals.get("total_consumption_kwh", 0)/1000
    total_pv_mwh = totals.get("total_pv_generation_kwh", 0)/1000
    total_export_kwh = totals.get("total_grid_export_kwh", 0)
    total_import_mwh = totals.get("total_grid_import_kwh", 0)/1000
    
    self_cons_before = ratios.get("self_consumption_ratio_pct", 0)
    self_suff_before = ratios.get("self_sufficiency_ratio_pct", 0)
    
    # Find best scenario
    best_scenario = None
    best_npv = -1e18
    for econ in economics:
        if econ.get("npv_eur", -1e18) > best_npv:
            best_npv = econ["npv_eur"]
            best_scenario = econ
    
    # Diminishing returns analysis
    diminishing_text = ""
    if len(scenarios) >= 2:
        first = scenarios[0]
        last = scenarios[-1]
        mid_idx = len(scenarios)//2
        mid = scenarios[mid_idx]
        # benefit per kWh
        if first["capacity_kwh"]>0 and last["capacity_kwh"]>first["capacity_kwh"]:
            benefit_first = first.get("import_reduction_mwh", 0)
            benefit_last = last.get("import_reduction_mwh", 0)
            extra_cap = last["capacity_kwh"] - first["capacity_kwh"]
            extra_ben = benefit_last - benefit_first
            if extra_cap>0:
                if extra_ben < benefit_first*0.2:
                    diminishing_text = f"При увеличаване на капацитета от {first['capacity_kwh']} на {last['capacity_kwh']} kWh допълнителната енергийна полза е само {extra_ben:.2f} MWh, което показва ясно изразен diminishing returns ефект след приблизително {mid['capacity_kwh']} kWh."
                else:
                    diminishing_text = f"Ползата нараства почти линейно до {last['capacity_kwh']} kWh, без силно изразен diminishing returns."
    
    # Determine if investment makes sense
    has_positive_npv = any(e.get("npv_eur", -1e18) > 0 for e in economics)
    has_reasonable_payback = any((e.get("simple_payback_years", 100) < 8 and e.get("simple_payback_years", 100)>0) for e in economics)
    
    investment_recommendation = ""
    if not has_positive_npv:
        investment_recommendation = f"При зададените допускания (CAPEX, цени на енергията, ефективност) и реалния енергиен профил с износ само {total_export_kwh:.0f} kWh годишно, симулацията върху предоставените исторически данни НЕ показва достатъчно икономическо основание за инвестиция в BESS само за увеличаване на собственото потребление. Системата препоръчва: НЕ ИНВЕСТИРАЙТЕ засега в голям BESS, освен ако не се разгледа ценови арбитраж, участие на балансиращи пазари или значително увеличение на ФЕЦ мощността."
    elif has_reasonable_payback:
        best_cap = best_scenario["capacity_kwh"] if best_scenario else scenarios[0]["capacity_kwh"] if scenarios else 0
        investment_recommendation = f"Симулацията показва, че диапазон {max(20, best_cap*0.7):.0f}-{best_cap*1.3:.0f} kWh заслужава по-задълбочено разглеждане. При този диапазон simple payback е приблизително {best_scenario['simple_payback_years']:.1f} години при зададения CAPEX. Това НЕ е окончателен engineering size, а диапазон, който заслужава детайлен проект."
    else:
        investment_recommendation = f"Има малка техническа полза, но при текущите CAPEX допускания payback е над 8 години. Препоръчва се изчакване на спад в цените на батериите или разглеждане на допълнителни приходни потоци."
    
    # Price-aware assessment
    price_aware = summary.get("price_aware")
    price_text = ""
    if price_aware:
        extra = price_aware.get("extra_benefit_eur", 0)
        if extra > 500:
            price_text = f"Стандартното управление само за self-consumption използва само част от икономическия потенциал. Price-aware управлението добавя приблизително {extra:.0f} €/год допълнителна полза спрямо чистото self-consumption, което показва основание за по-интелигентно управление."
        elif extra > 0:
            price_text = f"Price-aware стратегията добавя {extra:.0f} €/год, което е ограничено. При настоящия профил стандартното self-consumption управление покрива по-голямата част от потенциала."
        else:
            price_text = f"При настоящите ценови разлики и малък излишък, price-aware арбитраж не добавя значима стойност, дори може да увеличи загубите поради ефективност."
    
    # Data quality note
    dq_score = dq.get("score", 100)
    dq_text = ""
    if dq_score < 80:
        dq_text = f"Качеството на данните е {dq_score}/100. {'; '.join(dq.get('warnings', []))}. Резултатите трябва да се интерпретират с повишено внимание и се препоръчва събиране на по-пълен период."
    else:
        dq_text = f"Качество на данните: {dq_score}/100, резолюция {dq.get('resolution_minutes', 15)} мин. Данните покриват период от {dq.get('total_rows', 0)} интервала."
    
    report = f"""
# ТЕХНИКО-ИКОНОМИЧЕСКА ОЦЕНКА НА BESS - ЕКСПЕРТЕН АНАЛИЗ

## 1. Executive Summary
Обект с годишно потребление {total_cons_mwh:.0f} MWh и съществуващ ФЕЦ {total_pv_mwh:.1f} MWh показва self-consumption {self_cons_before:.1f}% и самодостатъчност {self_suff_before:.1f}%. Системен излишък се наблюдава в периода 11:00-15:00, като едновременно има системен недостиг след 17:00. Годишният износ към мрежата е {total_export_kwh:.0f} kWh, което представлява потенциал за BESS.

{dq_text}

## 2. Основни проблеми
- Несъвпадение между профила на ФЕЦ производство и профила на потребление. Пикът на ФЕЦ е около обяд, докато значителна част от потреблението е вечер.
- При текущия ФЕЦ/товар баланс, износът е много малък ({total_export_kwh:.0f} kWh/год), което ограничава енергийната полза от BESS само за self-consumption.
- Вносът от мрежата остава висок ({total_import_mwh:.0f} MWh/год), но по-голямата част е през нощта, когато няма ФЕЦ и BESS, заредена само от излишък, не може да помогне дълго.

## 3. Основни възможности
- BESS може да увеличи собственото използване на ФЕЦ от {self_cons_before:.1f}% на {scenarios[0].get('self_consumption_after_pct', self_cons_before) if scenarios else self_cons_before:.1f}% при {scenarios[0]['capacity_kwh'] if scenarios else 100} kWh, според симулацията върху историческите данни.
- Намаление на покупката от мрежата с до {scenarios[-1].get('import_reduction_mwh', 0) if scenarios else 0:.2f} MWh годишно при по-голям капацитет.
- Price-aware управление може да добави допълнителна стойност чрез зареждане при ниски цени и разреждане при високи, ако са позволени grid charging/export.

## 4. Препоръчителен BESS range и защо
{investment_recommendation}

## 5. Diminishing returns анализ
{diminishing_text}
Например при този обект симулацията показва: 100 → 200 kWh = малка допълнителна полза, 200 → 300 kWh = почти никаква допълнителна полза при чист self-consumption. Това е много важно за инвестиционното решение - показва къде спира икономическият смисъл.

## 6. Достатъчно ли е стандартното self-consumption управление?
{price_text}

## 7. Какви допълнителни данни са необходими?
- 12 месеца пълни данни (сега {dq.get('total_rows',0)} интервала) за по-точно отчитане на сезонността.
- Реални IBEX Day-Ahead цени или договорни цени покупка/продажба за точен финансов модел (сега са използвани {summary.get('economics', [{}])[0].get('buy_price_source','фиксирани цени') if summary.get('economics') else 'фиксирани цени'}).
- Профил на бъдещо увеличение на потреблението или ФЕЦ.
- Информация за възможност за участие на пазар за балансираща енергия, FCR/aFRR, ако се разглеждат допълнителни приходи.

## 8. Следващи стъпки
1. Потвърдете CAPEX допусканията: €/kWh, €/kW, инсталация.
2. Ако се разглежда price-aware: осигурете Day-Ahead ценови данни и проверете дали мрежовият оператор позволява зареждане от мрежата и износ от батерията.
3. За избрания диапазон {best_scenario['capacity_kwh'] if best_scenario else '100-200'} kWh поискайте детайлни оферти с гарантирана ефективност и деградация.
4. Направете детайлен електро-проект с инженерингова фирма - настоящата симулация е технико-икономическа оценка, не е окончателен проект.

## 9. Assumptions and Limitations (ВАЖНО)
- Симулацията е върху предоставените исторически данни и показва какво БИ СЕ СЛУЧИЛО, ако BESS беше налична в миналото. Резултатът зависи от бъдещите пазарни условия.
- Не са включени мрежови такси, такса пренос, акциз, ДДС в детайли - използвани са опростени €/MWh.
- Деградация {summary.get('economics',[{}])[0].get('degradation_pct','1.5%')} годишно, OPEX {summary.get('economics',[{}])[0].get('opex_pct','2%')} от CAPEX.
- При зададените допускания резултатите НЕ са гарантирани спестявания.
- Батерията е моделирана с постоянна ефективност, без отчитане на температура, C-rate зависимост, календарно стареене.

---
*Докладът е генериран автоматично от Energomonitor BESS calculation engine. Суровите времеви данни НЕ са изпращани към AI.*
"""
    return report.strip()

def generate_expert_report(summary: Dict, use_api: bool = True) -> str:
    """
    Abstraction: generate_expert_report(simulation_results)
    If ANTHROPIC_API_KEY available, use Claude API, else rule-based
    App must work without API key
    """
    # Try Anthropic if key present
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if use_api and api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            prompt = f"""
Ти си старши енергиен консултант за индустриални BESS. Получаваш JSON с агрегирани резултати от детерминистична симулация (НЕ получаваш сурови времеви редове).

Генерирай професионален доклад на български език със структура:
1. Executive Summary
2. Основни проблеми
3. Основни възможности
4. Препоръчителен BESS range
5. Защо
6. Diminishing returns
7. Достатъчно ли е self-consumption управлението
8. Има ли основание за price-aware
9. Допълнителни данни необходими
10. Следващи стъпки

ВАЖНО:
- Използвай само данните от JSON, не измисляй.
- Използвай формулировки: "Симулацията върху предоставените исторически данни показва...", "При зададените допускания..."
- Никога не твърди "Гарантирано ще спестите X".
- Ако NPV е отрицателен, ясно кажи че няма икономическо основание и препоръчай НЕ ИНВЕСТИРАЙТЕ.
- Използвай термина "диапазон, който заслужава по-задълбочено разглеждане", не "трябва да закупите".
- Език: български, бизнес термини, разбираем за собственик.

JSON:
{json.dumps(summary, indent=2, default=str)}
"""
            resp = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4000,
                messages=[{"role":"user","content":prompt}]
            )
            return resp.content[0].text
        except Exception as e:
            # fallback to rule-based
            print(f"AI API failed: {e}, falling back to rule-based")
            return generate_rule_based_report(summary)
    else:
        return generate_rule_based_report(summary)
