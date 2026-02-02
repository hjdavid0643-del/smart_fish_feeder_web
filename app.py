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
        FIREBASE_KEY_PATH = "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
        FIREBASE_KEY_PATH = (
            "/etc/secrets/"
            "authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
        )
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_app = firebase_admin.initialize_app(cred)
@@ -55,8 +57,6 @@

db = init_firebase()



# =========================
# HELPERS
# =========================
@@ -66,6 +66,7 @@
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


@@ -75,10 +76,12 @@
        if "user" not in session:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated


def normalize_turbidity(value):
    """Convert raw turbidity to a sane float or None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
@@ -91,75 +94,32 @@


def to_float_or_none(value):
    """Convert to float, return None if invalid."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def normalize_turbidity(value):
    ...
    return v


def to_float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@app.route("/update_temp_ph", methods=["POST"])
def update_temp_ph():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500

    try:
        data = request.get_json() or {}
        temperature = to_float_or_none(data.get("temperature"))
        ph = to_float_or_none(data.get("ph"))

        if temperature is None or ph is None:
            return jsonify(
                {"status": "error", "message": "temperature and ph required"}
            ), 400

        db.collection("devices").document("ESP32_001").set(
            {
                "temperature": temperature,
                "ph": ph,
                "updatedAt": datetime.utcnow(),
            },
            merge=True,
        )
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================
# BASIC ROUTES
# =========================
@app.route("/")
def home():
    
   return redirect(url_for("login"))

    return redirect(url_for("login"))

# =========================
# AUTH ROUTES (simple session auth)
# =========================
# Hard‑coded test user; replace with real user storage later.
VALID_USERS = {
    "hjdavid0643@iskwela.psau.edu.ph": "0123456789",
}



# =========================
# AUTH ROUTES (simple session auth)
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if FIRESTORE_LOGIN_DISABLED or os.environ.get("FIRESTORE_LOGIN_DISABLED", "0") == "1":
    if FIRESTORE_LOGIN_DISABLED or os.environ.get(
        "FIRESTORE_LOGIN_DISABLED", "0"
    ) == "1":
        return render_template(
            "login.html",
            error="Login temporarily disabled. Please try again later.",
@@ -170,7 +130,9 @@
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("login.html", error="Email and password are required.")
            return render_template(
                "login.html", error="Email and password are required."
            )

        expected_password = VALID_USERS.get(email)
        if expected_password and expected_password == password:
@@ -195,6 +157,43 @@
def register():
    return "Registration is managed separately (not implemented in backend)."

# =========================
# UPDATE TEMP/PH (optional small API)
# =========================
@app.route("/update_temp_ph", methods=["POST"])
def update_temp_ph():
    if db is None:
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        data = request.get_json() or {}
        temperature = to_float_or_none(data.get("temperature"))
        ph = to_float_or_none(data.get("ph"))

        if temperature is None or ph is None:
            return (
                jsonify(
                    {"status": "error", "message": "temperature and ph required"}
                ),
                400,
            )

        db.collection("devices").document("ESP32_001").set(
            {
                "temperature": temperature,
                "ph": ph,
                "updatedAt": datetime.utcnow(),
            },
            merge=True,
        )
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# DASHBOARD
@@ -316,7 +315,9 @@
        hopperdoc = db.collection("devices").document("ESP32002").get()
        if hopperdoc.exists:
            hdata = hopperdoc.to_dict() or {}
            levelpercent = hdata.get("feedlevelpercent") or hdata.get("waterlevelpercent")
            levelpercent = hdata.get("feedlevelpercent") or hdata.get(
                "waterlevelpercent"
            )
            if levelpercent is not None and levelpercent < 20:
                lowfeedalert = (
                    f"Low feed level ({levelpercent:.1f}%). Please refill the hopper."
@@ -348,7 +349,6 @@
        lowfeedcolor=lowfeedcolor,
    )


# =========================
# MOSFET PAGE
# =========================
@@ -388,7 +388,6 @@

    return render_template("mosfet.html", readings=data)


# =========================
# FEEDING CONTROL PAGE
# =========================
@@ -500,17 +499,19 @@
            chartturbidity=[],
        )


# =========================
# PDF EXPORT (LAST 24 HOURS)
# =========================
@app.route("/exportpdf")
@login_required
def exportpdf():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        now = datetime.utcnow()
@@ -526,12 +527,16 @@
        try:
            readings_cursor = readings_ref.stream()
        except ResourceExhausted:
            return jsonify(
                {
                    "status": "error",
                    "message": "Database quota exceeded while generating PDF. Please try again later.",
                }
            ), 503
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Database quota exceeded while generating PDF. "
                        "Please try again later.",
                    }
                ),
                503,
            )

        data = []
        for r in readings_cursor:
@@ -569,7 +574,9 @@
        )
        elements.append(Spacer(1, 0.2 * inch))

        tabledata = [["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]]
        tabledata = [
            ["Time", "Temperature (°C)", "pH", "Ammonia (ppm)", "Turbidity (NTU)"]
        ]

        if data:
            for r in data:
@@ -606,7 +613,9 @@
            )
        )

        elements.append(Paragraph("Recent Sensor Readings (24 hours)", styles["Heading2"]))
        elements.append(
            Paragraph("Recent Sensor Readings (24 hours)", styles["Heading2"])
        )
        elements.append(table)

        docpdf.build(elements)
@@ -621,17 +630,19 @@
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
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        data = request.get_json() or request.form
@@ -665,9 +676,10 @@
        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify(
                    {"status": "error", "message": "Speed must be 0-100"}
                ), 400
                return (
                    jsonify({"status": "error", "message": "Speed must be 0-100"}),
                    400,
                )

            db.collection("devices").document("ESP32001").set(
                {
@@ -690,9 +702,12 @@
@api_login_required
def getmotorstatus():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
@@ -714,9 +729,12 @@
@api_login_required
def controlfeeder():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        data = request.get_json() or request.form
@@ -750,9 +768,10 @@
        elif action == "setspeed":
            speedvalue = int(speed)
            if speedvalue < 0 or speedvalue > 100:
                return jsonify(
                    {"status": "error", "message": "Speed must be 0-100"}
                ), 400
                return (
                    jsonify({"status": "error", "message": "Speed must be 0-100"}),
                    400,
                )

            db.collection("devices").document("ESP32001").set(
                {
@@ -775,9 +794,12 @@
@api_login_required
def getfeedingstatus():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
@@ -795,17 +817,19 @@
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
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        data = request.get_json() or request.form
@@ -814,7 +838,10 @@
        duration = data.get("duration")

        if not firstfeed or not secondfeed or not duration:
            return jsonify({"status": "error", "message": "All fields required"}), 400
            return (
                jsonify({"status": "error", "message": "All fields required"}),
                400,
            )

        db.collection("devices").document("ESP32001").set(
            {
@@ -837,9 +864,12 @@
@api_login_required
def getfeedingscheduleinfo():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        devicedoc = db.collection("devices").document("ESP32001").get()
@@ -858,16 +888,18 @@
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
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        data = request.get_json()
@@ -908,86 +940,12 @@
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
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
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


# === INSERTED: DEVICE CONTROL API FOR ESP32_002 ===
@app.route("/api/device_control", methods=["GET"])
def api_device_control():
    """
    Control API for ESP32_002 water pump board.
    Returns simple on/off states for pump, fill valve, drain valve.
    For now, everything is OFF by default.
    """
    return jsonify(
        {
            "pump_status": "off",          # "on" or "off"
            "fill_valve_status": "off",    # "on" or "off"
            "drain_valve_status": "off",   # "on" or "off"
        }
    ), 200
# === END INSERT ===

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
@@ -1041,9 +999,12 @@
@app.route("/historical", methods=["GET"])
def historical():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        readings_ref = (
@@ -1081,9 +1042,12 @@
@app.route("/apiultrasonicesp322", methods=["GET"])
def apiultrasonicesp322():
    if db is None:
        return jsonify(
            {"status": "error", "message": "Firestore not initialized on server"}
        ), 500
        return (
            jsonify(
                {"status": "error", "message": "Firestore not initialized on server"}
            ),
            500,
        )

    try:
        readings_ref = (
@@ -1119,22 +1083,47 @@
        return jsonify({"status": "error", "message": str(e)}), 500


# === DEVICE CONTROL API FOR ESP32_002 ===
@app.route("/api/device_control", methods=["GET"])
def api_device_control():
    """
    Control API for ESP32_002 water pump board.
    Returns simple on/off states for pump, fill valve, drain valve.
    For now, everything is OFF by default.
    """
    return jsonify(
        {
            "pump_status": "off",
            "fill_valve_status": "off",
            "drain_valve_status": "off",
        }
    ), 200

# =========================
# FEED COMMAND CHECK
# =========================
@app.route("/apicheckfeedcommand", methods=["GET"])
def apicheckfeedcommand():
    deviceid = request.args.get("deviceid", "ESP32001")
    # For now always "none"; later you can add real commands
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
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Firestore not initialized on server",
                    }
                ),
                500,
            )
        doc = db.collection("devices").document("ESP32001").get()
        return jsonify({"status": "ok", "exists": doc.exists}), 200
    except Exception as e:
@@ -1145,9 +1134,8 @@
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
