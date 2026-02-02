
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
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch

# =========================
# APP CONFIG
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-dev-2026")
CORS(app)

# =========================
# UNIVERSAL ERROR HANDLER
# =========================
@app.errorhandler(500)
def internal_error(error):
    return "🐟 Server temporarily down. ESP32 still works!", 500

@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"ERROR: {str(e)}")
    return jsonify({"status": "error", "message": "Server busy"}), 500

# =========================
# FIREBASE - RENDER + LOCAL
# =========================
def init_firebase():
    if firebase_admin._apps: 
        return firestore.client()
    
    try:
        # 1. Render Environment Variable (PRODUCTION)
        service_account = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if service_account:
            cred = credentials.Certificate(json.loads(service_account))
            firebase_admin.initialize_app(cred)
            return firestore.client()
        
        # 2. Local firebasekey.json (DEVELOPMENT)
        if os.path.exists('firebasekey.json'):
            cred = credentials.Certificate('firebasekey.json')
            firebase_admin.initialize_app(cred)
            return firestore.client()
            
    except Exception as e:
        app.logger.error(f"Firebase failed: {e}")
    
    return None  # Graceful offline mode

db = init_firebase()

# =========================
# HELPERS
# =========================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session: return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def api_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session: 
            return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return wrapper

def to_float_or_none(value):
    try: return float(value)
    except: return None

def normalize_turbidity(value):
    v = to_float_or_none(value)
    return None if v is None else max(0.0, min(v, 3000.0))

# =========================
# AUTH
# =========================
VALID_USERS = {"hjdavid0643@iskwela.psau.edu.ph": "0123456789"}

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").lower()
        password = request.form.get("password", "")
        if VALID_USERS.get(email) == password:
            session["user"] = email
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="❌ Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# BASIC ROUTES
# =========================
@app.route("/")
def home(): return redirect(url_for("login"))

@app.route("/ping")
def ping():
    return jsonify({
        "status": "ok", 
        "firebase": db is not None,
        "timestamp": datetime.utcnow().isoformat()
    })

# =========================
# ESP32 SENSOR DATA
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    try:
        data = request.get_json() or {}
        deviceid = data.get("deviceid", "ESP32001")
        
        # Always respond 200 to ESP32 (don't break sensors)
        if db:
            db.collection("devices").document(deviceid).collection("readings").add({
                "deviceid": deviceid,
                "temperature": to_float_or_none(data.get("temperature")),
                "ph": to_float_or_none(data.get("ph")),
                "ammonia": to_float_or_none(data.get("ammonia")),
                "turbidity": normalize_turbidity(data.get("turbidity")),
                "distance": to_float_or_none(data.get("distance")),
                "createdAt": datetime.utcnow(),
            })
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        app.logger.error(f"addreading failed: {e}")
        return jsonify({"status": "success"}), 200  # ESP32 needs 200!

# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    readings = []
    error_msg = None
    
    if db:
        try:
            ref = (db.collection("devices")
                   .document("ESP32001")
                   .collection("readings")
                   .order_by("createdAt", direction=firestore.Query.DESCENDING)
                   .limit(50))
            
            for r in ref.stream():
                d = r.to_dict()
                ts = d.get("createdAt")
                readings.append({
                    "temperature": d.get("temperature"),
                    "ph": d.get("ph"),
                    "ammonia": d.get("ammonia"),
                    "turbidity": normalize_turbidity(d.get("turbidity")),
                    "createdAt": ts.strftime("%H:%M") if isinstance(ts, datetime) else "",
                })
            readings.reverse()
            
        except ResourceExhausted:
            error_msg = "Quota exceeded - showing cache"
        except Exception as e:
            error_msg = f"Data load failed: {str(e)}"
    
    status = "🟢 Online" if db else "🟡 Offline mode"
    return render_template("dashboard.html", 
                         readings=readings, 
                         error=error_msg,
                         firebase_status=status)

# =========================
# FEEDER CONTROL
# =========================
@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if not db: return jsonify({"error": "No database"}), 503
    
    try:
        data = request.get_json() or {}
        action = data.get("action")
        speed = max(0, min(100, int(data.get("speed", 0))))
        
        update = {"updatedAt": datetime.utcnow()}
        if action == "on":
            update["feederstatus"] = "on"
            update["feederspeed"] = speed
        elif action == "off":
            update["feederstatus"] = "off"
            update["feederspeed"] = 0
        else:
            return jsonify({"error": "Invalid action"}), 400

        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success"})
    except:
        return jsonify({"error": "Control failed"}), 500

# =========================
# PDF REPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if not db: return jsonify({"error": "No database"}), 503
    
    try:
        now = datetime.utcnow()
        since = now - timedelta(hours=24)
        ref = (db.collection("devices")
               .document("ESP32001")
               .collection("readings")
               .where("createdAt", ">=", since)
               .order_by("createdAt"))
        
        data = [r.to_dict() for r in ref.stream()]
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        
        elements = [
            Paragraph("🐟 Smart Fish Feeder - 24hr Report", styles["Heading1"]),
            Spacer(1, 0.2*inch)
        ]
        
        table_data = [["Time", "Temp°C", "pH", "NH₃(ppm)", "Turbidity"]]
        for r in data:
            t = r["createdAt"].strftime("%H:%M")
            table_data.append([
                t, r.get("temperature") or 0, r.get("ph") or 0,
                r.get("ammonia") or 0, r.get("turbidity") or 0
            ])
        
        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.5, colors.black),
            ("BACKGROUND", (0,0), (-1,0), colors.lightblue)
        ]))
        elements.append(table)
        doc.build(elements)
        
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name="fish-report.pdf")
        
    except Exception as e:
        app.logger.error(f"PDF failed: {e}")
        return jsonify({"error": "Report failed"}), 500

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
