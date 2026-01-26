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
    """
    Initialize Firebase Admin using a JSON string stored in
    env var GOOGLE_APPLICATION_CREDENTIALS_JSON on Render.
    """
    try:
        if not firebase_admin.apps:
            sa_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
            if not sa_json:
                raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS_JSON not set")
            info = json.loads(sa_json)
            cred = credentials.Certificate(info)
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

# ... (rest of your routes remain exactly the same as in your original file)
# MOTOR / FEEDER CONTROL, FEEDING SCHEDULE, SENSOR APIs, /testfirestore, /ping, etc.

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
