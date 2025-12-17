from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from datetime import datetime
import matplotlib.pyplot as plt  
import pandas as pd
import os

def generate_full_report(data_sensor1, data_sensor2, feed_logs, system_logs, filename="System_Report.pdf"):

    pdf = SimpleDocTemplate(filename, pagesize=A4)
    styles = getSampleStyleSheet()
    large = ParagraphStyle("large", parent=styles["Heading1"], fontSize=22, alignment=1)
    medium = ParagraphStyle("medium", parent=styles["Heading2"], fontSize=16, spaceBefore=10)

    content = []

    # ======================================
    # PAGE 1 — Title + Summary
    # ======================================

    title = Paragraph("📊 Smart Fish Feeder — System Report", large)
    date = Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y — %H:%M')}", styles["Normal"])
    content += [title, Spacer(1, 10), date, Spacer(1, 20)]

    # KPI SUMMARY BOXES
    summary_table = [
        ["Water pH Avg", round(sum(data_sensor1)/len(data_sensor1),2),
         "Feeding Events", len(feed_logs)],
        ["Temperature Avg", round(sum(data_sensor2)/len(data_sensor2),2),
         "System Alerts", len(system_logs)]
    ]

    table = Table(summary_table, colWidths=[160,80,160,80])
    table.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 1, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.lightblue),
        ("GRID", (0,0), (-1,-1), 1, colors.grey)
    ]))

    content += [table, Spacer(1, 30)]


    # ======================================
    # PAGE 2 — Sensor Graphs (Chart Export)
    # ======================================

    # Chart 1: pH trend
    plt.plot(data_sensor1); plt.title("Water pH Trend"); plt.xlabel("Time"); plt.ylabel("pH Level")
    img1 = "chart_ph.png"; plt.savefig(img1); plt.close()

    content += [Paragraph("📈 Water Quality Trends", medium), Image(img1, width=400, height=300), Spacer(1, 20)]

    # Chart 2: Temperature trend
    plt.plot(data_sensor2); plt.title("Temperature Trend"); plt.xlabel("Time"); plt.ylabel("°C")
    img2 = "chart_temp.png"; plt.savefig(img2); plt.close()

    content += [Image(img2, width=400, height=300), PageBreak()]


    # ======================================
    # PAGE 3 — Feeding Logs
    # ======================================

    content += [Paragraph("🍤 Feeding Activities", medium), Spacer(1,10)]

    feed_data = [[row["time"], row["amount"], row["status"]] for row in feed_logs]
    feed_table = Table([["Time","Amount","Status"]] + feed_data)

    feed_table.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),1,colors.black),
        ("BACKGROUND",(0,0),(-1,0),colors.lightgreen)
    ]))

    content += [feed_table, PageBreak()]


    # ======================================
    # PAGE 4 — Weekly Summary
    # ======================================

    content += [Paragraph("📅 Weekly Summary", medium), Spacer(1,10)]
    weekly_avg_ph = sum(data_sensor1) / len(data_sensor1)
    weekly_avg_temp = sum(data_sensor2) / len(data_sensor2)

    summary = f"""
    Average pH: {weekly_avg_ph:.2f}<br/>
    Average Temperature: {weekly_avg_temp:.2f}<br/>
    Total Feeding Cycles: {len(feed_logs)}<br/>
    Alerts Logged: {len(system_logs)}
    """
    content += [Paragraph(summary, styles["BodyText"]), PageBreak()]


    # ======================================
    # PAGE 5 — System Alerts & Logs
    # ======================================

    content += [Paragraph("⚠ System Logs & Alerts", medium), Spacer(1,10)]

    logs_data = [[log["time"], log["event"]] for log in system_logs]
    log_table = Table([["Time","Event"]] + logs_data)

    log_table.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),1,colors.black),
        ("BACKGROUND",(0,0),(-1,0),colors.pink)
    ]))

    content += [log_table]


    # Build PDF file
    pdf.build(content)

    os.remove(img1)
    os.remove(img2)

    return filename
