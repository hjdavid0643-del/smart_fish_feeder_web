from flask import Flask, request, render_template, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import firebase_admin
from firebase_admin import credentials, firestore
from flask_cors import CORS

# ================= FIREBASE SETUP ====================
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"
CORS(app)

# Get current logged user info
def current_user():
    return session.get("user", {})

# =============== LOGIN REQUIRED DECORATOR =============
def login_required(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return decorated

# ================ ROLE REQUIRED (AUTH) =================
def role_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            user = session.get("user")
            if not user or user.get("role") not in roles:
                flash("Unauthorized access!", "danger")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return decorated
    return wrapper

# =============== CREATE ACCOUNT (SUPER ADMIN ONLY) ================
@app.route("/create_user", methods=["GET", "POST"])
@login_required
@role_required("super_admin")  # Only super admin can create accounts
def create_user():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        role = request.form["role"]  # admin / super_admin

        password_hash = generate_password_hash(password)

        db.collection("users").add({
            "email": email,
            "password": password_hash,
            "role": role
        })
        flash("User Created Successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("create_user.html")

# ===================== LOGIN PAGE ======================
@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        user_query = db.collection("users").where("email", "==", email).get()

        if not user_query:
            flash("User not found", "danger")
            return redirect(url_for("login"))

        user = user_query[0].to_dict()

        if check_password_hash(user["password"], password):
            session["user"] = {"email": email, "role": user["role"]}
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid password", "danger")

    return render_template("login.html")

# ===================== DASHBOARD =======================
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", user=current_user())

# ===================== SUPER ADMIN PANEL ======================
@app.route("/super-admin")
@login_required
@role_required("super_admin")
def super_admin_panel():
    return render_template("super_admin_panel.html")

# ====================== ADMIN PANEL ===========================
@app.route("/admin-panel")
@login_required
@role_required("admin", "super_admin")
def admin_panel():
    return render_template("admin_panel.html")

# ====================== LOGOUT ===========================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)
