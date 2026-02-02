from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify, send_file
)
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from datetime import datetime, timedelta
import os
import io
import json
from google.api_core.exceptions import ResourceExhausted
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# =========================
# CONFIG
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
CORS(app)

# =========================
# FIREBASE INIT - PRODUCTION READY
# =========================
def init_firebase():
    if firebase_admin._apps:
        return firestore.client()
    
    try:
        # Render.com: FIREBASE_SERVICE_ACCOUNT env var (JSON string)
        service_account = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if service_account:
            cred_dict = json.loads(service_account)
            cred = credentials.Certificate(cred_dict)
            app = firebase_admin.initialize_app(cred)
            return firestore.client(app=app)
        
        # Local dev: FIREBASE_KEY_PATH file
        key_path = os.environ.get("FIREBASE_KEY_PATH")
        if key_path and os.path.exists(key_path):
            cred = credentials.Certificate(key_path)
            app = firebase_admin.initialize_app(cred)
            return firestore.client(app=app)
            
    except Exception as e:
        print(f"❌ Firebase init failed: {e}")
        return None

db = init_firebase()

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

def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_key = os.environ.get("API_SECRET", "fishfeeder123")
        if not api_key or api_key != expected_key:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def normalize_turbidity(value):
    try:
        if value is None: return None
        val = float(value)
        return max(0, min(100, val))
    except: return None

def to_float_or_none(value):
    if value is None: return None
    try: return float(value)
    except: return None

VALID_USERS = {
    "admin@example.com": "admin123",
    "worker@example.com": "worker123",
}

# =========================
# AUTH ROUTES
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            return render_template("login.html", error="Email and password required.")
        if VALID_USERS.get(email) == password:
            session["user"] = email
            session["role"] = "admin" if email == "admin@example.com" else "worker"
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register")
def register():
    return render_template("register.html")

# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    if not db:
        return render_template("dashboard.html", readings=[], summary="No DB", 
                             alertcolor="gray", timelabels=[], tempvalues=[], 
                             phvalues=[], ammoniavalues=[], turbidityvalues=[],
                             feederalert="N/A", feederalertcolor="gray")

    try:
        readings_ref = (db.collection("devices").document("ESP32001")
                       .collection("readings")
                       .order_by("createdAt", direction=firestore.Query.DESCENDING)
                       .limit(50))
        data = []
        for r in readings_ref.stream():
            doc = r.to_dict()
            created = doc.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M") if hasattr(created, 'strftime') else str(created)
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"), 
                "ammonia": doc.get("ammonia"),
                "turbidity": normalize_turbidity(doc.get("turbidity")),
                "createdAt": created_str,
            })
        data = list(reversed(data))
    except:
        data = []

    # Status checks
    summary, alertcolor = "All systems normal.", "green"
    if data: 
        last_turb = data[-1].get("turbidity", 0)
        if last_turb > 100: summary, alertcolor = "Water too cloudy! Danger", "red"
        elif last_turb > 50: summary, alertcolor = "Water getting cloudy", "orange"

    feederalert, feederalertcolor = "Feeder OFF", "lightcoral"
    try:
        dev = db.collection("devices").document("ESP32001").get()
        if dev.exists:
            d = dev.to_dict()
            if d.get("feederstatus") == "on":
                feederalert = f"Feeding {d.get('feederspeed', 0)}%"
                feederalertcolor = "limegreen"
    except: pass

    return render_template("dashboard.html",
        readings=data[-10:],
        summary=summary, alertcolor=alertcolor,
        timelabels=[r["createdAt"] for r in data],
        tempvalues=[r["temperature"] or 0 for r in data],
        phvalues=[r["ph"] or 0 for r in data],
        ammoniavalues=[r["ammonia"] or 0 for r in data],
        turbidityvalues=[r["turbidity"] or 0 for r in data],
        feederalert=feederalert, feederalertcolor=feederalertcolor)

@app.route("/controlfeeding")
@login_required
def controlfeeding():
    if not db:
        return render_template("control.html", readings=[], summary="No DB")
    
    try:
        readings_ref = (db.collection("devices").document("ESP32001")
                       .collection("readings")
                       .order_by("createdAt", direction=firestore.Query.DESCENDING)
                       .limit(50))
        data = []
        for r in readings_ref.stream():
            doc = r.to_dict()
            created = doc.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M") if hasattr(created, 'strftime') else str(created)
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "createdAt": created_str,
            })
    except: data = []
    
    return render_template("control.html", readings=data[-10:], summary="Feeder Control")

# =========================
# PDF EXPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if not db: return jsonify({"error": "No DB"}), 500
    
    try:
        now = datetime.utcnow()
        ago = now - timedelta(hours=24)
        readings_ref = (db.collection("devices").document("ESP32001")
                       .collection("readings")
                       .where("createdAt", ">=", ago)
                       .order_by("createdAt"))
        
        data = []
        for r in readings_ref.stream():
            doc = r.to_dict()
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "createdAt": doc.get("createdAt")
            })

        pdf_buffer = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        
        elements.append(Paragraph("🐟 Smart Fish Feeder - 24hr Report", styles["Title"]))
        elements.append(Paragraph(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]))
        elements.append(Spacer(1, 12))
        
        table_data = [["Time", "Temp (°C)", "pH"]]
        for r in data[-20:]:
            time_str = r["createdAt"].strftime("%H:%M") if hasattr(r["createdAt"], 'strftime') else "N/A"
            table_data.append([
                time_str,
                f"{r['temperature']:.1f}" if r['temperature'] else "-",
                f"{r['ph']:.1f}" if r['ph'] else "-"
            ])
        
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1f77b4")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, as_attachment=True, 
                        download_name=f"fishfeeder_{now.strftime('%Y%m%d')}.pdf")
    except: return jsonify({"error": "PDF failed"}), 500

# =========================
# ESP32 API - PUBLIC SENSOR DATA
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    if not db:
        return jsonify({"status": "error", "message": "No database"}), 500
    
    try:
        data = request.get_json() or {}
        deviceid = data.get("deviceid", "ESP32001")
        
        doc_ref = (db.collection("devices")
                  .document(deviceid)
                  .collection("readings")
                  .document())
        
        doc_ref.set({
            "temperature": to_float_or_none(data.get("temperature")),
            "ph": to_float_or_none(data.get("ph")),
            "ammonia": to_float_or_none(data.get("ammonia")),
            "turbidity": normalize_turbidity(data.get("turbidity")),
            "distance": to_float_or_none(data.get("distance")),
            "createdAt": datetime.utcnow()
        })
        
        return jsonify({"status": "success", "device": deviceid})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# ESP32 CONTROL API (Protected)
# =========================
@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if not db: return jsonify({"error": "No DB"}), 500
    
    try:
        data = request.get_json() or {}
        action = data.get("action")
        speed = max(0, min(100, int(data.get("speed", 0))))
        
        status = "off" if speed == 0 else "on"
        db.collection("devices").document("ESP32001").set({
            "feederstatus": status,
            "feederspeed": speed,
            "updatedAt": datetime.utcnow()
        }, merge=True)
        
        return jsonify({"status": "success", "speed": speed, "status": status})
    except: return jsonify({"error": "Control failed"}), 500

@app.route("/getfeederstatus", methods=["GET"])
@api_login_required
def getfeederstatus():
    if not db: return jsonify({"speed": 0, "status": "off"})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        if doc.exists:
            data = doc.to_dict()
            return jsonify({
                "speed": data.get("feederspeed", 0),
                "status": data.get("feederstatus", "off")
            })
    except: pass
    return jsonify({"speed": 0, "status": "off"})

# =========================
# HEALTH CHECKS
# =========================
@app.route("/ping")
def ping(): return jsonify({"status": "ok", "db": db is not None})

@app.route("/testdb")
def testdb():
    if not db: return jsonify({"status": "error", "db": False})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        return jsonify({"status": "ok", "exists": doc.exists if doc else False})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# =========================
# ROOT - SIMPLE DASHBOARD LINK
# =========================
@app.route("/")
def home():
    return '''
    <h1>🐟 Smart Fish Feeder</h1>
    <a href="/login">Login → Dashboard</a><br><br>
    <h3>ESP32 Test:</h3>
    <a href="/ping">/ping</a> | <a href="/testdb">/testdb</a>
    '''

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "False").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
