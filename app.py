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
DEVICE_MAIN = "ESP32001"
DEVICE_HOPPER = "ESP32002"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
CORS(app)

# =========================
# FIREBASE INIT
# =========================
def init_firebase():
    try:
        key_path = os.environ.get("FIREBASE_KEY_PATH")
        if not key_path:
            raise RuntimeError("FIREBASE_KEY_PATH not set")

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
    if FIRESTORE_LOGIN_DISABLED or os.environ.get("FIRESTORE_LOGIN_DISABLED") == "1":
        return render_template("login.html", error="Login disabled")

    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")

        if VALID_USERS.get(email) == password:
            session["user"] = email
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid credentials")

    if "user" in session:
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# SENSOR INPUT
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    if not db:
        return jsonify({"error": "Firestore unavailable"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    deviceid = data.get("deviceid", DEVICE_MAIN)

    db.collection("devices").document(deviceid).collection("readings").add(
        {
            "temperature": to_float_or_none(data.get("temperature")),
            "ph": to_float_or_none(data.get("ph")),
            "ammonia": to_float_or_none(data.get("ammonia")),
            "turbidity": normalize_turbidity(data.get("turbidity")),
            "distance": to_float_or_none(data.get("distance")),
            "createdAt": datetime.utcnow(),
        }
    )

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
        readings = (
            db.collection("devices")
            .document(DEVICE_MAIN)
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(10)
            .stream()
        )
    except ResourceExhausted:
        return render_template("dashboard.html", readings=[])

    data = []
    for r in readings:
        d = r.to_dict()
        data.append(
            {
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": d.get("turbidity"),
                "createdAt": d.get("createdAt").strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    return render_template("dashboard.html", readings=list(reversed(data)))

# =========================
# API READINGS
# =========================
@app.route("/apilatestreadings")
def apilatestreadings():
    if not db:
        return jsonify({"error": "Firestore unavailable"}), 500

    readings = (
        db.collection("devices")
        .document(DEVICE_MAIN)
        .collection("readings")
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )

    data = []
    for r in readings:
        d = r.to_dict()
        data.append(
            {
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": d.get("turbidity"),
                "createdAt": d.get("createdAt").strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    data.reverse()
    return jsonify(data), 200

# =========================
# PDF EXPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if not db:
        return jsonify({"error": "Firestore unavailable"}), 500

    now = datetime.utcnow()
    since = now - timedelta(hours=24)

    readings = (
        db.collection("devices")
        .document(DEVICE_MAIN)
        .collection("readings")
        .where("createdAt", ">=", since)
        .order_by("createdAt")
        .stream()
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("Water Quality Report (24h)", styles["Heading1"]))
    elements.append(Spacer(1, 0.2 * inch))

    table_data = [["Time", "Temp", "pH", "Ammonia", "Turbidity"]]
    for r in readings:
        d = r.to_dict()
        table_data.append(
            [
                d["createdAt"].strftime("%Y-%m-%d %H:%M"),
                d.get("temperature"),
                d.get("ph"),
                d.get("ammonia"),
                d.get("turbidity"),
            ]
        )

    table = Table(table_data)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
            ]
        )
    )

    elements.append(table)
    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="water_quality_24h.pdf",
    )

# =========================
# HEALTH
# =========================
@app.route("/ping")
def ping():
    return jsonify({"status": "ok"}), 200

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
