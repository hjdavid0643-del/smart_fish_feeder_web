from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from functools import wraps
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import inch
import io

app = Flask(__name__)
app.secret_key = "your_secret_key_here_change_in_production"

# Initialize Firebase
cred = credentials.Certificate("path/to/your/serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ========== AUTHENTICATION ROUTES ==========
@app.route("/")
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        try:
            user = auth.create_user(email=email, password=password)
            db.collection("users").document(user.uid).set({
                "email": email,
                "createdAt": datetime.utcnow()
            })
            return redirect(url_for('login'))
        except Exception as e:
            return render_template("register.html", error=str(e))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        try:
            user = auth.get_user_by_email(email)
            session['user_id'] = user.uid
            session['email'] = email
            return redirect(url_for('dashboard'))
        except Exception as e:
            return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/reset", methods=["GET", "POST"])
def reset():
    if request.method == "POST":
        email = request.form.get("email")
        # Implement password reset logic here
        return render_template("reset.html", message="Reset link sent to email")
    return render_template("reset.html")

@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        # Implement password change logic
        return redirect(url_for('dashboard'))
    return render_template("change.html")

# ========== DASHBOARD ROUTE ==========
@app.route("/dashboard")
@login_required
def dashboard():
    try:
        # Get last 10 sensor readings
        readings_ref = db.collection("sensor_data").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(10)
        readings = []
        for doc in readings_ref.stream():
            data = doc.to_dict()
            readings.append(data)
        
        # Prepare chart data
        chart_labels = []
        chart_temp = []
        chart_ph = []
        chart_ammonia = []
        chart_turbidity = []
        
        for r in reversed(readings):
            chart_labels.append(r.get('createdAt', 'N/A'))
            chart_temp.append(r.get('temperature', 0))
            chart_ph.append(r.get('ph', 0))
            chart_ammonia.append(r.get('ammonia', 0))
            chart_turbidity.append(r.get('turbidity', 0))
        
        # Generate summary
        if readings:
            latest = readings[0]
            summary = f"<strong>Latest Reading:</strong> Temp: {latest.get('temperature')}°C | pH: {latest.get('ph')} | Ammonia: {latest.get('ammonia')}ppm | Turbidity: {latest.get('turbidity')}NTU"
        else:
            summary = "No sensor data available"
        
        return render_template("dashboard.html", 
                             readings=readings,
                             summary=summary,
                             chart_labels=chart_labels,
                             chart_temp=chart_temp,
                             chart_ph=chart_ph,
                             chart_ammonia=chart_ammonia,
                             chart_turbidity=chart_turbidity)
    except Exception as e:
        return render_template("dashboard.html", error=str(e), readings=[], summary="Error loading data")

@app.route("/mosfet")
@login_required
def mosfet():
    try:
        readings_ref = db.collection("sensor_data").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(50)
        readings = []
        for doc in readings_ref.stream():
            readings.append(doc.to_dict())
        
        summary = "Historical sensor data"
        return render_template("mosfet.html", readings=readings, summary=summary)
    except Exception as e:
        return render_template("mosfet.html", error=str(e), readings=[], summary="Error loading data")

# ========== FEEDING CONTROL ROUTES ==========
@app.route("/control_feeding", methods=["POST"])
@login_required
def control_feeding():
    try:
        data = request.get_json()
        action = data.get("action")
        speed = data.get("speed", 50)
        
        db.collection("devices").document("ESP32_001").set({
            "feed_status": action,
            "feed_speed": speed,
            "updatedAt": datetime.utcnow()
        }, merge=True)
        
        return jsonify({"status": "success", "message": f"Feeding {action}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_feeding_status", methods=["GET"])
@login_required
def get_feeding_status():
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            data = device_doc.to_dict()
            return jsonify({
                "status": "success",
                "feed_status": data.get("feed_status", "off"),
                "feed_speed": data.get("feed_speed", 0)
            }), 200
        return jsonify({"status": "success", "feed_status": "off", "feed_speed": 0}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== PUMP CONTROL ROUTES ==========
@app.route("/control_pump", methods=["POST"])
@login_required
def control_pump():
    try:
        data = request.get_json()
        action = data.get("action")
        speed = data.get("speed", 50)
        
        db.collection("devices").document("ESP32_001").set({
            "pump_status": action,
            "pump_speed": speed,
            "updatedAt": datetime.utcnow()
        }, merge=True)
        
        return jsonify({"status": "success", "message": f"Pump {action}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/get_pump_status", methods=["GET"])
@login_required
def get_pump_status():
    try:
        device_doc = db.collection("devices").document("ESP32_001").get()
        if device_doc.exists:
            data = device_doc.to_dict()
            return jsonify({
                "status": "success",
                "pump_status": data.get("pump_status", "off"),
                "pump_speed": data.get("pump_speed", 0)
            }), 200
        return jsonify({"status": "success", "pump_status": "off", "pump_speed": 0}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== FEEDING SCHEDULE ROUTES ==========
@app.route("/feed_schedule/add", methods=["POST"])
@login_required
def add_feed_schedule():
    try:
        data = request.get_json()
        schedule_ref = db.collection("feed_schedules").document()
        data['id'] = schedule_ref.id
        data['user_id'] = session.get('user_id')
        data['createdAt'] = datetime.utcnow()
        schedule_ref.set(data)
        return jsonify({"status": "success", "message": "Schedule added"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/feed_schedule/list", methods=["GET"])
@login_required
def list_feed_schedules():
    try:
        schedules = []
        user_id = session.get('user_id')
        docs = db.collection("feed_schedules").where("user_id", "==", user_id).stream()
        for doc in docs:
            schedules.append(doc.to_dict())
        return jsonify({"status": "success", "schedules": schedules}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/feed_schedule/update", methods=["POST"])
@login_required
def update_feed_schedule():
    try:
        data = request.get_json()
        schedule_id = data.get('id')
        db.collection("feed_schedules").document(schedule_id).set(data, merge=True)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/feed_schedule/delete", methods=["POST"])
@login_required
def delete_feed_schedule():
    try:
        data = request.get_json()
        schedule_id = data.get('id')
        db.collection("feed_schedules").document(schedule_id).delete()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== PUMP SCHEDULE ROUTES ==========
@app.route("/pump_schedule/add", methods=["POST"])
@login_required
def add_pump_schedule():
    try:
        data = request.get_json()
        schedule_ref = db.collection("pump_schedules").document()
        data['id'] = schedule_ref.id
        data['user_id'] = session.get('user_id')
        data['createdAt'] = datetime.utcnow()
        schedule_ref.set(data)
        return jsonify({"status": "success", "message": "Schedule added"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/pump_schedule/list", methods=["GET"])
@login_required
def list_pump_schedules():
    try:
        schedules = []
        user_id = session.get('user_id')
        docs = db.collection("pump_schedules").where("user_id", "==", user_id).stream()
        for doc in docs:
            schedules.append(doc.to_dict())
        return jsonify({"status": "success", "schedules": schedules}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/pump_schedule/update", methods=["POST"])
@login_required
def update_pump_schedule():
    try:
        data = request.get_json()
        schedule_id = data.get('id')
        db.collection("pump_schedules").document(schedule_id).set(data, merge=True)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/pump_schedule/delete", methods=["POST"])
@login_required
def delete_pump_schedule():
    try:
        data = request.get_json()
        schedule_id = data.get('id')
        db.collection("pump_schedules").document(schedule_id).delete()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== ESP32 CONFIG ROUTES ==========
@app.route("/get_pin_config", methods=["GET"])
def get_pin_config():
    """Return ESP32 pin configuration for sensors and actuators"""
    pin_config = {
        "sensors": {
            "ammonia_pin": 34,
            "ph_pin": 35,
            "turbidity_pin": 32,
            "temperature_pin": 4
        },
        "actuators": {
            "servo_pin": 13,
            "motor_pwm_pin": 25,
            "pump_pwm_pin": 26
        },
        "i2c": {
            "sda_pin": 21,
            "scl_pin": 22
        },
        "servo_positions": {
            "open": 180,
            "close": 0
        },
        "thresholds": {
            "temperature_min": 20.0,
            "temperature_max": 30.0,
            "ph_min": 6.5,
            "ph_max": 8.5,
            "ammonia_max": 0.5,
            "turbidity_max": 50.0
        }
    }
    return jsonify({"status": "success", "config": pin_config}), 200

# ========== PDF GENERATION ==========
@app.route("/download_pdf")
@login_required
def download_pdf():
    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        
        elements = []
        styles = getSampleStyleSheet()
        
        # Title
        title = Paragraph("🐟 Smart Fish Feeder Report", styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 0.3*inch))
        
        # Get sensor data
        readings_ref = db.collection("sensor_data").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(20)
        readings = []
        for doc in readings_ref.stream():
            data = doc.to_dict()
            readings.append([
                data.get('createdAt', 'N/A'),
                f"{data.get('temperature', 0)}°C",
                str(data.get('ph', 0)),
                f"{data.get('ammonia', 0)}ppm",
                f"{data.get('turbidity', 0)}NTU"
            ])
        
        # Create table
        table_data = [['Timestamp', 'Temperature', 'pH', 'Ammonia', 'Turbidity']]
        table_data.extend(readings)
        
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name="sensor_report.pdf", mimetype='application/pdf')
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
