from flask import Flask, request, jsonify, render_template, redirect, url_for
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)


cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

@app.route('/')
def dashboard():
    readings_ref = (
        db.collection('devices')
        .document('ESP32_001')
        .collection('readings')
        .order_by('createdAt', direction=firestore.Query.DESCENDING)
        .limit(10)
    )
    readings = readings_ref.stream()

    data = []
    for r in readings:
        doc = r.to_dict()
        data.append({
            "temperature": doc.get("temperature"),
            "ph": doc.get("ph"),
            "ammonia": doc.get("ammonia"),
            "turbidity": doc.get("turbidity"),
            "createdAt": doc.get("createdAt").strftime("%Y-%m-%d %H:%M:%S") if doc.get("createdAt") else ""
        })

    data = list(reversed(data))

    summary = "🟢 All systems normal."
    alert_color = "green"

    if len(data) > 0:
        last = data[-1]

        if last["temperature"] > 30 or last["temperature"] < 20:
            summary = "Temperature out of range!"
            alert_color = "red"

        elif last["ph"] < 6.5 or last["ph"] > 8.5:
            summary = "pH level is abnormal!"
            alert_color = "orange"

        elif last["ammonia"] > 0.5:
            summary = "High ammonia detected!"
            alert_color = "darkred"

        elif last["turbidity"] > 50:
            summary = "Water is too cloudy!"
            alert_color = "gold"

    return render_template("dashboard.html", readings=data, summary=summary, alert_color=alert_color)


@app.route('/add_reading', methods=['POST'])
def add_reading():
    try:
        data = request.get_json()

        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400

        device_id = data.get("device_id", "ESP32_001")

        temperature = data.get("temperature")
        ph = data.get("ph")
        ammonia = data.get("ammonia")
        turbidity = data.get("turbidity")

        if None in [temperature, ph, ammonia, turbidity]:
            return jsonify({"status": "error", "message": "Incomplete sensor data"}), 400

        try:
            temperature = float(temperature)
            ph = float(ph)
            ammonia = float(ammonia)
            turbidity = float(turbidity)
        except ValueError:
            return jsonify({"status": "error", "message": "Sensor values must be numeric"}), 400

        doc_ref = (
            db.collection('devices')
            .document(device_id)
            .collection('readings')
            .document()
        )

        doc_ref.set({
            "temperature": temperature,
            "ph": ph,
            "ammonia": ammonia,
            "turbidity": turbidity,
            "createdAt": datetime.utcnow()
        })

        return jsonify({"status": "success", "message": f"Reading saved for device {device_id}"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/feed/manual', methods=['POST'])
def manual_feed():
    action = request.form.get('action')

    if action == "on":
        return jsonify({"status": "success", "message": "Manual feeding ON triggered"}), 200

    elif action == "off":
        return jsonify({"status": "success", "message": "Manual feeding OFF triggered"}), 200

    else:
        return jsonify({"status": "error", "message": "Invalid action"}), 400


@app.route('/feed/schedule', methods=['POST'])
def set_schedule():
    return jsonify({"status": "success", "message": "Automatic feeding schedule set for 9AM and 4PM"}), 200


@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "ok", "message": "Server reachable"}), 200


@app.route('/historical', methods=['GET'])
def historical():
    try:
        readings_ref = (
            db.collection('devices')
            .document('ESP32_001')
            .collection('readings')
            .order_by('createdAt', direction=firestore.Query.DESCENDING)
        )
        readings = readings_ref.stream()

        data = []
        for r in readings:
            doc = r.to_dict()
            data.append({
                "temperature": doc.get("temperature"),
                "ph": doc.get("ph"),
                "ammonia": doc.get("ammonia"),
                "turbidity": doc.get("turbidity"),
                "createdAt": doc.get("createdAt").strftime("%Y-%m-%d %H:%M:%S") if doc.get("createdAt") else ""
            })

        return jsonify({"status": "success", "readings": data}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
