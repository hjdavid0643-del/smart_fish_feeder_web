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
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch

# =========================
# APP CONFIG
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
CORS(app)

FIRESTORE_LOGIN_DISABLED = False

# =========================
# FIREBASE INIT
# =========================
def init_firebase():
    try:
        key_path = "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
        if not firebase_admin._apps:
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        print("Firebase init error:", e)
        return None

db = init_firebase()

# =========================
# HELPERS
# =========================
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
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def normalize_turbidity(value):
    v = to_float_or_none(value)
    if v is None:
        return None
    return max(0.0, min(v, 3000.0))

# =========================
# AUTH
# =========================
VALID_USERS = {
    "hjdavid0643@iskwela.psau.edu.ph": "0123456789",
}

@app.route("/login", methods=["GET", "POST"])
def login():
    if FIRESTORE_LOGIN_DISABLED:
        return render_template("login.html", error="Login disabled")

    if request.method == "POST":
        email = request.form.get("email", "").lower()
        password = request.form.get("password", "")

        if VALID_USERS.get(email) == password:
            session["user"] = email
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# BASIC
# =========================
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/ping")
def ping():
    return jsonify({"status": "ok"})

# =========================
# SENSOR UPDATE
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    if not db:
        return jsonify({"error": "Firestore not ready"}), 500

    data = request.get_json()
    deviceid = data.get("deviceid", "ESP32001")

    db.collection("devices").document(deviceid).collection("readings").add({
        "temperature": to_float_or_none(data.get("temperature")),
        "ph": to_float_or_none(data.get("ph")),
        "ammonia": to_float_or_none(data.get("ammonia")),
        "turbidity": normalize_turbidity(data.get("turbidity")),
        "distance": to_float_or_none(data.get("distance")),
        "createdAt": datetime.utcnow(),
    })

    return jsonify({"status": "success"}), 200

# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
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

# =========================
# FEEDER CONTROL
# =========================
@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if not db:
        return jsonify({"error": "Firestore not ready"}), 500

    data = request.get_json()
    action = data.get("action")
    speed = int(data.get("speed", 0))

    update = {"updatedAt": datetime.utcnow()}

    if action == "on":
        update.update({"feederstatus": "on", "feederspeed": speed})
    elif action == "off":
        update.update({"feederstatus": "off", "feederspeed": 0})
    else:
        return jsonify({"error": "Invalid action"}), 400

    db.collection("devices").document("ESP32001").set(update, merge=True)
    return jsonify({"status": "success"})

# =========================
# PDF EXPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if not db:
        return jsonify({"error": "Firestore not ready"}), 500

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
        Paragraph("Water Quality Report", styles["Heading1"]),
        Spacer(1, 0.2 * inch),
    ]

    table_data = [["Time", "Temp", "pH", "Ammonia", "Turbidity"]]

    for r in data:
        t = r["createdAt"].strftime("%Y-%m-%d %H:%M:%S")
        table_data.append([
            t,
            r.get("temperature"),
            r.get("ph"),
            r.get("ammonia"),
            r.get("turbidity"),
        ])

    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.lightblue),
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="report.pdf")

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
