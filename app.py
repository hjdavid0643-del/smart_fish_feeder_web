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
from werkzeug.routing import BuildError

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-dev-2026")
CORS(app)

# ERROR HANDLERS - SHOW REAL ERRORS
@app.errorhandler(500)
def internal_error(error):
    return f"🐟 Error 500: {str(error)}<br>Check Render logs!", 500

@app.errorhandler(BuildError)
def handle_build_error(error):
    return "🔗 Template link error. Check login.html register link!", 500

def init_firebase():
    if firebase_admin._apps: return firestore.client()
    try:
        service_account = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if service_account:
            cred = credentials.Certificate(json.loads(service_account))
            firebase_admin.initialize_app(cred)
            return firestore.client()
        if os.path.exists('firebasekey.json'):
            cred = credentials.Certificate('firebasekey.json')
            firebase_admin.initialize_app(cred)
            return firestore.client()
    except Exception as e:
        print(f"Firebase failed: {e}")
    return None

db = init_firebase()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session: return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def api_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session: return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return wrapper

def to_float_or_none(value):
    try: return float(value)
    except: return None

def normalize_turbidity(value):
    v = to_float_or_none(value)
    return None if v is None else max(0.0, min(v, 3000.0))

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

@app.route("/")
def home(): return redirect(url_for("login"))

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "firebase": db is not None})

# ESP32 SENSOR DATA
@app.route("/addreading", methods=["POST"])
def addreading():
    try:
        data = request.get_json() or {}
        deviceid = data.get("deviceid", "ESP32001")
        if db:
            db.collection("devices").document(deviceid).collection("readings").add({
                "deviceid": deviceid,
                "temperature": to_float_or_none(data.get("temperature")),
                "ph": to_float_or_none(data.get("ph")),
                "ammonia": to_float_or_none(data.get("ammonia")),
                "turbidity": normalize_turbidity(data.get("turbidity")),
                "distance": to_float_or_none(data.get("distance")),
                "createdAt": datetime.utcnow()
            })
        return jsonify({"status": "success"}), 200
    except:
        return jsonify({"status": "success"}), 200  # ESP32 always gets 200

# DASHBOARD CHART DATA
@app.route("/apilatestreadings")
@login_required
def apilatestreadings():
    if not db: 
        return jsonify({"labels": [], "temp": [], "ph": [], "ammonia": [], "turbidity": []})
    try:
        ref = (db.collection("devices").document("ESP32001").collection("readings")
               .order_by("createdAt", direction=firestore.Query.DESCENDING).limit(50))
        readings = [r.to_dict() for r in ref.stream()]
        readings.reverse()
        
        labels, temp, ph, ammonia, turbidity = [], [], [], [], []
        for r in readings[-20:]:
            ts = r.get("createdAt")
            labels.append(ts.strftime("%H:%M") if isinstance(ts, datetime) else "")
            temp.append(r.get("temperature") or 0)
            ph.append(r.get("ph") or 0)
            ammonia.append(r.get("ammonia") or 0)
            turbidity.append(r.get("turbidity") or 0)
        
        return jsonify({"labels": labels, "temp": temp, "ph": ph, 
                       "ammonia": ammonia, "turbidity": turbidity})
    except:
        return jsonify({"labels": [], "temp": [], "ph": [], "ammonia": [], "turbidity": []})

@app.route("/historical")
@login_required
def historical():
    if not db: return jsonify({"status": "error", "readings": []})
    try:
        ref = (db.collection("devices").document("ESP32001").collection("readings")
               .order_by("createdAt", direction=firestore.Query.DESCENDING).limit(100))
        readings = []
        for r in ref.stream():
            d = r.to_dict()
            ts = d.get("createdAt")
            readings.append({
                "createdAt": ts.strftime("%Y-%m-%d %H:%M") if isinstance(ts, datetime) else "",
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": d.get("turbidity")
            })
        readings.reverse()
        return jsonify({"status": "success", "readings": readings[:50]})
    except: return jsonify({"status": "error", "readings": []})

# FEEDER CONTROL
@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if not db: return jsonify({"error": "No database"}), 503
    try:
        data = request.get_json() or {}
        action = data.get("action")
        speed = max(0, min(100, int(data.get("speed", 50))))
        update = {"updatedAt": datetime.utcnow()}
        
        if action == "on":
            update["feederstatus"] = "on"
            update["feederspeed"] = speed
        elif action == "off":
            update["feederstatus"] = "off"
            update["feederspeed"] = 0
        elif action == "setspeed":
            update["feederstatus"] = "on"
            update["feederspeed"] = speed
        else:
            return jsonify({"error": "Invalid action"}), 400
        
        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success", "message": "Feeder updated"})
    except: return jsonify({"error": "Control failed"}), 500

@app.route("/getfeedingstatus")
@login_required
def getfeedingstatus():
    if not db: 
        return jsonify({"status": "error", "feederstatus": "off", "feederspeed": 0})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        data = doc.to_dict() if doc.exists else {}
        return jsonify({
            "status": "success",
            "feederstatus": data.get("feederstatus", "off"),
            "feederspeed": data.get("feederspeed", 0)
        })
    except: 
        return jsonify({"status": "error", "feederstatus": "off", "feederspeed": 0})

# MOTOR CONTROL
@app.route("/controlmotor", methods=["POST"])
@api_login_required
def controlmotor():
    if not db: return jsonify({"error": "No database"}), 503
    try:
        data = request.get_json() or {}
        action = data.get("action")
        speed = max(0, min(100, int(data.get("speed", 50))))
        update = {"updatedAt": datetime.utcnow()}
        
        if action == "on":
            update["motorstatus"] = "on"
            update["motorspeed"] = speed
        elif action == "off":
            update["motorstatus"] = "off"
            update["motorspeed"] = 0
        elif action == "setspeed":
            update["motorstatus"] = "on"
            update["motorspeed"] = speed
        else:
            return jsonify({"error": "Invalid action"}), 400
        
        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success", "message": "Motor updated"})
    except: return jsonify({"error": "Control failed"}), 500

@app.route("/getmotorstatus")
@login_required
def getmotorstatus():
    if not db: 
        return jsonify({"status": "error", "motorstatus": "off", "motorspeed": 0})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        data = doc.to_dict() if doc.exists else {}
        return jsonify({
            "status": "success",
            "motorstatus": data.get("motorstatus", "off"),
            "motorspeed": data.get("motorspeed", 0)
        })
    except: 
        return jsonify({"status": "error", "motorstatus": "off", "motorspeed": 0})

# SCHEDULE CONTROL
@app.route("/savefeedingschedule", methods=["POST"])
@api_login_required
def savefeedingschedule():
    if not db: return jsonify({"error": "No database"}), 503
    try:
        data = request.get_json() or {}
        schedule = {
            "firstfeed": data.get("firstfeed", ""),
            "secondfeed": data.get("secondfeed", ""),
            "duration": int(data.get("duration", 5)),
            "enabled": True,
            "updatedAt": datetime.utcnow()
        }
        db.collection("devices").document("ESP32001").set({"schedule": schedule}, merge=True)
        return jsonify({"status": "success", "message": "Schedule saved"})
    except: return jsonify({"error": "Save failed"}), 500

@app.route("/getfeedingscheduleinfo")
@login_required
def getfeedingscheduleinfo():
    if not db: return jsonify({"status": "error", "schedule": {}, "enabled": False})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        data = doc.to_dict() if doc.exists else {}
        schedule = data.get("schedule", {})
        return jsonify({
            "status": "success",
            "schedule": schedule,
            "enabled": schedule.get("enabled", False)
        })
    except: return jsonify({"status": "error", "schedule": {}, "enabled": False})

@app.route("/apiultrasonicesp322")
@login_required
def apiultrasonicesp322():
    if not db: return jsonify({"status": "error", "distance": []})
    try:
        ref = (db.collection("devices").document("ESP32001").collection("readings")
               .order_by("createdAt", direction=firestore.Query.DESCENDING).limit(10))
        distances = [r.to_dict().get("distance", 0) for r in ref.stream()]
        return jsonify({"status": "success", "distance": distances})
    except: return jsonify({"status": "error", "distance": []})

# FIXED DASHBOARD - ALL CHART DATA INCLUDED
@app.route("/dashboard")
@login_required
def dashboard():
    readings = []
    error = None
    status = "🟡 Offline mode"
    
    # Chart data arrays - ALWAYS provided
    timelabels, tempvalues, phvalues, ammoniavalues, turbidityvalues = [], [], [], [], []
    
    if db:
        try:
            ref = (db.collection("devices").document("ESP32001").collection("readings")
                   .order_by("createdAt", direction=firestore.Query.DESCENDING).limit(20))
            
            for r in ref.stream():
                d = r.to_dict()
                ts = d.get("createdAt")
                if isinstance(ts, datetime):
                    # Table data
                    readings.append({
                        "createdAt": ts.strftime("%H:%M"),
                        "temperature": d.get("temperature"),
                        "ph": d.get("ph"),
                        "ammonia": d.get("ammonia"),
                        "turbidity": normalize_turbidity(d.get("turbidity"))
                    })
                    # Chart data
                    timelabels.append(ts.strftime("%H:%M"))
                    tempvalues.append(d.get("temperature") or 0)
                    phvalues.append(d.get("ph") or 0)
                    ammoniavalues.append(d.get("ammonia") or 0)
                    turbidityvalues.append(normalize_turbidity(d.get("turbidity")) or 0)
            
            readings.reverse()
            status = "🟢 Online - Live Data"
        except Exception as e:
            error = f"Load failed: {str(e)}"
    
    # Safe defaults
    summary = len(readings) and "✅ Sensors: Live Data" or "🔄 Waiting for ESP32..."
    alertcolor = len(readings) and "#28a745" or "#ffc107"
    
    return render_template("dashboard.html",
                         readings=readings,
                         error=error,
                         firebase_status=status,
                         summary=summary,
                         alertcolor=alertcolor,
                         timelabels=timelabels,
                         tempvalues=tempvalues,
                         phvalues=phvalues,
                         ammoniavalues=ammoniavalues,
                         turbidityvalues=turbidityvalues)

@app.route("/exportpdf")
@login_required
def exportpdf():
    if not db: return jsonify({"error": "No database"}), 503
    try:
        now = datetime.utcnow()
        since = now - timedelta(hours=24)
        ref = (db.collection("devices").document("ESP32001").collection("readings")
               .where("createdAt", ">=", since).order_by("createdAt"))
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
            t = r.get("createdAt", now).strftime("%H:%M") if hasattr(r.get("createdAt"), 'strftime') else "N/A"
            table_data.append([
                t,
                r.get("temperature") or 0,
                r.get("ph") or 0,
                r.get("ammonia") or 0,
                r.get("turbidity") or 0
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
    except: return jsonify({"error": "PDF failed"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
