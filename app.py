from flask import (
    Flask, render_template, request, redirect, url_for, session, 
    jsonify, send_file, Response
)
from flask_cors import CORS
from functools import wraps
from datetime import datetime, timedelta
import os
import io
import json
from collections import deque, defaultdict
import threading
import time

# ReportLab for PDF (install: pip install reportlab)
try:
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# =========================
# CONFIGURATION
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-secret-2026-v4")
CORS(app)

# =========================
# MEMORY STORAGE (Primary - No Firebase Required)
# =========================
sensor_data = deque(maxlen=2000)  # Last 2000 readings
device_status = {
    "ESP32001": {"feederstatus": "off", "feederspeed": 0, "limit_switch": "OK"},
    "ESP32002": {"motorstatus": "off", "motorspeed": 0, "distance": 0}
}
feeding_schedule = {"firstfeed": "09:00", "secondfeed": "16:00", "duration": 5, "enabled": False}

# =========================
# FIREBASE (Optional Fallback)
# =========================
FIRESTORE_DB = None
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    def init_firebase():
        key_path = "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
        if os.path.exists(key_path) and not firebase_admin._apps:
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
            return firestore.client()
        return None
    FIRESTORE_DB = init_firebase()
except:
    FIRESTORE_DB = None
    print("⚠️ Firebase unavailable - using memory storage only")

# =========================
# DECORATORS
# =========================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# =========================
# UTILITY FUNCTIONS
# =========================
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

def save_to_firestore(collection, document_id, data):
    """Optional Firestore backup"""
    if FIRESTORE_DB:
        try:
            FIRESTORE_DB.collection(collection).document(document_id).set(data, merge=True)
        except:
            pass  # Silently fail

# =========================
# AUTHENTICATION
# =========================
VALID_USERS = {"hjdavid0643@iskwela.psau.edu.ph": "0123456789"}

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
        return render_template("login.html", error="❌ Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# ESP32 PUBLIC API (No Auth Required)
# =========================
@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "alive", "version": "v4.0"}), 200

@app.route("/addreading", methods=["POST"])
def addreading():
    """ESP32 posts sensor data here every 10 seconds"""
    try:
        data = request.get_json() or {}
        deviceid = data.get("deviceid", "ESP32001")
        
        reading = {
            "deviceid": deviceid,
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
        
        sensor_data.append(reading)
        save_to_firestore("readings", f"{deviceid}_{int(time.time())}", reading)
        
        print(f"🐟 [{deviceid}] NH3={reading['ammonia']:.2f} Turb={reading['turbidity']:.0f}")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/getfeedingstatus", methods=["GET"])
def getfeedingstatus():
    """ESP32 polls every 2 seconds"""
    status = device_status.get("ESP32001", {})
    return jsonify({
        "feederstatus": status.get("feederstatus", "off"),
        "feederspeed": status.get("feederspeed", 0)
    })

# =========================
# DASHBOARD (Main Page)
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    readings = list(sensor_data)[-10:]
    
    # Water quality analysis
    summary = "✅ All systems normal"
    alertcolor = "#2e7d32"
    if readings:
        latest = readings[-1]
        ammonia = latest.get("ammonia", 0)
        turbidity = latest.get("turbidity", 0)
        if ammonia > 2.0 or turbidity > 100:
            summary = "🚨 WATER QUALITY ALERT!"
            alertcolor = "#e53935"
        elif turbidity > 50:
            summary = "⚠️ Check water clarity"
            alertcolor = "#ff7043"
    
    # Chart data
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
                         timelabels=timelabels,
                         tempvalues=tempvalues,
                         phvalues=phvalues,
                         ammoniavalues=ammoniavalues,
                         turbidityvalues=turbidityvalues)

# =========================
# API ROUTES (Dashboard JavaScript)
# =========================
@app.route("/apilatestreadings")
@login_required
def apilatestreadings():
    data = list(sensor_data)[-50:]
    labels = [r["timestamp"][11:16] for r in data]
    temp = [r.get("temperature", 0) for r in data]
    ph = [r.get("ph", 0) for r in data]
    ammonia = [r.get("ammonia", 0) for r in data]
    turbidity = [r.get("turbidity", 0) for r in data]
    return jsonify({"labels": labels, "temp": temp, "ph": ph, "ammonia": ammonia, "turbidity": turbidity})

@app.route("/historical")
@login_required
def historical():
    data = list(sensor_data)[-100:]
    return jsonify({"status": "success", "readings": data})

@app.route("/apiultrasonicesp322")
@login_required
def apiultrasonicesp322():
    distances = [r.get("distance", 0) for r in list(sensor_data)[-20:]]
    return jsonify({"status": "success", "distance": distances})

@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    data = request.get_json() or request.form
    action = data.get("action")
    speed = int(data.get("speed", 50))
    
    device_status["ESP32001"].update({
        "feederstatus": "on" if action == "on" else "off",
        "feederspeed": speed if action == "on" else 0
    })
    save_to_firestore("devices", "ESP32001", device_status["ESP32001"])
    
    return jsonify({"status": "success", "message": f"Feeder {action} ({speed}%)"})

@app.route("/getfeedingstatus", methods=["GET"])
@api_login_required
def getfeedingstatus_web():
    status = device_status.get("ESP32001", {})
    return jsonify({"status": "success", "feederstatus": status.get("feederstatus"), "feederspeed": status.get("feederspeed")})

@app.route("/controlmotor", methods=["POST"])
@api_login_required
def controlmotor():
    data = request.get_json() or request.form
    action = data.get("action")
    speed = int(data.get("speed", 50))
    
    device_status["ESP32002"].update({
        "motorstatus": "on" if action == "on" else "off",
        "motorspeed": speed if action == "on" else 0
    })
    save_to_firestore("devices", "ESP32002", device_status["ESP32002"])
    
    return jsonify({"status": "success", "message": f"Motor {action} ({speed}%)"})

@app.route("/getmotorstatus", methods=["GET"])
@api_login_required
def getmotorstatus():
    status = device_status.get("ESP32002", {})
    return jsonify({"status": "success", "motorstatus": status.get("motorstatus"), "motorspeed": status.get("motorspeed")})

@app.route("/savefeedingschedule", methods=["POST"])
@api_login_required
def savefeedingschedule():
    data = request.get_json() or request.form
    global feeding_schedule
    feeding_schedule.update({
        "firstfeed": data.get("firstfeed"),
        "secondfeed": data.get("secondfeed"),
        "duration": int(data.get("duration", 5)),
        "enabled": True
    })
    return jsonify({"status": "success", "message": "Schedule saved"})

@app.route("/getfeedingscheduleinfo")
@api_login_required
def getfeedingscheduleinfo():
    return jsonify({"status": "success", "schedule": feeding_schedule, "enabled": feeding_schedule["enabled"]})

# =========================
# PDF EXPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if not REPORTLAB_AVAILABLE:
        return "Install reportlab: pip install reportlab", 500
    
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter)
    elements = []
    styles = getSampleStyleSheet()
    
    elements.append(Paragraph("🐟 Smart Fish Feeder Report", styles["Title"]))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    elements.append(Spacer(1, 0.3*inch))
    
    # Recent data table
    table_data = [["Time", "Temp(°C)", "pH", "NH₃(ppm)", "Turb(NTU)", "Feeder"]]
    recent = list(sensor_data)[-24:]
    for r in recent:
        time_str = r["timestamp"][11:16]
        table_data.append([
            time_str,
            f"{r.get('temperature', 0):.1f}",
            f"{r.get('ph', 0):.1f}",
            f"{r.get('ammonia', 0):.2f}",
            f"{r.get('turbidity', 0):.0f}",
            r.get('feeder_status', 'OFF')
        ])
    
    table = Table(table_data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2575fc")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
    ]))
    
    elements.append(table)
    doc.build(elements)
    
    pdf_buffer.seek(0)
    return send_file(pdf_buffer, as_attachment=True, download_name=f"fishfeeder_{datetime.now().strftime('%Y%m%d')}.pdf", mimetype='application/pdf')

# =========================
# START SERVER
# =========================
if __name__ == "__main__":
    print("🐟 Smart Fish Feeder Server v4.0 - FULLY COMPATIBLE")
    print("📡 ESP32: POST /addreading | GET /getfeedingstatus")
    print("📱 Dashboard: http://0.0.0.0:5000/dashboard")
    print("🔐 Login: hjdavid0643@iskwela.psau.edu.ph / 0123456789")
    app.run(host="0.0.0.0", port=5000, debug=True)
