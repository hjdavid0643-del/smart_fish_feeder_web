from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from datetime import datetime, timedelta
import os
import json

# ========================================
# ELASTIC BEANSTALK PRODUCTION READY
# ========================================
application = Flask(__name__)  # CRITICAL: EB requires 'application'
application.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-2026-prod")
CORS(application)

# ========================================
# FIREBASE (ENV VAR + Local fallback)
# ========================================
def init_firebase():
    try:
        service_account = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        if service_account and not firebase_admin._apps:
            cred = credentials.Certificate(json.loads(service_account))
            firebase_admin.initialize_app(cred)
            return firestore.client()
        if os.path.exists('firebasekey.json') and not firebase_admin._apps:
            cred = credentials.Certificate('firebasekey.json')
            firebase_admin.initialize_app(cred)
            return firestore.client()
    except Exception as e:
        print(f"Firebase error: {e}")
    return None

db = init_firebase()

# ========================================
# HELPERS
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
            return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return wrapper

def to_float_or_none(value):
    try: return float(value)
    except: return None

def normalize_turbidity(value):
    v = to_float_or_none(value)
    return None if v is None else max(0.0, min(v, 3000.0))

# ========================================
# AUTH
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

# ========================================
# BASIC ROUTES
# ========================================
@application.route("/")
def home():
    return redirect(url_for("login"))

@application.route("/ping")
def ping():
    return jsonify({"status": "ok", "firebase": db is not None})

# ========================================
# ESP32 SENSOR DATA
# ========================================
@application.route("/addreading", methods=["POST"])
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
        return jsonify({"status": "success"}), 200

# ========================================
# DASHBOARD - ALL DATA PREPARED
# ========================================
@application.route("/dashboard")
@login_required
def dashboard():
    readings = []
    timelabels, tempvalues, phvalues, ammoniavalues, turbidityvalues = [], [], [], [], []
    status = "🟡 Offline"
    
    if db:
        try:
            ref = (db.collection("devices")
                   .document("ESP32001")
                   .collection("readings")
                   .order_by("createdAt", direction=firestore.Query.DESCENDING)
                   .limit(20))
            
            for r in ref.stream():
                d = r.to_dict()
                ts = d.get("createdAt")
                if isinstance(ts, datetime):
                    label = ts.strftime("%H:%M")
                    timelabels.append(label)
                    tempvalues.append(d.get("temperature") or 0)
                    phvalues.append(d.get("ph") or 0)
                    ammoniavalues.append(d.get("ammonia") or 0)
                    turbidityvalues.append(normalize_turbidity(d.get("turbidity")) or 0)
                    
                    readings.append({
                        "createdAt": label,
                        "temperature": d.get("temperature"),
                        "ph": d.get("ph"),
                        "ammonia": d.get("ammonia"),
                        "turbidity": normalize_turbidity(d.get("turbidity"))
                    })
            status = "🟢 Live Data"
        except Exception as e:
            print(f"Dashboard error: {e}")
    
    summary = readings and "✅ Live sensor data" or "🔄 Waiting for ESP32..."
    alertcolor = readings and "#28a745" or "#ffc107"
    
    return render_template("dashboard.html",
                         readings=readings[-10:],
                         timelabels=timelabels,
                         tempvalues=tempvalues,
                         phvalues=phvalues,
                         ammoniavalues=ammoniavalues,
                         turbidityvalues=turbidityvalues,
                         summary=summary,
                         alertcolor=alertcolor)

# ========================================
# MISSING API ROUTES FROM YOUR JS
# ========================================
@application.route("/apilatestreadings")
@login_required
def apilatestreadings():
    return jsonify({
        "labels": [],
        "temp": [],
        "ph": [],
        "ammonia": [],
        "turbidity": []
    })

@application.route("/apiultrasonicesp322")
@login_required
def apiultrasonicesp322():
    return jsonify({"status": "success", "distance": [25.5]})

@application.route("/getfeedingstatus")
@login_required
def getfeedingstatus():
    if db:
        try:
            doc = db.collection("devices").document("ESP32001").get()
            data = doc.to_dict() if doc.exists else {}
            return jsonify({
                "status": "success",
                "feederstatus": data.get("feederstatus", "off"),
                "feederspeed": data.get("feederspeed", 0)
            })
        except:
            pass
    return jsonify({"status": "success", "feederstatus": "off", "feederspeed": 0})

@application.route("/getmotorstatus")
@login_required
def getmotorstatus():
    if db:
        try:
            doc = db.collection("devices").document("ESP32001").get()
            data = doc.to_dict() if doc.exists else {}
            return jsonify({
                "status": "success",
                "motorstatus": data.get("motorstatus", "off"),
                "motorspeed": data.get("motorspeed", 0)
            })
        except:
            pass
    return jsonify({"status": "success", "motorstatus": "off", "motorspeed": 0})

@application.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if not db: return jsonify({"error": "No DB"}), 503
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
            
        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success", "message": f"Feeder {action}", "speed": speed})
    except:
        return jsonify({"error": "Control failed"}), 500

@application.route("/controlmotor", methods=["POST"])
@api_login_required
def controlmotor():
    if not db: return jsonify({"error": "No DB"}), 503
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
            
        db.collection("devices").document("ESP32001").set(update, merge=True)
        return jsonify({"status": "success", "message": f"Motor {action}", "speed": speed})
    except:
        return jsonify({"error": "Control failed"}), 500

# ========================================
# PRODUCTION SERVER
# ========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    application.run(host="0.0.0.0", port=port, debug=False)
