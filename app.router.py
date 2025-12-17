@app.route("/generate_report", methods=["POST"])
@login_required
def generate_report():
    start = request.form.get("start")
    end = request.form.get("end")
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)  # inclusive
        docs = fetch_readings_for_range(start_dt, end_dt)
        charts = create_charts_from_docs(docs)
        feed_rows = []  # fetch feeding logs if you have
        log_rows = []   # fetch system logs if you have
        filename = f"report_custom_{start}_{end}.pdf"
        build_pdf(charts, feed_rows, log_rows, filename)
        recipients = [r for r in EMAIL_RECIPIENTS if r]
        if recipients:
            send_email_with_attachment(f"Custom Report {start} to {end}", "<p>Attached report</p>", recipients, filename)
        flash("Report generated and emailed.", "success")
    except Exception as e:
        flash(f"Error generating report: {e}", "danger")
    return redirect(url_for("dashboard"))
