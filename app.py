from flask import Flask, render_template, request, redirect, url_for, send_file, session
import sqlite3
import os
import csv
from werkzeug.utils import secure_filename
from io import StringIO, BytesIO
from functools import wraps
from datetime import datetime

app = Flask(__name__)

DB_NAME = "database.db"
UPLOAD_FOLDER = "uploads"

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "jpg", "jpeg", "png"}
MAX_UPLOAD_MB = 5

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ✅ Admin password
ADMIN_PASSWORD = "Vale228"

# ✅ Session secret (change this to something random when going live)
app.secret_key = "CHANGE_THIS_SECRET_KEY_2026"


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            phone TEXT,
            town TEXT,
            primary_trade TEXT,
            primary_ticket TEXT,
            additional_info TEXT,
            cv_filename TEXT,
            tickets_filename TEXT,
            consent INTEGER DEFAULT 0,
            consent_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS contact_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            company TEXT,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            consent INTEGER DEFAULT 0,
            consent_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ----------------------------
# ✅ Admin Session Protection
# ----------------------------
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("is_admin") is True:
            return f(*args, **kwargs)
        return redirect(url_for("admin_login"))
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin"))
        error = "Wrong password"
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("home"))


# ----------------------------
# Public Pages
# ----------------------------
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/services")
def services():
    return render_template("services.html")


@app.route("/candidates")
def candidates():
    return render_template("candidates.html")


@app.route("/employers")
def employers():
    return render_template("employers.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ----------------------------
# Contact Form (GDPR consent required)
# ----------------------------
@app.route("/contact", methods=["GET", "POST"])
def contact():
    error = None

    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip()
        company = request.form.get("company", "").strip()
        message = request.form["message"].strip()
        consent = 1 if request.form.get("consent") == "on" else 0

        if consent != 1:
            error = "Please confirm you have read the Privacy Policy."
            return render_template("contact.html", error=error)

        now = datetime.utcnow().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO contact_requests (name, email, phone, company, message, created_at, consent, consent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, email, phone, company, message, now, consent, now))
        conn.commit()
        conn.close()

        return render_template("thanks.html")

    return render_template("contact.html", error=error)


# ----------------------------
# Registration Form (GDPR consent required)
# ----------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    error = None

    if request.method == "POST":
        first_name = request.form["first_name"].strip()
        last_name = request.form["last_name"].strip()
        email = request.form["email"].strip()
        phone = request.form["phone"].strip()
        town = request.form["town"].strip()
        primary_trade = request.form["primary_trade"].strip()
        primary_ticket = request.form["primary_ticket"].strip()
        additional_info = request.form.get("additional_info", "").strip()

        consent = 1 if request.form.get("consent") == "on" else 0
        if consent != 1:
            error = "Please confirm you have read the Privacy Policy."
            return render_template("register.html", error=error)

        cv_filename = None
        tickets_filename = None

        cv_file = request.files.get("cv")
        if cv_file and cv_file.filename:
            if allowed_file(cv_file.filename):
                safe = secure_filename(cv_file.filename)
                cv_filename = f"{first_name}_{last_name}_CV_{safe}"
                cv_file.save(os.path.join(UPLOAD_FOLDER, cv_filename))
            else:
                error = "Invalid CV file type. Use PDF, DOC, DOCX, JPG or PNG."

        tickets_file = request.files.get("tickets")
        if tickets_file and tickets_file.filename and not error:
            if allowed_file(tickets_file.filename):
                safe = secure_filename(tickets_file.filename)
                tickets_filename = f"{first_name}_{last_name}_TICKETS_{safe}"
                tickets_file.save(os.path.join(UPLOAD_FOLDER, tickets_filename))
            else:
                error = "Invalid tickets file type. Use PDF, DOC, DOCX, JPG or PNG."

        if error:
            return render_template("register.html", error=error)

        now = datetime.utcnow().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO registrations
            (first_name, last_name, email, phone, town, primary_trade, primary_ticket,
             additional_info, cv_filename, tickets_filename, consent, consent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            first_name, last_name, email, phone, town,
            primary_trade, primary_ticket,
            additional_info, cv_filename, tickets_filename,
            consent, now
        ))
        conn.commit()
        conn.close()

        return redirect(url_for("thanks"))

    return render_template("register.html", error=error)


@app.route("/thanks")
def thanks():
    return render_template("thanks.html")


# ----------------------------
# Admin Dashboard + Exports (session-based)
# ----------------------------
@app.route("/admin")
@admin_required
def admin():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT * FROM registrations ORDER BY id DESC")
    registrations = c.fetchall()

    c.execute("SELECT * FROM contact_requests ORDER BY id DESC")
    contacts = c.fetchall()

    conn.close()

    return render_template("admin.html", registrations=registrations, contacts=contacts)


@app.route("/admin/export")
@admin_required
def export_registrations_csv():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM registrations")
    rows = c.fetchall()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)

    if rows:
        writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow([row[key] for key in row.keys()])

    output = BytesIO()
    output.write(si.getvalue().encode("utf-8"))
    output.seek(0)

    return send_file(output,
                     mimetype="text/csv",
                     as_attachment=True,
                     download_name="registrations.csv")


@app.route("/admin/export-contacts")
@admin_required
def export_contacts_csv():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM contact_requests")
    rows = c.fetchall()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)

    if rows:
        writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow([row[key] for key in row.keys()])

    output = BytesIO()
    output.write(si.getvalue().encode("utf-8"))
    output.seek(0)

    return send_file(output,
                     mimetype="text/csv",
                     as_attachment=True,
                     download_name="contact_requests.csv")


if __name__ == "__main__":
    app.run()
