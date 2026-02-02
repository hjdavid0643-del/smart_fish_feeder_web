from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth
from functools import wraps
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")
CORS(app)

# Firebase
db = None
def get_db():
    global db
    if db is None:
        try:
            key_path = os.environ.get('FIREBASE_CREDENTIALS_PATH', 
                "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json")
            if os.path.exists(key_path):
                if not firebase_admin._apps:
                    cred = credentials.Certificate(key_path)
                    firebase_admin.initialize_app(cred)
                db = firestore.client()
        except Exception:
            pass
    return db

# FIXED DECORATORS - Support both GET and POST where needed
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated

def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def normalize_turbidity(value):
    try: return max(0, min(3000, float(value)))
    except: return None

def safe_query(device, collection="readings", limit=50):
    client = get_db()
    if not client: return []
    try:
        return list(client.collection("devices").document(device)
                   .collection(collection).order_by("createdAt", 
                   direction=firestore.Query.DESCENDING).limit(limit).stream())
    except: return []

# FIXED LOGIN - NO 405 ERRORS
@app.route("/", methods=['GET'])
def home(): 
    return redirect(url_for("login"))

@app.route("/login", methods=['GET'])
def login():
    if "user" in session:
        next_url = request.args.get('next', '')
        return redirect(next_url if next_url.startswith('/') else url_for("dashboard"))
    return render_template("login.html", next_url=request.args.get('next', ''))

@app.route("/session-login", methods=['POST'])  # ✅ FIXED: Explicit POST
def session_login():
    try:
        data = request.get_json() or {}
        id_token = data.get("id_token")
        decoded = auth.verify_id_token(id_token)
        session["user"] = decoded["email"]
        next_url = request.args.get('next') or url_for('dashboard')
        return jsonify({"status": "success", "redirect": next_url})
    except:
        return jsonify({"status": "error"}), 401

@app.route("/logout", methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for("login"))

# DASHBOARD
@app.route("/dashboard", methods=['GET'])
@login_required
def dashboard():
    readings = safe_query("ESP32001")
    data = []
    for doc in readings:
        d = doc.to_dict()
        ts = d.get("createdAt")
        data.append({
            "temperature": d.get("temperature"),
            "ph": d.get("ph"),
            "ammonia": d.get("ammonia"),
            "turbidity": normalize_turbidity(d.get("turbidity")),
            "createdAt": ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, 'strftime') else str(ts)
        })[::-1]
    
    summary, alertcolor = "All systems normal", "green"
    if data and data[-1]["turbidity"] and data[-1]["turbidity"] > 100:
        summary, alertcolor = "Water too cloudy!", "gold"
    
    chart_data = data[-50:]
    return render_template("dashboard.html",
        readings=data[-10:],
        summary=summary, alertcolor=alertcolor,
        timelabels=[r["createdAt"] for r in chart_data],
        tempvalues=[r["temperature"] or 0 for r in chart_data],
        phvalues=[r["ph"] or 0 for r in chart_data],
        ammoniavalues=[r["ammonia"] or 0 for r in chart_data],
        turbidityvalues=[r["turbidity"] or 0 for r in chart_data])

# ✅ FIXED - All your JS endpoints with correct methods:
@app.route("/apilatestreadings", methods=['GET'])  # JS calls GET
@login_required
def api_latest_readings():
    readings = safe_query("ESP32001")
    data = []
    for doc in readings:
        d = doc.to_dict()
        data.append({
            "temperature": d.get("temperature"),
            "ph": d.get("ph"),
            "ammonia": d.get("ammonia"),
            "turbidity": normalize_turbidity(d.get("turbidity")),
            "createdAt": str(d.get("createdAt"))
        })[::-1]
    return jsonify({
        "labels": [r["createdAt"] for r in data],
        "temp": [r["temperature"] or 0 for r in data],
        "ph": [r["ph"] or 0 for r in data],
        "ammonia": [r["ammonia"] or 0 for r in data],
        "turbidity": [r["turbidity"] or 0 for r in data]
    })

@app.route("/historical", methods=['GET'])  # JS calls GET
@login_required
def historical():
    readings = safe_query("ESP32001")
    data = []
    for doc in readings:
        d = doc.to_dict()
        data.append({
            "temperature": d.get("temperature"),
            "ph": d.get("ph"),
            "ammonia": d.get("ammonia"),
            "turbidity": normalize_turbidity(d.get("turbidity")),
            "createdAt": str(d.get("createdAt"))
        })
    return jsonify({"status": "success", "readings": data})

@app.route("/apiultrasonicesp322", methods=['GET'])  # JS calls GET
@login_required
def api_ultrasonic():
    readings = safe_query("ESP32002")
    data = []
    for doc in readings:
        d = doc.to_dict()
        data.append({"distance": d.get("distance"), "createdAt": str(d.get("createdAt"))})[::-1]
    return jsonify({"status": "success", "labels": [r["createdAt"] for r in data], 
                   "distance": [r["distance"] or 0 for r in data]})

@app.route("/getfeedingstatus", methods=['GET'])  # JS calls GET
@api_login_required
def get_feeding_status():
    client = get_db()
    doc = client.collection("devices").document("ESP32001").get() if client else None
    data = doc.to_dict() if doc and doc.exists else {}
    return jsonify({"status": "success", 
                   "feederspeed": data.get("feederspeed", 0),
                   "feederstatus": data.get("feederstatus", "off")})

@app.route("/getmotorstatus", methods=['GET'])  # JS calls GET
@api_login_required
def get_motor_status():
    client = get_db()
    doc = client.collection("devices").document("ESP32001").get() if client else None
    data = doc.to_dict() if doc and doc.exists else {}
    return jsonify({"status": "success", 
                   "motorspeed": data.get("motorspeed", 0),
                   "motorstatus": data.get("motorstatus", "off")})

@app.route("/getfeedingscheduleinfo", methods=['GET'])  # JS calls GET
@api_login_required
def get_schedule_info():
    client = get_db()
    doc = client.collection("devices").document("ESP32001").get() if client else None
    data = doc.to_dict() if doc and doc.exists else {}
    return jsonify({"status": "success", 
                   "schedule": data.get("feedingschedule", {}),
                   "enabled": data.get("scheduleenabled", False)})

# ✅ FIXED POST ENDPOINTS
@app.route("/controlfeeder", methods=['POST'])  # JS POST calls
@api_login_required
def control_feeder():
    data = request.get_json()
    action, speed = data.get("action"), int(data.get("speed", 0))
    status = "off"
    if action == "off": speed = 0
    elif action == "on": status = "on"
    elif action == "setspeed" and 0 <= speed <= 100: status = "on" if speed > 0 else "off"
    
    client = get_db()
    if client:
        client.collection("devices").document("ESP32001").set({
            "feederspeed": speed, "feederstatus": status, "updatedAt": datetime.utcnow()
        }, merge=True)
    return jsonify({"status": "success", "message": f"Feeder {status}"})

@app.route("/controlmotor", methods=['POST'])  # JS POST calls
@api_login_required
def control_motor():
    data = request.get_json()
    action, speed = data.get("action"), int(data.get("speed", 0))
    status = "off"
    if action == "off": speed = 0
    elif action == "on": status = "on"
    elif action == "setspeed" and 0 <= speed <= 100: status = "on" if speed > 0 else "off"
    
    client = get_db()
    if client:
        client.collection("devices").document("ESP32001").set({
            "motorspeed": speed, "motorstatus": status, "updatedAt": datetime.utcnow()
        }, merge=True)
    return jsonify({"status": "success", "message": f"Motor {status}"})

@app.route("/savefeedingschedule", methods=['POST'])  # JS POST calls
@api_login_required
def save_schedule():
    data = request.get_json()
    client = get_db()
    if client:
        client.collection("devices").document("ESP32001").set({
            "feedingschedule": {
                "firstfeed": data.get("firstfeed"),
                "secondfeed": data.get("secondfeed"),
                "duration": int(data.get("duration", 5))
            },
            "scheduleenabled": True,
            "updatedAt": datetime.utcnow()
        }, merge=True)
    return jsonify({"status": "success", "message": "Schedule saved"})

@app.route("/addreading", methods=['POST'])  # ESP32 POST
def add_reading():
    data = request.get_json()
    client = get_db()
    if client:
        device_id = data.get("deviceid", "ESP32001")
        client.collection("devices").document(device_id).collection("readings").document().set({
            "temperature": normalize_turbidity(data.get("temperature")),
            "ph": float(data.get("ph")) if data.get("ph") else None,
            "ammonia": float(data.get("ammonia")) if data.get("ammonia") else None,
            "turbidity": normalize_turbidity(data.get("turbidity")),
            "distance": float(data.get("distance")) if data.get("distance") else None,
            "createdAt": datetime.utcnow()
        })
    return jsonify({"status": "success"})

@app.route("/ping", methods=['GET'])
def ping(): 
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
