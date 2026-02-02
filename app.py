from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from datetime import datetime, timedelta
import os, io
from google.api_core.exceptions import ResourceExhausted

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
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
# FIREBASE INIT
# =========================
def init_firebase():
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(
                "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
            )
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        print("🔥 Firebase init failed:", e)
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

def to_float_or_none(v):
    try:
        return float(v)
    except:
        return None

def normalize_turbidity(v):
    try:
        return float(v)
    except:
        return None

# =========================
# BASIC ROUTES
# =========================
@app.route("/")
def home():
    return redirect(url_for("login"))

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
        email = request.form.get("email")
        password = request.form.get("password")

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
# DASHBOARD
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")

# =========================
# ESP32 SENSOR INPUT
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    if not db:
        return jsonify({"status": "error"}), 500

    data = request.get_json() or {}
    data["createdAt"] = datetime.utcnow()

    db.collection("devices").document("ESP32001").collection("readings").add(data)
    return jsonify({"status": "success"}), 200

# =========================
# FEEDER CONTROL (ESP32)
# =========================
@app.route("/getfeedingstatus", methods=["GET"])
def getfeedingstatus():
    if not db:
        return jsonify({"feederstatus": "off", "feederspeed": 0})

    doc = db.collection("devices").document("ESP32001").get()
    d = doc.to_dict() or {}
    return jsonify({
        "feederstatus": d.get("feederstatus", "off"),
        "feederspeed": d.get("feederspeed", 0)
    })

@app.route("/setfeeder", methods=["POST"])
@api_login_required
def setfeeder():
    data = request.get_json()
    db.collection("devices").document("ESP32001").set({
        "feederstatus": data.get("status", "off"),
        "feederspeed": int(data.get("speed", 0))
    }, merge=True)
    return jsonify({"ok": True})

# =========================
# PDF EXPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    now = datetime.utcnow()
    since = now - timedelta(hours=24)

    try:
        ref = (
            db.collection("devices")
            .document("ESP32001")
            .collection("readings")
            .where("createdAt", ">=", since)
            .order_by("createdAt")
        )
        rows = [r.to_dict() for r in ref.stream()]
    except ResourceExhausted:
        return jsonify({"status": "error", "message": "Quota exceeded"}), 503

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Sensor Readings (24 Hours)", styles["Title"]), Spacer(1, 0.2*inch)]

    table_data = [["Time", "Temp", "pH", "Ammonia", "Turbidity"]]
    for r in rows:
        table_data.append([
            r.get("createdAt").strftime("%Y-%m-%d %H:%M"),
            r.get("temperature"),
            r.get("ph"),
            r.get("ammonia"),
            r.get("turbidity"),
        ])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 1, colors.black)
    ]))
    elements.append(table)
    doc.build(elements)

    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="readings.pdf")

# =========================
# HEALTH CHECK
# =========================
@app.route("/ping")
def ping():
    return jsonify({"status": "ok"}), 200

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
