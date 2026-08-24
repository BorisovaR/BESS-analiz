"""
PHASE 6 - PDF Report Generator
Creates professional B2B report: ТЕХНИКО-ИКОНОМИЧЕСКА ОЦЕНКА НА BESS
"""
from __future__ import annotations
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
import io
from datetime import datetime

def create_pdf_report(title: str, expert_report_text: str, summary: dict, output_path: str = None) -> bytes:
    """
    Creates PDF from expert report + summary data
    Returns bytes if output_path None, else writes file
    """
    buffer = io.BytesIO() if output_path is None else None
    target = buffer if output_path is None else output_path
    
    doc = SimpleDocTemplate(
        target,
        pagesize=A4,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm
    )
    
    styles = getSampleStyleSheet()
    style_h1 = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=18, spaceAfter=12, textColor=colors.HexColor("#0B1F3A"))
    style_h2 = ParagraphStyle('h2', parent=styles['Heading2'], fontSize=13, spaceAfter=8, spaceBefore=12, textColor=colors.HexColor("#1A365D"))
    style_normal = ParagraphStyle('normal', parent=styles['Normal'], fontSize=10, leading=14, spaceAfter=6)
    style_small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8, leading=10, textColor=colors.grey)
    
    story = []
    
    # Title page
    story.append(Paragraph("ТЕХНИКО-ИКОНОМИЧЕСКА ОЦЕНКА НА BESS", style_h1))
    story.append(Paragraph(f"Energomonitor • {datetime.now().strftime('%d.%m.%Y')}", style_small))
    story.append(Spacer(1, 12))
    
    baseline = summary.get("baseline", {})
    totals = baseline.get("totals_kwh", {})
    ratios = baseline.get("ratios_pct", {})
    
    # Quick facts table
    data = [
        ["Показател", "Стойност"],
        ["Годишно потребление", f"{totals.get('total_consumption_kwh',0)/1000:.0f} MWh"],
        ["ФЕЦ производство", f"{totals.get('total_pv_generation_kwh',0)/1000:.1f} MWh"],
        ["Self-consumption", f"{ratios.get('self_consumption_ratio_pct',0):.1f}%"],
        ["Самодостатъчност", f"{ratios.get('self_sufficiency_ratio_pct',0):.1f}%"],
        ["Износ (потенциал BESS)", f"{totals.get('total_grid_export_kwh',0):.0f} kWh"],
        ["Data Quality", f"{summary.get('data_quality',{}).get('score',100)}/100"],
    ]
    t = Table(data, colWidths=[80*mm, 60*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#0B1F3A")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#F7FAFC")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))
    
    # Scenarios table if available
    economics = summary.get("economics", [])
    if economics:
        story.append(Paragraph("Сравнение на BESS размери", style_h2))
        econ_data = [["Капацитет kWh","CAPEX €","Год. полза €","Payback год","NPV €"]]
        for econ in economics[:6]:
            econ_data.append([
                f"{econ.get('capacity_kwh',0)}",
                f"{econ.get('capex_eur',0):,.0f}",
                f"{econ.get('annual_gross_benefit_eur',0):.0f}",
                f"{econ.get('simple_payback_years',0):.1f}" if econ.get('simple_payback_years',0)!=float('inf') else "∞",
                f"{econ.get('npv_eur',0):,.0f}"
            ])
        t2 = Table(econ_data, colWidths=[25*mm, 25*mm, 25*mm, 25*mm, 30*mm])
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2D3748")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('ALIGN', (1,1), (-1,-1), 'RIGHT'),
        ]))
        story.append(t2)
        story.append(Spacer(1, 12))
    
    # Expert report - split by lines and headings
    story.append(PageBreak())
    story.append(Paragraph("Експертен анализ", style_h1))
    story.append(Spacer(1, 12))
    
    # Simple markdown-like parsing
    for line in expert_report_text.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 6))
            continue
        if line.startswith("# "):
            story.append(Paragraph(line[2:], style_h1))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], style_h2))
        elif line.startswith("### "):
            story.append(Paragraph(line[4:], style_h2))
        elif line.startswith("- ") or line.startswith("* "):
            story.append(Paragraph(f"• {line[2:]}", style_normal))
        else:
            # escape for reportlab
            safe = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            story.append(Paragraph(safe, style_normal))
    
    story.append(Spacer(1, 24))
    story.append(Paragraph("Assumptions and limitations: Симулацията е върху исторически данни, не е гаранция. Резултатите зависят от бъдещи пазарни условия. При зададените допускания резултатите не са гарантирани спестявания. Докладът е технико-икономическа оценка, не окончателен инженерингов проект.", style_small))
    story.append(Paragraph("Вашите сурови енергийни данни са използвани само за изчисляване на анализа и не са изпращани към AI модели в суров вид, а само като агрегиран JSON.", style_small))
    
    doc.build(story)
    
    if buffer:
        buffer.seek(0)
        return buffer.getvalue()
    else:
        return b""
