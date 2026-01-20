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

from google.api_core.exceptions import ResourceExhausted

from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors


# ================== APP SETUP ==================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
CORS(app)

# When set to "1" in Render env vars, app will not call Firestore for login
FIRESTORE_LOGIN_DISABLED = os.environ.get("FIRESTORE_LOGIN_DISABLED", "0") == "1"


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
        import traceback
        traceback.print_exc()
        print("Error initializing Firebase:", e)
        return None


db = init_firebase()
serializer = URLSafeTimedSerializer(app.secret_key)


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
    # If quota is currently exhausted, short‑circuit immediately
    if FIRESTORE_LOGIN_DISABLED:
        return render_template(
            "login.html",
            error="Login temporarily disabled: database quota exceeded. "
                  "Please try again later."
        )

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template(
                "login.html",
                error="Please enter email and password"
            )

        if db is None:
            return render_template(
                "login.html",
                error="Firestore not initialized on server"
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
            # Optional: flip the flag so following requests also short‑circuit
            # (requires restarting or re-reading env to take effect)
            return render_template(
                "login.html",
                error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template(
                "login.html",
                error=f"Firestore error: {e}"
            )

        if not user_doc:
            return render_template(
                "login.html",
                error="Invalid email or password"
            )

        data = user_doc.to_dict() or {}
        if data.get("password") != password:
            return render_template(
                "login.html",
                error="Invalid email or password"
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
                error="Please fill all fields"
            )

        if db is None:
            return render_template(
                "register.html",
                error="Firestore not initialized on server"
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
                return render_template(
                    "register.html",
                    error="Email already exists"
                )

            db.collection("users").add(
                {"email": email, "password": password, "role": "worker"}
            )
        except ResourceExhausted:
            return render_template(
                "register.html",
                error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template(
                "register.html",
                error=f"Firestore error: {e}"
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
                error="Please enter your email"
            )

        if db is None:
            return render_template(
                "reset.html",
                error="Firestore not initialized on server"
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
                "reset.html",
                error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template(
                "reset.html",
                error=f"Firestore error: {e}"
            )

        if not user_doc:
            return render_template("reset.html", error="Email not found")

        token = serializer.dumps(email, salt="password-reset")
        reset_link = url_for("change_password", token=token, _external=True)

        return render_template(
            "reset.html",
            success=True,
            reset_link=reset_link
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
                error="Please enter a new password"
            )

        if db is None:
            return render_template(
                "change.html",
                error="Firestore not initialized on server"
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
                "change.html",
                error="Database quota exceeded. Please try again later."
            )
        except Exception as e:
            return render_template(
                "change.html",
                error=f"Firestore error: {e}"
            )

        if not user_doc:
            return "User not found"

        user_doc.reference.update({"password": new_password})
        return redirect(url_for("login"))

    return render_template("change.html")


# ================== DASHBOARD (unchanged below) ==================
# (keep the rest of your file exactly as you posted)

# ... copy the rest of your routes from dashboard() downwards without changes ...

# ================== MAIN (LOCAL ONLY) ==================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
