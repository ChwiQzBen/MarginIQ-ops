"""
app/core/pdf_reports.py
========================
Inventory PDF report generation — extracted out of main.py.

This is a pure function: it takes inventory_items / stock_df / kpis as
arguments and returns a file path, with no session_state or Constants
coupling. All reportlab imports happen inside the function body (as they
did in main.py), so this module has no hard dependency on reportlab at
import time — only when a report is actually generated.

Usage from main.py:

    from app.core.pdf_reports import generate_enhanced_pdf_report
"""

from __future__ import annotations
import streamlit as st

from core.advanced_security import rate_limited


@rate_limited(max_calls=5, period=300)  # 5 reports per 5 minutes
def generate_enhanced_pdf_report(inventory_items, stock_df=None, kpis=None, report_type="All Inventory"):
    """
    Generate an enhanced PDF report for ALL inventory items
    
    🔐 Rate limited to 5 reports per 5 minutes to prevent abuse.
    
    Args:
        inventory_items: Dictionary with all inventory items
        stock_df: DataFrame from Google Sheets (optional)
        kpis: Dictionary with KPI values (optional)
    
    Returns:
        Path to the generated PDF file
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_CENTER
        import os
        from datetime import datetime
        
        # Create the report
        _type_slug = report_type.replace(' ', '_').lower()
        report_path = f"inventory_report_{_type_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        doc = SimpleDocTemplate(report_path, pagesize=letter, 
                                rightMargin=72, leftMargin=72, 
                                topMargin=72, bottomMargin=72)
        
        styles = getSampleStyleSheet()
        elements = []
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1f77b4'),
            alignment=TA_CENTER,
            spaceAfter=30
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#333333'),
            spaceAfter=12
        )
        
        # ============================================================
        # 1. TITLE & HEADER
        # ============================================================
        _report_titles = {
            "All Inventory": "Inventory Management Report",
            "Low Stock": "Low Stock Report",
            "Valuable Items": "Valuable Items Report",
        }
        elements.append(Paragraph(_report_titles.get(report_type, "Inventory Management Report"), title_style))
        elements.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", 
                                 styles['Normal']))
        elements.append(Spacer(1, 20))
        
        # ============================================================
        # 2. EXECUTIVE SUMMARY
        # ============================================================
        elements.append(Paragraph("Executive Summary", heading_style))
        
        # Calculate metrics from inventory items
        total_items = len(inventory_items)
        total_stock = sum(details.get('stock', 0) for details in inventory_items.values())
        
        # Count items by status
        low_stock_items = 0
        critical_items = 0
        overstocked_items = 0
        healthy_items = 0
        total_value = 0
        
        for item_name, details in inventory_items.items():
            stock = details.get('stock', 0)
            reorder = details.get('reorder', 0)
            max_stock = details.get('max', stock * 2)
            price = details.get('price', 0)
            
            if price > 0:
                total_value += stock * price
            
            if stock <= 0:
                critical_items += 1
            elif stock < reorder:
                low_stock_items += 1
            elif stock > max_stock * 1.5:
                overstocked_items += 1
            else:
                healthy_items += 1
        
        categories = set(details.get('category', 'Uncategorized') for details in inventory_items.values())
        
        summary_data = [
            ['Metric', 'Value', 'Status'],
            ['Total Items', f"{total_items:,}", ''],
            ['Total Stock', f"{total_stock:,.0f} units", ''],
            ['Total Value', f"KSh {total_value:,.0f}", ''],
            ['Categories', f"{len(categories)}", ''],
            ['Healthy Items', f"{healthy_items}", '✅' if healthy_items > total_items * 0.5 else '⚠️'],
            ['Low Stock', f"{low_stock_items}", '⚠️' if low_stock_items > 0 else '✅'],
            ['Critical (Out of Stock)', f"{critical_items}", '🔴' if critical_items > 0 else '✅'],
            ['Overstocked', f"{overstocked_items}", ''],
        ]
        
        summary_table = Table(summary_data, colWidths=[2*inch, 1.5*inch, 1*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4e79a7')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 11),
            ('BOTTOMPADDING', (0,0), (-1,0), 10),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9fa')),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#dee2e6')),
            ('FONTSIZE', (0,1), (-1,-1), 10),
            ('TOPPADDING', (0,1), (-1,-1), 6),
            ('BOTTOMPADDING', (0,1), (-1,-1), 6),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 20))
        
        # ============================================================
        # 3. INVENTORY STATUS
        # ============================================================
        elements.append(Paragraph("Inventory Status Overview", heading_style))
        
        status_data = [
            ['Status', 'Count', 'Percentage'],
            ['✅ Healthy', f"{healthy_items}", f"{(healthy_items/total_items*100):.1f}%" if total_items > 0 else '0%'],
            ['⚠️ Low Stock', f"{low_stock_items}", f"{(low_stock_items/total_items*100):.1f}%" if total_items > 0 else '0%'],
            ['🔴 Critical', f"{critical_items}", f"{(critical_items/total_items*100):.1f}%" if total_items > 0 else '0%'],
            ['📦 Overstocked', f"{overstocked_items}", f"{(overstocked_items/total_items*100):.1f}%" if total_items > 0 else '0%'],
        ]
        
        status_table = Table(status_data, colWidths=[1.5*inch, 1.5*inch, 1.5*inch])
        status_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2ecc71')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 11),
            ('BOTTOMPADDING', (0,0), (-1,0), 10),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9fa')),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#dee2e6')),
            ('FONTSIZE', (0,1), (-1,-1), 10),
            ('TOPPADDING', (0,1), (-1,-1), 6),
            ('BOTTOMPADDING', (0,1), (-1,-1), 6),
        ]))
        elements.append(status_table)
        elements.append(Spacer(1, 20))
        
        # ============================================================
        # 4. CATEGORY BREAKDOWN
        # ============================================================
        if len(categories) > 1:
            elements.append(Paragraph("Category Breakdown", heading_style))
            
            category_data = [['Category', 'Items', 'Total Stock', 'Avg Stock/Item']]
            
            for category in sorted(categories):
                cat_items = [item for item, details in inventory_items.items() 
                            if details.get('category', 'Uncategorized') == category]
                cat_count = len(cat_items)
                cat_stock = sum(details.get('stock', 0) for item, details in inventory_items.items() 
                               if details.get('category', 'Uncategorized') == category)
                avg_stock = cat_stock / cat_count if cat_count > 0 else 0
                
                category_data.append([
                    category,
                    f"{cat_count}",
                    f"{cat_stock:,.0f}",
                    f"{avg_stock:.0f}"
                ])
            
            category_table = Table(category_data, colWidths=[1.8*inch, 1.2*inch, 1.5*inch, 1.5*inch])
            category_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#3498db')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,0), 11),
                ('BOTTOMPADDING', (0,0), (-1,0), 10),
                ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9fa')),
                ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#dee2e6')),
                ('FONTSIZE', (0,1), (-1,-1), 10),
                ('TOPPADDING', (0,1), (-1,-1), 6),
                ('BOTTOMPADDING', (0,1), (-1,-1), 6),
            ]))
            elements.append(category_table)
            elements.append(Spacer(1, 20))
        
        # ============================================================
        # 5. TOP VALUABLE ITEMS
        # ============================================================
        valuable_items = []
        for item_name, details in inventory_items.items():
            stock = details.get('stock', 0)
            price = details.get('price', 0)
            if price > 0 and stock > 0:
                value = stock * price
                valuable_items.append({
                    'name': item_name,
                    'value': value,
                    'stock': stock,
                    'price': price,
                    'category': details.get('category', 'Uncategorized')
                })
        
        if valuable_items and report_type != "Low Stock":
            elements.append(Paragraph("Top 10 Most Valuable Items", heading_style))
            
            valuable_items.sort(key=lambda x: x['value'], reverse=True)
            top_items = valuable_items[:10]
            
            value_data = [['Item', 'Category', 'Stock', 'Unit Price', 'Total Value']]
            
            for item in top_items:
                value_data.append([
                    item['name'][:30] + ('...' if len(item['name']) > 30 else ''),
                    item['category'],
                    f"{item['stock']:,.0f}",
                    f"KSh {item['price']:,.2f}",
                    f"KSh {item['value']:,.0f}"
                ])
            
            value_table = Table(value_data, colWidths=[2*inch, 1.2*inch, 0.8*inch, 1*inch, 1.2*inch])
            value_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e74c3c')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,0), 10),
                ('BOTTOMPADDING', (0,0), (-1,0), 8),
                ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9fa')),
                ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#dee2e6')),
                ('FONTSIZE', (0,1), (-1,-1), 9),
                ('TOPPADDING', (0,1), (-1,-1), 5),
                ('BOTTOMPADDING', (0,1), (-1,-1), 5),
            ]))
            elements.append(value_table)
            elements.append(Spacer(1, 20))
        
        # ============================================================
        # 6. LOW STOCK ITEMS
        # ============================================================
        low_items = []
        for item_name, details in inventory_items.items():
            stock = details.get('stock', 0)
            reorder = details.get('reorder', 0)
            if stock < reorder:
                low_items.append({
                    'name': item_name,
                    'stock': stock,
                    'reorder': reorder,
                    'deficit': reorder - stock,
                    'category': details.get('category', 'Uncategorized'),
                    'unit': details.get('unit', 'units')
                })
        
        if low_items and report_type != "Valuable Items":
            elements.append(Paragraph(f"⚠️ Items Below Reorder Point ({len(low_items)} items)", heading_style))
            
            low_items.sort(key=lambda x: x['deficit'], reverse=True)
            
            low_data = [['Item', 'Category', 'Current Stock', 'Reorder Point', 'Deficit']]
            
            for item in low_items[:20]:
                low_data.append([
                    item['name'][:25] + ('...' if len(item['name']) > 25 else ''),
                    item['category'],
                    f"{item['stock']:.0f} {item['unit']}",
                    f"{item['reorder']:.0f} {item['unit']}",
                    f"{item['deficit']:.0f} {item['unit']}"
                ])
            
            if len(low_items) > 20:
                low_data.append(['', '', '', f'And {len(low_items) - 20} more items...', ''])
            
            low_table = Table(low_data, colWidths=[1.8*inch, 1.2*inch, 1*inch, 1*inch, 1*inch])
            low_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#ff9800')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,0), 10),
                ('BOTTOMPADDING', (0,0), (-1,0), 8),
                ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#fff3e0')),
                ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#dee2e6')),
                ('FONTSIZE', (0,1), (-1,-1), 9),
                ('TOPPADDING', (0,1), (-1,-1), 5),
                ('BOTTOMPADDING', (0,1), (-1,-1), 5),
            ]))
            elements.append(low_table)
            elements.append(Spacer(1, 20))
        
        # ============================================================
        # 7. RECOMMENDATIONS
        # ============================================================
        elements.append(Paragraph("Recommendations", heading_style))

        recommendations = []

        if report_type == "Valuable Items":
            # Value-concentration insight, not stock-level triage -- shortage
            # counts don't belong in a report about what's worth the most.
            if valuable_items:
                total_val = sum(i['value'] for i in valuable_items)
                top3_val = sum(i['value'] for i in valuable_items[:3])
                if total_val > 0:
                    concentration = top3_val / total_val * 100
                    recommendations.append(
                        f"• 💰 Your top 3 items account for {concentration:.0f}% of total tracked "
                        f"value here — prioritize these for cycle counts and price accuracy checks."
                    )
            no_price_count = sum(1 for d in inventory_items.values() if not d.get('price') or d.get('price') <= 0)
            if no_price_count > 0:
                recommendations.append(
                    f"• ⚠️ {no_price_count} item(s) here have no price recorded — total value "
                    f"is understated until those are filled in."
                )
            recommendations.append("• 📋 Recheck unit prices periodically — stale prices distort this report's totals.")

        elif report_type == "Low Stock":
            if critical_items > 0:
                recommendations.append(f"• 🔴 {critical_items} of these are completely OUT OF STOCK — order immediately.")
            if low_items:
                worst = sorted(low_items, key=lambda x: x['deficit'], reverse=True)[:3]
                worst_list = ', '.join(f"{i['name']} ({i['deficit']:.0f} {i['unit']} short)" for i in worst)
                recommendations.append(f"• 📉 Largest deficits: {worst_list}")
            if len(categories) > 1:
                category_counts = {}
                for item_name, details in inventory_items.items():
                    category = details.get('category', 'Uncategorized')
                    category_counts[category] = category_counts.get(category, 0) + 1
                for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
                    recommendations.append(f"• 📂 {cat}: {count} item(s) short")
            recommendations.append(
                "• 📋 A shortage list this size may mean reorder thresholds are set too high, "
                "or replenishment is lagging demand — worth reviewing both."
            )

        else:  # "All Inventory" -- unchanged, full picture
            if critical_items > 0:
                recommendations.append(f"• 🔴 {critical_items} items are OUT OF STOCK - Order immediately")
            if low_stock_items > 0:
                recommendations.append(f"• ⚠️ {low_stock_items} items are below reorder point - Review and replenish")
            if overstocked_items > 0:
                recommendations.append(f"• 📦 {overstocked_items} items are overstocked - Consider reducing orders")
            if healthy_items < total_items * 0.5:
                recommendations.append("• 📊 Overall inventory health is below 50% - Review all items")
            if len(categories) > 1:
                category_issues = {}
                for item_name, details in inventory_items.items():
                    category = details.get('category', 'Uncategorized')
                    stock = details.get('stock', 0)
                    reorder = details.get('reorder', 0)
                    if category not in category_issues:
                        category_issues[category] = {'low': 0, 'total': 0}
                    category_issues[category]['total'] += 1
                    if stock < reorder:
                        category_issues[category]['low'] += 1
                for cat, data in category_issues.items():
                    if data['low'] > 0:
                        recommendations.append(f"• 📂 {cat}: {data['low']}/{data['total']} items need attention")
            if not recommendations:
                recommendations.append("• ✅ All inventory items are well-stocked. Continue monitoring.")
            recommendations.append("• 📋 Review reorder points regularly based on demand patterns")
            recommendations.append("• 📊 Consider ABC analysis to prioritize high-value items")

        if not recommendations:
            recommendations.append("• ✅ Nothing further to flag for this report.")
        
        for rec in recommendations:
            elements.append(Paragraph(rec, styles['Normal']))
            elements.append(Spacer(1, 6))
        
        # ============================================================
        # 8. FOOTER
        # ============================================================
        elements.append(Spacer(1, 30))
        elements.append(Paragraph(f"Report generated by Browns Food Co - Inventory Management System", 
                                 styles['Normal']))
        elements.append(Paragraph(f"© {datetime.now().year} - All Rights Reserved", 
                                 styles['Normal']))
        
        # Build the report
        doc.build(elements)
        
        return report_path
        
    except ImportError as e:
        st.error(f"Report generation failed: Missing required library - {e}")
        st.info("Please install reportlab: pip install reportlab")
        return None
    except Exception as e:
        st.error(f"Error generating report: {e}")
        return None