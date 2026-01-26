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
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime, timedelta
import os
import io
import json
from google.api_core.exceptions import ResourceExhausted

# =========================
# CONFIG / FLAGS
# =========================
FIRESTORE_LOGIN_DISABLED = False  # temporary hardcoded flag to bypass login on quota issues

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
CORS(app)

# =========================
# FIREBASE / FIRESTORE INIT
# =========================
def init_firebase():
    try:
        FIREBASE_KEY_PATH = "/etc/secrets/authentication-fish-feeder-firebase-…"  # exact filename

        cred = credentials.Certificate(FIREBASE_KEY_PATH)

        if not firebase_admin.apps:
            firebase_admin.initialize_app(cred)

        return firestore.client()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("Error initializing Firebase:", e)
        return None


db = init_firebase()
serializer = URLSafeTimedSerializer(app.secret_key)

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
# BASIC ROUTES
# =========================
@app.route("/")
def home():
    return redirect(url_for("login"))


# =========================
# AUTH ROUTES
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    # optional global flag to disable login when Firestore quota is dead
    if FIRESTORE_LOGIN_DISABLED or os.environ.get("FIRESTORE_LOGIN_DISABLED", "0") == "1":
        return render_template(
            "login.html",
            error="Login temporarily disabled. Firestore quota exceeded. Please try again later.",
        )

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template("login.html", error="Please enter email and password")

        if db is None:
            return render_template("login.html", error="Firestore not initialized on server")

        try:
            users_q = (
                db.collection("users")
                .where("email", "==", email)
                .limit(1)
                .stream()
            )
            user_doc = next(users_q, None)
        except ResourceExhausted:
            return render_template(
                "login.html", error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template("login.html", error=f"Firestore error: {e}")

        if not user_doc:
            return render_template("login.html", error="Invalid email or password")

        data = user_doc.to_dict() or {}
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


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template("register.html", error="Please fill all fields")

        if db is None:
            return render_template(
                "register.html", error="Firestore not initialized on server"
            )

        try:
            existing_q = (
                db.collection("users")
                .where("email", "==", email)
                .limit(1)
                .stream()
            )
            existing_doc = next(existing_q, None)
            if existing_doc:
                return render_template("register.html", error="Email already exists")

            db.collection("users").add(
                {"email": email, "password": password, "role": "worker"}
            )
        except ResourceExhausted:
            return render_template(
                "register.html", error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template("register.html", error=f"Firestore error: {e}")

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/resetpassword", methods=["GET", "POST"])
def resetpassword():
    if request.method == "POST":
        email = request.form.get("email")
        if not email:
            return render_template("reset.html", error="Please enter your email")

        if db is None:
            return render_template(
                "reset.html", error="Firestore not initialized on server"
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
            return render_template(
                "reset.html", error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template("reset.html", error=f"Firestore error: {e}")

        if not user_doc:
            return render_template("reset.html", error="Email not found")

        token = serializer.dumps(email, salt="password-reset")
        reset_link = url_for("changepassword", token=token, _external=True)
        return render_template("reset.html", success=True, resetlink=reset_link)

    return render_template("reset.html")


@app.route("/changepassword/<token>", methods=["GET", "POST"])
def changepassword(token):
    try:
        email = serializer.loads(token, salt="password-reset", max_age=600)
    except Exception:
        return "Invalid or expired token"

    if request.method == "POST":
        new_password = request.form.get("password")
        if not new_password:
            return render_template("change.html", error="Please enter a new password")

        if db is None:
            return render_template(
                "change.html", error="Firestore not initialized on server"
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
            return render_template(
                "change.html", error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template("change.html", error=f"Firestore error: {e}")

        if not user_doc:
            return "User not found"

        user_doc.reference.update({"password": new_password})
        return redirect(url_for("login"))

    return render_template("change.html")


# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    if db is None:
        return render_template(
            "dashboard.html",
            readings=[],
            summary="Firestore not initialized on server",
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
    except ResourceExhausted:
        return render_template(
            "dashboard.html",
            readings=[],
            summary="Database quota exceeded. Please try again later.",
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
    except Exception:
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
        if isinstance(created, datetime):
            created_str = created.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_str = created
        turb = normalize_turbidity(docdata.get("turbidity"))
        data.append(
            {
                "temperature": docdata.get("temperature"),
                "ph": docdata.get("ph"),
                "ammonia": docdata.get("ammonia"),
                "turbidity": turb,
                "createdAt": created_str,
            }
        )

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
        feederalert = "Feeder status unavailable"
        feederalertcolor = "gray"

    lowfeedalert = None
    lowfeedcolor = "#ff7043"
    try:
        hopperdoc = db.collection("devices").document("ESP32002").get()
        if hopperdoc.exists:
            hdata = hopperdoc.to_dict() or {}
            levelpercent = hdata.get("feedlevelpercent") or hdata.get(
                "waterlevelpercent"
            )
            if levelpercent is not None and levelpercent < 20:
                lowfeedalert = (
                    f"Low feed level ({levelpercent:.1f}%). Please refill the hopper."
                )
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
# MOSFET PAGE
# =========================
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
    readings_cursor = readings_ref.stream()

    data = []
    for r in readings_cursor:
        docdata = r.to_dict() or {}
        created = docdata.get("createdAt")
        if isinstance(created, datetime):
            created_str = created.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_str = created
        turb = normalize_turbidity(docdata.get("turbidity"))
        data.append(
            {
                "temperature": docdata.get("temperature"),
                "ph": docdata.get("ph"),
                "ammonia": docdata.get("ammonia"),
                "turbidity": turb,
                "createdAt": created_str,
            }
        )

    return render_template("mosfet.html", readings=data)


# =========================
# FEEDING CONTROL PAGE
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
            if isinstance(created, datetime):
                created_str = created.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_str = created
            readings.append(
                {
                    "temperature": d.get("temperature"),
                    "ph": d.get("ph"),
                    "ammonia": d.get("ammonia"),
                    "turbidity": normalize_turbidity(d.get("turbidity")),
                    "createdAt": created_str,
                }
            )

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
            if isinstance(created, datetime):
                created_str = created.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_str = created
            allreadings.append(
                {
                    "temperature": d.get("temperature"),
                    "ph": d.get("ph"),
                    "ammonia": d.get("ammonia"),
                    "turbidity": normalize_turbidity(d.get("turbidity")),
                    "createdAt": created_str,
                }
            )

        chartlabels = []
        charttemp = []
        chartph = []
        chartammonia = []
        chartturbidity = []

        for r in reversed(readings):
            chartlabels.append(r.get("createdAt", "N/A"))
            charttemp.append(r.get("temperature", 0))
            chartph.append(r.get("ph", 0))
            chartammonia.append(r.get("ammonia", 0))
            chartturbidity.append(r.get("turbidity", 0))

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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch


@app.route("/exportpdf")
@login_required
def exportpdf():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

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
            return jsonify(
                {
                    "status": "error",
                    "message": "Database quota exceeded while generating PDF. Please try again later.",
                }
            ), 503

        data = []
        for r in readings_cursor:
            docdata = r.to_dict() or {}
            data.append(
                {
                    "temperature": docdata.get("temperature"),
                    "ph": docdata.get("ph"),
                    "ammonia": docdata.get("ammonia"),
                    "turbidity": normalize_turbidity(docdata.get("turbidity")),
                    "createdAt": docdata.get("createdAt"),
                }
            )

        pdf_buffer = io.BytesIO()
        docpdf = SimpleDocTemplate(pdf_buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=20,
            textColor=colors.HexColor("#1f77b4"),
            alignment=1,  # center
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
                if isinstance(createddt, datetime):
                    createdstr = createddt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    createdstr = str(createddt) if createddt else ""
                tabledata.append(
                    [
                        createdstr,
                        "" if r["temperature"] is None else f"{r['temperature']:.2f}",
                        "" if r["ph"] is None else f"{r['ph']:.2f}",
                        "" if r["ammonia"] is None else f"{r['ammonia']:.2f}",
                        "" if r["turbidity"] is None else f"{r['turbidity']:.2f}",
                    ]
                )
        else:
            tabledata.append(["No data in last 24 hours", "", "", "", ""])

        table = Table(tabledata, repeatRows=1)
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
# MOTOR / FEEDER CONTROL (ESP32001)
# =========================
@app.route("/controlmotor", methods=["POST"])
@api_login_required
def controlmotor():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set(
                {
                    "motorspeed": 0,
                    "motorstatus": "off",
                    "updatedAt": datetime.utcnow(),
                },
                merge=True,
            )
            return jsonify({"status": "success", "message": "Motor turned OFF"}), 200

        elif action == "on":
            db.collection("devices").document("ESP32001").set(
                {
                    "motorspeed": int(speed),
                    "motorstatus": "on",
                    "updatedAt": datetime.utcnow(),
                },
                merge=True,
            )
            return jsonify(
                {"status": "success", "message": f"Motor turned ON at {speed}"}
            ), 200

        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify(
                    {"status": "error", "message": "Speed must be 0-100"}
                ), 400

            db.collection("devices").document("ESP32001").set(
                {
                    "motorspeed": speedvalue,
                    "motorstatus": "on" if speedvalue > 0 else "off",
                    "updatedAt": datetime.utcnow(),
                },
                merge=True,
            )
            return jsonify(
                {"status": "success", "message": f"Speed set to {speedvalue}"}
            ), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/getmotorstatus", methods=["GET"])
@api_login_required
def getmotorstatus():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            data = devicedoc.to_dict() or {}
            return jsonify(
                {
                    "status": "success",
                    "motorspeed": data.get("motorspeed", 0),
                    "motorstatus": data.get("motorstatus", "off"),
                }
            ), 200
        return jsonify({"status": "success", "motorspeed": 0, "motorstatus": "off"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/controlfeeder", methods=["POST"])
@api_login_required
def controlfeeder():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        data = request.get_json() or request.form
        action = data.get("action")
        speed = data.get("speed", 50)

        if action == "off":
            db.collection("devices").document("ESP32001").set(
                {
                    "feederspeed": 0,
                    "feederstatus": "off",
                    "updatedAt": datetime.utcnow(),
                },
                merge=True,
            )
            return jsonify({"status": "success", "message": "Feeder turned OFF"}), 200

        elif action == "on":
            db.collection("devices").document("ESP32001").set(
                {
                    "feederspeed": int(speed),
                    "feederstatus": "on",
                    "updatedAt": datetime.utcnow(),
                },
                merge=True,
            )
            return jsonify(
                {"status": "success", "message": f"Feeder turned ON at {speed}"}
            ), 200

        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify(
                    {"status": "error", "message": "Speed must be 0-100"}
                ), 400

            db.collection("devices").document("ESP32001").set(
                {
                    "feederspeed": speedvalue,
                    "feederstatus": "on" if speedvalue > 0 else "off",
                    "updatedAt": datetime.utcnow(),
                },
                merge=True,
            )
            return jsonify(
                {"status": "success", "message": f"Feeder speed set to {speedvalue}"}
            ), 200

        return jsonify({"status": "error", "message": "Invalid action"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/getfeedingstatus", methods=["GET"])
@api_login_required
def getfeedingstatus():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            data = devicedoc.to_dict() or {}
            return jsonify(
                {
                    "status": "success",
                    "feederspeed": data.get("feederspeed", 0),
                    "feederstatus": data.get("feederstatus", "off"),
                }
            ), 200

        return jsonify({"status": "success", "feederspeed": 0, "feederstatus": "off"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================
# FEEDING SCHEDULE (ESP32001)
# =========================
@app.route("/savefeedingschedule", methods=["POST"])
@api_login_required
def savefeedingschedule():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        data = request.get_json() or request.form
        firstfeed = data.get("firstfeed")
        secondfeed = data.get("secondfeed")
        duration = data.get("duration")

        if not firstfeed or not secondfeed or not duration:
            return jsonify({"status": "error", "message": "All fields required"}), 400

        db.collection("devices").document("ESP32001").set(
            {
                "feedingschedule": {
                    "firstfeed": firstfeed,
                    "secondfeed": secondfeed,
                    "duration": int(duration),
                },
                "scheduleenabled": True,
                "updatedAt": datetime.utcnow(),
            },
            merge=True,
        )
        return jsonify({"status": "success", "message": "Feeding schedule saved"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/getfeedingscheduleinfo", methods=["GET"])
@api_login_required
def getfeedingscheduleinfo():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
        if devicedoc.exists:
            data = devicedoc.to_dict() or {}
            schedule = data.get("feedingschedule", {})
            return jsonify(
                {
                    "status": "success",
                    "schedule": schedule,
                    "enabled": data.get("scheduleenabled", False),
                }
            ), 200

        return jsonify({"status": "success", "schedule": {}, "enabled": False}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================
# SENSOR API ROUTES (ESP32001 + ESP32002)
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

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
        docref.set(
            {
                "temperature": temperature,
                "ph": ph,
                "ammonia": ammonia,
                "turbidity": turbidity,
                "distance": distance,
                "createdAt": datetime.utcnow(),
            }
        )

        return jsonify(
            {"status": "success", "message": f"Reading saved for {deviceid}"}
        ), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/apilatestreadings", methods=["GET"])
def apilatestreadings():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(50)
        )
        readings_cursor = readings_ref.stream()

        data = []
        for r in readings_cursor:
            docdata = r.to_dict() or {}
            created = docdata.get("createdAt")
            if isinstance(created, datetime):
                created_str = created.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_str = created
            turb = normalize_turbidity(docdata.get("turbidity"))
            data.append(
                {
                    "temperature": docdata.get("temperature"),
                    "ph": docdata.get("ph"),
                    "ammonia": docdata.get("ammonia"),
                    "turbidity": turb,
                    "createdAt": created_str,
                }
            )

        data = list(reversed(data))
        labels = [r["createdAt"] for r in data]
        temp = [r["temperature"] for r in data]
        ph = [r["ph"] for r in data]
        ammonia = [r["ammonia"] for r in data]
        turbidity = [r["turbidity"] for r in data]

        return jsonify(
            {
                "labels": labels,
                "temp": temp,
                "ph": ph,
                "ammonia": ammonia,
                "turbidity": turbidity,
            }
        ), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/historical", methods=["GET"])
def historical():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32001")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
        )
        readings = readings_ref.stream()

        data = []
        for r in readings:
            docdata = r.to_dict() or {}
            created = docdata.get("createdAt")
            if isinstance(created, datetime):
                created_str = created.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_str = created
            turb = normalize_turbidity(docdata.get("turbidity"))
            data.append(
                {
                    "temperature": docdata.get("temperature"),
                    "ph": docdata.get("ph"),
                    "ammonia": docdata.get("ammonia"),
                    "turbidity": turb,
                    "createdAt": created_str,
                }
            )

        return jsonify({"status": "success", "readings": data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/apiultrasonicesp322", methods=["GET"])
def apiultrasonicesp322():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        readings_ref = (
            db.collection("devices")
            .document("ESP32002")
            .collection("readings")
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(100)
        )
        readings_cursor = readings_ref.stream()

        data = []
        for r in readings_cursor:
            docdata = r.to_dict() or {}
            created = docdata.get("createdAt")
            if isinstance(created, datetime):
                created_str = created.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_str = created
            data.append(
                {
                    "distance": docdata.get("distance"),
                    "createdAt": created_str,
                }
            )

        data = list(reversed(data))
        labels = [r["createdAt"] for r in data]
        distances = [r["distance"] for r in data]

        return jsonify({"status": "success", "labels": labels, "distance": distances}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/apicheckfeedcommand", methods=["GET"])
def apicheckfeedcommand():
    deviceid = request.args.get("deviceid", "ESP32001")
    # Placeholder: always returns "none"
    return jsonify({"status": "success", "deviceid": deviceid, "command": "none"}), 200


# =========================
# HEALTH CHECK
# =========================
@app.route("/testfirestore")
def testfirestore():
    try:
        if db is None:
            return jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ), 500
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
    app.run(host="0.0.0.0", port=5000, debug=True)
