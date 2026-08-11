"""
app/core/commercial_reports.py
=================================
Report data builder + PDF generator for Commercial (Sales + LPO) — mirrors
the split already used for Manufacturing's production_reports.py: a plain
dataclass built from cheese_data_access queries, a text summary, and a
reportlab PDF, all independent of Streamlit.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Dict, Any


@dataclass
class CommercialReportData:
    start_date: date
    end_date: date
    total_revenue: float
    total_kg: float
    total_sales_transactions: int
    revenue_by_product: List[Dict[str, Any]] = field(default_factory=list)
    revenue_by_customer: List[Dict[str, Any]] = field(default_factory=list)
    lpo_total_kg: float = 0.0
    lpo_fill_rate_pct: float = 0.0
    lpo_cancelled_count: int = 0
    lpo_status_counts: Dict[str, int] = field(default_factory=dict)
    revenue_by_day: List[Dict[str, Any]] = field(default_factory=list)
    total_returned_kg: float = 0.0
    total_return_value: float = 0.0
    return_rate_pct: float = 0.0
    net_revenue: float = 0.0
    return_value_is_exact: bool = True  # False if any return had no price and was estimated
    returns_by_customer: List[Dict[str, Any]] = field(default_factory=list)


def build_commercial_report_data(sales: List[Dict[str, Any]],
                                  lpo_lines: List[Dict[str, Any]],
                                  start_date: date, end_date: date,
                                  returns: List[Dict[str, Any]] = None) -> CommercialReportData:
    returns = returns or []
    total_revenue = sum(float(s["revenue"]) for s in sales)
    total_kg = sum(float(s["quantity_kg"]) for s in sales)

    by_product: Dict[str, Dict[str, float]] = {}
    for s in sales:
        d = by_product.setdefault(s["cheese_name"], {"revenue": 0.0, "kg": 0.0, "count": 0})
        d["revenue"] += float(s["revenue"])
        d["kg"] += float(s["quantity_kg"])
        d["count"] += 1
    revenue_by_product = sorted(
        [{"cheese_name": k, **v} for k, v in by_product.items()],
        key=lambda r: r["revenue"], reverse=True,
    )

    by_customer: Dict[str, Dict[str, float]] = {}
    for s in sales:
        name = (s.get("customer") or "").strip()
        if not name:
            continue
        d = by_customer.setdefault(name, {"revenue": 0.0, "kg": 0.0, "count": 0})
        d["revenue"] += float(s["revenue"])
        d["kg"] += float(s["quantity_kg"])
        d["count"] += 1
    revenue_by_customer = sorted(
        [{"customer": k, **v} for k, v in by_customer.items()],
        key=lambda r: r["revenue"], reverse=True,
    )

    # Returns -- grouped by customer_name (freetext), same convention as
    # revenue_by_customer above, NOT a customer_id join. Value is exact where
    # a return carries its own price_per_kg (set when the Returns sheet's
    # Price per Unit column is filled in); otherwise it falls back to that
    # customer's own blended avg revenue/kg from by_customer -- same
    # estimation logic as customer_analytics.compute_return_metrics, kept
    # separate here (by name, not id) so this module stays dependency-free.
    avg_price_by_customer = {
        name: (v["revenue"] / v["kg"]) if v["kg"] else 0.0
        for name, v in by_customer.items()
    }
    returns_agg: Dict[str, Dict[str, Any]] = {}
    total_return_value = 0.0
    total_returned_kg = 0.0
    any_return_estimated = False
    for r in returns:
        name = (r.get("customer_name") or "").strip()
        if not name:
            continue
        r_kg = float(r["quantity_kg"])
        r_price = r.get("price_per_kg")
        if r_price:
            r_value = r_kg * float(r_price)
            r_exact = True
        else:
            r_value = r_kg * avg_price_by_customer.get(name, 0.0)
            r_exact = False
            any_return_estimated = True
        total_return_value += r_value
        total_returned_kg += r_kg
        d = returns_agg.setdefault(name, {"returned_kg": 0.0, "return_value": 0.0, "count": 0, "all_exact": True})
        d["returned_kg"] += r_kg
        d["return_value"] += r_value
        d["count"] += 1
        if not r_exact:
            d["all_exact"] = False
    returns_by_customer = sorted(
        [{"customer": k, **v} for k, v in returns_agg.items()],
        key=lambda r: r["return_value"], reverse=True,
    )
    return_rate_pct = (total_returned_kg / total_kg * 100) if total_kg > 0 else 0.0

    by_day: Dict[str, Dict[str, float]] = {}
    for s in sales:
        d = by_day.setdefault(s["date"], {"revenue": 0.0, "kg": 0.0})
        d["revenue"] += float(s["revenue"])
        d["kg"] += float(s["quantity_kg"])
    revenue_by_day = sorted(
        [{"date": k, **v} for k, v in by_day.items()], key=lambda r: r["date"],
    )

    lpo_total_kg = sum(float(l["quantity_kg"]) for l in lpo_lines)
    delivered = [l for l in lpo_lines if l["status"] in ("Delivered", "Partially Delivered")]
    delivered_kg = sum(float(l.get("quantity_delivered_kg") or 0) for l in delivered)
    fill_rate = (delivered_kg / lpo_total_kg * 100) if lpo_total_kg > 0 else 0.0
    cancelled = sum(1 for l in lpo_lines if l["status"] == "Cancelled")
    status_counts: Dict[str, int] = {}
    for l in lpo_lines:
        status_counts[l["status"]] = status_counts.get(l["status"], 0) + 1

    return CommercialReportData(
        start_date=start_date, end_date=end_date,
        total_revenue=total_revenue, total_kg=total_kg, total_sales_transactions=len(sales),
        revenue_by_product=revenue_by_product, revenue_by_customer=revenue_by_customer,
        lpo_total_kg=lpo_total_kg, lpo_fill_rate_pct=fill_rate,
        lpo_cancelled_count=cancelled, lpo_status_counts=status_counts,
        revenue_by_day=revenue_by_day,
        total_returned_kg=total_returned_kg, total_return_value=total_return_value,
        return_rate_pct=return_rate_pct, net_revenue=total_revenue - total_return_value,
        return_value_is_exact=not any_return_estimated, returns_by_customer=returns_by_customer,
    )


def summarize_commercial_report_data(data: CommercialReportData) -> str:
    lines = [
        f"Commercial Report — {data.start_date} to {data.end_date}",
        f"Revenue: KSh {data.total_revenue:,.0f} across {data.total_sales_transactions} sale(s), "
        f"{data.total_kg:,.1f} kg total.",
        f"LPO volume: {data.lpo_total_kg:,.1f} kg, {data.lpo_fill_rate_pct:.0f}% fill rate, "
        f"{data.lpo_cancelled_count} cancelled.",
    ]
    if data.revenue_by_product:
        top = data.revenue_by_product[0]
        lines.append(f"Top product: {top['cheese_name']} (KSh {top['revenue']:,.0f}).")
    if data.revenue_by_customer:
        top_c = data.revenue_by_customer[0]
        lines.append(f"Top customer: {top_c['customer']} (KSh {top_c['revenue']:,.0f}).")
    if data.total_returned_kg > 0:
        value_tag = "" if data.return_value_is_exact else " (partly estimated)"
        lines.append(
            f"Returns: {data.total_returned_kg:,.1f} kg ({data.return_rate_pct:.1f}% of volume sold), "
            f"KSh {data.total_return_value:,.0f}{value_tag} — net revenue after returns: "
            f"KSh {data.net_revenue:,.0f}."
        )
        if data.returns_by_customer:
            top_r = data.returns_by_customer[0]
            lines.append(f"Highest return cost: {top_r['customer']} (KSh {top_r['return_value']:,.0f}).")
    return "\n".join(lines)


def generate_commercial_report(data: CommercialReportData, output_path: str) -> str:
    """Builds a PDF at output_path and returns the path — same reportlab
    pattern as main.py's generate_enhanced_pdf_report / the dry ice reports,
    so the download flow in the UI tab matches Production Reports exactly."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_CENTER
    except ImportError as e:
        raise ImportError("PDF generation requires reportlab: pip install reportlab") from e

    doc = SimpleDocTemplate(output_path, pagesize=letter,
                             rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=72)
    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=22,
                                  textColor=colors.HexColor('#1f77b4'), alignment=TA_CENTER, spaceAfter=24)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontSize=15,
                                    textColor=colors.HexColor('#333333'), spaceAfter=10)

    elements.append(Paragraph("Commercial Report", title_style))
    elements.append(Paragraph(f"{data.start_date.strftime('%b %d, %Y')} \u2013 {data.end_date.strftime('%b %d, %Y')}",
                               styles['Normal']))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", styles['Normal']))
    elements.append(Spacer(1, 16))

    elements.append(Paragraph("Summary", heading_style))
    summary_data = [
        ['Metric', 'Value'],
        ['Total Revenue', f"KSh {data.total_revenue:,.0f}"],
        ['Total Volume', f"{data.total_kg:,.1f} kg"],
        ['Sales Transactions', f"{data.total_sales_transactions}"],
        ['LPO Volume', f"{data.lpo_total_kg:,.1f} kg"],
        ['LPO Fill Rate', f"{data.lpo_fill_rate_pct:.0f}%"],
        ['LPOs Cancelled', f"{data.lpo_cancelled_count}"],
        ['Return Value', f"KSh {data.total_return_value:,.0f}"
                          + ("" if data.return_value_is_exact else " (est.)")],
        ['Return Rate', f"{data.return_rate_pct:.1f}%"],
        ['Net Revenue (after returns)', f"KSh {data.net_revenue:,.0f}"],
    ]
    summary_table = Table(summary_data, colWidths=[2.5 * inch, 2.5 * inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4e79a7')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 1), (-1, -1), 6), ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 20))

    if data.revenue_by_product:
        elements.append(Paragraph("Revenue by Product", heading_style))
        prod_data = [['Cheese', 'Revenue', 'Kg', 'Sales']]
        for r in data.revenue_by_product:
            prod_data.append([r['cheese_name'], f"KSh {r['revenue']:,.0f}", f"{r['kg']:,.1f}", f"{r['count']}"])
        prod_table = Table(prod_data, colWidths=[2 * inch, 1.7 * inch, 1.3 * inch, 1 * inch])
        prod_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2ecc71')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 5), ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ]))
        elements.append(prod_table)
        elements.append(Spacer(1, 20))

    if data.revenue_by_customer:
        elements.append(Paragraph("Revenue by Customer", heading_style))
        cust_data = [['Customer', 'Revenue', 'Kg', 'Sales']]
        for r in data.revenue_by_customer[:20]:
            cust_data.append([r['customer'], f"KSh {r['revenue']:,.0f}", f"{r['kg']:,.1f}", f"{r['count']}"])
        if len(data.revenue_by_customer) > 20:
            cust_data.append(['', '', f'and {len(data.revenue_by_customer) - 20} more...', ''])
        cust_table = Table(cust_data, colWidths=[2 * inch, 1.7 * inch, 1.3 * inch, 1 * inch])
        cust_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 5), ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ]))
        elements.append(cust_table)
        elements.append(Spacer(1, 20))

    if data.returns_by_customer:
        elements.append(Paragraph("Returns by Customer", heading_style))
        ret_data = [['Customer', 'Value', 'Kg', 'Returns']]
        for r in data.returns_by_customer[:20]:
            tag = '' if r['all_exact'] else ' (est.)'
            ret_data.append([r['customer'], f"KSh {r['return_value']:,.0f}{tag}",
                              f"{r['returned_kg']:,.1f}", f"{r['count']}"])
        if len(data.returns_by_customer) > 20:
            ret_data.append(['', '', f"and {len(data.returns_by_customer) - 20} more...", ''])
        ret_table = Table(ret_data, colWidths=[2 * inch, 1.9 * inch, 1.1 * inch, 1 * inch])
        ret_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d35400')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, -1), 5), ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ]))
        elements.append(ret_table)
        elements.append(Spacer(1, 20))

    if data.lpo_status_counts:
        elements.append(Paragraph("LPO Status Breakdown", heading_style))
        status_data = [['Status', 'Count']] + [[k, str(v)] for k, v in data.lpo_status_counts.items()]
        status_table = Table(status_data, colWidths=[2.5 * inch, 2.5 * inch])
        status_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff9800')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fff3e0')),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 1), (-1, -1), 6), ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ]))
        elements.append(status_table)

    elements.append(Spacer(1, 30))
    elements.append(Paragraph("Report generated by Browns Cheese \u2014 MarginIQ Ops Suite", styles['Normal']))

    doc.build(elements)
    return output_path


if __name__ == "__main__":
    import os

    sales = [
        {"date": "2026-07-01", "cheese_name": "Mozzarella", "quantity_kg": 10.0, "revenue": 6500.0, "customer": "Java House"},
        {"date": "2026-07-02", "cheese_name": "Cheddar", "quantity_kg": 5.0, "revenue": 3750.0, "customer": "Carrefour"},
        {"date": "2026-07-03", "cheese_name": "Mozzarella", "quantity_kg": 8.0, "revenue": 5200.0, "customer": ""},
    ]
    lpo_lines = [
        {"quantity_kg": 15.0, "quantity_delivered_kg": 15.0, "status": "Delivered"},
        {"quantity_kg": 10.0, "quantity_delivered_kg": None, "status": "Pending"},
        {"quantity_kg": 5.0, "quantity_delivered_kg": None, "status": "Cancelled"},
    ]
    returns = [
        # priced -- exact. Cheddar sells to Carrefour at 3750/5=750/kg, but this
        # return carries its own price (700/kg), so it must use 700, not 750.
        {"customer_name": "Carrefour", "quantity_kg": 2.0, "price_per_kg": 700.0},
        # unpriced -- falls back to Carrefour's blended avg revenue/kg (750)
        {"customer_name": "Carrefour", "quantity_kg": 1.0},
        {"customer_name": "Java House", "quantity_kg": 1.0, "price_per_kg": 650.0},
        {"customer_name": "", "quantity_kg": 5.0},  # blank customer -- excluded, same as sales
    ]

    print("Test 1: build_commercial_report_data")
    data = build_commercial_report_data(sales, lpo_lines, date(2026, 7, 1), date(2026, 7, 31), returns=returns)
    print(f"  total_revenue={data.total_revenue}, total_kg={data.total_kg}, "
          f"transactions={data.total_sales_transactions}")
    assert data.total_revenue == 15450.0
    assert data.total_kg == 23.0
    assert data.total_sales_transactions == 3
    assert len(data.revenue_by_customer) == 2, "blank customer sale should be excluded"
    assert data.revenue_by_product[0]["cheese_name"] == "Mozzarella"  # 6500+5200=11700 > Cheddar's 3750
    assert data.lpo_total_kg == 30.0
    assert data.lpo_fill_rate_pct == 50.0, f"got {data.lpo_fill_rate_pct}"  # 15/30
    assert data.lpo_cancelled_count == 1

    print(f"  total_returned_kg={data.total_returned_kg}, total_return_value={data.total_return_value}, "
          f"return_rate={data.return_rate_pct}%, net_revenue={data.net_revenue}, "
          f"exact={data.return_value_is_exact}")
    assert data.total_returned_kg == 4.0, "blank-customer return (5kg) must be excluded, same as sales"
    # 2kg @ 700 (own price) + 1kg @ 750 (Carrefour's blended avg, estimated) + 1kg @ 650 (own price)
    assert data.total_return_value == round(2.0 * 700.0 + 1.0 * 750.0 + 1.0 * 650.0, 2)
    assert data.return_value_is_exact is False, "one unpriced return should make this a mix"
    assert data.net_revenue == round(data.total_revenue - data.total_return_value, 2)
    assert len(data.returns_by_customer) == 2, "blank customer return should be excluded"
    assert data.returns_by_customer[0]["customer"] == "Carrefour", \
        "Carrefour has the higher return value, should sort first"
    carrefour_ret = data.returns_by_customer[0]
    assert carrefour_ret["all_exact"] is False, "Carrefour's return mix includes one unpriced row"
    java_ret = next(r for r in data.returns_by_customer if r["customer"] == "Java House")
    assert java_ret["all_exact"] is True, "Java House's only return was priced"

    print("\nTest 2: summarize_commercial_report_data")
    summary = summarize_commercial_report_data(data)
    print(summary)

    print("\nTest 3: generate_commercial_report (actual PDF via reportlab)")
    out_path = "/tmp/test_commercial_report.pdf"
    result_path = generate_commercial_report(data, out_path)
    assert os.path.exists(result_path), "PDF file should exist"
    size = os.path.getsize(result_path)
    print(f"  PDF generated at {result_path}, size={size} bytes")
    assert size > 1000, "PDF should have real content, not be near-empty"
    with open(result_path, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-", "file should be a valid PDF"
    os.remove(result_path)

    print("\nAll commercial_reports checks passed.")