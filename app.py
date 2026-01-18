from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, send_file
)
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime, timedelta
import os
import io
import json

from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors

from google.api_core.exceptions import ResourceExhausted


# ================== APP SETUP ==================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
CORS(app)

serializer = URLSafeTimedSerializer(app.secret_key)


# ================== FIREBASE SETUP ==================

def init_firebase():
    """Initialize Firebase Admin from env var or secret file."""
    firebase_creds = os.environ.get("FIREBASE_CREDENTIALS")
    try:
        if firebase_creds:
            cred_dict = json.loads(firebase_creds)
            cred = credentials.Certificate(cred_dict)
        else:
            FIREBASE_KEY_PATH = (
                "/etc/secrets/"
                "authentication-fish-feeder-firebase-adminsdk-fbsvc-a724074a37.json"
            )
            cred = credentials.Certificate(FIREBASE_KEY_PATH)

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credential=cred)

        return firestore.client()
    except Exception as e:
        print("Error initializing Firebase:", e)
        return None


db = init_firebase()


# ================== HELPERS ==================

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


# ================== AUTH ROUTES ==================

@app.route("/")
def home():
    return redirect(url_for("login"))


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

        if db is None:
            return render_template(
                "login.html",
                error="Firestore not initialized on server",
            )

        try:
            users_q = (
                db.collection("users")
                .where("email", "==", email)
                .limit(1)
                .stream()
            )
            user_doc = next(users_q, None)
        except ResourceExhausted:
            # Firestore quota exceeded (429)
            return render_template(
                "login.html",
                error="Database quota exceeded. Please try again later.",
            )
        except Exception as e:
            return render_template(
                "login.html",
                error=f"Firestore error: {e}",
            )

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

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template(
                "register.html",
                error="Please fill all fields",
            )

        if db is None:
            return render_template(
                "register.html",
                error="Firestore not initialized on server",
            )

        try:
            existing = (
                db.collection("users")
                .where("email", "==", email)
                .limit(1)
                .stream()
            )
            if next(existing, None):
                return render_template(
                    "register.html",
                    error="Email already exists",
                )

            db.collection("users").add(
                {"email": email, "password": password, "role": "worker"}
            )
        except Exception as e:
            return render_template(
                "register.html",
                error=f"Firestore error: {e}",
            )

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email")
        if not email:
            return render_template(
                "reset.html",
                error="Please enter your email",
            )

        if db is None:
            return render_template(
                "reset.html",
                error="Firestore not initialized on server",
            )

        try:
            users_q = (
                db.collection("users")
                .where("email", "==", email)
                .limit(1)
                .stream()
            )
            user_doc = next(users_q, None)
        except Exception as e:
            return render_template(
                "reset.html",
                error=f"Firestore error: {e}",
            )

        if not user_doc:
            return render_template("reset.html", error="Email not found")

        token = serializer.dumps(email, salt="password-reset")
        reset_link = url_for("change_password", token=token, _external=True)

        return render_template(
            "reset.html",
            success=True,
            reset_link=reset_link,
        )

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

        if db is None:
            return render_template(
                "change.html",
                error="Firestore not initialized on server",
            )

        try:
            users_q = (
                db.collection("users")
                .where("email", "==", email)
                .limit(1)
                .stream()
            )
            user_doc = next(users_q, None)
        except Exception as e:
            return render_template(
                "change.html",
                error=f"Firestore error: {e}",
            )

        if not user_doc:
            return "User not found"

        user_doc.reference.update({"password": new_password})
        return redirect(url_for("login"))

    return render_template("change.html")


# ================== DASHBOARD ==================

@app.route("/dashboard")
@login_required
def dashboard():
    if db is None:
        return render_template(
            "dashboard.html",
            readings=[],
            summary="Firestore not initialized on server",
            alert_color="gray",
            time_labels=[],
            temp_values=[],
            ph_values=[],
            ammonia_values=[],
            turbidity_values=[],
            feeder_alert="Feeder status unavailable",
            feeder_alert_color="gray",
            low_feed_alert=None,
            low_feed_color="#ff7043",
        )

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
        doc_data = r.to_dict()
        created = doc_data.get("createdAt")
        created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
        turb = normalize_turbidity(doc_data.get("turbidity"))
        data.append(
            {
                "temperature": doc_data.get("temperature"),
                "ph": doc_data.get("ph"),
                "ammonia": doc_data.get("ammonia"),
                "turbidity": turb,
                "createdAt": created_str,
            }
        )

    data = list(reversed(data))

    summary = "🟢 All systems normal."
    alert_color = "green"

    if data:
        last = data[-1]
        if last["turbidity"] is not None:
            if last["turbidity"] > 100:
                summary = "⚠️ Water is too cloudy! (Danger)"
                alert_color = "gold"
            elif last["turbidity"] > 50:
                summary = "⚠️ Water is getting cloudy."
                alert_color = "orange"

    feeder_alert = "Feeder is currently OFF"
    feeder_alert_color = "lightcoral"
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            d = device_doc.to_dict()
            feeder_status = d.get("feeder_status", "off")
            feeder_speed = d.get("feeder_speed", 0)

            if feeder_status == "on" and feeder_speed and feeder_speed > 0:
                feeder_alert = f"🐟 Feeding in progress at {feeder_speed}% speed"
                feeder_alert_color = "limegreen"
    except Exception:
        feeder_alert = "Feeder status unavailable"
        feeder_alert_color = "gray"

    low_feed_alert = None
    low_feed_color = "#ff7043"
    try:
        hopper_doc = db.collection("devices").document("ESP32_002").get()
        if hopper_doc.exists:
            hdata = hopper_doc.to_dict()
            level_percent = (
                hdata.get("feed_level_percent")
                or hdata.get("water_level_percent")
            )
            if level_percent is not None and level_percent < 20:
                low_feed_alert = (
                    f"⚠️ Low feed level: {level_percent:.1f}% – please refill the hopper"
                )
    except Exception:
        pass

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
        feeder_alert=feeder_alert,
        feeder_alert_color=feeder_alert_color,
        low_feed_alert=low_feed_alert,
        low_feed_color=low_feed_color,
    )


# ================== MOSFET PAGE ==================

@app.route("/mosfet")
@login_required
def mosfet():
    if db is None:
        return render_template("mosfet.html", readings=[])

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
        doc_data = r.to_dict()
        created = doc_data.get("createdAt")
        created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
        turb = normalize_turbidity(doc_data.get("turbidity"))
        data.append(
            {
                "temperature": doc_data.get("temperature"),
                "ph": doc_data.get("ph"),
                "ammonia": doc_data.get("ammonia"),
                "turbidity": turb,
                "createdAt": created_str,
            }
        )

    return render_template("mosfet.html", readings=data)


# ================== FEEDING CONTROL PAGE ==================

@app.route("/control_feeding")
@login_required
def control_feeding_page():
    if db is None:
        return render_template(
            "control.html",
            error="Firestore not initialized on server",
            readings=[],
            all_readings=[],
            summary="Error loading data",
            chart_labels=[],
            chart_temp=[],
            chart_ph=[],
            chart_ammonia=[],
            chart_turbidity=[],
        )
    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32_001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(10)
        )
        readings = []
        for doc_snap in readings_ref.stream():
            d = doc_snap.to_dict()
            created = d.get("createdAt")
            readings.append(
                {
                    "temperature": d.get("temperature"),
                    "ph": d.get("ph"),
                    "ammonia": d.get("ammonia"),
                    "turbidity": normalize_turbidity(d.get("turbidity")),
                    "createdAt": (
                        created.strftime("%Y-%m-%d %H:%M:%S")
                        if created
                        else ""
                    ),
                }
            )

        all_readings_ref = (
            db.collection("devices")
            .document("ESP32_001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        all_readings = []
        for doc_snap in all_readings_ref.stream():
            d = doc_snap.to_dict()
            created = d.get("createdAt")
            all_readings.append(
                {
                    "temperature": d.get("temperature"),
                    "ph": d.get("ph"),
                    "ammonia": d.get("ammonia"),
                    "turbidity": normalize_turbidity(d.get("turbidity")),
                    "createdAt": (
                        created.strftime("%Y-%m-%d %H:%M:%S")
                        if created
                        else ""
                    ),
                }
            )

        chart_labels = []
        chart_temp = []
        chart_ph = []
        chart_ammonia = []
        chart_turbidity = []

        for r in reversed(readings):
            chart_labels.append(r.get("createdAt", "N/A"))
            chart_temp.append(r.get("temperature", 0))
            chart_ph.append(r.get("ph", 0))
            chart_ammonia.append(r.get("ammonia", 0))
            chart_turbidity.append(r.get("turbidity", 0))

        summary = "Feeding & Motor Control Dashboard"

        return render_template(
            "control.html",
            readings=readings,
            all_readings=all_readings,
            summary=summary,
            chart_labels=chart_labels,
            chart_temp=chart_temp,
            chart_ph=chart_ph,
            chart_ammonia=chart_ammonia,
            chart_turbidity=chart_turbidity,
        )
    except Exception as e:
        return render_template(
            "control.html",
            error=str(e),
            readings=[],
            all_readings=[],
            summary="Error loading data",
            chart_labels=[],
            chart_temp=[],
            chart_ph=[],
            chart_ammonia=[],
            chart_turbidity=[],
        )


# ================== PDF EXPORT (LAST 24 HOURS) ==================

@app.route("/export_pdf")
@login_required
def export_pdf():
    if db is None:
        return jsonify(
            {"status": "error",
             "message": "Firestore not initialized on server"}
        ), 500

    try:
        now = datetime.utcnow()
        twenty_four_hours_ago = now - timedelta(hours=24)

        readings_ref = (
            db.collection("devices")
            .document("ESP32_001")
            .collection("readings")
            .where("createdAt", ">=", twenty_four_hours_ago)
            .order_by("createdAt", direction=firestore.Query.ASCENDING)
        )

        readings_cursor = readings_ref.stream()
        data = []
        for r in readings_cursor:
            doc_data = r.to_dict()
            data.append(
                {
                    "temperature": doc_data.get("temperature"),
                    "ph": doc_data.get("ph"),
                    "ammonia": doc_data.get("ammonia"),
                    "turbidity": normalize_turbidity(
                        doc_data.get("turbidity")
                    ),
                    "createdAt": doc_data.get("createdAt"),
                }
            )

        if len(data) > 300:
            data = data[-300:]

        pdf_buffer = io.BytesIO()
        doc_pdf = SimpleDocTemplate(pdf_buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=20,
            textColor=colors.HexColor("#1f77b4"),
            alignment=1,
            spaceAfter=20,
        )
        elements.append(
            Paragraph("🐟 Water Quality Monitoring Report", title_style)
        )
        elements.append(
            Paragraph(
                f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} (last 24 hours)",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 0.2 * inch))

        table_data = [
            ["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]
        ]

        if data:
            for r in data:
                created_dt = r["createdAt"]
                if isinstance(created_dt, datetime):
                    created_str = created_dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    created_str = str(created_dt) if created_dt else ""
                table_data.append(
                    [
                        created_str,
                        "" if r["temperature"] is None
                        else f"{r['temperature']:.2f}",
                        "" if r["ph"] is None
                        else f"{r['ph']:.2f}",
                        "" if r["ammonia"] is None
                        else f"{r['ammonia']:.2f}",
                        "" if r["turbidity"] is None
                        else f"{r['turbidity']:.2f}",
                    ]
                )
        else:
            table_data.append(["No data in last 24 hours", "", "", "", ""])

        table = Table(table_data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f77b4")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ]
            )
        )

        elements.append(
            Paragraph("Recent Sensor Readings (24 hours)", styles["Heading2"])
        )
        elements.append(table)

        doc_pdf.build(elements)
        pdf_buffer.seek(0)

        timestamp = now.strftime("%Y%m%d_%H%M%S")
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"water_quality_24h_{timestamp}.pdf",
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ================== TEST & HEALTH ==================

@app.route("/test_firestore")
def test_firestore():
    try:
        if db is None:
            return jsonify(
                {"status": "error",
                 "message": "Firestore not initialized on server"}
            ), 500

        doc = db.collection("devices").document("ESP32_001").get()
        return jsonify({"status": "ok", "exists": doc.exists}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200


# ================== MAIN (LOCAL ONLY) ==================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
