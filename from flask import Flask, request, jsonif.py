from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from flask_cors import CORS
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

# --------------------- CONFIG ---------------------
app = Flask(__name__)
app.secret_key = "replace_with_a_strong_secret_key"
CORS(app)

# Initialize Firebase
cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# --------------------- HELPERS ---------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = session.get('user')
            if not user:
                return redirect(url_for('login', next=request.path))
            if user.get('role') not in roles:
                return "Access denied", 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# --------------------- AUTH ROUTES ---------------------
@app.route('/register', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def register():
    # Only super_admin can register new users
    if request.method == 'POST':
        data = request.form
        email = data.get('email')
        password = data.get('password')
        role = data.get('role', 'fish_worker')

        if not email or not password:
            return jsonify({'status': 'error', 'message': 'Email and password required'}), 400

        users_ref = db.collection('users')
        # check existing
        existing = users_ref.where('email', '==', email).get()
        if existing:
            return jsonify({'status': 'error', 'message': 'User already exists'}), 400

        hashed = generate_password_hash(password)
        users_ref.add({
            'email': email,
            'password': hashed,
            'role': role,
            'createdAt': datetime.utcnow()
        })
        return jsonify({'status': 'success', 'message': 'User created'}), 200

    # GET - basic HTML form (optional)
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            return render_template('login.html', error='Email and password required')

        users_ref = db.collection('users')
        docs = users_ref.where('email', '==', email).limit(1).get()
        if not docs:
            return render_template('login.html', error='Invalid credentials')

        user_doc = docs[0]
        user = user_doc.to_dict()
        if not check_password_hash(user.get('password', ''), password):
            return render_template('login.html', error='Invalid credentials')

        # Set session (don't store password)
        session['user'] = {
            'id': user_doc.id,
            'email': user.get('email'),
            'role': user.get('role', 'fish_worker')
        }
        next_url = request.args.get('next') or url_for('dashboard')
        return redirect(next_url)

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

# --------------------- DASHBOARD (protected) ---------------------
@app.route('/')
@login_required
def dashboard():
    # A simple protected dashboard
    return render_template('dashboard.html', user=session.get('user'))

# --------------------- READINGS (same as before) ---------------------
@app.route('/add_reading', methods=['POST'])
def add_reading():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        device_id = data.get("device_id", "ESP32_001")
        temperature = float(data.get("temperature"))
        ph = float(data.get("ph"))
        ammonia = float(data.get("ammonia"))
        turbidity = float(data.get("turbidity"))

        db.collection('devices').document(device_id).collection('readings').add({
            "temperature": temperature,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity,
            "createdAt": datetime.utcnow()
        })

        return jsonify({"status": "success", "message": f"Reading saved for device {device_id}"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --------------------- EXPORT TO EXCEL / PDF ---------------------
@app.route('/export', methods=['GET'])
@login_required
@role_required('super_admin', 'admin')
def export():
    # Query params: start, end (YYYY-mm-dd) and format=excel|pdf
    start = request.args.get('start')
    end = request.args.get('end')
    fmt = request.args.get('format', 'excel').lower()

    try:
        if start:
            start_dt = datetime.strptime(start, '%Y-%m-%d')
        else:
            # default to 30 days ago
            start_dt = datetime.utcnow() - pd.Timedelta(days=30)
        if end:
            # include entire day
            end_dt = datetime.strptime(end, '%Y-%m-%d')
        else:
            end_dt = datetime.utcnow()

        readings_ref = db.collection('devices').document('ESP32_001').collection('readings')
        query = readings_ref.where('createdAt', '>=', start_dt).where('createdAt', '<=', end_dt).order_by('createdAt')
        docs = query.stream()

        rows = []
        for d in docs:
            doc = d.to_dict()
            rows.append({
                'temperature': doc.get('temperature'),
                'ph': doc.get('ph'),
                'ammonia': doc.get('ammonia'),
                'turbidity': doc.get('turbidity'),
                'createdAt': doc.get('createdAt').strftime('%Y-%m-%d %H:%M:%S') if doc.get('createdAt') else ''
            })

        if not rows:
            return jsonify({'status': 'error', 'message': 'No data found for the given date range'}), 404

        df = pd.DataFrame(rows)

        if fmt == 'excel':
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='readings')
            output.seek(0)
            filename = f"readings_{start_dt.date()}_to_{end_dt.date()}.xlsx"
            return send_file(output, download_name=filename, as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        elif fmt == 'pdf':
            output = io.BytesIO()
            # Build a simple PDF table using reportlab
            doc = SimpleDocTemplate(output, pagesize=landscape(letter))
            data_table = [list(df.columns)] + df.values.tolist()
            table = Table(data_table)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.grey),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0,0), (-1,0), 12),
                ('GRID', (0,0), (-1,-1), 0.25, colors.black),
            ]))
            elems = [table]
            doc.build(elems)
            output.seek(0)
            filename = f"readings_{start_dt.date()}_to_{end_dt.date()}.pdf"
            return send_file(output, download_name=filename, as_attachment=True, mimetype='application/pdf')

        else:
            return jsonify({'status': 'error', 'message': 'Invalid format. Use excel or pdf'}), 400

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --------------------- BOOTSTRAP: Create a default super_admin if none exist ---------------------
def ensure_super_admin():
    users_ref = db.collection('users')
    docs = users_ref.where('role', '==', 'super_admin').limit(1).get()
    if not docs:
        # Create default super admin (change password immediately)
        users_ref.add({
            'email': 'superadmin@example.com',
            'password': generate_password_hash('ChangeMe123!'),
            'role': 'super_admin',
            'createdAt': datetime.utcnow()
        })

ensure_super_admin()

# --------------------- RUN ---------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
