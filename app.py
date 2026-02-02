
from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify, send_file, flash
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
from urllib.parse import urlparse, urljoin

# =========================
# CONFIGURATION
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-in-production!")
CORS(app)

FIRESTORE_LOGIN_DISABLED = os.environ.get("FIRESTORE_LOGIN_DISABLED", "0") == "1"

# =========================
# FIREBASE INITIALIZATION (FIXED)
# =========================
db = None
def get_firestore_client():
    """Lazy initialization with proper error handling"""
    global db
    if db is None:
        try:
            # Try environment variable first, fallback to hardcoded path
            key_path = os.environ.get('FIREBASE_CREDENTIALS_PATH')
            if not key_path:
                key_path = "/etc/secrets/authentication-fish-feeder-firebase-adminsdk-fbsvc-84079a47f4.json"
            
            if not os.path.exists(key_path):
                print(f"❌ Firebase credentials not found: {key_path}")
                return None
            
            if not firebase_admin._apps:
                cred = credentials.Certificate(key_path)
                firebase_admin.initialize_app(cred)
                print("✅ Firebase initialized successfully")
            
            db = firestore.client()
        except Exception as e:
            print(f"❌ Firebase initialization failed: {e}")
            return None
    return db

# =========================
# SECURITY HELPERS
# =========================
def is_safe_url(target):
    """Prevent open redirect attacks"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def redirect_back(default='dashboard', param='next'):
    """Safe redirect to intended destination or default"""
    for target in request.args.get(param), request.form.get(param):
        if not target: continue
        if is_safe_url(target): return redirect(target)
    return redirect(url_for(default))

# =========================
# DECORATORS (ENHANCED)
# =========================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            # Store intended destination
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated

def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

# =========================
# DATA PROCESSING HELPERS
# =========================
def normalize_turbidity(value):
    """Normalize turbidity sensor readings"""
    try:
        v = float(value)
        return max(0.0, min(3000.0, v))
    except (TypeError, ValueError):
        return None

def to_float_or_none(value):
    """Safely convert to float or return None"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def process_readings(docs, limit=50):
    """Process Firestore readings documents into display format"""
    data = []
    for doc in docs[-limit:]:  # Take recent readings only
        doc_data = doc.to_dict() or {}
        created = doc_data.get('createdAt')
        timestamp = (created.strftime('%Y-%m-%d %H:%M:%S') 
                    if isinstance(created, datetime) else str(created))
        
        data.append({
            'temperature': doc_data.get('temperature'),
            'ph': doc_data.get('ph'),
            'ammonia': doc_data.get('ammonia'),
            'turbidity': normalize_turbidity(doc_data.get('turbidity')),
            'distance': doc_data.get('distance'),
            'createdAt': timestamp
        })
    return list(reversed(data))  # Chronological order

def get_device_status(device_id):
    """Get device status from Firestore"""
    db_client = get_firestore_client()
    if not db_client: return {}
    
    try:
        doc = db_client.collection('devices').document(device_id).get()
        return doc.to_dict() or {} if doc.exists else {}
    except:
        return {}

# =========================
# QUERY HELPERS (DRY)
# =========================
def query_readings(device_id, limit=50):
    """Safe Firestore readings query"""
    db_client = get_firestore_client()
    if not db_client: return []
    
    try:
        ref = (db_client.collection('devices')
              .document(device_id)
              .collection('readings')
              .order_by('createdAt', direction=firestore.Query.DESCENDING)
              .limit(limit))
        return list(ref.stream())
    except ResourceExhausted:
        print("⚠️ Firestore quota exceeded")
    except Exception as e:
        print(f"❌ Query error: {e}")
    return []

# =========================
# ROUTES: BASIC
# =========================
@app.route('/')
def home():
    return redirect(url_for('login'))

# =========================
# ROUTES: AUTHENTICATION
# =========================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if FIRESTORE_LOGIN_DISABLED:
        return render_template('login.html', error="Login temporarily disabled")
    
    if 'user' in session:
        return redirect_back()
    
    return render_template('login.html', next=request.args.get('next'))

@app.route('/session-login', methods=['POST'])
def session_login():
    """Handle Firebase ID token verification"""
    try:
        data = request.get_json() or {}
        id_token = data.get('id_token')
        if not id_token:
            return jsonify({'error': 'Missing token'}), 400

        decoded = auth.verify_id_token(id_token)
        email = decoded.get('email')
        if not email:
            return jsonify({'error': 'Invalid token'}), 400

        session['user'] = email
        session['role'] = decoded.get('role', 'worker')
        
        # Return redirect URL for client-side handling
        next_url = request.args.get('next')
        redirect_url = next_url if next_url and is_safe_url(next_url) else url_for('dashboard')
        
        return jsonify({'status': 'success', 'redirect': redirect_url})
        
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Authentication failed'}), 401

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register')
def register():
    return "User registration managed via Firebase Console"

# =========================
# ROUTES: DASHBOARD
# =========================
@app.route('/dashboard')
@login_required
def dashboard():
    readings = query_readings('ESP32001', 50)
    data = process_readings(readings)
    
    # Water quality summary
    summary = "All systems normal"
    alertcolor = "green"
    if data and data[-1].get('turbidity', 0) > 100:
        summary = "🚨 Water too cloudy - DANGER"
        alertcolor = "gold"
    elif data and data[-1].get('turbidity', 0) > 50:
        summary = "⚠️ Water getting cloudy"
        alertcolor = "orange"
    
    # Device status
    device_status = get_device_status('ESP32001')
    feeder_status = device_status.get('feederstatus', 'off')
    feeder_speed = device_status.get('feederspeed', 0)
    feederalert = f"Feeding at {feeder_speed}%" if feeder_status == 'on' else "Feeder OFF"
    feederalertcolor = "limegreen" if feeder_status == 'on' else "lightcoral"
    
    hopper_status = get_device_status('ESP32002')
    low_feed_alert = None
    if hopper_status.get('feedlevelpercent', 100) < 20:
        low_feed_alert = "🥄 LOW FEED - Please refill hopper!"
    
    # Chart data
    chart_data = data[-50:] if data else []
    labels = [r['createdAt'] for r in chart_data]
    temp_values = [r['temperature'] or 0 for r in chart_data]
    ph_values = [r['ph'] or 0 for r in chart_data]
    ammonia_values = [r['ammonia'] or 0 for r in chart_data]
    turbidity_values = [r['turbidity'] or 0 for r in chart_data]
    
    return render_template('dashboard.html',
                         readings=data[-10:],
                         summary=summary, alertcolor=alertcolor,
                         feederalert=feederalert, feederalertcolor=feederalertcolor,
                         lowfeedalert=low_feed_alert,
                         timelabels=labels, tempvalues=temp_values,
                         phvalues=ph_values, ammoniavalues=ammonia_values,
                         turbidityvalues=turbidity_values)

# =========================
# ROUTES: CONTROL PANELS
# =========================
@app.route('/controlfeeding')
@login_required
def controlfeeding():
    readings = query_readings('ESP32001', 50)
    data = process_readings(readings)
    
    chart_labels = [r['createdAt'] for r in data[-20:]]
    chart_temp = [r['temperature'] or 0 for r in data[-20:]]
    chart_ph = [r['ph'] or 0 for r in data[-20:]]
    chart_ammonia = [r['ammonia'] or 0 for r in data[-20:]]
    chart_turbidity = [r['turbidity'] or 0 for r in data[-20:]]
    
    return render_template('control.html',
                         readings=data[-10:],
                         chartlabels=chart_labels,
                         charttemp=chart_temp, chartph=chart_ph,
                         chartammonia=chart_ammonia, chartturbidity=chart_turbidity)

@app.route('/mosfet')
@login_required
def mosfet():
    readings = query_readings('ESP32001', 50)
    return render_template('mosfet.html', readings=process_readings(readings))

# =========================
# ROUTES: API - CONTROL
# =========================
@app.route('/controlmotor', methods=['POST'])
@api_login_required
def control_motor():
    db_client = get_firestore_client()
    if not db_client: return jsonify({'error': 'Database unavailable'}), 500
    
    data = request.get_json() or request.form
    action = data.get('action')
    speed = int(data.get('speed', 0))
    
    if action not in ['on', 'off', 'setspeed']:
        return jsonify({'error': 'Invalid action'}), 400
    
    if action == 'off':
        speed = 0
        status = 'off'
    elif action == 'setspeed' and (speed < 0 or speed > 100):
        return jsonify({'error': 'Speed must be 0-100'}), 400
    else:
        status = 'on' if speed > 0 else 'off'
    
    db_client.collection('devices').document('ESP32001').set({
        'motorspeed': speed,
        'motorstatus': status,
        'updatedAt': datetime.utcnow()
    }, merge=True)
    
    return jsonify({'status': 'success', 'speed': speed, 'status': status})

@app.route('/controlfeeder', methods=['POST'])
@api_login_required
def control_feeder():
    db_client = get_firestore_client()
    if not db_client: return jsonify({'error': 'Database unavailable'}), 500
    
    data = request.get_json() or request.form
    action = data.get('action')
    speed = int(data.get('speed', 0))
    
    if action not in ['on', 'off', 'setspeed']:
        return jsonify({'error': 'Invalid action'}), 400
    
    if action == 'off':
        speed = 0
        status = 'off'
    elif action == 'setspeed' and (speed < 0 or speed > 100):
        return jsonify({'error': 'Speed must be 0-100'}), 400
    else:
        status = 'on' if speed > 0 else 'off'
    
    db_client.collection('devices').document('ESP32001').set({
        'feederspeed': speed,
        'feederstatus': status,
        'updatedAt': datetime.utcnow()
    }, merge=True)
    
    return jsonify({'status': 'success', 'speed': speed, 'status': status})

# =========================
# ROUTES: API - SENSORS
# =========================
@app.route('/addreading', methods=['POST'])
def add_reading():
    """ESP32 posts sensor data here"""
    db_client = get_firestore_client()
    if not db_client: return jsonify({'error': 'Database unavailable'}), 500
    
    try:
        data = request.get_json()
        device_id = data.get('deviceid', 'ESP32001')
        
        doc_ref = (db_client.collection('devices')
                  .document(device_id)
                  .collection('readings').document())
        
        doc_ref.set({
            'temperature': to_float_or_none(data.get('temperature')),
            'ph': to_float_or_none(data.get('ph')),
            'ammonia': to_float_or_none(data.get('ammonia')),
            'turbidity': normalize_turbidity(data.get('turbidity')),
            'distance': to_float_or_none(data.get('distance')),
            'createdAt': datetime.utcnow()
        })
        
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/latest')
def api_latest_readings():
    readings = query_readings('ESP32001', 50)
    data = process_readings(readings)
    
    return jsonify({
        'labels': [r['createdAt'] for r in data],
        'temperature': [r['temperature'] or 0 for r in data],
        'ph': [r['ph'] or 0 for r in data],
        'ammonia': [r['ammonia'] or 0 for r in data],
        'turbidity': [r['turbidity'] or 0 for r in data]
    })

# =========================
# ROUTES: PDF REPORTS
# =========================
@app.route('/exportpdf')
@login_required
def export_pdf():
    db_client = get_firestore_client()
    if not db_client: return jsonify({'error': 'Database unavailable'}), 500
    
    try:
        now = datetime.utcnow()
        since = now - timedelta(hours=24)
        
        readings_ref = (db_client.collection('devices')
                       .document('ESP32001')
                       .collection('readings')
                       .where('createdAt', '>=', since)
                       .order_by('createdAt'))
        
        readings = list(readings_ref.stream())
        data = process_readings(readings)
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        
        # Title
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=20,
                                   textColor=colors.HexColor('#1f77b4'), alignment=1)
        elements.extend([
            Paragraph('🐟 Fish Feeder Water Quality Report', title_style),
            Paragraph(f'Generated: {now.strftime("%Y-%m-%d %H:%M:%S UTC")}', styles['Normal']),
            Spacer(1, 0.3*inch)
        ])
        
        # Data table
        table_data = [['Time', 'Temp (°C)', 'pH', 'NH₃ (ppm)', 'Turbidity']]
        for reading in data:
            row = [
                reading['createdAt'][-16:],  # Short timestamp
                f"{reading['temperature']:.1f}" if reading['temperature'] else '',
                f"{reading['ph']:.2f}" if reading['ph'] else '',
                f"{reading['ammonia']:.2f}" if reading['ammonia'] else '',
                f"{reading['turbidity']:.0f}" if reading['turbidity'] else ''
            ]
            table_data.append(row)
        
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1f77b4')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige)
        ]))
        
        elements.extend([Paragraph('Last 24 Hours Data', styles['Heading2']), table])
        doc.build(elements)
        
        buffer.seek(0)
        return send_file(buffer, mimetype='application/pdf',
                        as_attachment=True, 
                        download_name=f'fishfeeder-report-{now.strftime("%Y%m%d")}.pdf')
                        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =========================
# ROUTES: HEALTH CHECKS
# =========================
@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})

@app.route('/health')
def health():
    db_client = get_firestore_client()
    return jsonify({
        'status': 'healthy',
        'database': bool(db_client),
        'user_count': len(session) > 0
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
