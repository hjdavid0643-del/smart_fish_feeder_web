# ---- AUTO SCHEDULE CONTROL ----
@app.route('/mosfet/schedule', methods=['POST'])
@role_required(['Super Admin','Admin'])
def mosfet_schedule():
    try:
        data = request.get_json()
        
        device_id = data.get("device_id", DEFAULT_DEVICE_ID)
        pin = data.get("pin")                  # mosfet1, mosfet2
        on_time = data.get("on_time")          # "06:00"
        off_time = data.get("off_time")        # "06:00"
        repeat = data.get("repeat", "daily")   # daily / weekly / once

        if not pin or not on_time or not off_time:
            return jsonify({"status": "error", "message": "Missing schedule fields"}), 400

        schedule_ref = db.collection("devices").document(device_id).collection("schedule").document("control")
        schedule_ref.set({
            f"{pin}_on": on_time,
            f"{pin}_off": off_time,
            "repeat": repeat,
            "updatedAt": datetime.utcnow()
        }, merge=True)

        return jsonify({
            "status": "success",
            "message": f"Schedule set: {pin} ON at {on_time}, OFF at {off_time}"
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
