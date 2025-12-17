@app.route("/send_report", methods=["POST"])
def send_report():
    start = request.form["start_date"]
    end   = request.form["end_date"]
    email = request.form["email"]

    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt   = datetime.strptime(end, "%Y-%m-%d")

    # FETCH FIREBASE RECORDS BETWEEN DATE RANGE
    ref = db.collection("sensor_logs").where("timestamp", ">=", start_dt).where("timestamp", "<=", end_dt)
    docs = ref.get()

    data = []
    for d in docs:
        x = d.to_dict()
        data.append({
            "date": x["timestamp"].strftime("%Y-%m-%d"),
            "temp": x["temp"],
            "ph": x["ph"],
            "ammonia": x["ammonia"]
            "turbidity":  x["turbidity"]
        })

    # Render PDF HTML
    rendered = render_template("report_pdf.html", records=data, start=start, end=end,
                              chart1="static/chart1.png", chart2="static/chart2.png")

    pdf_file = "generated_report.pdf"
    pdfkit.from_string(rendered, pdf_file)

    send_email_with_pdf(email, pdf_file)

    return "📨 Report Sent Successfully to " + email

# ---- EMAIL SENDER ---- #
def send_email_with_pdf(target_email, file):
    sender = "youremail@gmail.com"
    password = "your-gmail-app-password"    # Use 2FA App Password

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = target_email
    msg["Subject"] = "📊 IoT Report PDF"

    msg.attach(MIMEText("Attached is your IoT report summary.", "plain"))

    with open(file, "rb") as a:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(a.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={file}")
        msg.attach(part)

    server = smtplib.SMTP("smtp.gmail.com",587)
    server.starttls()
    server.login(sender,password)
    server.sendmail(sender,target_email,msg.as_string())
    server.quit()
      