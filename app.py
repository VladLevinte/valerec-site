from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, session, send_file
)
import sqlite3
import os
import csv
import math
from werkzeug.utils import secure_filename
from io import StringIO, BytesIO
from functools import wraps
from datetime import datetime

app = Flask(__name__)

DB_NAME = "database.db"
UPLOAD_FOLDER = "uploads"

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "jpg", "jpeg", "png"}

MAX_UPLOAD_MB_EACH = 5
MAX_TICKETS_FILES = 5

# Allow enough total request size for CV + up to 5 tickets + ID document
app.config["MAX_CONTENT_LENGTH"] = (MAX_UPLOAD_MB_EACH * (MAX_TICKETS_FILES + 3)) * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ADMIN_PASSWORD = "Vale228"
app.secret_key = "CHANGE_THIS_SECRET_KEY_2026"


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def db_connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table: str, column: str, coltype: str):
    """
    Add column if missing. Safe to run on every start.
    IMPORTANT: PRAGMA table_info returns tuples, so use row[1] for column name.
    """
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in c.fetchall()}  # row[1] = column name
    if column not in existing:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Existing table
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

    # Existing table
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

    # NEW TABLE: New Starters
    c.execute("""
        CREATE TABLE IF NOT EXISTS new_starters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            town TEXT NOT NULL,
            primary_trade TEXT NOT NULL,
            primary_ticket TEXT NOT NULL,
            utr TEXT,
            national_insurance TEXT NOT NULL,
            sort_code TEXT NOT NULL,
            account_number TEXT NOT NULL,
            id_document_filename TEXT NOT NULL,
            created_date TEXT NOT NULL
        )
    """)

    conn.commit()

    # Keep compatibility for templates/admin
    ensure_column(conn, "registrations", "created_date", "TEXT")
    ensure_column(conn, "contact_requests", "created_date", "TEXT")

    conn.close()


init_db()


# ----------------------------
# Admin Session Protection
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
            return redirect(url_for("admin_dashboard"))
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
def candidates_page():
    return render_template("candidates.html")


@app.route("/employers")
def employers():
    return render_template("employers.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ----------------------------
# Contact Form
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

        now_ts = datetime.utcnow().isoformat()
        now_date = datetime.utcnow().date().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO contact_requests (name, email, phone, company, message, created_at, created_date, consent, consent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, email, phone, company, message, now_ts, now_date, consent, now_ts))
        conn.commit()
        conn.close()

        return render_template("thanks.html")

    return render_template("contact.html", error=error)


# ----------------------------
# Registration Form (New Candidates)
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
        tickets_filename = None  # "file1|file2|file3"

        cv_file = request.files.get("cv")
        if cv_file and cv_file.filename:
            if not allowed_file(cv_file.filename):
                return render_template("register.html", error="Invalid CV file type. Use PDF, DOC, DOCX, JPG or PNG.")
            safe = secure_filename(cv_file.filename)
            cv_filename = f"{first_name}_{last_name}_CV_{safe}"
            cv_file.save(os.path.join(UPLOAD_FOLDER, cv_filename))

        ticket_files = request.files.getlist("tickets")
        ticket_files = [f for f in ticket_files if f and f.filename]

        if len(ticket_files) > MAX_TICKETS_FILES:
            return render_template("register.html", error=f"You can upload up to {MAX_TICKETS_FILES} ticket files.")

        saved = []
        for idx, f in enumerate(ticket_files, start=1):
            if not allowed_file(f.filename):
                return render_template("register.html", error="Invalid tickets file type. Use PDF, DOC, DOCX, JPG or PNG.")
            safe = secure_filename(f.filename)
            filename = f"{first_name}_{last_name}_TICKET{idx}_{safe}"
            f.save(os.path.join(UPLOAD_FOLDER, filename))
            saved.append(filename)

        if saved:
            tickets_filename = "|".join(saved)

        now_ts = datetime.utcnow().isoformat()
        now_date = datetime.utcnow().date().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO registrations
            (first_name, last_name, email, phone, town, primary_trade, primary_ticket,
             additional_info, cv_filename, tickets_filename, consent, consent_at, created_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            first_name, last_name, email, phone, town,
            primary_trade, primary_ticket,
            additional_info, cv_filename, tickets_filename,
            consent, now_ts, now_date
        ))
        conn.commit()
        conn.close()

        return redirect(url_for("thanks"))

    return render_template("register.html", error=error)


# ----------------------------
# NEW: Candidate Register (New Starters)
# Hidden link: /candidateRegister
# ----------------------------
@app.route("/candidateRegister", methods=["GET", "POST"])
def candidate_register():
    error = None

    if request.method == "POST":
        first_name = request.form["first_name"].strip()
        last_name = request.form["last_name"].strip()
        email = request.form["email"].strip()
        phone = request.form["phone"].strip()
        town = request.form["town"].strip()
        primary_trade = request.form["primary_trade"].strip()
        primary_ticket = request.form["primary_ticket"].strip()

        utr = request.form.get("utr", "").strip()  # optional
        national_insurance = request.form["national_insurance"].strip()
        sort_code = request.form["sort_code"].strip()
        account_number = request.form["account_number"].strip()

        # Required upload
        id_doc = request.files.get("id_document")
        if not id_doc or not id_doc.filename:
            return render_template("candidate_register.html", error="Please upload your passport or birth certificate.")

        if not allowed_file(id_doc.filename):
            return render_template("candidate_register.html", error="Invalid file type. Use PDF, JPG or PNG.")

        safe = secure_filename(id_doc.filename)
        now_stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        id_document_filename = f"{first_name}_{last_name}_ID_{now_stamp}_{safe}"
        id_doc.save(os.path.join(UPLOAD_FOLDER, id_document_filename))

        created_date = datetime.utcnow().date().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO new_starters
            (first_name, last_name, email, phone, town, primary_trade, primary_ticket,
             utr, national_insurance, sort_code, account_number, id_document_filename, created_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            first_name, last_name, email, phone, town,
            primary_trade, primary_ticket,
            utr, national_insurance, sort_code, account_number,
            id_document_filename, created_date
        ))
        conn.commit()
        conn.close()

        return redirect(url_for("thanks"))

    return render_template("candidate_register.html", error=error)


@app.route("/thanks")
def thanks():
    return render_template("thanks.html")


# ----------------------------
# Admin download route
# ----------------------------
@app.route("/admin/download/<path:filename>")
@admin_required
def admin_download(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


# ----------------------------
# Admin Dashboard (New Candidates)
# ----------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    per_page = 15
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    conn = db_connect()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) AS cnt FROM registrations")
    total = c.fetchone()["cnt"]
    total_pages = max(1, math.ceil(total / per_page))
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    c.execute("SELECT * FROM registrations ORDER BY id DESC LIMIT ? OFFSET ?", (per_page, offset))
    rows = c.fetchall()
    conn.close()

    candidates = []
    for r in rows:
        d = dict(r)
        if not d.get("created_date"):
            d["created_date"] = (d.get("consent_at") or "")[:10]
        candidates.append(d)

    return render_template("admin.html", candidates=candidates, page=page, total_pages=total_pages)


# ----------------------------
# Admin CSV Exports
# ----------------------------
@app.route("/admin/export-candidates")
@admin_required
def export_candidates_csv():
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT * FROM registrations ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)

    if rows:
        keys = rows[0].keys()
        writer.writerow(keys)
        for row in rows:
            writer.writerow([row[k] for k in keys])

    output = BytesIO(si.getvalue().encode("utf-8"))
    output.seek(0)
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name="candidates.csv")


@app.route("/admin/export-contacts")
@admin_required
def export_contacts_csv():
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT * FROM contact_requests ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()

    si = StringIO()
    writer = csv.writer(si)

    if rows:
        keys = rows[0].keys()
        writer.writerow(keys)
        for row in rows:
            writer.writerow([row[k] for k in keys])

    output = BytesIO(si.getvalue().encode("utf-8"))
    output.seek(0)
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name="contacts.csv")


if __name__ == "__main__":
    app.run()
