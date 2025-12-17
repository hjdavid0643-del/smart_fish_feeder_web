@app.route('/export/pdf')
@role_required(['Super Admin','Admin','Worker'])
def export_pdf():
    device_id=request.args.get('device_id',DEFAULT_DEVICE_ID)
    readings_ref=db.collection('devices').document(device_id).collection('readings').order_by('createdAt',direction=firestore.Query.DESCENDING).limit(500)
    readings=[r.to_dict() for r in readings_ref.stream()]
    buffer=BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=letter)
    elements=[]
    styles=getSampleStyleSheet()
    elements.append(Paragraph('Smart Fish Feeder Report',styles['Title']))
    elements.append(Paragraph(f'Device: {device_id}',styles['Normal']))
    elements.append(Paragraph(f'Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}',styles['Normal']))
    elements.append(Spacer(1,12))
    # Table
    table_data=[['Timestamp','Temperature','pH','Ammonia','Turbidity']]
    for r in readings:
        ts=r.get('createdAt').strftime('%Y-%m-%d %H:%M:%S') if r.get('createdAt') else ''
        table_data.append([ts,r.get('temperature',''),r.get('ph',''),r.get('ammonia',''),r.get('turbidity','')])
    t=Table(table_data,repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('GRID',(0,0),(-1,-1),0.5,colors.grey),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold')]))
    elements.append(t)
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer,as_attachment=True,download_name=f'report_{device_id}.pdf',mimetype='application/pdf')


def ensure_super_admin():
    users_ref=db.collection('users')
    if not list(users_ref.limit(1).stream()):
        users_ref.add({'username':'admin','password_hash':generate_password_hash('admin123'),'role':'Super Admin','createdAt':datetime.utcnow()})
        print("Created default Super Admin: username=admin, password=admin123")

if __name__=="__main__":
    ensure_super_admin()
    app.run(host='0.0.0.0',port=5000,debug=True)