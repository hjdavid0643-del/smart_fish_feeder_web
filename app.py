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

# ---- hard-coded admin (via env vars) ----
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


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

        # ---- NO FIRESTORE HERE ----
        if email != ADMIN_EMAIL or password != ADMIN_PASSWORD:
            return render_template(
                "login.html",
                error="Invalid email or password",
            )

        # login success
        session["user"] = email
        session["role"] = "admin"
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# You can keep /register, /reset_password, /change_password
# OR comment them out if you won't use them now.
# They still touch Firestore, but they are not required
# for a single-admin setup.

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
# (unchanged)
# ... keep the rest of your code from dashboard() onwards exactly as you posted ...


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
