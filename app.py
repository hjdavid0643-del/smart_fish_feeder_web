from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, send_file
)
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime
import os
import io

# PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-change-this-in-production")
CORS(app)

# ========== FIREBASE SETUP ==========

firebase_creds = os.environ.get("FIREBASE_CREDENTIALS")
if firebase_creds:
    import json
    cred = credentials.Certificate(json.loads(firebase_creds))
else:
    FIREBASE_KEY_PATH = "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-a724074a37.json"
    cred = credentials.Certificate(FIREBASE_KEY_PATH)

firebase_admin.initialize_app(cred)
db = firestore.client()

serializer = URLSafeTimedSerializer(app.secret_key)

# ========== HELPERS ==========

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ========== AUTH ROUTES ==========

@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template("login.html", error="Please enter email and password")

        try:
            users_ref = db.collection("users").where("email", "==", email).limit(1)
            users = list(users_ref.stream())
        except Exception as e:
            print("Firestore error in /login:", e)
            return render_template(
                "login.html",
                error="Login temporarily unavailable. Please try again later."
            )

        user_doc = users[0] if users else None
        if not user_doc:
            return render_template("login.html", error="Invalid email or password")

        data = user_doc.to_dict()
        if data.get("password") != password:
            return render_template("login.html", error="Invalid email or password")

        session["user"] = email
        session["role"] = data.get("role", "worker")
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# (optional) simple register retained
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template("register.html", error="Please fill all fields")

        try:
            existing = db.collection("users").where("email", "==", email).limit(1).stream()
            if next(existing, None):
                return render_template("register.html", error="Email already exists")
            db.collection("users").add({"email": email, "password": password, "role": "worker"})
        except Exception as e:
            print("Firestore error in /register:", e)
            return render_template("register.html", error="Registration failed. Try again.")

        return redirect(url_for("login"))

    return render_template("register.html")

# ========== DASHBOARD (simplified) ==========

@app.route("/dashboard")
@login_required
def dashboard():
    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32_001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(50)
        )

        readings_cursor = readings_ref.stream()
        data = []
        for r in readings_cursor:
            doc = r.to_dict()
            created = doc.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "ammonia": doc.get("ammonia"),
                "turbidity": doc.get("turbidity"),
                "createdAt": created_str,
            })

        data = list(reversed(data))

        summary = "🟢 All systems normal."
        alert_color = "green"

        if data:
            last = data[-1]
            if last["temperature"] is not None and (last["temperature"] > 30 or last["temperature"] < 20):
                summary = "⚠️ Temperature out of range!"
                alert_color = "red"
            if last["ph"] is not None and (last["ph"] < 6.5 or last["ph"] > 8.5):
                summary = "⚠️ pH level is abnormal!"
                alert_color = "orange"
            if last["ammonia"] is not None and last["ammonia"] > 0.5:
                summary = "⚠️ High ammonia detected!"
                alert_color = "darkred"
            if last["turbidity"] is not None and last["turbidity"] > 50:
                summary = "⚠️ Water is too cloudy!"
                alert_color = "gold"

        time_labels = [r["createdAt"] for r in data]
        temp_values = [r["temperature"] for r in data]
        ph_values = [r["ph"] for r in data]
        ammonia_values = [r["ammonia"] for r in data]
        turbidity_values = [r["turbidity"] for r in data]
        latest_10 = data[-10:]

        return render_template(
            "dashboard.html",
            readings=latest_10,
            summary=summary,
            alert_color=alert_color,
            time_labels=time_labels,
            temp_values=temp_values,
            ph_values=ph_values,
            ammonia_values=ammonia_values,
            turbidity_values=turbidity_values,
        )
    except Exception as e:
        print("Firestore error in /dashboard:", e)
        return render_template(
            "dashboard.html",
            readings=[],
            summary="Error loading data",
            alert_color="red",
            time_labels=[],
            temp_values=[],
            ph_values=[],
            ammonia_values=[],
            turbidity_values=[],
        )

# ========== SENSOR API (ESP32) ==========

@app.route("/add_reading", methods=["POST"])
def add_reading():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        device_id = data.get("device_id", "ESP32_001")
        temperature = float(data.get("temperature"))
        ph = float(data.get("ph"))
        ammonia = float(data.get("ammonia"))
        turbidity = float(data.get("turbidity"))

        doc_ref = db.collection("devices").document(device_id).collection("readings").document()
        doc_ref.set({
            "temperature": temperature,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity,
            "createdAt": datetime.utcnow(),
        })

        return jsonify({"status": "success", "message": f"Reading saved for {device_id}"}), 200
    except Exception as e:
        print("Firestore error in /add_reading:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== SIMPLE API FOR CHARTS ==========

@app.route("/api/latest_readings", methods=["GET"])
def api_latest_readings():
    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32_001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(50)
        )

        readings_cursor = readings_ref.stream()
        data = []
        for r in readings_cursor:
            doc = r.to_dict()
            created = doc.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "ammonia": doc.get("ammonia"),
                "turbidity": doc.get("turbidity"),
                "createdAt": created_str,
            })

        data = list(reversed(data))

        labels = [r["createdAt"] for r in data]
        temp = [r["temperature"] for r in data]
        ph = [r["ph"] for r in data]
        ammonia = [r["ammonia"] for r in data]
        turbidity = [r["turbidity"] for r in data]

        return jsonify({
            "labels": labels,
            "temp": temp,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity
        }), 200
    except Exception as e:
        print("Firestore error in /api/latest_readings:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== FIRESTORE TEST & HEALTH ==========

@app.route("/test_firestore")
def test_firestore():
    try:
        doc = db.collection("devices").document("ESP32_001").get(timeout=3)
        return jsonify({"status": "ok", "exists": doc.exists}), 200
    except Exception as e:
        print("Firestore error in /test_firestore:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
