from flask import Flask, request, render_template, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from firebase_admin import credentials, initialize_app, firestore
from functools import wraps

app = Flask(__name__)
app.secret_key = "YOUR_SECRET_KEY"

# --------------------------------------------------------- FIREBASE INIT
cred = credentials.Certificate("firebase-key.json")
initialize_app(cred)
db = firestore.client()
USERS = "users"

# --------------------------------------------------------- SESSION HANDLER
def current_user():
    uid = session.get("uid")
    if not uid: 
        return None

    doc = db.collection(USERS).document(uid).get()
    if doc.exists:
        user = doc.to_dict()
        user["id"] = doc.id
        return user
    return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not session.get("uid"):
            flash("Please login first","warning")
            return redirect(url_for("login"))
        return fn(*a, **k)
    return wrapper


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*a,**k):
            user = current_user()
            if not user or user.get("role") not in roles:
                flash("Access Denied","danger")
                return redirect(url_for("dashboard"))
            return fn(*a,**k)
        return wrapper
    return decorator

# --------------------------------------------------------- REGISTER
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].lower()
        password = request.form["password"]
        role = request.form.get("role", "fish_worker")

        # Check email duplicate
        exists = db.collection(USERS).where("email", "==", email).get()
        if exists:
            flash("Email already registered.","danger")
            return redirect(url_for("register"))

        uid = db.collection(USERS).document()
        uid.set({
            "email": email,
            "password": generate_password_hash(password),
            "role": role,
            "status": "active"
        })

        flash("Registration successful! Login now.","success")
        return redirect(url_for("login"))

    return render_template("register.html")

# --------------------------------------------------------- LOGIN
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].lower()
        password = request.form["password"]

        user_doc = list(db.collection(USERS).where("email","==",email).limit(1).stream())

        if not user_doc:
            flash("Email not found","danger")
            return redirect(url_for("login"))

        data = user_doc[0].to_dict()
        uid = user_doc[0].id

        if not check_password_hash(data["password"], password):
            flash("Wrong password","danger")
            return redirect(url_for("login"))

        if data["status"] == "inactive":
            flash("Account is inactive.","danger")
            return redirect(url_for("login"))

        session["uid"] = uid
        return redirect(url_for("dashboard"))

    return render_template("login.html")

# --------------------------------------------------------- LOGOUT
@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.","info")
    return redirect(url_for("login"))

# --------------------------------------------------------- DASHBOARDS
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", user=current_user())

@app.route("/super-admin")
@login_required
@role_required("super_admin")
def super_admin():
    return render_template("super_admin_panel.html")

@app.route("/admin")
@login_required
@role_required("admin","super_admin")
def admin_panel():
    return render_template("admin_panel.html")

@app.route("/worker")
@login_required
@role_required("fish_worker","admin","super_admin")
def worker_dashboard():
    return render_template("worker_dashboard.html")

# --------------------------------------------------------- USER MANAGEMENT
@app.route("/manage-users")
@login_required
@role_required("super_admin")
def manage_users():
    users = [{**d.to_dict(), "id": d.id} for d in db.collection(USERS).stream()]
    return render_template("manage_users.html", users=users)

@app.route("/update-role/<id>/<role>")
@login_required
@role_required("super_admin")
def update_role(id, role):
    db.collection(USERS).document(id).update({"role": role})
    flash("User role updated","success")
    return redirect(url_for("manage_users"))

@app.route("/toggle-status/<id>")
@login_required
@role_required("super_admin")
def toggle_status(id):
    ref = db.collection(USERS).document(id)
    user = ref.get().to_dict()
    new_status = "inactive" if user["status"] == "active" else "active"
    ref.update({"status": new_status})
    flash("Account status changed","info")
    return redirect(url_for("manage_users"))


# --------------------------------------------------------- RUN
if __name__ == "__main__":
    app.run(debug=True)
