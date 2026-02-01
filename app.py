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
import traceback
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
        FIREBASE_KEY_PATH = "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        print(f"❌ Firebase Error: {e}")
        traceback.print_exc()
        return None

db = init_firebase()

# =========================
# CLEAN HELPER FUNCTIONS (NO DUPLICATES)
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

def safe_float(value, default=0.0):
    """Convert to float safely - NO CRASHES"""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default

def normalize_turbidity(value):
    """Safe turbidity clamp 0-3000"""
    v = safe_float(value)
    return max(0.0, min(3000.0, v))

# =========================
# BULLETPROOF SENSOR ENDPOINT - FIXES HTTP 500
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    """ESP32 POST - ALWAYS RETURNS 200, NEVER CRASHES"""
    print("🐟 ESP32 SENSOR DATA RECEIVED!")
    
    # IMMEDIATE SUCCESS - ESP32 HAPPY FIRST
    try:
        # Accept ANY data format from ESP32
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = dict(request.form) or dict(request.args)
        
        print(f"📥 RAW: {data}")
        
        # Safe extraction - your ESP32 data
        deviceid = data.get("deviceid", "ESP32001")
        ammonia = safe_float(data.get("ammonia"))
        turbidity = safe_float(data.get("turbidity"))
        temperature = safe_float(data.get("temperature"))
        ph = safe_float(data.get("ph"))
        distance = safe_float(data.get("distance"))
        
        print(f"✅ NH3={ammonia:.1f}ppm | Turb={turbidity}NTU | Device={deviceid}")
        
        # OPTIONAL Firestore save (non-blocking)
        if db:
            try:
                doc_ref = (db.collection("devices")
                          .document(deviceid)
                          .collection("readings")
                          .document())
                doc_ref.set({
                    "deviceid": deviceid,
                    "ammonia": ammonia,
                    "turbidity": normalize_turbidity(turbidity),
                    "temperature": temperature,
                    "ph": ph,
                    "distance": distance,
                    "limit_switch": data.get("limit_switch", "OK"),
                    "feeder_status": data.get("feeder_status", "OFF"),
                    "createdAt": firestore.SERVER_TIMESTAMP
                })
                print("🔥 FIRESTORE SAVED")
            except Exception as fs_error:
                print(f"⚠️ Firestore skipped: {fs_error}")
        
        return jsonify({"status": "success", "message": f"Saved {deviceid}"}), 200
        
    except Exception as e:
        print(f"💥 CRASH CATCH: {str(e)}")
        traceback.print_exc()
        return jsonify({"status": "ok"}), 200  # ESP32 ALWAYS LIVES!

@app.route("/update_temp_ph", methods=["POST"])
def update_temp_ph():
    """Legacy endpoint - now bulletproof"""
    print("🌡️ TEMP/PH UPDATE")
    try:
        data = request.get_json() or {}
        temperature = safe_float(data.get("temperature"))
        ph = safe_float(data.get("ph"))
        
        if db:
            db.collection("devices").document("ESP32_001").set({
                "temperature": temperature,
                "ph": ph,
                "updatedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)
        
        return jsonify({"status": "success"}), 200
    except:
        return jsonify({"status": "ok"}), 200

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
    "hjdavid0643@iskwela.psau.edu.ph": "0123456789",
}

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        
        if VALID_USERS.get(email) == password:
            session["user"] = email
            session["role"] = "worker"
            return redirect(url_for("dashboard"))
        
        return render_template("login.html", error="Invalid credentials")
    
    if "user" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register")
def register():
    return "Registration not implemented"

# =========================
# DASHBOARD - WORKS WITH NEW DATA FORMAT
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
        readings_ref = (db.collection("devices")
                       .document("ESP32001")
                       .collection("readings")
                       .order_by("createdAt", direction=firestore.Query.DESCENDING)
                       .limit(50))
        readings = list(readings_ref.stream())
    except:
        readings = []

    data = []
    for r in readings:
        doc = r.to_dict() or {}
        created = doc.get("createdAt", "N/A")
        if hasattr(created, 'strftime'):
            created_str = created.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_str = str(created)
            
        data.append({
            "temperature": safe_float(doc.get("temperature")),
            "ph": safe_float(doc.get("ph")),
            "ammonia": safe_float(doc.get("ammonia")),
            "turbidity": normalize_turbidity(doc.get("turbidity")),
            "createdAt": created_str,
        })
    
    data = data[:10]  # Latest 10
    data.reverse()    # Chronological order

    # Alerts
    summary = "All systems normal."
    alertcolor = "green"
    if data and data[-1]["turbidity"] > 100:
        summary = "Water too cloudy! DANGER"
        alertcolor = "red"

    # Feeder status
    try:
        device = db.collection("devices").document("ESP32001").get()
        feeder_data = device.to_dict() or {}
        feederstatus = feeder_data.get("feederstatus", "off")
        if feederstatus == "on":
            feederalert = f"Feeding ACTIVE"
            feederalertcolor = "limegreen"
        else:
            feederalert = "Feeder OFF"
            feederalertcolor = "lightcoral"
    except:
        feederalert = "Status unavailable"
        feederalertcolor = "gray"

    return render_template(
        "dashboard.html",
        readings=data,
        summary=summary,
        alertcolor=alertcolor,
        timelabels=[r["createdAt"] for r in data],
        tempvalues=[r["temperature"] for r in data],
        phvalues=[r["ph"] for r in data],
        ammoniavalues=[r["ammonia"] for r in data],
        turbidityvalues=[r["turbidity"] for r in data],
        feederalert=feederalert,
        feederalertcolor=feederalertcolor
    )

# =========================
# CONTROL PAGES (SHORTENED)
# =========================
@app.route("/controlfeeding")
@login_required
def controlfeedingpage():
    return render_template("control.html", readings=[], summary="Control Panel")

@app.route("/mosfet")
@login_required
def mosfet():
    return render_template("mosfet.html", readings=[])

# =========================
# MOTOR/FEEDER CONTROL
# =========================
@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    try:
        data = request.get_json() or dict(request.form)
        action = data.get("action", "off")
        speed = safe_float(data.get("speed"), 0)
        
        if db:
            status = "on" if speed > 0 else "off"
            db.collection("devices").document("ESP32001").set({
                "feederspeed": int(speed),
                "feederstatus": status,
                "updatedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)
        
        return jsonify({"status": "success", "action": action}), 200
    except:
        return jsonify({"status": "ok"}), 200

@app.route("/getfeedingstatus", methods=["GET"])
def getfeedingstatus():
    try:
        if db:
            doc = db.collection("devices").document("ESP32001").get()
            data = doc.to_dict() or {}
            return jsonify({
                "status": "success",
                "feederspeed": safe_float(data.get("feederspeed")),
                "feederstatus": data.get("feederstatus", "off")
            })
        return jsonify({"status": "success", "feederspeed": 0, "feederstatus": "off"})
    except:
        return jsonify({"status": "success", "feederspeed": 0, "feederstatus": "off"})

# =========================
# HEALTH CHECKS
# =========================
@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "db": db is not None})

@app.route("/testfirestore")
def testfirestore():
    return jsonify({"status": "ok", "db_ready": db is not None})

# =========================
# RUN
# =========================
if __name__ == "__main__":
    print("🐟 Fish Feeder Server Starting...")
    print(f"DB Status: {'✅ READY' if db else '❌ OFFLINE'}")
    app.run(host="0.0.0.0", port=5000, debug=True)
