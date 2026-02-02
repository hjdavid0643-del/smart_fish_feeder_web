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

# ========================================
# FIXED: EB REQUIRES 'application'
# ========================================
application = Flask(__name__)  # ✅ FIXED
application.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-prod-2026")
CORS(application)

# ========================================
# FIXED FIREBASE - ENV VAR ONLY
# ========================================
def init_firebase():
    try:
        # ✅ FIXED: No hardcoded path
        service_account = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if service_account and not firebase_admin._apps:
            cred = credentials.Certificate(json.loads(service_account))
            firebase_admin.initialize_app(cred)
            return firestore.client()
        # Local fallback only
        if os.path.exists('firebasekey.json') and not firebase_admin._apps:
            cred = credentials.Certificate('firebasekey.json')
            firebase_admin.initialize_app(cred)
            return firestore.client()
    except Exception as e:
        print(f"Firebase init error: {e}")
    return None

db = init_firebase()

# ========================================
# YOUR HELPERS (MINOR IMPROVEMENTS)
# ========================================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def api_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

def to_float_or_none(value):
    try: return float(value)
    except (TypeError, ValueError): return None

def normalize_turbidity(value):
    v = to_float_or_none(value)
    return None if v is None else max(0.0, min(v, 3000.0))

# ========================================
# YOUR ROUTES - ALL FIXED (@app → @application)
# ========================================
VALID_USERS = {"hjdavid0643@iskwela.psau.edu.ph": "0123456789"}

@application.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").lower()
        password = request.form.get("password", "")
        if VALID_USERS.get(email) == password:
            session["user"] = email
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="❌ Invalid credentials")
    return render_template("login.html")

@application.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@application.route("/")
def home():
    return redirect(url_for("login"))

@application.route("/ping")
def ping():
    return jsonify({"status": "ok", "firebase": db is not None})

@application.route("/addreading", methods=["POST"])
def addreading():
    try:
        data = request.get_json() or {}
        deviceid = data.get("deviceid", "ESP32001")
        if db:
            db.collection("devices").document(deviceid).collection("readings").add({
                "temperature": to_float_or_none(data.get("temperature")),
                "ph": to_float_or_none(data.get("ph")),
                "ammonia": to_float_or_none(data.get("ammonia")),
                "turbidity": normalize_turbidity(data.get("turbidity")),
                "distance": to_float_or_none(data.get("distance")),
                "createdAt": datetime.utcnow(),
            })
        return jsonify({"status": "success"}), 200
    except:
        return jsonify({"status": "success"}), 200  # ESP32 doesn't care

@application.route("/dashboard")
@login_required
def dashboard():
    if not db:
        return render_template("dashboard.html", readings=[])
    
    try:
        ref = (db.collection("devices")
               .document("ESP32001")
               .collection("readings")
               .order_by("createdAt", direction=firestore.Query.DESCENDING)
               .limit(50))
        
        data = []
        for r in ref.stream():
            d = r.to_dict()
            ts = d.get("createdAt")
            data.append({
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": normalize_turbidity(d.get("turbidity")),
                "createdAt": ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else "",
            })
        data.reverse()
        return render_template("dashboard.html", readings=data)
    except ResourceExhausted:
        return render_template("dashboard.html", readings=[], error="Quota exceeded")
    except Exception as e:
        return render_template("dashboard.html", readings=[], error=f"Load error: {e}")

@application.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if not db: return jsonify({"error": "Firestore not ready"}), 500
    
    try:
        data = request.get_json() or {}
        action = data.get("action")
        speed = max(0, min(100, int(data.get("speed", 0))))
        
        update = {"updatedAt": datetime.utcnow()}
        if action == "on":
            update.update({"feederstatus": "on", "feederspeed": speed})
        elif action == "off":
            update.update({"feederstatus": "off", "feederspeed": 0})
        else:
            return jsonify({"error": "Invalid action"}), 400
            
        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success"})
    except:
        return jsonify({"error": "Control failed"}), 500

@application.route("/exportpdf")
@login_required
def exportpdf():
    if not db:
        return jsonify({"error": "Firestore not ready"}), 500
    
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
        
        elements = [Paragraph("🐟 Fish Feeder Water Quality Report", styles["Heading1"]), Spacer(1, 0.2*inch)]
        
        # ✅ FIXED: Handle empty data
        if data:
            table_data = [["Time", "Temp°C", "pH", "NH₃", "Turbidity"]]
            for r in data:
                ts = r.get("createdAt")
                t = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else "N/A"
                table_data.append([
                    t, r.get("temperature", "N/A"), r.get("ph", "N/A"),
                    r.get("ammonia", "N/A"), normalize_turbidity(r.get("turbidity", "N/A"))
                ])
            table = Table(table_data, repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0,0), (-1,-1), 0.5, colors.black),
                ("BACKGROUND", (0,0), (-1,0), colors.lightblue),
            ]))
            elements.append(table)
        else:
            elements.append(Paragraph("No data available for last 24 hours", styles["Normal"]))
        
        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name="fish-report.pdf")
    except:
        return jsonify({"error": "PDF generation failed"}), 500

# ========================================
# PRODUCTION SERVER - FIXED
# ========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # ✅ FIXED: Dynamic port
    application.run(host="0.0.0.0", port=port, debug=False)  # ✅ FIXED: debug=False
