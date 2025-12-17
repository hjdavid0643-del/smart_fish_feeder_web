EMAIL_USER = "youremail@gmail.com"
EMAIL_PASS = "your_gmail_app_password"

def send_email(to, subject, message):
    msg = MIMEText(message, "html")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = to

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.sendmail(EMAIL_USER, to, msg.as_string())
        return True
    except Exception as e:
        print("Email error:", e)
        return False
