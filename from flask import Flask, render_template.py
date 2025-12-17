from flask import Flask, render_template, request
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import pdfkit, smtplib, os
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

app = Flask(__name__)

cred = credentials.Certificate("firebase-key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

@app.route("/report")
def report_page():
    return render_template("report.html")
