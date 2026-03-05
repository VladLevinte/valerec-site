from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, send_from_directory, session, jsonify
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

# Allow a bit more because multi uploads can exceed 5MB total
app.config["MAX_CONTENT_LENGTH"] = (MAX_UPLOAD_MB_EACH * (MAX_TICKETS_FILES + 6)) * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ADMIN_PASSWORD = "Vale228"
app.secret_key = "CHANGE_THIS_SECRET_KEY_2026"


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def db_connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, coltype):
    c = conn.cursor()
    c.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in c.fetchall()}
    if column not in existing:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()


def safe_remove_file(filename):
    if not filename:
        return
    path = os.path.join(UPLOAD_FOLDER, filename)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def split_files(piped):
    piped = (piped or "").strip()
    if not piped:
        return []
    return [x for x in piped.split("|") if x]


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
            consent_at TEXT,
            created_date TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS contact_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            phone TEXT,
            company TEXT,
            message TEXT,
            created_at TEXT,
            created_date TEXT,
            consent INTEGER,
            consent_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS new_starters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            phone TEXT,
            town TEXT,
            primary_trade TEXT,
            primary_ticket TEXT,
            utr TEXT,
            national_insurance TEXT,
            sort_code TEXT,
            account_number TEXT,
            id_document_filename TEXT,
            tickets_filename TEXT,
            created_date TEXT
        )
    """)

    conn.commit()

    # ensure columns exist (safe for old databases)
    ensure_column(conn, "registrations", "created_date", "TEXT")
    ensure_column(conn, "contact_requests", "created_date", "TEXT")
    ensure_column(conn, "new_starters", "tickets_filename", "TEXT")

    # starter “notes” fields (editable in admin expanded row)
    ensure_column(conn, "new_starters", "client_name", "TEXT")
    ensure_column(conn, "new_starters", "start_date", "TEXT")
    ensure_column(conn, "new_starters", "job_postcode", "TEXT")
    ensure_column(conn, "new_starters", "pay_rate", "TEXT")

    conn.close()


init_db()


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("is_admin"):
            return f(*args, **kwargs)
        return redirect(url_for("admin_login"))
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Wrong password"
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("home"))


# ---------------- Public pages ----------------
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


@app.route("/contact", methods=["GET", "POST"])
def contact():
    error = None
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        phone = request.form.get("phone", "")
        company = request.form.get("company", "")
        message = request.form["message"]
        consent = 1 if request.form.get("consent") == "on" else 0

        if consent != 1:
            return render_template("contact.html", error="Please confirm privacy policy.")

        now_ts = datetime.utcnow().isoformat()
        now_date = datetime.utcnow().date().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO contact_requests
            (name,email,phone,company,message,created_at,created_date,consent,consent_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (name, email, phone, company, message, now_ts, now_date, consent, now_ts))
        conn.commit()
        conn.close()

        return render_template("thanks.html")

    return render_template("contact.html", error=error)


# ---------------- New Candidates (/register) ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    error = None

    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        phone = request.form["phone"]
        town = request.form["town"]
        primary_trade = request.form["primary_trade"]
        primary_ticket = request.form["primary_ticket"]
        additional_info = request.form.get("additional_info", "")

        consent = 1 if request.form.get("consent") == "on" else 0
        if consent != 1:
            return render_template("register.html", error="Please confirm privacy policy.")

        cv_filename = None
        tickets_filename = None

        cv_file = request.files.get("cv")
        if cv_file and cv_file.filename:
            if not allowed_file(cv_file.filename):
                return render_template("register.html", error="Invalid CV file type.")
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
                return render_template("register.html", error="Invalid ticket file type.")
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
            (first_name,last_name,email,phone,town,primary_trade,primary_ticket,
             additional_info,cv_filename,tickets_filename,consent,consent_at,created_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (first_name, last_name, email, phone, town, primary_trade, primary_ticket,
              additional_info, cv_filename, tickets_filename, consent, now_ts, now_date))
        conn.commit()
        conn.close()

        return redirect(url_for("thanks"))

    return render_template("register.html", error=error)


# ---------------- New Starters (/candidateRegister) ----------------
@app.route("/candidateRegister", methods=["GET", "POST"])
def candidate_register():
    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        phone = request.form["phone"]
        town = request.form["town"]
        primary_trade = request.form["primary_trade"]  # this is “Position you’re starting” label in template
        primary_ticket = request.form["primary_ticket"]

        utr = request.form.get("utr", "")

        national_insurance = request.form["national_insurance"]
        sort_code = request.form["sort_code"]
        account_number = request.form["account_number"]

        id_doc = request.files.get("id_document")
        if not id_doc or not id_doc.filename:
            return render_template("candidate_register.html", error="Upload passport or birth certificate.")

        if not allowed_file(id_doc.filename):
            return render_template("candidate_register.html", error="Invalid ID document file type.")

        safe = secure_filename(id_doc.filename)
        now_stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        id_document_filename = f"{first_name}_{last_name}_ID_{now_stamp}_{safe}"
        id_doc.save(os.path.join(UPLOAD_FOLDER, id_document_filename))

        ticket_files = request.files.getlist("tickets")
        ticket_files = [f for f in ticket_files if f and f.filename]

        if len(ticket_files) > MAX_TICKETS_FILES:
            return render_template("candidate_register.html", error=f"You can upload up to {MAX_TICKETS_FILES} ticket files.")

        saved = []
        for idx, f in enumerate(ticket_files, start=1):
            if not allowed_file(f.filename):
                return render_template("candidate_register.html", error="Invalid ticket file.")
            safe_ticket = secure_filename(f.filename)
            filename = f"{first_name}_{last_name}_STARTER_TICKET{idx}_{now_stamp}_{safe_ticket}"
            f.save(os.path.join(UPLOAD_FOLDER, filename))
            saved.append(filename)

        tickets_filename = "|".join(saved) if saved else None
        created_date = datetime.utcnow().date().isoformat()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""
            INSERT INTO new_starters
            (first_name,last_name,email,phone,town,primary_trade,primary_ticket,
             utr,national_insurance,sort_code,account_number,
             id_document_filename,tickets_filename,created_date,
             client_name,start_date,job_postcode,pay_rate)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (first_name, last_name, email, phone, town, primary_trade, primary_ticket,
              utr, national_insurance, sort_code, account_number,
              id_document_filename, tickets_filename, created_date,
              None, None, None, None))
        conn.commit()
        conn.close()

        return redirect(url_for("thanks"))

    return render_template("candidate_register.html")


@app.route("/thanks")
def thanks():
    return render_template("thanks.html")


# ---------------- Admin downloads ----------------
@app.route("/admin/download/<path:filename>")
@admin_required
def admin_download(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


# ---------------- Search helpers ----------------
def build_like_where(columns, q):
    """
    returns (where_sql, params)
    """
    q = (q or "").strip()
    if not q:
        return "", []
    like = f"%{q}%"
    parts = [f"{col} LIKE ?" for col in columns]
    where = " WHERE " + " OR ".join(parts)
    params = [like] * len(columns)
    return where, params


# ---------------- Admin dashboard (two views + search) ----------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    view = request.args.get("view", "candidates").strip().lower()
    if view not in ("candidates", "starters"):
        view = "candidates"

    q = (request.args.get("q") or "").strip()

    per_page = 15
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    conn = db_connect()
    c = conn.cursor()

    if view == "candidates":
        where, params = build_like_where(
            ["first_name", "last_name", "email", "phone", "town", "primary_trade", "primary_ticket", "additional_info"],
            q
        )
        c.execute(f"SELECT COUNT(*) AS cnt FROM registrations{where}", params)
        total = c.fetchone()["cnt"]
        total_pages = max(1, math.ceil(total / per_page))
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page
        c.execute(
            f"SELECT * FROM registrations{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [per_page, offset]
        )
        rows = c.fetchall()
        conn.close()

        candidates = []
        for r in rows:
            d = dict(r)
            d["tickets_files"] = split_files(d.get("tickets_filename"))
            return_candidate = d
            candidates.append(return_candidate)

        return render_template(
            "admin.html",
            view=view,
            q=q,
            candidates=candidates,
            starters=[],
            page=page,
            total_pages=total_pages
        )

    # starters
    where, params = build_like_where(
        ["first_name", "last_name", "email", "phone", "town", "primary_trade", "primary_ticket",
         "utr", "national_insurance", "sort_code", "account_number",
         "client_name", "start_date", "job_postcode", "pay_rate"],
        q
    )
    c.execute(f"SELECT COUNT(*) AS cnt FROM new_starters{where}", params)
    total = c.fetchone()["cnt"]
    total_pages = max(1, math.ceil(total / per_page))
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    c.execute(
        f"SELECT * FROM new_starters{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, offset]
    )
    rows = c.fetchall()
    conn.close()

    starters = []
    for r in rows:
        d = dict(r)
        d["tickets_files"] = split_files(d.get("tickets_filename"))
        starters.append(d)

    return render_template(
        "admin.html",
        view=view,
        q=q,
        candidates=[],
        starters=starters,
        page=page,
        total_pages=total_pages
    )


# ---------------- Admin: save starter notes (expanded row) ----------------
@app.route("/admin/starter-notes/<int:starter_id>", methods=["POST"])
@admin_required
def save_starter_notes(starter_id):
    data = request.get_json(silent=True) or {}
    client_name = (data.get("client_name") or "").strip()
    start_date = (data.get("start_date") or "").strip()
    job_postcode = (data.get("job_postcode") or "").strip()
    pay_rate = (data.get("pay_rate") or "").strip()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        UPDATE new_starters
        SET client_name = ?, start_date = ?, job_postcode = ?, pay_rate = ?
        WHERE id = ?
    """, (client_name, start_date, job_postcode, pay_rate, starter_id))
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


# ---------------- Admin: delete selected ----------------
@app.route("/admin/delete", methods=["POST"])
@admin_required
def admin_delete():
    view = (request.form.get("view") or "candidates").strip().lower()
    ids_raw = request.form.get("ids", "").strip()
    q = (request.form.get("q") or "").strip()
    page = request.form.get("page", "1").strip()

    # parse ids
    ids = []
    for part in ids_raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))

    if not ids:
        return redirect(url_for("admin_dashboard", view=view, q=q, page=page))

    conn = db_connect()
    c = conn.cursor()

    if view == "candidates":
        # delete files then rows
        qmarks = ",".join(["?"] * len(ids))
        c.execute(f"SELECT cv_filename, tickets_filename FROM registrations WHERE id IN ({qmarks})", ids)
        rows = c.fetchall()
        for r in rows:
            safe_remove_file(r["cv_filename"])
            for f in split_files(r["tickets_filename"]):
                safe_remove_file(f)

        c.execute(f"DELETE FROM registrations WHERE id IN ({qmarks})", ids)
        conn.commit()
        conn.close()
        return redirect(url_for("admin_dashboard", view="candidates", q=q, page=page))

    # starters
    qmarks = ",".join(["?"] * len(ids))
    c.execute(f"SELECT id_document_filename, tickets_filename FROM new_starters WHERE id IN ({qmarks})", ids)
    rows = c.fetchall()
    for r in rows:
        safe_remove_file(r["id_document_filename"])
        for f in split_files(r["tickets_filename"]):
            safe_remove_file(f)

    c.execute(f"DELETE FROM new_starters WHERE id IN ({qmarks})", ids)
    conn.commit()
    conn.close()

    return redirect(url_for("admin_dashboard", view="starters", q=q, page=page))


# ---------------- Exports ----------------
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
