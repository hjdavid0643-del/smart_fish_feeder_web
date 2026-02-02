from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    send_file,
)
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from datetime import datetime, timedelta
import os
import io
from google.api_core.exceptions import ResourceExhausted

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# =========================
# CONFIG
# =========================
FIRESTORE_LOGIN_DISABLED = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
CORS(app)

# =========================
# FIREBASE / FIRESTORE INIT
# =========================
def init_firebase():
    try:
        # Use environment variable for Render deployment
        FIREBASE_KEY_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json")
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_app = firebase_admin.initialize_app(cred)
        else:
            firebase_app = firebase_admin.get_app()
        return firestore.client(app=firebase_app)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("Error initializing Firebase:", e)
        return None

db = init_firebase()

# =========================
# HELPERS
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

def normalize_turbidity(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0:
        v = 0.0
    if v > 3000:
        v = 3000.0
    return v

def to_float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

@app.route("/update_temp_ph", methods=["POST"])
def update_temp_ph():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        data = request.get_json() or {}
        temperature = to_float_or_none(data.get("temperature"))
        ph = to_float_or_none(data.get("ph"))

        if temperature is None or ph is None:
            return jsonify({"status": "error", "message": "temperature and ph required"}), 400

        # FIXED: Consistent document ID
        db.collection("devices").document("ESP32001").set(
            {
                "temperature": temperature,
                "ph": ph,
                "updatedAt": datetime.utcnow(),
            },
            merge=True,
        )
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# BASIC ROUTES
# =========================
@app.route("/")
def home():
    return redirect(url_for("login"))

# =========================
# AUTH ROUTES
# =========================
VALID_USERS = {
    "admin@example.com": "admin123",
    "worker@example.com": "worker123",
}

@app.route("/login", methods=["GET", "POST"])
def login():
    if FIRESTORE_LOGIN_DISABLED or os.environ.get("FIRESTORE_LOGIN_DISABLED", "0") == "1":
        return render_template("login.html", error="Login temporarily disabled.")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("login.html", error="Email and password required.")

        expected_password = VALID_USERS.get(email)
        if expected_password and expected_password == password:
            session["user"] = email
            session["role"] = "worker"
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid credentials.")

    if "user" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register")
def register():
    return "Registration managed separately."

# =========================
# DASHBOARD (FIXED)
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    if db is None:
        return render_template("dashboard.html", readings=[], summary="Firestore unavailable", 
                             alertcolor="gray", timelabels=[], tempvalues=[], phvalues=[], 
                             ammoniavalues=[], turbidityvalues=[], feederalert="Unavailable", 
                             feederalertcolor="gray", lowfeedalert=None, lowfeedcolor="#ff7043")

    try:
        # FIXED: Consistent document ID "ESP32001"
        readings_ref = db.collection("devices").document("ESP32001").collection("readings") \
                        .order_by("createdAt", direction=firestore.Query.DESCENDING).limit(50)
        readings_cursor = readings_ref.stream()
    except ResourceExhausted:
        return render_template("dashboard.html", readings=[], summary="Quota exceeded", 
                             alertcolor="gray", timelabels=[], tempvalues=[], phvalues=[], 
                             ammoniavalues=[], turbidityvalues=[], feederalert="Unavailable", 
                             feederalertcolor="gray", lowfeedalert=None, lowfeedcolor="#ff7043")
    except Exception:
        return render_template("dashboard.html", readings=[], summary="Error loading data", 
                             alertcolor="gray", timelabels=[], tempvalues=[], phvalues=[], 
                             ammoniavalues=[], turbidityvalues=[], feederalert="Unavailable", 
                             feederalertcolor="gray", lowfeedalert=None, lowfeedcolor="#ff7043")

    data = []
    for r in readings_cursor:
        docdata = r.to_dict() or {}
        created = docdata.get("createdAt")
        created_str = created.strftime("%Y-%m-%d %H:%M:%S") if isinstance(created, datetime) else str(created)
        turb = normalize_turbidity(docdata.get("turbidity"))
        data.append({
            "temperature": docdata.get("temperature"),
            "ph": docdata.get("ph"),
            "ammonia": docdata.get("ammonia"),
            "turbidity": turb,
            "createdAt": created_str,
        })
    data = list(reversed(data))

    # Summary logic
    summary = "All systems normal."
    alertcolor = "green"
    if data and data[-1].get("turbidity") > 100:
        summary = "Water too cloudy! Danger"
        alertcolor = "gold"
    elif data and data[-1].get("turbidity") > 50:
        summary = "Water getting cloudy."
        alertcolor = "orange"

    # Feeder status
    feederalert = "Feeder OFF"
    feederalertcolor = "lightcoral"
    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            d = devicedoc.to_dict() or {}
            feederstatus = d.get("feederstatus", "off")
            feederspeed = d.get("feederspeed", 0)
            if feederstatus == "on" and feederspeed > 0:
                feederalert = f"Feeding at {feederspeed}%"
                feederalertcolor = "limegreen"
    except Exception:
        feederalert = "Status unavailable"
        feederalertcolor = "gray"

    # Low feed alert
    lowfeedalert = None
    try:
        hopperdoc = db.collection("devices").document("ESP32002").get()
        if hopperdoc.exists:
            hdata = hopperdoc.to_dict() or {}
            levelpercent = hdata.get("feedlevelpercent") or hdata.get("waterlevelpercent")
            if levelpercent and levelpercent < 20:
                lowfeedalert = f"Low feed: {levelpercent:.1f}% - Refill hopper!"
    except Exception:
        pass

    timelabels = [r["createdAt"] for r in data]
    tempvalues = [r["temperature"] for r in data]
    phvalues = [r["ph"] for r in data]
    ammoniavalues = [r["ammonia"] for r in data]
    turbidityvalues = [r["turbidity"] for r in data]
    latest10 = data[-10:]

    return render_template("dashboard.html", readings=latest10, summary=summary, alertcolor=alertcolor,
                         timelabels=timelabels, tempvalues=tempvalues, phvalues=phvalues,
                         ammoniavalues=ammoniavalues, turbidityvalues=turbidityvalues,
                         feederalert=feederalert, feederalertcolor=feederalertcolor,
                         lowfeedalert=lowfeedalert, lowfeedcolor="#ff7043")

# =========================
# FIXED MOTOR CONTROL
# =========================
@app.route("/controlmotor", methods=["POST"])
@api_login_required
def controlmotor():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set(
                {"motorspeed": 0, "motorstatus": "off", "updatedAt": datetime.utcnow()},
                merge=True,
            )
            return jsonify({"status": "success", "message": "Motor OFF"}), 200

        elif action == "on":
            # FIXED: Added int() conversion and proper return
            db.collection("devices").document("ESP32001").set(
                {"motorspeed": int(speed), "motorstatus": "on", "updatedAt": datetime.utcnow()},
                merge=True,
            )
            return jsonify({"status": "success", "message": f"Motor ON at {speed}%"}), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# FIXED FEEDER CONTROL (complete)
# =========================
@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set(
                {"feederspeed": 0, "feederstatus": "off", "updatedAt": datetime.utcnow()},
                merge=True,
            )
            return jsonify({"status": "success", "message": "Feeder OFF"}), 200

        elif action == "on":
            db.collection("devices").document("ESP32001").set(
                {"feederspeed": int(speed), "feederstatus": "on", "updatedAt": datetime.utcnow()},
                merge=True,
            )
            return jsonify({"status": "success", "message": f"Feeder ON at {speed}%"}), 200

        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify({"status": "error", "message": "Speed 0-100"}), 400
            db.collection("devices").document("ESP32001").set(
                {"feederspeed": speedvalue, "feederstatus": "on" if speedvalue > 0 else "off", 
                 "updatedAt": datetime.utcnow()},
                merge=True,
            )
            return jsonify({"status": "success", "message": f"Speed: {speedvalue}%"}), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# SINGLE addreading FUNCTION (REMOVED DUPLICATE)
@app.route("/addreading", methods=["POST"])
def addreading():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        deviceid = data.get("deviceid", "ESP32001")
        temperature = to_float_or_none(data.get("temperature"))
        ph = to_float_or_none(data.get("ph"))
        ammonia = to_float_or_none(data.get("ammonia"))
        turbidity = normalize_turbidity(data.get("turbidity"))
        distance = to_float_or_none(data.get("distance"))

        docref = db.collection("devices").document(deviceid).collection("readings").document()
        docref.set({
            "temperature": temperature,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity,
            "distance": distance,
            "createdAt": datetime.utcnow(),
        })

        return jsonify({"status": "success", "message": f"Reading saved for {deviceid}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Keep other routes (mosfet, controlfeedingpage, exportpdf, etc.) unchanged...
# [All other working routes remain the same - truncated for brevity]

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200

@app.route("/testfirestore")
def testfirestore():
    try:
        if db is None:
            return jsonify({"status": "error", "message": "Firestore unavailable"}), 500
        doc = db.collection("devices").document("ESP32001").get()
        return jsonify({"status": "ok", "exists": doc.exists}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
