from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_cors import CORS

import firebase_admin
from firebase_admin import credentials, firestore, messaging

from functools import wraps
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime
import os
import requests


app = Flask(__name__)
app.secret_key = "AIzaSyAiR0kD9irw4heL4xtnoKSnkEONM8afDxw"
CORS(app)

from flask import redirect, url_for

@app.route("/")
def home():
    return redirect(url_for("login"))

# ---------- Firebase setup ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebasekey.json")
cred = credentials.Certificate(FIREBASE_KEY_PATH)
firebase_admin.initialize_app(cred)
db = firestore.client()

serializer = URLSafeTimedSerializer(app.secret_key)


# ---------- Helpers ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def send_sms(message, phone_number="09364782808"):
    url = "https://sms.iprogtech.com/api/v1/sms_messages"
    data = {
        "api_token": "63eec8eb96306825e8073faf986453548c94a811",
        "message": message,
        "phone_number": phone_number,
    }
    try:
        response = requests.post(url, data=data)
        return response.json()
    except Exception as e:
        print(f"SMS send error: {e}")
        return None


# ---------- Entry route ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template(
                "login.html",
                error="Please enter email and password",
            )

        users = (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
        user_doc = None
        for u in users:
            user_doc = u
            break

        if not user_doc:
            return render_template(
                "login.html",
                error="Invalid email or password",
            )

        data = user_doc.to_dict()
        if data.get("password") != password:
            return render_template(
                "login.html",
                error="Invalid email or password",
            )

        session["user"] = email
        session["role"] = data.get("role", "worker")
        return redirect(url_for("dashboard"))

    # GET: show the email/password form
    return render_template("login.html")



@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Auth: register ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return redirect(url_for("register"))

        existing = (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
        for _ in existing:
            return redirect(url_for("register"))

        db.collection("users").add(
            {
                "email": email,
                "password": password,
                "role": "worker",
            }
        )

        return redirect(url_for("login"))

    return render_template("login.html")


# ---------- Password reset ----------
@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email")
        if not email:
            return render_template("reset.html", error="Please enter your email")

        users = (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
        user_doc = None
        for u in users:
            user_doc = u
            break

        if not user_doc:
            return render_template("reset.html", error="Email not found")

        token = serializer.dumps(email, salt="password-reset")
        reset_link = url_for("change_password", token=token, _external=True)
        return f"Password reset link: {reset_link}"

    return render_template("reset.html")


@app.route("/change_password/<token>", methods=["GET", "POST"])
def change_password(token):
    try:
        email = serializer.loads(token, salt="password-reset", max_age=600)
    except Exception:
        return "Invalid or expired token"

    if request.method == "POST":
        new_password = request.form.get("password")
        if not new_password:
            return render_template(
                "change.html",
                error="Please enter a new password",
            )

        users = (
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
        user_doc = None
        for u in users:
            user_doc = u
            break

        if not user_doc:
            return "User not found"

        user_doc.reference.update({"password": new_password})
        return redirect(url_for("login"))

    return render_template("change.html")


# ---------- Dashboard ----------
@app.route("/dashboard")
@login_required
def dashboard():
    # last 50 readings for table + chart
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
        data.append(
            {
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "ammonia": doc.get("ammonia"),
                "turbidity": doc.get("turbidity"),
                "createdAt": doc.get("createdAt").strftime("%Y-%m-%d %H:%M:%S")
                if doc.get("createdAt")
                else "",
            }
        )

    data = list(reversed(data))

    summary = "🟢 All systems normal."
    alert_color = "green"

    if data:
        last = data[-1]

        if last["temperature"] is not None and (
            last["temperature"] > 30 or last["temperature"] < 20
        ):
            summary = "Temperature out of range!"
            alert_color = "red"
            send_sms(
                f"Alert: {summary} Temperature: {last['temperature']}°C"
            )

        if last["ph"] is not None and (last["ph"] < 6.5 or last["ph"] > 8.5):
            summary = "pH level is abnormal!"
            alert_color = "orange"
            send_sms(f"Alert: {summary} pH: {last['ph']}")

        if last["ammonia"] is not None and last["ammonia"] > 0.5:
            summary = "High ammonia detected!"
            alert_color = "darkred"
            send_sms(
                f"Alert: {summary} Ammonia: {last['ammonia']} ppm"
            )

        if last["turbidity"] is not None and last["turbidity"] > 50:
            summary = "Water is too cloudy!"
            alert_color = "gold"
            send_sms(
                f"Alert: {summary} Turbidity: {last['turbidity']} NTU"
            )

    # data for Sensor Trends chart
    time_labels = [r["createdAt"] for r in data]
    temp_values = [r["temperature"] for r in data]
    ph_values = [r["ph"] for r in data]
    ammonia_values = [r["ammonia"] for r in data]
    turbidity_values = [r["turbidity"] for r in data]

    # only latest 10 for table
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


# ---------- API routes ----------
@app.route("/add_reading", methods=["POST"])
def add_reading():
    try:
        data = request.get_json()
        if not data:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "No JSON data provided",
                    }
                ),
                400,
            )

        device_id = data.get("device_id", "ESP32_001")
        temperature = data.get("temperature")
        ph = data.get("ph")
        ammonia = data.get("ammonia")
        turbidity = data.get("turbidity")

        if None in [temperature, ph, ammonia, turbidity]:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Incomplete sensor data",
                    }
                ),
                400,
            )

        try:
            temperature = float(temperature)
            ph = float(ph)
            ammonia = float(ammonia)
            turbidity = float(turbidity)
        except ValueError:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Sensor values must be numeric",
                    }
                ),
                400,
            )

        doc_ref = (
            db.collection("devices")
            .document(device_id)
            .collection("readings")
            .document()
        )

        doc_ref.set(
            {
                "temperature": temperature,
                "ph": ph,
                "ammonia": ammonia,
                "turbidity": turbidity,
                "createdAt": datetime.utcnow(),
            }
        )

        return (
            jsonify(
                {
                    "status": "success",
                    "message": f"Reading saved for device {device_id}",
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/feed/manual", methods=["POST"])
def manual_feed():
    action = request.form.get("action")
    if action == "on":
        return (
            jsonify(
                {
                    "status": "success",
                    "message": "Manual feeding ON triggered",
                }
            ),
            200,
        )
    if action == "off":
        return (
            jsonify(
                {
                    "status": "success",
                    "message": "Manual feeding OFF triggered",
                }
            ),
            200,
        )
    return jsonify({"status": "error", "message": "Invalid action"}), 400


@app.route("/feed/schedule", methods=["POST"])
def set_schedule():
    return (
        jsonify(
            {
                "status": "success",
                "message": "Automatic feeding schedule set for 9AM and 4PM",
            }
        ),
        200,
    )


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200


@app.route("/historical", methods=["GET"])
def historical():
    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32_001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
        )

        readings = readings_ref.stream()
        data = []
        for r in readings:
            doc = r.to_dict()
            data.append(
                {
                    "temperature": doc.get("temperature"),
                    "ph": doc.get("ph"),
                    "ammonia": doc.get("ammonia"),
                    "turbidity": doc.get("turbidity"),
                    "createdAt": doc.get("createdAt").strftime("%Y-%m-%d %H:%M:%S")
                    if doc.get("createdAt")
                    else "",
                }
            )

        return jsonify({"status": "success", "readings": data}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/send_notification", methods=["POST"])
def send_notification():
    try:
        data = request.get_json()
        token = data.get("token")
        title = data.get("title", "Alert")
        body = data.get("body", "Notification from Smart Fish Feeder")

        if not token:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Token is required",
                    }
                ),
                400,
            )

        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            token=token,
        )
        response = messaging.send(message)
        return (
            jsonify(
                {
                    "status": "success",
                    "message": f"Notification sent: {response}",
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------- Run ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
