# Add near other routes (after mosfet_schedule)
API_KEY = 'replace_with_a_strong_api_key_change_me'

@app.route('/device/commands/<device_id>', methods=['GET'])
def device_commands(device_id):
    # simple API key check (pass ?api_key=API_KEY)
    req_key = request.args.get('api_key', '')
    if req_key != API_KEY:
        return jsonify({'status': 'error', 'message': 'unauthorized'}), 401

    try:
        # Read current control document
        ctrl_ref = db.collection('devices').document(device_id).collection('control').document('command')
        ctrl_doc = ctrl_ref.get()
        control = ctrl_doc.to_dict() if ctrl_doc.exists else {}

        # Read schedule document
        sched_ref = db.collection('devices').document(device_id).collection('schedule').document('control')
        sched_doc = sched_ref.get()
        schedule = sched_doc.to_dict() if sched_doc.exists else {}

        return jsonify({
            'status': 'success',
            'device_id': device_id,
            'control': control,
            'schedule': schedule,
            'server_time_utc': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        }), 200

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
