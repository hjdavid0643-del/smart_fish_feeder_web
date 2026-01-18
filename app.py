from datetime import datetime, timedelta
import io

from flask import send_file, jsonify
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from firebase_admin import firestore


@app.route("/export_pdf")
@login_required
def export_pdf():
    try:
        # ----- Query last 24 hours -----
        now = datetime.utcnow()
        twenty_four_hours_ago = now - timedelta(hours=24)

        readings_ref = (
            db.collection("devices")
            .document("ESP32_001")
            .collection("readings")
            .where("createdAt", ">=", twenty_four_hours_ago)
            .order_by("createdAt", direction=firestore.Query.ASCENDING)
        )

        readings_cursor = readings_ref.stream()
        data = []
        for r in readings_cursor:
            doc_data = r.to_dict()
            data.append({
                "temperature": doc_data.get("temperature"),
                "ph": doc_data.get("ph"),
                "ammonia": doc_data.get("ammonia"),
                "turbidity": normalize_turbidity(doc_data.get("turbidity")),
                "createdAt": doc_data.get("createdAt"),
            })

        # ----- Create PDF in memory -----
        pdf_buffer = io.BytesIO()
        doc_pdf = SimpleDocTemplate(pdf_buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        # Title and header
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=20,
            textColor=colors.HexColor("#1f77b4"),
            alignment=1,
            spaceAfter=20,
        )
        elements.append(Paragraph("🐟 Water Quality Monitoring Report", title_style))
        elements.append(Paragraph(
            f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} (last 24 hours)",
            styles["Normal"]
        ))
        elements.append(Spacer(1, 0.2 * inch))

        # Table data
        table_data = [["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]]

        if data:
            for r in data:
                created_dt = r["createdAt"]
                if isinstance(created_dt, datetime):
                    created_str = created_dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    created_str = str(created_dt) if created_dt else ""
                table_data.append([
                    created_str,
                    "" if r["temperature"] is None else f"{r['temperature']:.2f}",
                    "" if r["ph"] is None else f"{r['ph']:.2f}",
                    "" if r["ammonia"] is None else f"{r['ammonia']:.2f}",
                    "" if r["turbidity"] is None else f"{r['turbidity']:.2f}",
                ])
        else:
            table_data.append(["No data in last 24 hours", "", "", "", ""])

        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f77b4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ]))

        elements.append(Paragraph("Recent Sensor Readings (24 hours)", styles["Heading2"]))
        elements.append(table)

        # Build PDF
        doc_pdf.build(elements)
        pdf_buffer.seek(0)

        # Send file
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"water_quality_24h_{timestamp}.pdf",
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
