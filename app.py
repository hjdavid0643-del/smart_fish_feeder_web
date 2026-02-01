from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from flask_cors import CORS
from datetime import datetime, timedelta
import os
import io
import json
from collections import deque
import threading
import time

# =========================
# CONFIG
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-secret-2026")
CORS(app)

# =========================
# MEMORY STORAGE (No Firebase needed)
# =========================
sensor_data = deque(maxlen=1000)  # Last 1000 readings
feeder_status = {
    "status": "off",
    "speed": 0,
    "hopper_level": "OK",
    "timestamp": datetime.now().isoformat()
}
device_status = {
    "ESP32001": {"feederstatus": "off", "feederspeed": 0, "limit_switch": "OK"}
}

# User credentials (hardcoded for testing)
VALID_USERS = {
    "hjdavid0643@iskwela.psau.edu.ph": "0123456789"
}

# =========================
# HELPERS
# =========================
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def normalize_turbidity(value):
    try:
        v = float(value)
        return max(0.0, min(3000.0, v))
    except:
        return None

def to_float_or_none(value):
    try:
        return float(value)
    except:
        return None

# =========================
# ESP32 PUBLIC API (NO AUTH REQUIRED)
# =========================
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "alive", "server": "FishFeeder-v3"}), 200

@app.route("/getfeedingstatus", methods=["GET"])
def getfeedingstatus():
    """ESP32 polls this every 2 seconds"""
    global device_status
    status = device_status.get("ESP32001", {})
    return jsonify({
        "feederstatus": status.get("feederstatus", "off"),
        "feederspeed": status.get("feederspeed", 0)
    }), 200

@app.route("/addreading", methods=["POST"])
def addreading():
    """ESP32 POSTs sensor data here every 10 seconds"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON"}), 400
        
        # Store sensor reading
        reading = {
            "deviceid": data.get("deviceid", "ESP32001"),
            "timestamp": datetime.now().isoformat(),
            "temperature": to_float_or_none(data.get("temperature")),
            "ph": to_float_or_none(data.get("ph")),
            "ammonia": to_float_or_none(data.get("ammonia")),
            "turbidity": normalize_turbidity(data.get("turbidity")),
            "feeder_status": data.get("feeder_status", "OFF"),
            "limit_switch": data.get("limit_switch", "OK"),
            "servo_angle": data.get("servo_angle", 90)
        }
        
        sensor_data.append(reading)
        print(f"🐟 ESP32 DATA: NH3={reading['ammonia']:.2f}ppm | Turb={reading['turbidity']:.0f}NTU")
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        print(f"❌ addreading error: {e}")
        return jsonify({"status": "error"}), 500

# =========================
# AUTH ROUTES
# =========================
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        
        if VALID_USERS.get(email) == password:
            session["user"] = email
            session["role"] = "admin"
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials")
    
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    # Get latest 10 readings
    readings = list(sensor_data)[-10:]
    
    # Water quality analysis
    summary = "All systems normal"
    alertcolor = "green"
    
    if readings:
        latest = readings[-1]
        turbidity = latest.get("turbidity")
        ammonia = latest.get("ammonia")
        
        if turbidity and turbidity > 100:
            summary = "🚨 HIGH TURBIDITY - Clean tank!"
            alertcolor = "red"
        elif turbidity and turbidity > 50:
            summary = "⚠️ Water cloudy - consider cleaning"
            alertcolor = "orange"
        elif ammonia and ammonia > 2.0:
            summary = "🚨 HIGH AMMONIA - Water change needed!"
            alertcolor = "red"
    
    # Feeder status
    status = device_status.get("ESP32001", {})
    feederalert = f"Feeder {status.get('feederstatus', 'OFF')} ({status.get('feederspeed', 0)}%)"
    feederalertcolor = "limegreen" if status.get('feederstatus') == "on" else "lightcoral"
    
    # Charts data
    chart_data = list(sensor_data)[-20:] if len(sensor_data) >= 20 else list(sensor_data)
    timelabels = [r["timestamp"][11:16] for r in chart_data]
    tempvalues = [r.get("temperature", 0) for r in chart_data]
    phvalues = [r.get("ph", 0) for r in chart_data]
    ammoniavalues = [r.get("ammonia", 0) for r in chart_data]
    turbidityvalues = [r.get("turbidity", 0) for r in chart_data]
    
    return render_template("dashboard.html",
                         readings=readings,
                         summary=summary,
                         alertcolor=alertcolor,
                         feederalert=feederalert,
                         feederalertcolor=feederalertcolor,
                         timelabels=timelabels,
                         tempvalues=tempvalues,
                         phvalues=phvalues,
                         ammoniavalues=ammoniavalues,
                         turbidityvalues=turbidityvalues)

# =========================
# FEEDER CONTROL
# =========================
@app.route("/controlfeeding", methods=["GET", "POST"])
@login_required
def controlfeeding():
    if request.method == "POST":
        action = request.form.get("action")
        speed = int(request.form.get("speed", 50))
        
        device_status["ESP32001"] = {
            "feederstatus": "on" if action == "on" else "off",
            "feederspeed": speed if action == "on" else 0,
            "limit_switch": device_status["ESP32001"].get("limit_switch", "OK"),
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"🎮 FEEDER: {action} at {speed}%")
        return redirect(url_for("controlfeeding"))
    
    readings = list(sensor_data)[-15:]
    return render_template("control.html", readings=readings)

# =========================
# MOSFET CONTROL PAGE
# =========================
@app.route("/mosfet")
@login_required
def mosfet():
    readings = list(sensor_data)[-20:]
    return render_template("mosfet.html", readings=readings)

# =========================
# PDF REPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    try:
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        
        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        
        # Title
        elements.append(Paragraph("🐟 Smart Fish Feeder - Water Quality Report", 
                                styles["Title"]))
        elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 
                                styles["Normal"]))
        elements.append(Spacer(1, 0.2*inch))
        
        # Data table
        table_data = [["Time", "Temp (°C)", "pH", "NH3 (ppm)", "Turbidity (NTU)", "Feeder"]]
        recent_data = list(sensor_data)[-24:]  # Last ~24 readings
        
        for reading in recent_data:
            time_str = reading["timestamp"][11:16]
            table_data.append([
                time_str,
                f"{reading.get('temperature', 'N/A'):.1f}",
                f"{reading.get('ph', 'N/A'):.1f}",
                f"{reading.get('ammonia', 'N/A'):.2f}",
                f"{reading.get('turbidity', 'N/A'):.0f}",
                reading.get('feeder_status', 'OFF')
            ])
        
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1f77b4")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        pdf_buffer.seek(0)
        filename = f"fishfeeder_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
        
    except ImportError:
        return "PDF generation requires: pip install reportlab", 500

# =========================
# API ENDPOINTS
# =========================
@app.route("/api/latest")
@login_required
def api_latest():
    readings = list(sensor_data)[-50:]
    return jsonify({
        "status": "success",
        "count": len(readings),
        "latest": readings[-1] if readings else None,
        "readings": readings
    })

@app.route("/api/feederstatus")
@login_required
def api_feederstatus():
    return jsonify(device_status.get("ESP32001", {}))

# =========================
# HEALTH CHECKS
# =========================
@app.route("/status")
def status():
    return jsonify({
        "status": "running",
        "readings_count": len(sensor_data),
        "feeder": device_status.get("ESP32001", {}),
        "uptime": "FishFeeder v3.0"
    })

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    print("🐟 Smart Fish Feeder Server v3.0")
    print("📡 ESP32 → http://YOUR_IP:5000/addreading")
    print("📱 Dashboard → http://YOUR_IP:5000/dashboard")
    print("🚀 Starting on http://0.0.0.0:5000")
    print("-" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True)
