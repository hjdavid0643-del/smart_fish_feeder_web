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

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
CORS(app)

# =========================
# FIREBASE / FIRESTORE INIT
# =========================
def init_firebase():
    try:
        FIREBASE_KEY_PATH = os.environ.get(
            "FIREBASE_KEY_PATH", 
            "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
        )
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_app = firebase_admin.initialize_app(cred)
        else:
            firebase_app = firebase_admin.get_app()
        return firestore.client(app=firebase_app)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("Error initializing Firebase:", e)
        return None

db = init_firebase()

# =========================
# HELPERS - ALL FIXED
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
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_key = os.environ.get("API_SECRET", "fishfeeder123")
        if not api_key or api_key != expected_key:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def normalize_turbidity(value):
    try:
        if value is None:
            return None
        val = float(value)
        return max(0, min(100, val))  # Clamp 0-100 NTU
    except:
        return None

def to_float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

# Hard-coded test users
VALID_USERS = {
    "admin@example.com": "admin123",
    "worker@example.com": "worker123",
}

# =========================
# ROUTES
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            return render_template("login.html", error="Email and password required.")
        expected_password = VALID_USERS.get(email)
        if expected_password and expected_password == password:
            session["user"] = email
            session["role"] = "worker"
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register")  # REMOVED @login_required
def register():
    return render_template("register.html", error=None)

@app.route("/dashboard")
@login_required
def dashboard():
    if db is None:
        return render_template(
            "dashboard.html",
            readings=[],
            summary="Firestore not available",
            alertcolor="gray",
            timelabels=[],
            tempvalues=[],
            phvalues=[],
            ammoniavalues=[],
            turbidityvalues=[],
            feederalert="Feeder status unavailable",
            feederalertcolor="gray",
            lowfeedalert=None,
            lowfeedcolor="#ff7043",
        )

    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        readings_cursor = readings_ref.stream()
    except:
        return render_template(
            "dashboard.html",
            readings=[],
            summary="Error loading data.",
            alertcolor="gray",
            timelabels=[],
            tempvalues=[],
            phvalues=[],
            ammoniavalues=[],
            turbidityvalues=[],
            feederalert="Feeder status unavailable",
            feederalertcolor="gray",
            lowfeedalert=None,
            lowfeedcolor="#ff7043",
        )

    data = []
    for r in readings_cursor:
        docdata = r.to_dict() or {}
        created = docdata.get("createdAt")
        created_str = created.strftime("%Y-%m-%d %H:%M:%S") if isinstance(created, datetime) else str(created)
        turb = normalize_turbidity(docdata.get("turbidity"))
        data.append({
            "temperature": docdata.get("temperature"),
            "ph": docdata.get("ph"),
            "ammonia": docdata.get("ammonia"),
            "turbidity": turb,
            "createdAt": created_str,
        })

    data = list(reversed(data))
    
    # Water quality summary
    summary = "All systems normal."
    alertcolor = "green"
    if data:
        last_turb = data[-1].get("turbidity", 0)
        if last_turb > 100:
            summary = "Water is too cloudy! Danger"
            alertcolor = "gold"
        elif last_turb > 50:
            summary = "Water is getting cloudy."
            alertcolor = "orange"

    # Feeder status
    feederalert = "Feeder OFF"
    feederalertcolor = "lightcoral"
    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            d = devicedoc.to_dict() or {}
            if d.get("feederstatus") == "on" and d.get("feederspeed", 0) > 0:
                feederalert = f"Feeding {d.get('feederspeed')}%"
                feederalertcolor = "limegreen"
    except:
        pass

    # Low feed alert
    lowfeedalert = None
    try:
        hopperdoc = db.collection("devices").document("ESP32002").get()
        if hopperdoc.exists:
            level = hopperdoc.to_dict().get("feedlevelpercent")
            if level and level < 20:
                lowfeedalert = f"Low feed: {level:.1f}%"
    except:
        pass

    return render_template(
        "dashboard.html",
        readings=data[-10:],
        summary=summary,
        alertcolor=alertcolor,
        timelabels=[r["createdAt"] for r in data],
        tempvalues=[r["temperature"] or 0 for r in data],
        phvalues=[r["ph"] or 0 for r in data],
        ammoniavalues=[r["ammonia"] or 0 for r in data],
        turbidityvalues=[r["turbidity"] or 0 for r in data],
        feederalert=feederalert,
        feederalertcolor=feederalertcolor,
        lowfeedalert=lowfeedalert,
    )

@app.route("/mosfet")
@login_required
def mosfet():
    if db is None:
        return render_template("mosfet.html", readings=[])
    
    readings_ref = (
        db.collection("devices")
        .document("ESP32001")
        .collection("readings")
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(50)
    )
    data = []
    for r in readings_ref.stream():
        docdata = r.to_dict() or {}
        created = docdata.get("createdAt")
        created_str = created.strftime("%Y-%m-%d %H:%M:%S") if isinstance(created, datetime) else str(created)
        data.append({
            "temperature": docdata.get("temperature"),
            "ph": docdata.get("ph"),
            "ammonia": docdata.get("ammonia"),
            "turbidity": normalize_turbidity(docdata.get("turbidity")),
            "createdAt": created_str,
        })
    return render_template("mosfet.html", readings=data)

# =========================
# FEEDING CONTROL PAGE (KEEP ONLY THIS ONE)
# =========================
@app.route("/controlfeeding")
@login_required
def controlfeedingpage():
    if db is None:
        return render_template(
            "control.html",
            error="Firestore not initialized on server",
            readings=[],
            allreadings=[],
            summary="Error loading data",
            chartlabels=[],
            charttemp=[],
            chartph=[],
            chartammonia=[],
            chartturbidity=[],
        )

    try:
        # Recent readings (10)
        readings_ref = (
            db.collection("devices")
            .document("ESP32001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(10)
        )
        readings = []
        for docsnap in readings_ref.stream():
            d = docsnap.to_dict() or {}
            created = d.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if isinstance(created, datetime) else str(created)
            readings.append({
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": normalize_turbidity(d.get("turbidity")),
                "createdAt": created_str,
            })

        # Chart data (50)
        allreadings_ref = (
            db.collection("devices")
            .document("ESP32001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        allreadings = []
        for docsnap in allreadings_ref.stream():
            d = docsnap.to_dict() or {}
            created = d.get("createdAt")
            created_str = created.strftime("%Y-%m-%d %H:%M:%S") if isinstance(created, datetime) else str(created)
            allreadings.append({
                "temperature": d.get("temperature"),
                "ph": d.get("ph"),
                "ammonia": d.get("ammonia"),
                "turbidity": normalize_turbidity(d.get("turbidity")),
                "createdAt": created_str,
            })

        chartlabels = [r.get("createdAt", "N/A") for r in reversed(allreadings)]
        charttemp = [r.get("temperature", 0) or 0 for r in reversed(allreadings)]
        chartph = [r.get("ph", 0) or 0 for r in reversed(allreadings)]
        chartammonia = [r.get("ammonia", 0) or 0 for r in reversed(allreadings)]
        chartturbidity = [r.get("turbidity", 0) or 0 for r in reversed(allreadings)]

        summary = "Feeding Motor Control Dashboard"

        return render_template(
            "control.html",
            readings=readings,
            allreadings=allreadings,
            summary=summary,
            chartlabels=chartlabels,
            charttemp=charttemp,
            chartph=chartph,
            chartammonia=chartammonia,
            chartturbidity=chartturbidity,
        )
    except Exception as e:
        return render_template(
            "control.html",
            error=str(e),
            readings=[],
            allreadings=[],
            summary="Error loading data",
            chartlabels=[],
            charttemp=[],
            chartph=[],
            chartammonia=[],
            chartturbidity=[],
        )

# =========================
# PDF EXPORT (LAST 24 HOURS)
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        now = datetime.utcnow()
        twentyfour_hours_ago = now - timedelta(hours=24)
        readings_ref = (
            db.collection("devices")
            .document("ESP32001")
            .collection("readings")
            .where("createdAt", ">=", twentyfour_hours_ago)
            .order_by("createdAt", direction=firestore.Query.ASCENDING)
        )

        try:
            readings_cursor = readings_ref.stream()
        except ResourceExhausted:
            return jsonify({
                "status": "error",
                "message": "Database quota exceeded while generating PDF."
            }), 503

        data = []
        for r in readings_cursor:
            docdata = r.to_dict() or {}
            data.append({
                "temperature": docdata.get("temperature"),
                "ph": docdata.get("ph"),
                "ammonia": docdata.get("ammonia"),
                "turbidity": normalize_turbidity(docdata.get("turbidity")),
                "createdAt": docdata.get("createdAt"),
            })

        pdf_buffer = io.BytesIO()
        docpdf = SimpleDocTemplate(pdf_buffer, pagesize=letter)
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

        elements.append(Paragraph("Water Quality Monitoring Report", title_style))
        elements.append(
            Paragraph(
                f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} (last 24 hours)",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 0.2 * inch))

        tabledata = [["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]]
        if data:
            for r in data:
                createddt = r["createdAt"]
                createdstr = createddt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(createddt, datetime) else str(createddt)
                tabledata.append([
                    createdstr,
                    f"{r['temperature']:.2f}" if r['temperature'] is not None else "",
                    f"{r['ph']:.2f}" if r['ph'] is not None else "",
                    f"{r['ammonia']:.2f}" if r['ammonia'] is not None else "",
                    f"{r['turbidity']:.2f}" if r['turbidity'] is not None else "",
                ])
        else:
            tabledata.append(["No data in last 24 hours", "", "", "", ""])

        table = Table(tabledata, repeatRows=1)
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

        docpdf.build(elements)
        pdf_buffer.seek(0)
        timestamp = now.strftime("%Y%m%d%H%M%S")
        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"waterquality24h_{timestamp}.pdf",
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# API ROUTES (ESP32)
# =========================
@app.route("/controlmotor", methods=["POST"])
@api_login_required
def controlmotor():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not available"}), 500

    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set({
                "motorspeed": 0,
                "motorstatus": "off",
                "updatedAt": datetime.utcnow(),
            }, merge=True)
            return jsonify({"status": "success", "message": "Motor OFF"}), 200

        elif action == "on":
            db.collection("devices").document("ESP32001").set({
                "motorspeed": int(speed),
                "motorstatus": "on",
                "updatedAt": datetime.utcnow(),
            }, merge=True)
            return jsonify({"status": "success", "message": f"Motor ON at {speed}%"}), 200

        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify({"status": "error", "message": "Speed 0-100"}), 400

            db.collection("devices").document("ESP32001").set({
                "motorspeed": speedvalue,
                "motorstatus": "on" if speedvalue > 0 else "off",
                "updatedAt": datetime.utcnow(),
            }, merge=True)
            return jsonify({"status": "success", "message": f"Speed {speedvalue}%"}), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/getmotorstatus", methods=["GET"])
@api_login_required
def getmotorstatus():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not available"}), 500

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            data = devicedoc.to_dict() or {}
            return jsonify({
                "status": "success",
                "motorspeed": data.get("motorspeed", 0),
                "motorstatus": data.get("motorstatus", "off"),
            }), 200
        return jsonify({"status": "success", "motorspeed": 0, "motorstatus": "off"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not available"}), 500

    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set({
                "feederspeed": 0,
                "feederstatus": "off",
                "updatedAt": datetime.utcnow(),
            }, merge=True)
            return jsonify({"status": "success", "message": "Feeder OFF"}), 200

        elif action == "on":
            db.collection("devices").document("ESP32001").set({
                "feederspeed": int(speed),
                "feederstatus": "on",
                "updatedAt": datetime.utcnow(),
            }, merge=True)
            return jsonify({"status": "success", "message": f"Feeder ON at {speed}%"}), 200

        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify({"status": "error", "message": "Speed 0-100"}), 400

            db.collection("devices").document("ESP32001").set({
                "feederspeed": speedvalue,
                "feederstatus": "on" if speedvalue > 0 else "off",
                "updatedAt": datetime.utcnow(),
            }, merge=True)
            return jsonify({"status": "success", "message": f"Feeder speed {speedvalue}%"}), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/getfeedingstatus", methods=["GET"])
@api_login_required
def getfeedingstatus():
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not available"}), 500

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            data = devicedoc.to_dict() or {}
            return jsonify({
                "status": "success",
                "feederspeed": data.get("feederspeed", 0),
                "feederstatus": data.get("feederstatus", "off"),
            }), 200
        return jsonify({"status": "success", "feederspeed": 0, "feederstatus": "off"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/addreading", methods=["POST"])
def addreading():  # Public for ESP32
    if db is None:
        return jsonify({"status": "error", "message": "Firestore not available"}), 500

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        deviceid = data.get("deviceid", "ESP32001")
        temperature = to_float_or_none(data.get("temperature"))
        ph = to_float_or_none(data.get("ph"))
        ammonia = to_float_or_none(data.get("ammonia"))
        turbidity = normalize_turbidity(data.get("turbidity"))
        distance = to_float_or_none(data.get("distance"))

        docref = (
            db.collection("devices")
            .document(deviceid)
            .collection("readings")
            .document()
        )
        docref.set({
            "temperature": temperature,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity,
            "distance": distance,
            "createdAt": datetime.utcnow(),
        })

        return jsonify({"status": "success", "message": f"Reading saved for {deviceid}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# HEALTH CHECK
# =========================
@app.route("/testfirestore")
def testfirestore():
    try:
        if db is None:
            return jsonify({"status": "error", "message": "Firestore not initialized"}), 500
        doc = db.collection("devices").document("ESP32001").get()
        return jsonify({"status": "ok", "exists": doc.exists}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
