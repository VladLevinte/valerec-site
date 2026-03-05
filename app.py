from flask import Flask, render_template, request, redirect, url_for, send_file, send_from_directory, session, jsonify
from flask import Response
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

app.config["MAX_CONTENT_LENGTH"] = (MAX_UPLOAD_MB_EACH * (MAX_TICKETS_FILES + 3)) * 1024 * 1024
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
            sharecode TEXT,
            national_insurance TEXT,
            sort_code TEXT,
            account_number TEXT,
            id_document_filename TEXT,
            tickets_filename TEXT,
            created_date TEXT,
            client_name TEXT,
            start_date TEXT,
            job_postcode TEXT,
            pay_rate TEXT
        )
    """)

    conn.commit()

    ensure_column(conn, "registrations", "created_date", "TEXT")
    ensure_column(conn, "contact_requests", "created_date", "TEXT")
    ensure_column(conn, "new_starters", "tickets_filename", "TEXT")
    ensure_column(conn, "new_starters", "sharecode", "TEXT")
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
        """,(name,email,phone,company,message,now_ts,now_date,consent,now_ts))

        conn.commit()
        conn.close()

        return render_template("thanks.html")

    return render_template("contact.html", error=error)


@app.route("/register", methods=["GET","POST"])
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
        additional_info = request.form.get("additional_info","")

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
        """,(first_name,last_name,email,phone,town,primary_trade,primary_ticket,
             additional_info,cv_filename,tickets_filename,consent,now_ts,now_date))

        conn.commit()
        conn.close()

        return redirect(url_for("thanks"))

    return render_template("register.html", error=error)


@app.route("/candidateRegister", methods=["GET","POST"])
def candidate_register():
    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        phone = request.form["phone"]
        town = request.form["town"]
        primary_trade = request.form["primary_trade"]
        primary_ticket = request.form["primary_ticket"]

        utr = request.form.get("utr","").strip()
        sharecode = request.form.get("sharecode","").strip()

        national_insurance = request.form["national_insurance"]
        sort_code = request.form["sort_code"]
        account_number = request.form["account_number"]

        consent = 1 if request.form.get("consent") == "on" else 0
        if consent != 1:
            return render_template("candidate_register.html", error="Please confirm you have read the Privacy Policy and agree to the processing of your details.")

        id_doc = request.files.get("id_document")
        if not id_doc or not id_doc.filename:
            return render_template("candidate_register.html", error="Upload passport or birth certificate.")

        safe = secure_filename(id_doc.filename)
        now_stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        id_document_filename = f"{first_name}_{last_name}_ID_{now_stamp}_{safe}"
        id_doc.save(os.path.join(UPLOAD_FOLDER, id_document_filename))

        ticket_files = request.files.getlist("tickets")
        ticket_files = [f for f in ticket_files if f and f.filename]

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
             utr,sharecode,national_insurance,sort_code,account_number,
             id_document_filename,tickets_filename,created_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(first_name,last_name,email,phone,town,primary_trade,primary_ticket,
             utr,sharecode,national_insurance,sort_code,account_number,
             id_document_filename,tickets_filename,created_date))

        conn.commit()
        conn.close()

        return redirect(url_for("thanks"))

    return render_template("candidate_register.html")


@app.route("/thanks")
def thanks():
    return render_template("thanks.html")


@app.route("/admin/download/<path:filename>")
@admin_required
def admin_download(filename):
    # default: keep original filename
    download_name = filename

    # If it's a Ticket file, rename download to: "Ticket X - Full Name.ext"
    # Your saved ticket filenames look like:
    #   First_Last_TICKET1_original.ext
    #   First_Last_STARTER_TICKET2_timestamp_original.ext
    try:
        base = os.path.basename(filename)

        # detect whether it's a ticket and extract ticket number + name
        if "_TICKET" in base:
            parts = base.split("_")
            # first and last name are usually first 2 parts
            first = parts[0] if len(parts) > 0 else ""
            last  = parts[1] if len(parts) > 1 else ""
            full_name = (first + " " + last).strip()

            # extract ticket number after "TICKET"
            # handles "TICKET1" and "STARTER_TICKET1"
            ticket_num = None
            for p in parts:
                if p.startswith("TICKET"):
                    ticket_num = p.replace("TICKET", "")
                    break

            # file extension
            _, ext = os.path.splitext(base)

            if ticket_num and full_name:
                download_name = f"Ticket {ticket_num} - {full_name}{ext}"
    except Exception:
        pass

    return send_from_directory(
        UPLOAD_FOLDER,
        filename,
        as_attachment=True,
        download_name=download_name
    )


@app.route("/admin/starter-notes/<int:starter_id>", methods=["POST"])
@admin_required
def starter_notes(starter_id):
    data = request.get_json(force=True) or {}
    client_name = (data.get("client_name") or "").strip()
    start_date = (data.get("start_date") or "").strip()      # YYYY-MM-DD from date picker
    job_postcode = (data.get("job_postcode") or "").strip()
    pay_rate = (data.get("pay_rate") or "").strip()

    conn = db_connect()
    c = conn.cursor()
    c.execute("""
        UPDATE new_starters
        SET client_name=?, start_date=?, job_postcode=?, pay_rate=?
        WHERE id=?
    """,(client_name, start_date, job_postcode, pay_rate, starter_id))
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


@app.route("/admin")
@admin_required
def admin_dashboard():
    per_page = 15
    page = request.args.get("page", 1, type=int)
    view = request.args.get("view", "candidates")
    q = (request.args.get("q") or "").strip()

    conn = db_connect()
    c = conn.cursor()

    if view == "starters":
        where = ""
        params = []
        if q:
            where = """
              WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR phone LIKE ?
                 OR town LIKE ? OR primary_trade LIKE ? OR primary_ticket LIKE ?
                 OR utr LIKE ? OR national_insurance LIKE ? OR sort_code LIKE ?
                 OR account_number LIKE ? OR sharecode LIKE ?
            """
            like = f"%{q}%"
            params = [like]*12

        c.execute(f"SELECT COUNT(*) AS cnt FROM new_starters {where}", params)
        total = c.fetchone()["cnt"]
        total_pages = max(1, math.ceil(total / per_page))
        offset = (page - 1) * per_page

        c.execute(f"SELECT * FROM new_starters {where} ORDER BY id DESC LIMIT ? OFFSET ?", params + [per_page, offset])
        rows = c.fetchall()
        starters = [dict(r) for r in rows]

        # split tickets
        for s in starters:
            raw = s.get("tickets_filename") or ""
            s["tickets_files"] = [x for x in raw.split("|") if x] if raw else []

        conn.close()
        return render_template("admin.html", view="starters", starters=starters, candidates=[],
                               page=page, total_pages=total_pages, q=q)

    # candidates view
    where = ""
    params = []
    if q:
        where = """
          WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR phone LIKE ?
             OR town LIKE ? OR primary_trade LIKE ? OR primary_ticket LIKE ?
             OR additional_info LIKE ? OR tickets_filename LIKE ?
        """
        like = f"%{q}%"
        params = [like]*9

    c.execute(f"SELECT COUNT(*) AS cnt FROM registrations {where}", params)
    total = c.fetchone()["cnt"]
    total_pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page

    c.execute(f"SELECT * FROM registrations {where} ORDER BY id DESC LIMIT ? OFFSET ?", params + [per_page, offset])
    rows = c.fetchall()
    candidates = [dict(r) for r in rows]

    for cand in candidates:
        raw = cand.get("tickets_filename") or ""
        cand["tickets_files"] = [x for x in raw.split("|") if x] if raw else []

    conn.close()

    return render_template("admin.html",
        view="candidates",
        candidates=candidates,
        starters=[],
        page=page,
        total_pages=total_pages,
        q=q
    )


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

    return send_file(output,
        mimetype="text/csv",
        as_attachment=True,
        download_name="candidates.csv"
    )


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

    return send_file(output,
        mimetype="text/csv",
        as_attachment=True,
        download_name="contacts.csv"
    )


if __name__ == "__main__":
    app.run()


