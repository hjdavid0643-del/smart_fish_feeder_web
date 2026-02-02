from flask import (
    Flask, render_template_string, request, redirect, url_for, session, jsonify, send_file
)
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore
from functools import wraps
from datetime import datetime, timedelta
import os
import io
import json

try:
    from google.api_core.exceptions import ResourceExhausted
except ImportError:
    ResourceExhausted = Exception

# Disable ReportLab for now (templates first)
REPORTLAB_AVAILABLE = False
try:
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except ImportError:
    print("⚠️ ReportLab not installed - PDF disabled")

# =========================
# CONFIG
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fishfeeder-dev-secret")
CORS(app)

# =========================
# FIREBASE INIT
# =========================
def init_firebase():
    if firebase_admin._apps:
        return firestore.client()
    
    try:
        service_account = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if service_account:
            cred_dict = json.loads(service_account)
            cred = credentials.Certificate(cred_dict)
            firebase_app = firebase_admin.initialize_app(cred)
            return firestore.client(app=firebase_app)
        
        key_path = os.environ.get("FIREBASE_KEY_PATH")
        if key_path and os.path.exists(key_path):
            cred = credentials.Certificate(key_path)
            firebase_app = firebase_admin.initialize_app(cred)
            return firestore.client(app=firebase_app)
            
    except Exception as e:
        print(f"❌ Firebase failed: {e}")
        return None

db = init_firebase()

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
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_key = os.environ.get("API_SECRET", "fishfeeder123")
        if api_key != expected_key:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def safe_float(value):
    try: return float(value) if value is not None else None
    except: return None

# =========================
# INLINE TEMPLATES (No HTML files needed)
# =========================
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head><title>Fish Feeder Login</title>
<style>
body { font-family: Arial; max-width: 400px; margin: 100px auto; padding: 20px; }
input { width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; }
button { background: #1f77b4; color: white; padding: 12px; width: 100%; border: none; cursor: pointer; }
.error { color: red; }
</style></head>
<body>
<h2>🐟 Smart Fish Feeder</h2>
{{ error|safe }}
<form method="POST">
  <input type="email" name="email" placeholder="admin@example.com" required>
  <input type="password" name="password" placeholder="admin123" required>
  <button type="submit">Login</button>
</form>
</body>
</html>
'''

DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html><head><title>Dashboard</title>
<style>
body { font-family: Arial; margin: 20px; }
.reading { border: 1px solid #ccc; padding: 10px; margin: 10px 0; }
.alert { padding: 15px; border-radius: 5px; font-weight: bold; }
.status { padding: 10px; background: #f0f0f0; margin: 10px 0; }
</style></head>
<body>
<h1>🐟 Fish Feeder Dashboard</h1>
<a href="/logout">Logout</a> | <a href="/controlfeeding">Feeder Control</a>
<div class="alert" style="background: {{alertcolor}}; color: white;">{{summary}}</div>

<div class="status">
  Feeder: <span style="color: {{feederalertcolor}};">{{feederalert}}</span>
</div>

<h3>Recent Readings:</h3>
{% for r in readings %}
<div class="reading">
  {{r.createdAt}} | 🌡{{r.temperature}}°C | 🧪pH{{r.ph}} | NH3{{r.ammonia}} | ☁{{r.turbidity}}
</div>
{% endfor %}
</body>
</html>
'''

# =========================
# ROUTES
# =========================
@app.route("/")
def home():
    return '''
    <h1>🐟 Smart Fish Feeder API</h1>
    <p><a href="/login">→ Login Dashboard</a></p>
    <h3>ESP32 Tests:</h3>
    <p><a href="/ping">/ping</a> | <a href="/testdb">/testdb</a></p>
    <h3>Status: ''' + ("✅ LIVE" if db else "❌ No Database") + '''</h3>
    '''

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if email == "admin@example.com" and password == "admin123":
            session["user"] = email
            return redirect(url_for("dashboard"))
        return render_template_string(LOGIN_TEMPLATE, error="<p style='color:red'>Wrong credentials</p>")
    return render_template_string(LOGIN_TEMPLATE, error="")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    readings = []
    summary = "No data"
    alertcolor = "gray"
    feederalert = "Offline"
    feederalertcolor = "gray"
    
    if db:
        try:
            readings_ref = (db.collection("devices")
                          .document("ESP32001")
                          .collection("readings")
                          .order_by("createdAt", direction=firestore.Query.DESCENDING)
                          .limit(20))
            
            readings = []
            for doc in readings_ref.stream():
                data = doc.to_dict()
                readings.append({
                    'temperature': safe_float(data.get('temperature')),
                    'ph': safe_float(data.get('ph')),
                    'ammonia': safe_float(data.get('ammonia')),
                    'turbidity': safe_float(data.get('turbidity')),
                    'createdAt': str(data.get('createdAt', ''))[:16]
                })
            
            # Status logic
            if readings:
                last = readings[0]
                summary = "All normal"
                alertcolor = "green"
                if last.get('turbidity', 0) > 50:
                    summary = "High turbidity!"
                    alertcolor = "orange"
            
            # Feeder status
            dev_doc = db.collection("devices").document("ESP32001").get()
            if dev_doc.exists:
                dev_data = dev_doc.to_dict()
                speed = dev_data.get('feederspeed', 0)
                if speed and speed > 0:
                    feederalert = f"Feeding {speed}%"
                    feederalertcolor = "limegreen"
                else:
                    feederalert = "Idle"
                    feederalertcolor = "lightcoral"
                    
        except Exception as e:
            summary = f"Error: {e}"
            alertcolor = "red"
    
    return render_template_string(DASHBOARD_TEMPLATE, 
        readings=readings[-10:],
        summary=summary, alertcolor=alertcolor,
        feederalert=feederalert, feederalertcolor=feederalertcolor)

@app.route("/controlfeeding")
@login_required
def controlfeeding():
    return '''
    <h1>Feeder Control</h1>
    <a href="/dashboard">← Dashboard</a><br>
    <a href="/logout">Logout</a>
    <p>Feeder motor controls coming soon...</p>
    '''

# =========================
# CRITICAL: ESP32 ENDPOINT
# =========================
@app.route("/addreading", methods=["POST"])
def addreading():
    print("📨 ESP32 POST received")
    
    if not db:
        print("❌ No database")
        return jsonify({"status": "error", "message": "No database"}), 500
    
    try:
        data = request.get_json() or {}
        print(f"Data: {data}")
        
        deviceid = data.get("deviceid", "ESP32001")
        
        doc_ref = (db.collection("devices")
                  .document(deviceid)
                  .collection("readings")
                  .document())
        
        doc_ref.set({
            "temperature": safe_float(data.get("temperature")),
            "ph": safe_float(data.get("ph")),
            "ammonia": safe_float(data.get("ammonia")),
            "turbidity": safe_float(data.get("turbidity")),
            "distance": safe_float(data.get("distance")),
            "createdAt": firestore.SERVER_TIMESTAMP  # Auto-timestamp
        })
        
        print(f"✅ Saved for {deviceid}")
        return jsonify({"status": "success", "device": deviceid})
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================
# HEALTH CHECKS
# =========================
@app.route("/ping")
def ping():
    return jsonify({
        "status": "ok", 
        "database": db is not None,
        "firebase_apps": len(firebase_admin._apps)
    })

@app.route("/testdb")
def testdb():
    if not db:
        return jsonify({"status": "error", "message": "No database"})
    try:
        doc = db.collection("devices").document("ESP32001").get()
        return jsonify({
            "status": "ok", 
            "document_exists": doc.exists,
            "readings_count": len(list(db.collection("devices").document("ESP32001").collection("readings").limit(1).stream()))
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting on port {port}, DB: {'✅' if db else '❌'}")
    app.run(host="0.0.0.0", port=port, debug=True)
