@app.route('/dashboard')
@role_required(['Super Admin','Admin','Worker'])
def dashboard():
    # Fetch readings like before
    readings_ref = db.collection('devices').document('ESP32_001').collection('readings') \
                     .order_by('createdAt', direction=firestore.Query.DESCENDING).limit(10)
    readings = [r.to_dict() for r in readings_ref.stream()]
    readings = list(reversed(readings))
    user = session['user']
    return render_template('dashboard.html', readings=readings, user=user)
 