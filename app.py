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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-dev-2026")
CORS(app)

def init_firebase():
    if firebase_admin._apps: 
        return firestore.client()
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
        if "user" not in session: 
            return redirect(url_for("login"))
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
def home(): 
    return redirect(url_for("login"))

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "firebase": db is not None})

@app.route("/addreading", methods=["POST"])
def addreading():
    try:
        data = request.get_json() or {}
        deviceid = data.get("deviceid", "ESP32001")
        if db:
            db.collection("devices").document(deviceid).collection("readings").add({
                "deviceid": deviceid, "temperature": to_float_or_none(data.get("temperature")),
                "ph": to_float_or_none(data.get("ph")), "ammonia": to_float_or_none(data.get("ammonia")),
                "turbidity": normalize_turbidity(data.get("turbidity")), 
                "distance": to_float_or_none(data.get("distance")),
                "createdAt": datetime.utcnow()
            })
        return jsonify({"status": "success"}), 200
    except:
        return jsonify({"status": "success"}), 200

@app.route("/dashboard")
@login_required
def dashboard():
    readings = []; error = None; status = "🟡 Offline mode"
    timelabels, tempvalues, phvalues, ammoniavalues, turbidityvalues = [], [], [], [], []
    
    if db:
        try:
            ref = (db.collection("devices").document("ESP32001").collection("readings")
                   .order_by("createdAt", direction=firestore.Query.DESCENDING).limit(20))
            for r in ref.stream():
                d = r.to_dict()
                ts = d.get("createdAt")
                if isinstance(ts, datetime):
                    timelabels.append(ts.strftime("%H:%M"))
                    tempvalues.append(d.get("temperature") or 0)
                    phvalues.append(d.get("ph") or 0)
                    ammoniavalues.append(d.get("ammonia") or 0)
                    turbidityvalues.append(normalize_turbidity(d.get("turbidity")) or 0)
                    readings.append({
                        "createdAt": ts.strftime("%H:%M"),
                        "temperature": d.get("temperature"),
                        "ph": d.get("ph"),
                        "ammonia": d.get("ammonia"),
                        "turbidity": normalize_turbidity(d.get("turbidity"))
                    })
            status = "🟢 Online - Live Data"
        except Exception as e:
            error = f"Load failed: {str(e)}"
    
    summary = readings and "✅ Sensors: Live Data" or "🔄 Waiting for ESP32 data..."
    alertcolor = readings and "#28a745" or "#ffc107"
    
    return render_template("dashboard.html", 
                         readings=readings, error=error, firebase_status=status,
                         summary=summary, alertcolor=alertcolor,
                         timelabels=timelabels, tempvalues=tempvalues,
                         phvalues=phvalues, ammoniavalues=ammoniavalues,
                         turbidityvalues=turbidityvalues)

# Control routes (feeder, motor, schedule)
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
            update["feederstatus"] = "on"; update["feederspeed"] = speed
        elif action == "off":
            update["feederstatus"] = "off"; update["feederspeed"] = 0
        elif action == "setspeed":
            update["feederstatus"] = "on"; update["feederspeed"] = speed
        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success"})
    except: 
        return jsonify({"error": "Control failed"}), 500

@app.route("/getfeedingstatus")
@login_required
def getfeedingstatus():
    if not db: return jsonify({"feederstatus": "off", "feederspeed": 0})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        data = doc.to_dict() if doc.exists else {}
        return jsonify({
            "feederstatus": data.get("feederstatus", "off"),
            "feederspeed": data.get("feederspeed", 0)
        })
    except: return jsonify({"feederstatus": "off", "feederspeed": 0})

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
            update["motorstatus"] = "on"; update["motorspeed"] = speed
        elif action == "off":
            update["motorstatus"] = "off"; update["motorspeed"] = 0
        elif action == "setspeed":
            update["motorstatus"] = "on"; update["motorspeed"] = speed
        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success"})
    except: return jsonify({"error": "Control failed"}), 500

@app.route("/getmotorstatus")
@login_required
def getmotorstatus():
    if not db: return jsonify({"motorstatus": "off", "motorspeed": 0})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        data = doc.to_dict() if doc.exists else {}
        return jsonify({
            "motorstatus": data.get("motorstatus", "off"),
            "motorspeed": data.get("motorspeed", 0)
        })
    except: return jsonify({"motorstatus": "off", "motorspeed": 0})

@app.route("/exportpdf")
@login_required
def exportpdf():
    if not db: return "No database", 503
    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        elements = [Paragraph("🐟 Smart Fish Feeder Report", styles["Heading1"]), Spacer(1, 0.2*inch)]
        
        table_data = [["Time", "Temp°C", "pH", "NH₃", "Turbidity"]]
        elements.append(Table(table_data))
        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name="fish-report.pdf")
    except: return "PDF failed", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
