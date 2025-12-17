from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
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
app.secret_key = "your-secret-key-change-this-in-production"
CORS(app)

# Firebase setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIREBASE_KEY_PATH = os.path.join(BASE_DIR, "firebasekey.json")
cred = credentials.Certificate(FIREBASE_KEY_PATH)
firebase_admin.initialize_app(cred)
db = firestore.client()

serializer = URLSafeTimedSerializer(app.secret_key)

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
            return render_template("dashboardauth.html", error="Please enter email and password")

        users = db.collection("users").where("email", "==", email).limit(1).stream()
        user_doc = None
        for u in users:
            user_doc = u
            break

        if not user_doc:
            return render_template("dashboardauth.html", error="Invalid email or password")

        data = user_doc.to_dict()
        if data.get("password") != password:
            return render_template("dashboardauth.html", error="Invalid email or password")

        session["user"] = email
        session["role"] = data.get("role", "worker")
        return redirect(url_for("dashboard"))

    return render_template("dashboardauth.html")

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

        existing = db.collection("users").where("email", "==", email).limit(1).stream()
        for _ in existing:
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

        users = db.collection("users").where("email", "==", email).limit(1).stream()
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
            return render_template("reset.html", error="Please enter a new password")

        users = db.collection("users").where("email", "==", email).limit(1).stream()
        user_doc = None
        for u in users:
            user_doc = u
            break

        if not user_doc:
            return "User not found"

        user_doc.reference.update({"password": new_password})
        return redirect(url_for("login"))

    return render_template("reset.html")

# ========== DASHBOARD ==========

@app.route("/dashboard")
@login_required
def dashboard():
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
        data.append({
            "temperature": doc.get("temperature"),
            "ph": doc.get("ph"),
            "ammonia": doc.get("ammonia"),
            "turbidity": doc.get("turbidity"),
            "createdAt": doc.get("createdAt").strftime("%Y-%m-%d %H:%M:%S")
            if doc.get("createdAt") else "",
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

# ========== PDF EXPORT ==========

@app.route("/export_pdf")
@login_required
def export_pdf():
    """Generate PDF report with sensor readings"""
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
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "ammonia": doc.get("ammonia"),
                "turbidity": doc.get("turbidity"),
                "createdAt": doc.get("createdAt"),
            })

        data = list(reversed(data))

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
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            styles["Normal"]
        ))
        elements.append(Spacer(1, 0.2 * inch))

        table_data = [["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]]
        for r in data:
            created = r["createdAt"].strftime("%Y-%m-%d %H:%M:%S") if r["createdAt"] else ""
            table_data.append([
                created,
                "" if r["temperature"] is None else f"{r['temperature']:.2f}",
                "" if r["ph"] is None else f"{r['ph']:.2f}",
                "" if r["ammonia"] is None else f"{r['ammonia']:.2f}",
                "" if r["turbidity"] is None else f"{r['turbidity']:.2f}",
            ])

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

        elements.append(Paragraph("Recent Sensor Readings", styles["Heading2"]))
        elements.append(table)
        doc_pdf.build(elements)
        pdf_buffer.seek(0)

        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"water_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== MOSFET FEEDING CONTROL ==========

@app.route("/control_feeding", methods=["POST"])
@login_required
def control_feeding():
    """Control feeding MOSFET"""
    try:
        data = request.get_json()
        action = data.get("action")
        speed = data.get("speed")
        
        if action == "off":
            db.collection("devices").document("ESP32_001").set({
                "feed_speed": 0,
                "feed_status": "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Feeder turned OFF"}), 200
            
        elif action == "on":
            db.collection("devices").document("ESP32_001").set({
                "feed_speed": 50,
                "feed_status": "on",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Feeder turned ON at 50%"}), 200
            
        elif action == "set_speed" and speed is not None:
            speed_value = int(speed)
            if speed_value < 0 or speed_value > 100:
                return jsonify({"status": "error", "message": "Speed must be 0-100"}), 400
            
            db.collection("devices").document("ESP32_001").set({
                "feed_speed": speed_value,
                "feed_status": "on" if speed_value > 0 else "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Feed speed set to {speed_value}%"}), 200
        
        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_feeding_status", methods=["GET"])
@login_required
def get_feeding_status():
    """Get current feeding MOSFET status"""
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            data = device_doc.to_dict()
            return jsonify({
                "status": "success",
                "feed_speed": data.get("feed_speed", 0),
                "feed_status": data.get("feed_status", "off")
            }), 200
        return jsonify({"status": "success", "feed_speed": 0, "feed_status": "off"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/feed/schedule", methods=["POST"])
@login_required
def set_feeding_schedule():
    """Set automatic feeding schedule"""
    try:
        data = request.get_json()
        time1 = data.get('time1', '09:00')
        time2 = data.get('time2', '16:00')
        duration = data.get('duration', 5)
        
        # Validate inputs
        if not time1 or not time2:
            return jsonify({
                "status": "error",
                "message": "Both feed times are required"
            }), 400
            
        if duration < 1 or duration > 60:
            return jsonify({
                "status": "error",
                "message": "Duration must be between 1-60 seconds"
            }), 400
        
        # Save schedule to Firestore
        db.collection("devices").document("ESP32_001").set({
            "feeding_schedule": {
                "time1": time1,
                "time2": time2,
                "duration": int(duration),
                "enabled": True,
                "lastUpdated": datetime.utcnow()
            }
        }, merge=True)
        
        return jsonify({
            "status": "success",
            "message": f"Schedule saved: {time1} & {time2} ({duration}s each)"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_feeding_schedule", methods=["GET"])
@login_required
def get_feeding_schedule():
    """Get current feeding schedule"""
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        
        if device_doc.exists:
            data = device_doc.to_dict()
            schedule = data.get("feeding_schedule", {
                "time1": "09:00",
                "time2": "16:00",
                "duration": 5,
                "enabled": False
            })
            return jsonify({
                "status": "success",
                "schedule": schedule
            }), 200
        
        return jsonify({
            "status": "success",
            "schedule": {
                "time1": "09:00",
                "time2": "16:00",
                "duration": 5,
                "enabled": False
            }
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== MOSFET MOTOR CONTROL ==========

@app.route("/control_motor", methods=["POST"])
@login_required
def control_motor():
    """Control motor RPM via MOSFET PWM"""
    try:
        data = request.get_json() or request.form
        rpm = data.get("rpm")
        action = data.get("action")
        
        if action == "off":
            db.collection("devices").document("ESP32_001").set({
                "motor_rpm": 0,
                "motor_status": "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Motor turned OFF"}), 200
            
        elif action == "on":
            db.collection("devices").document("ESP32_001").set({
                "motor_rpm": 50,
                "motor_status": "on",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Motor turned ON at 50%"}), 200
            
        elif action == "set_speed" and rpm is not None:
            rpm_value = int(rpm)
            if rpm_value < 0 or rpm_value > 100:
                return jsonify({"status": "error", "message": "RPM must be 0-100"}), 400
            
            db.collection("devices").document("ESP32_001").set({
                "motor_rpm": rpm_value,
                "motor_status": "on" if rpm_value > 0 else "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Speed set to {rpm_value}%"}), 200
        
        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_motor_status", methods=["GET"])
@login_required
def get_motor_status():
    """Get current motor RPM and status"""
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            data = device_doc.to_dict()
            return jsonify({
                "status": "success",
                "motor_rpm": data.get("motor_rpm", 0),
                "motor_status": data.get("motor_status", "off")
            }), 200
        return jsonify({"status": "success", "motor_rpm": 0, "motor_status": "off"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== API ROUTES ==========

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
            doc = r.to_dict()
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "ammonia": doc.get("ammonia"),
                "turbidity": doc.get("turbidity"),
                "createdAt": doc.get("createdAt").strftime("%Y-%m-%d %H:%M:%S")
                if doc.get("createdAt") else "",
            })

        return jsonify({"status": "success", "readings": data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
