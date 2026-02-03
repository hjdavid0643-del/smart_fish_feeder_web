import os
import io
import json
from functools import wraps
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, send_file
)
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from itsdangerous import URLSafeTimedSerializer

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors


app = Flask(__name__)
# Ensure you set a SECRET_KEY in Render Environment Variables for security
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-locally")
CORS(app)


# ========== FIREBASE SETUP ==========
def init_firebase():
    """
    Initializes Firebase using Environment Variable (Render) 
    or Local File (Computer).
    """
    try:
        # 1. Try Render Environment Variable
        firebase_creds = os.environ.get("FIREBASE_CREDENTIALS")
        if firebase_creds:
            print("Loading credentials from Environment Variable...")
            cred_dict = json.loads(firebase_creds)
            cred = credentials.Certificate(cred_dict)
        else:
            # 2. Try Local File (fallback)
            # Check for the specific Render secret path OR a local file
            secret_path = "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-a724074a37.json"
            local_path = "firebase-key.json"
            
            if os.path.exists(secret_path):
                print(f"Loading credentials from Secret File: {secret_path}")
                cred = credentials.Certificate(secret_path)
            elif os.path.exists(local_path):
                print(f"Loading credentials from Local File: {local_path}")
                cred = credentials.Certificate(local_path)
            else:
                print("❌ Error: No Firebase credentials found!")
                return None

        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        
        return firestore.client()

    except Exception as e:
        print(f"Firebase Init Error: {e}")
        return None

db = init_firebase()
serializer = URLSafeTimedSerializer(app.secret_key)


# ========== HELPERS ==========

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


# ========== AUTH ROUTES ==========

@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/create_admin")
def create_admin():
    """
    Temporary route to create an admin user since the DB might be empty.
    Go to /create_admin in your browser to run this.
    """
    if not db:
        return "Database not connected."
    
    email = "admin@example.com"
    password = "admin123"
    
    try:
        # Check if exists
        users = db.collection("users").where("email", "==", email).limit(1).stream()
        if next(users, None):
            return f"User {email} already exists."
        
        # Create user
        db.collection("users").add({
            "email": email,
            "password": password,
            "role": "admin"
        })
        return f"✅ Created user: {email} / {password} <br><a href='/login'>Go to Login</a>"
    except Exception as e:
        return f"Error creating user: {e}"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template("login.html", error="Please enter email and password")

        if not db:
            return render_template("login.html", error="Database connection failed")

        try:
            users = db.collection("users").where("email", "==", email).limit(1).stream()
            user_doc = next(users, None)

            if not user_doc:
                return render_template("login.html", error="Invalid email or password")

            data = user_doc.to_dict()
            # Note: In production, you should hash passwords!
            if data.get("password") != password:
                return render_template("login.html", error="Invalid email or password")

            session["user"] = email
            session["role"] = data.get("role", "worker")
            return redirect(url_for("dashboard"))
        except Exception as e:
             return render_template("login.html", error=f"Login error: {str(e)}")

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
            return render_template("register.html", error="Please fill all fields")

        if not db:
            return "Database Error"

        existing = db.collection("users").where("email", "==", email).limit(1).stream()
        if next(existing, None):
            return render_template("register.html", error="Email already exists")

        db.collection("users").add({"email": email, "password": password, "role": "worker"})
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email")
        if not email:
            return render_template("reset.html", error="Please enter your email")

        if not db:
            return "Database Error"

        users = db.collection("users").where("email", "==", email).limit(1).stream()
        user_doc = next(users, None)

        if not user_doc:
            return render_template("reset.html", error="Email not found")

        token = serializer.dumps(email, salt="password-reset")
        # In a real app, you would email this link. Here we just display it.
        reset_link = url_for("change_password", token=token, _external=True)
        return f"Password reset link (Copy this): <br> <a href='{reset_link}'>{reset_link}</a>"

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
            return render_template("change.html", error="Please enter a new password")

        if not db:
            return "Database Error"

        users = db.collection("users").where("email", "==", email).limit(1).stream()
        user_doc = next(users, None)

        if not user_doc:
            return "User not found"

        user_doc.reference.update({"password": new_password})
        return redirect(url_for("login"))

    return render_template("change.html")


# ========== DASHBOARD ==========

@app.route("/dashboard")
@login_required
def dashboard():
    if not db:
        return "Database not connected"

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
            doc_data = r.to_dict()
            created = doc_data.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            turb = normalize_turbidity(doc_data.get("turbidity"))
            data.append({
                "temperature": doc_data.get("temperature"),
                "ph": doc_data.get("ph"),
                "ammonia": doc_data.get("ammonia"),
                "turbidity": turb,
                "createdAt": created_str,
            })

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

        # ---------- Feeder status alert (MOSFET 1) ----------
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
                else:
                    feeder_alert = "Feeder is currently OFF"
                    feeder_alert_color = "lightcoral"
        except Exception:
            feeder_alert = "Feeder status unavailable"
            feeder_alert_color = "gray"

        # ---------- Low feed alert (hopper / ESP32_002) ----------
        low_feed_alert = None
        low_feed_color = "#ff7043"  # orange
        try:
            hopper_doc = db.collection("devices").document("ESP32_002").get()
            if hopper_doc.exists:
                hdata = hopper_doc.to_dict()
                # adjust this key to whatever ESP32_002 sends
                level_percent = hdata.get("feed_level_percent") or hdata.get("water_level_percent")
                if level_percent is not None and level_percent < 20:
                    low_feed_alert = f"⚠️ Low feed level: {level_percent:.1f}% – please refill the hopper"
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
    except Exception as e:
        return f"Dashboard Error: {str(e)}"


# ========== MOSFET PAGE ==========

@app.route("/mosfet")
@login_required
def mosfet():
    if not db:
        return "Database Error"

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
        data.append({
            "temperature": doc_data.get("temperature"),
            "ph": doc_data.get("ph"),
            "ammonia": doc_data.get("ammonia"),
            "turbidity": turb,
            "createdAt": created_str,
        })

    return render_template("mosfet.html", readings=data)


# ========== FEEDING CONTROL PAGE ==========

@app.route("/control_feeding")
@login_required
def control_feeding_page():
    if not db:
        return "Database Error"
        
    try:
        readings_ref = (
            db.collection("devices").document("ESP32_001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(10)
        )
        readings = []
        for doc_snap in readings_ref.stream():
            d = doc_snap.to_dict()
            created = d.get("createdAt")
            readings.append({
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": normalize_turbidity(d.get("turbidity")),
                "createdAt": created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            })

        all_readings_ref = (
            db.collection("devices").document("ESP32_001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        all_readings = []
        for doc_snap in all_readings_ref.stream():
            d = doc_snap.to_dict()
            created = d.get("createdAt")
            all_readings.append({
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": normalize_turbidity(d.get("turbidity")),
                "createdAt": created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            })

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


# ========== PDF EXPORT (LAST 24 HOURS) ==========

@app.route("/export_pdf")
@login_required
def export_pdf():
    if not db:
        return jsonify({"status": "error", "message": "Database not connected"}), 500

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
            data.append({
                "temperature": doc_data.get("temperature"),
                "ph": doc_data.get("ph"),
                "ammonia": doc_data.get("ammonia"),
                "turbidity": normalize_turbidity(doc_data.get("turbidity")),
                "createdAt": doc_data.get("createdAt"),
            })

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
        elements.append(Paragraph("🐟 Water Quality Monitoring Report", title_style))
        elements.append(Paragraph(
            f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} (last 24 hours)",
            styles["Normal"]
        ))
        elements.append(Spacer(1, 0.2 * inch))

        table_data = [["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]]

        if data:
            for r in data:
                created_dt = r["createdAt"]
                if isinstance(created_dt, datetime):
                    created_str = created_dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    created_str = str(created_dt) if created_dt else ""
                table_data.append([
                    created_str,
                    "" if r["temperature"] is None else f"{r['temperature']:.2f}",
                    "" if r["ph"] is None else f"{r['ph']:.2f}",
                    "" if r["ammonia"] is None else f"{r['ammonia']:.2f}",
                    "" if r["turbidity"] is None else f"{r['turbidity']:.2f}",
                ])
        else:
            table_data.append(["No data in last 24 hours", "", "", "", ""])

        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f77b4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ]))

        elements.append(Paragraph("Recent Sensor Readings (24 hours)", styles["Heading2"]))
        elements.append(table)

        doc_pdf.build(elements)
        pdf_buffer.seek(0)

        timestamp = now.strftime('%Y%m%d_%H%M%S')
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"water_quality_24h_{timestamp}.pdf",
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ========== MOSFET MOTOR CONTROL ==========

@app.route("/control_motor", methods=["POST"])
@api_login_required
def control_motor():
    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32_001").set({
                "motor_speed": 0,
                "motor_status": "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Motor turned OFF"}), 200

        elif action == "on":
            db.collection("devices").document("ESP32_001").set({
                "motor_speed": int(speed),
                "motor_status": "on",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Motor turned ON at {speed}%"}), 200

        elif action == "set_speed":
            speed_value = int(speed)
            if speed_value < 0 or speed_value > 100:
                return jsonify({"status": "error", "message": "Speed must be 0-100"}), 400

            db.collection("devices").document("ESP32_001").set({
                "motor_speed": speed_value,
                "motor_status": "on" if speed_value > 0 else "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Speed set to {speed_value}%"}), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get_motor_status", methods=["GET"])
@api_login_required
def get_motor_status():
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            data = device_doc.to_dict()
            return jsonify({
                "status": "success",
                "motor_speed": data.get("motor_speed", 0),
                "motor_status": data.get("motor_status", "off")
            }), 200
        return jsonify({
            "status": "success",
            "motor_speed": 0,
            "motor_status": "off"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ========== FEEDER CONTROL ==========

@app.route("/control_feeder", methods=["POST"])
@api_login_required
def control_feeder():
    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32_001").set({
                "feeder_speed": 0,
                "feeder_status": "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Feeder turned OFF"}), 200

        elif action == "on":
            db.collection("devices").document("ESP32_001").set({
                "feeder_speed": int(speed),
                "feeder_status": "on",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Feeder turned ON at {speed}%"}), 200

        elif action == "set_speed":
            speed_value = int(speed)
            if speed_value < 0 or speed_value > 100:
                return jsonify({"status": "error", "message": "Speed must be 0-100"}), 400

            db.collection("devices").document("ESP32_001").set({
                "feeder_speed": speed_value,
                "feeder_status": "on" if speed_value > 0 else "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Feeder speed set to {speed_value}%"}), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get_feeding_status", methods=["GET"])
@api_login_required
def get_feeding_status():
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            data = device_doc.to_dict()
            return jsonify({
                "status": "success",
                "feeder_speed": data.get("feeder_speed", 0),
                "feeder_status": data.get("feeder_status", "off")
            }), 200
        return jsonify({
            "status": "success",
            "feeder_speed": 0,
            "feeder_status": "off"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ========== FEEDING SCHEDULE ==========

@app.route("/save_feeding_schedule", methods=["POST"])
@api_login_required
def save_feeding_schedule():
    try:
        data = request.get_json() or request.form
        first_feed = data.get("first_feed")
        second_feed = data.get("second_feed")
        duration = data.get("duration")

        if not first_feed or not second_feed or not duration:
            return jsonify({"status": "error", "message": "All fields required"}), 400

        db.collection("devices").document("ESP32_001").set({
            "feeding_schedule": {
                "first_feed": first_feed,
                "second_feed": second_feed,
                "duration": int(duration)
            },
            "schedule_enabled": True,
            "updatedAt": datetime.utcnow()
        }, merge=True)

        return jsonify({"status": "success", "message": "Feeding schedule saved"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get_feeding_schedule_info", methods=["GET"])
@api_login_required
def get_feeding_schedule_info():
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            data = device_doc.to_dict()
            schedule = data.get("feeding_schedule", {})
            return jsonify({
                "status": "success",
                "schedule": schedule,
                "enabled": data.get("schedule_enabled", False)
            }), 200
        return jsonify({"status": "success", "schedule": {}, "enabled": False}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ========== SENSOR API ROUTES ==========

@app.route("/add_reading", methods=["POST"])
def add_reading():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        device_id = data.get("device_id", "ESP32_001")

        temperature = to_float_or_none(data.get("temperature"))
        ph          = to_float_or_none(data.get("ph"))
        ammonia     = to_float_or_none(data.get("ammonia"))
        turbidity   = normalize_turbidity(data.get("turbidity"))
        distance    = to_float_or_none(data.get("distance"))

        doc_ref = (
            db.collection("devices")
            .document(device_id)
            .collection("readings")
            .document()
        )
        doc_ref.set({
            "temperature": temperature,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity,
            "distance": distance,
            "createdAt": datetime.utcnow(),
        })

        return jsonify({"status": "success", "message": f"Reading saved for {device_id}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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
            doc_data = r.to_dict()
            created = doc_data.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            turb = normalize_turbidity(doc_data.get("turbidity"))
            data.append({
                "temperature": doc_data.get("temperature"),
                "ph": doc_data.get("ph"),
                "ammonia": doc_data.get("ammonia"),
                "turbidity": turb,
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
        return jsonify({"status": "error", "message": str(e)}), 500


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
            doc_data = r.to_dict()
            created = doc_data.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            turb = normalize_turbidity(doc_data.get("turbidity"))
            data.append({
                "temperature": doc_data.get("temperature"),
                "ph": doc_data.get("ph"),
                "ammonia": doc_data.get("ammonia"),
                "turbidity": turb,
                "createdAt": created_str,
            })

        return jsonify({"status": "success", "readings": data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ========== ULTRASONIC HISTORY FOR ESP32_002 ==========

@app.route("/api/ultrasonic_esp32_2", methods=["GET"])
def api_ultrasonic_esp32_2():
    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32_002")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(100)
        )

        readings_cursor = readings_ref.stream()
        data = []
        for r in readings_cursor:
            doc_data = r.to_dict()
            created = doc_data.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if created else ""
            data.append({
                "distance": doc_data.get("distance"),
                "createdAt": created_str,
            })

        data = list(reversed(data))

        labels = [r["createdAt"] for r in data]
        distances = [r["distance"] for r in data]

        return jsonify({"status": "success", "labels": labels, "distance": distances}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ========== FIRESTORE TEST ROUTE ==========

@app.route("/test_firestore")
def test_firestore():
    try:
        doc = db.collection("devices").document("ESP32_001").get()
        return jsonify({"status": "ok", "exists": doc.exists}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ========== HEALTH CHECK ==========

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200


if __name__ == "__main__":
    # Render requires binding to 0.0.0.0 and reading the PORT environment variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
