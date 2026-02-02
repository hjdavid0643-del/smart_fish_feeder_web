from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify, send_file
)
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth
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
# CONFIG / FLAGS
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
        FIREBASE_KEY_PATH = os.environ.get("FIREBASE_KEY_PATH", "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json")
        if not os.path.exists(FIREBASE_KEY_PATH):
            print(f"Firebase key not found at {FIREBASE_KEY_PATH}")
            return None
            
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_admin.initialize_app(cred)
        else:
            print("Firebase already initialized")
            
        return firestore.client()
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("Error initializing Firebase:", e)
        return None

db = init_firebase()

@app.route("/")
def home():
    return redirect(url_for("login"))

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

# =========================
# AUTH ROUTES
# =========================
@app.route("/login", methods=["GET"])
def login():
    if FIRESTORE_LOGIN_DISABLED or os.environ.get("FIRESTORE_LOGIN_DISABLED", "0") == "1":
        return render_template("login.html", error="Login temporarily disabled.")
    if "user" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/session-login", methods=["POST"])
def session_login():
    try:
        data = request.get_json() or {}
        id_token = data.get("id_token")
        if not id_token:
            return jsonify({"status": "error", "message": "Missing token"}), 400
        decoded = auth.verify_id_token(id_token)
        email = decoded.get("email")
        if not email:
            return jsonify({"status": "error", "message": "No email in token"}), 400
        session["user"] = email
        session["role"] = "worker"
        return redirect(url_for("dashboard"))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 401

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# DASHBOARD - FIXED SCOPE
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    if db is None:
        return render_template("dashboard.html", readings=[], summary="No Firestore.")

    readings_cursor = None
    try:
        readings_ref = (db.collection("devices").document("ESP32001")
                       .collection("readings")
                       .order_by("createdAt", direction=firestore.Query.DESCENDING)
                       .limit(50))
        readings_cursor = readings_ref.stream()
    except:
        pass

    data = []
    if readings_cursor:
        for r in readings_cursor:
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
    
    data = list(reversed(data))
    
    summary = "All systems normal."
    alertcolor = "green"
    if data:
        last = data[-1]
        last_turbidity = last.get("turbidity")
        if last_turbidity is not None:
            if last_turbidity > 100:
                summary = "Water is too cloudy! Danger"
                alertcolor = "gold"
            elif last_turbidity > 50:
                summary = "Water is getting cloudy."
                alertcolor = "orange"

    feederalert = "Feeder is currently OFF"
    feederalertcolor = "lightcoral"
    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            d = devicedoc.to_dict() or {}
            feederstatus = d.get("feederstatus", "off")
            feederspeed = d.get("feederspeed", 0)
            if feederstatus == "on" and feederspeed and feederspeed > 0:
                feederalert = f"Feeding in progress at {feederspeed}% speed"
                feederalertcolor = "limegreen"
    except Exception:
        pass

    lowfeedalert = None
    lowfeedcolor = "#ff7043"
    try:
        hopperdoc = db.collection("devices").document("ESP32002").get()
        if hopperdoc.exists:
            hdata = hopperdoc.to_dict() or {}
            levelpercent = hdata.get("feedlevelpercent") or hdata.get("waterlevelpercent")
            if levelpercent is not None and levelpercent < 20:
                lowfeedalert = f"Low feed level ({levelpercent:.1f}%). Please refill the hopper."
    except Exception:
        pass

    timelabels = [r["createdAt"] for r in data]
    tempvalues = [r["temperature"] for r in data]
    phvalues = [r["ph"] for r in data]
    ammoniavalues = [r["ammonia"] for r in data]
    turbidityvalues = [r["turbidity"] for r in data]
    latest10 = data[-10:]

    return render_template(
        "dashboard.html",
        readings=latest10,
        summary=summary,
        alertcolor=alertcolor,
        timelabels=timelabels,
        tempvalues=tempvalues,
        phvalues=phvalues,
        ammoniavalues=ammoniavalues,
        turbidityvalues=turbidityvalues,
        feederalert=feederalert,
        feederalertcolor=feederalertcolor,
        lowfeedalert=lowfeedalert,
        lowfeedcolor=lowfeedcolor,
    )

# =========================
# OTHER PAGES
# =========================
@app.route("/mosfet")
@login_required
def mosfet():
    if db is None:
        return render_template("mosfet.html", readings=[])

    readings_ref = (db.collection("devices").document("ESP32001")
                   .collection("readings")
                   .order_by("createdAt", direction=firestore.Query.DESCENDING)
                   .limit(50))
    readings_cursor = readings_ref.stream()

    data = []
    for r in readings_cursor:
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

@app.route("/controlfeeding")
@login_required
def controlfeedingpage():
    if db is None:
        return render_template("control.html", error="No Firestore.", readings=[], allreadings=[])

    try:
        readings_ref = (db.collection("devices").document("ESP32001")
                       .collection("readings")
                       .order_by("createdAt", direction=firestore.Query.DESCENDING)
                       .limit(10))
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

        chartlabels = [r.get("createdAt", "N/A") for r in reversed(readings)]
        charttemp = [r.get("temperature", 0) for r in reversed(readings)]
        chartph = [r.get("ph", 0) for r in reversed(readings)]
        chartammonia = [r.get("ammonia", 0) for r in reversed(readings)]
        chartturbidity = [r.get("turbidity", 0) for r in reversed(readings)]

        return render_template(
            "control.html",
            readings=readings,
            summary="Feeding Motor Control Dashboard",
            chartlabels=chartlabels,
            charttemp=charttemp,
            chartph=chartph,
            chartammonia=chartammonia,
            chartturbidity=chartturbidity,
        )
    except Exception:
        return render_template("control.html", error="Error loading data", readings=[])

# =========================
# PDF EXPORT
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if db is None:
        return jsonify({"status": "error", "message": "No Firestore"}), 500

    try:
        now = datetime.utcnow()
        twentyfour_hours_ago = now - timedelta(hours=24)
        readings_ref = (db.collection("devices").document("ESP32001")
                       .collection("readings")
                       .where("createdAt", ">=", twentyfour_hours_ago)
                       .order_by("createdAt", direction=firestore.Query.ASCENDING))
        readings_cursor = readings_ref.stream()

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
            "CustomTitle", parent=styles["Heading1"], fontSize=20,
            textColor=colors.HexColor("#1f77b4"), alignment=1, spaceAfter=20,
        )

        elements.append(Paragraph("Water Quality Monitoring Report", title_style))
        elements.append(Paragraph(
            f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} (last 24 hours)",
            styles["Normal"],
        ))
        elements.append(Spacer(1, 0.2 * inch))

        tabledata = [["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]]
        if data:
            for r in data:
                createddt = r["createdAt"]
                createdstr = createddt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(createddt, datetime) else str(createddt)
                tabledata.append([
                    createdstr,
                    f"{r['temperature']:.2f}" if r["temperature"] is not None else "",
                    f"{r['ph']:.2f}" if r["ph"] is not None else "",
                    f"{r['ammonia']:.2f}" if r["ammonia"] is not None else "",
                    f"{r['turbidity']:.2f}" if r["turbidity"] is not None else "",
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
# ESP32 MOTOR CONTROL - NO AUTH (ESP32 compatible)
# =========================
@app.route("/controlmotor", methods=["POST"])
def controlmotor():
    if db is None:
        return jsonify({"status": "error", "message": "No Firestore"}), 500
    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set({
                "motorspeed": 0, "motorstatus": "off", "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Motor OFF"}), 200
        elif action == "on":
            db.collection("devices").document("ESP32001").set({
                "motorspeed": int(speed), "motorstatus": "on", "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Motor ON at {speed}%"}), 200
        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify({"status": "error", "message": "Speed 0-100"}), 400
            db.collection("devices").document("ESP32001").set({
                "motorspeed": speedvalue,
                "motorstatus": "on" if speedvalue > 0 else "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Speed {speedvalue}%"}), 200
        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/getmotorstatus", methods=["GET"])
def getmotorstatus():
    if db is None:
        return jsonify({"status": "error", "message": "No Firestore"}), 500
    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        data = devicedoc.to_dict() or {}
        return jsonify({
            "status": "success",
            "motorspeed": data.get("motorspeed", 0),
            "motorstatus": data.get("motorstatus", "off")
        }), 200
    except:
        return jsonify({"status": "success", "motorspeed": 0, "motorstatus": "off"}), 200

@app.route("/controlfeeder", methods=["POST"])
def controlfeeder():
    if db is None:
        return jsonify({"status": "error", "message": "No Firestore"}), 500
    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set({
                "feederspeed": 0, "feederstatus": "off", "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": "Feeder OFF"}), 200
        elif action == "on":
            db.collection("devices").document("ESP32001").set({
                "feederspeed": int(speed), "feederstatus": "on", "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Feeder ON at {speed}%"}), 200
        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify({"status": "error", "message": "Speed 0-100"}), 400
            db.collection("devices").document("ESP32001").set({
                "feederspeed": speedvalue,
                "feederstatus": "on" if speedvalue > 0 else "off",
                "updatedAt": datetime.utcnow()
            }, merge=True)
            return jsonify({"status": "success", "message": f"Feeder speed {speedvalue}%"}), 200
        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/getfeedingstatus", methods=["GET"])
def getfeedingstatus():
    if db is None:
        return jsonify({"status": "error", "message": "No Firestore"}), 500
    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        data = devicedoc.to_dict() or {}
        return jsonify({
            "status": "success",
            "feederspeed": data.get("feederspeed", 0),
            "feederstatus": data.get("feederstatus", "off")
        }), 200
    except:
        return jsonify({"status": "success", "feederspeed": 0, "feederstatus": "off"}), 200

# =========================
# SENSOR API - ESP32 COMPATIBLE
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    if db is None:
        return jsonify({"status": "error", "message": "No Firestore"}), 500
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

        docref = (db.collection("devices").document(deviceid)
                 .collection("readings").document())
        docref.set({
            "temperature": temperature, "ph": ph, "ammonia": ammonia,
            "turbidity": turbidity, "distance": distance,
            "createdAt": datetime.utcnow()
        })

        return jsonify({"status": "success", "message": f"Reading saved for {deviceid}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# HEALTH CHECK
# =========================
@app.route("/testfirestore")
def testfirestore():
    if db is None:
        return jsonify({"status": "error", "message": "No Firestore"}), 500
    try:
        doc = db.collection("devices").document("ESP32001").get()
        return jsonify({"status": "ok", "exists": doc.exists}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
