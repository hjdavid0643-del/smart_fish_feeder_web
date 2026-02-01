from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from flask_cors import CORS
from functools import wraps
from datetime import datetime
import os
import io
from collections import deque

# =========================
# CONFIGURATION
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-secret-2026-v3")
CORS(app)

# =========================
# MEMORY STORAGE (No Firebase - Pure Local)
# =========================
sensor_data = deque(maxlen=1000)  # Last 1000 readings
device_status = {
    "ESP32001": {
        "feederstatus": "off", 
        "feederspeed": 0, 
        "limit_switch": "OK",
        "timestamp": datetime.now().isoformat()
    }
}

# User credentials
VALID_USERS = {
    "hjdavid0643@iskwela.psau.edu.ph": "0123456789"
}

# =========================
# DECORATORS & HELPERS
# =========================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def normalize_turbidity(value):
    """Safe turbidity normalization (0-3000 NTU)"""
    try:
        v = float(value)
        return max(0.0, min(3000.0, v))
    except:
        return None

def to_float_or_none(value):
    """Convert to float or return None"""
    try:
        return float(value)
    except:
        return None

# =========================
# ESP32 PUBLIC API (NO AUTH - ESP32 Direct Access)
# =========================
@app.route("/ping", methods=["GET"])
def ping():
    """Health check for ESP32"""
    return jsonify({"status": "alive", "server": "FishFeeder-v3.1"}), 200

@app.route("/getfeedingstatus", methods=["GET"])
def getfeedingstatus():
    """ESP32 polls this every 2 seconds for feeder commands"""
    global device_status
    status = device_status.get("ESP32001", {})
    return jsonify({
        "feederstatus": status.get("feederstatus", "off"),
        "feederspeed": status.get("feederspeed", 0)
    }), 200

@app.route("/addreading", methods=["POST"])
def addreading():
    """ESP32 POSTs sensor data every 10 seconds"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data"}), 400
        
        # Parse and validate sensor data
        reading = {
            "deviceid": data.get("deviceid", "ESP32001"),
            "timestamp": datetime.now().isoformat(),
            "temperature": to_float_or_none(data.get("temperature")),
            "ph": to_float_or_none(data.get("ph")),
            "ammonia": to_float_or_none(data.get("ammonia")),
            "turbidity": normalize_turbidity(data.get("turbidity")),
            "distance": to_float_or_none(data.get("distance")),
            "feeder_status": data.get("feeder_status", "OFF"),
            "limit_switch": data.get("limit_switch", "OK"),
            "servo_angle": data.get("servo_angle", 90)
        }
        
        # Store in memory
        sensor_data.append(reading)
        
        # Log to console
        print(f"🐟 ESP32[{reading['deviceid']}]: NH3={reading['ammonia']:.2f}ppm | "
              f"Turb={reading['turbidity']:.0f}NTU | Feeder={reading['feeder_status']} | "
              f"Hopper={reading['limit_switch']}")
        
        return jsonify({"status": "success", "message": "Data stored"}), 200
        
    except Exception as e:
        print(f"❌ addreading ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# AUTHENTICATION ROUTES
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
        else:
            return render_template("login.html", error="❌ Invalid email or password")
    
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# MAIN DASHBOARD
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    """Main dashboard with live data and charts"""
    # Latest readings
    readings = list(sensor_data)[-10:]
    
    # Water quality analysis
    summary = "✅ All systems normal"
    alertcolor = "#2e7d32"  # Green
    
    if readings:
        latest = readings[-1]
        turbidity = latest.get("turbidity", 0)
        ammonia = latest.get("ammonia", 0)
        
        if ammonia > 2.0 or turbidity > 100:
            summary = "🚨 WATER QUALITY ALERT!"
            alertcolor = "#e53935"  # Red
        elif turbidity > 50 or ammonia > 1.0:
            summary = "⚠️ Check water parameters"
            alertcolor = "#ff7043"  # Orange
    
    # Feeder status
    esp_status = device_status.get("ESP32001", {})
    feederalert = f"Feeder: {esp_status.get('feederstatus', 'OFF').upper()}"
    feederalertcolor = "#28a745" if esp_status.get('feederstatus') == "on" else "#dc3545"
    
    # Chart data (last 20 readings)
    chart_data = list(sensor_data)[-20:]
    timelabels = [r["timestamp"][11:16] for r in chart_data]
    tempvalues = [r.get("temperature", 0) for r in chart_data]
    phvalues = [r.get("ph", 7.8) for r in chart_data]
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
                         turbidityvalues=turbidityvalues,
                         total_readings=len(sensor_data))

# =========================
# FEEDER CONTROL PAGE
# =========================
@app.route("/controlfeeding", methods=["GET", "POST"])
@login_required
def controlfeeding():
    """Dedicated feeder control page"""
    if request.method == "POST":
        action = request.form.get("action")
        speed = int(request.form.get("speed", 50))
        
        # Update feeder status (ESP32 will poll this)
        global device_status
        device_status["ESP32001"].update({
            "feederstatus": "on" if action == "on" else "off",
            "feederspeed": speed if action == "on" else 0,
            "timestamp": datetime.now().isoformat()
        })
        
        print(f"🎮 FEEDER CONTROL: {action.upper()} {speed}% → ESP32001")
        return redirect(url_for("controlfeeding"))
    
    readings = list(sensor_data)[-15:]
    return render_template("control.html", readings=readings)

# =========================
# MOSFET CONTROL PAGE
# =========================
@app.route("/mosfet")
@login_required
def mosfet():
    """MOSFET motor control page"""
    readings = list(sensor_data)[-20:]
    return render_template("mosfet.html", readings=readings)

# =========================
# API ENDPOINTS (Dashboard JavaScript)
# =========================
@app.route("/status")
def status():
    """Live status for dashboard (public)"""
    latest = dict(sensor_data)[-1] if sensor_data else None
    return jsonify({
        "status": "running",
        "readings_count": len(sensor_data),
        "latest": latest,
        "feeder": device_status.get("ESP32001", {})
    })

@app.route("/api/latest")
@login_required
def api_latest():
    """Latest readings API for charts/tables"""
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
    """Current feeder status"""
    return jsonify(device_status.get("ESP32001", {}))

@app.route("/controlfeeding", methods=["POST"])
@login_required
def controlfeeding_api():
    """API version of feeder control"""
    action = request.json.get("action") if request.is_json else request.form.get("action")
    speed = int(request.json.get("speed", 50) if request.is_json else request.form.get("speed", 50))
    
    global device_status
    device_status["ESP32001"].update({
        "feederstatus": "on" if action == "on" else "off",
        "feederspeed": speed if action == "on" else 0,
        "timestamp": datetime.now().isoformat()
    })
    
    return jsonify({
        "status": "success", 
        "message": f"Feeder {action} at {speed}%"
    })

# =========================
# PDF EXPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    """Generate PDF report of last 24 readings"""
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
        
        # Header
        elements.append(Paragraph("🐟 Smart Fish Feeder - Water Quality Report", styles["Title"]))
        elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
        elements.append(Spacer(1, 0.3*inch))
        
        # Data table (last 24 readings)
        table_data = [["Time", "Temp (°C)", "pH", "NH₃ (ppm)", "Turbidity (NTU)", "Feeder Status"]]
        recent_data = list(sensor_data)[-24:]
        
        for reading in recent_data:
            time_str = reading["timestamp"][11:16]
            table_data.append([
                time_str,
                f"{reading.get('temperature', 0):.1f}",
                f"{reading.get('ph', 0):.1f}",
                f"{reading.get('ammonia', 0):.2f}",
                f"{reading.get('turbidity', 0):.0f}",
                reading.get('feeder_status', 'OFF')
            ])
        
        if not recent_data:
            table_data.append(["No data available", "", "", "", "", ""])
        
        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1f77b4")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.beige, colors.white])
        ]))
        
        elements.append(Paragraph("📊 Recent Sensor Readings", styles["Heading2"]))
        elements.append(table)
        
        # Feeder status summary
        feeder = device_status.get("ESP32001", {})
        elements.append(Spacer(1, 0.2*inch))
        elements.append(Paragraph(f"🤖 Feeder Status: {feeder.get('feederstatus', 'OFF')} ({feeder.get('feederspeed', 0)}%)", styles["Normal"]))
        
        doc.build(elements)
        pdf_buffer.seek(0)
        
        filename = f"fishfeeder_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(pdf_buffer, 
                        as_attachment=True, 
                        download_name=filename, 
                        mimetype='application/pdf')
        
    except ImportError:
        return jsonify({"error": "Install reportlab: pip install reportlab"}), 500
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500

# =========================
# MAIN ENTRY POINT
# =========================
if __name__ == "__main__":
    print("=" * 60)
    print("🐟 SMART FISH FEEDER SERVER v3.1 - PRODUCTION READY")
    print("=" * 60)
    print("📡 ESP32 ENDPOINTS:")
    print("   POST /addreading        ← Sensor data (every 10s)")
    print("   GET  /getfeedingstatus  ← Feeder control (every 2s)")
    print("   GET  /ping             ← Health check")
    print("📱 WEB DASHBOARD: http://YOUR_IP:5000/dashboard")
    print("🔐 LOGIN: hjdavid0643@iskwela.psau.edu.ph / 0123456789")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)
