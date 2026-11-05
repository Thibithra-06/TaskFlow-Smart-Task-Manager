import os
import sqlite3
import secrets
import smtplib
import ssl
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

from werkzeug.utils import secure_filename
# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "tasks.db")

load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)
UPLOAD_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "static",
    "uploads"
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app.secret_key = os.getenv("SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("SECRET_KEY is missing from your .env file.")


# Database connection
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# Initialize database
def initialize_database():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deadline_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            due_date TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            UNIQUE(task_id, due_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_otps (
            email TEXT PRIMARY KEY,
            otp_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER DEFAULT 0
        )
    """)

    otp_columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(email_otps)"
        ).fetchall()
    ]

    if "attempts" not in otp_columns:
        conn.execute(
            "ALTER TABLE email_otps ADD COLUMN attempts INTEGER DEFAULT 0"
        )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'Medium',
            status TEXT NOT NULL DEFAULT 'Pending'
        )
    """)

    columns = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    ]

    if "due_date" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")

    if "user_id" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER")
    user_columns = [
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    ]

    if "profile_photo" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN profile_photo TEXT"
        )
    conn.commit()
    conn.close()
def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper
def send_otp_email(email, otp):

    gmail_address = os.getenv("MAIL_USERNAME", "").strip()
    gmail_password = os.getenv("MAIL_PASSWORD", "").strip().replace(" ", "")

    if not gmail_address or not gmail_password:
        raise RuntimeError("Gmail credentials are missing in .env")

    message = EmailMessage()
    message["Subject"] = "TaskFlow - Email Verification OTP"
    message["From"] = gmail_address
    message["To"] = email

    message.set_content(
        f"""
Hello!

Welcome to TaskFlow.

Your email verification OTP is: {otp}

This OTP expires in 5 minutes.

If you did not request this, please ignore this email.

Thank you,
TaskFlow Team
"""
    )

    context = ssl.create_default_context()
    errors = []

    # First attempt: Gmail SSL port 465
    try:
        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465,
            context=context,
            timeout=20
        ) as server:

            server.login(gmail_address, gmail_password)
            server.send_message(message)

        app.logger.info("OTP email sent successfully.")
        return True

    except Exception as error:
        errors.append(f"Port 465: {type(error).__name__}: {error}")
        app.logger.warning("Gmail SSL attempt failed: %s", type(error).__name__)

    # Second attempt: Gmail STARTTLS port 587
    try:
        with smtplib.SMTP(
            "smtp.gmail.com",
            587,
            timeout=20
        ) as server:

            server.ehlo()
            server.starttls(context=context)
            server.ehlo()

            server.login(gmail_address, gmail_password)
            server.send_message(message)

        app.logger.info("OTP email sent successfully.")
        return True

    except Exception as error:
        errors.append(f"Port 587: {type(error).__name__}: {error}")
        app.logger.error("Gmail STARTTLS attempt failed: %s", type(error).__name__)

    raise RuntimeError("OTP email failed. " + " | ".join(errors))
def send_deadline_email(email, name, task_title, due_date):

    gmail_address = os.getenv("MAIL_USERNAME", "").strip()
    gmail_password = os.getenv("MAIL_PASSWORD", "").strip().replace(" ", "")

    if not gmail_address or not gmail_password:
        raise RuntimeError("Gmail credentials are missing.")

    message = EmailMessage()
    message["Subject"] = "TaskFlow - Task Deadline Reminder"
    message["From"] = gmail_address
    message["To"] = email

    message.set_content(f"""
Hello {name},

This is a reminder from TaskFlow.

Your task "{task_title}" has reached its deadline.

Due date: {due_date}

Please log in to TaskFlow and update your task.

Thank you,
TaskFlow Team
""")

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context,
        timeout=20
    ) as server:

        server.login(gmail_address, gmail_password)
        server.send_message(message)

    app.logger.info("Deadline reminder sent to %s", email)
def check_task_deadlines():

    conn = get_db_connection()

    today = date.today().isoformat()

    tasks = conn.execute("""
        SELECT
            tasks.id,
            tasks.title,
            tasks.due_date,
            users.name,
            users.email
        FROM tasks
        JOIN users ON tasks.user_id = users.id
        WHERE tasks.status = 'Pending'
        AND tasks.due_date IS NOT NULL
        AND tasks.due_date != ''
        AND tasks.due_date <= ?
        AND users.verified = 1
    """, (today,)).fetchall()

    for task in tasks:

        already_sent = conn.execute("""
            SELECT id
            FROM deadline_reminders
            WHERE task_id = ? AND due_date = ?
        """, (task["id"], task["due_date"])).fetchone()

        if already_sent:
            continue

        try:
            send_deadline_email(
                task["email"],
                task["name"],
                task["title"],
                task["due_date"]
            )

            conn.execute("""
                INSERT OR IGNORE INTO deadline_reminders
                (task_id, due_date, sent_at)
                VALUES (?, ?, ?)
            """, (
                task["id"],
                task["due_date"],
                datetime.now().isoformat()
            ))

            conn.commit()

        except Exception:
            app.logger.exception(
                "Failed to send deadline reminder for task %s",
                task["id"]
            )

    conn.close()
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("register"))

        if "@" not in email or "." not in email.split("@")[-1]:
            flash("Enter a valid email address.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return redirect(url_for("register"))

        conn = get_db_connection()

        existing_user = conn.execute(
            "SELECT id, verified FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing_user and existing_user["verified"]:
            conn.close()
            flash("This email is already registered. Please log in.", "error")
            return redirect(url_for("login"))

        password_hash = generate_password_hash(password)

        if existing_user:
            conn.execute("""
                UPDATE users
                SET name = ?, password = ?
                WHERE email = ?
            """, (name, password_hash, email))
        else:
            conn.execute("""
                INSERT INTO users (name, email, password)
                VALUES (?, ?, ?)
            """, (name, email, password_hash))

        otp = str(secrets.randbelow(900000) + 100000)
        otp_hash = generate_password_hash(otp)
        expires_at = (datetime.now() + timedelta(minutes=5)).isoformat()

        conn.execute("""
            INSERT OR REPLACE INTO email_otps
            (email, otp_hash, expires_at)
            VALUES (?, ?, ?)
        """, (email, otp_hash, expires_at))

        conn.commit()
        conn.close()

        try:
            send_otp_email(email, otp)
        except Exception:
            app.logger.exception("OTP email sending failed.")
            flash("Unable to send OTP. Check your Gmail settings in .env.", "error")
            return redirect(url_for("register"))

        session["pending_email"] = email
        flash("OTP sent to your email address.", "success")
        return redirect(url_for("verify_otp"))

    return render_template("register.html")


# Verify OTP
@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():

    email = session.get("pending_email")

    if not email:
        flash("Please register first.", "error")
        return redirect(url_for("register"))

    if request.method == "POST":

        entered_otp = request.form.get("otp", "").strip()

        conn = get_db_connection()

        otp_record = conn.execute(
            "SELECT * FROM email_otps WHERE email = ?",
            (email,)
        ).fetchone()

        if not otp_record:
            conn.close()
            flash("OTP not found. Please register again.", "error")
            return redirect(url_for("register"))

        if datetime.now() > datetime.fromisoformat(
            otp_record["expires_at"]
        ):
            conn.execute(
                "DELETE FROM email_otps WHERE email = ?",
                (email,)
            )
            conn.commit()
            conn.close()

            session.pop("pending_email", None)

            flash("OTP expired. Please register again.", "error")
            return redirect(url_for("register"))

        if otp_record["attempts"] >= 5:
            conn.close()
            session.pop("pending_email", None)

            flash("Too many attempts. Please register again.", "error")
            return redirect(url_for("register"))

        if not check_password_hash(
            otp_record["otp_hash"], entered_otp
        ):

            attempts = otp_record["attempts"] + 1

            if attempts >= 5:
                conn.execute(
                    "DELETE FROM email_otps WHERE email = ?",
                    (email,)
                )
                conn.commit()
                conn.close()

                session.pop("pending_email", None)

                flash(
                    "Too many incorrect attempts. Please register again.",
                    "error"
                )
                return redirect(url_for("register"))

            conn.execute(
                "UPDATE email_otps SET attempts = ? WHERE email = ?",
                (attempts, email)
            )

            conn.commit()
            conn.close()

            flash(
                f"Incorrect OTP. {5 - attempts} attempts remaining.",
                "error"
            )
            return redirect(url_for("verify_otp"))

        conn.execute(
            "UPDATE users SET verified = 1 WHERE email = ?",
            (email,)
        )

        conn.execute(
            "DELETE FROM email_otps WHERE email = ?",
            (email,)
        )

        conn.execute("""
            UPDATE tasks
            SET user_id = (
                SELECT id FROM users WHERE email = ?
            )
            WHERE user_id IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM users
                WHERE verified = 1 AND id != (
                    SELECT id FROM users WHERE email = ?
                )
            )
        """, (email, email))

        conn.commit()
        conn.close()

        session.pop("pending_email", None)

        flash("Email verified successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("verify_otp.html")
# Login
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db_connection()

        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        conn.close()

        if not user or not check_password_hash(user["password"], password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        if not user["verified"]:
            session["pending_email"] = email
            flash("Please verify your email first.", "error")
            return redirect(url_for("verify_otp"))

        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]

        return redirect(url_for("home"))

    return render_template("login.html")


# Logout
@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("You have logged out successfully.", "success")
    return redirect(url_for("login"))


# Dashboard
@app.route("/")
@login_required
def home():
    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "All")
    priority_filter = request.args.get("priority", "All")

    conn = get_db_connection()

    query = "SELECT * FROM tasks WHERE user_id = ?"
    params = [session["user_id"]]

    if search:
        query += " AND title LIKE ?"
        params.append(f"%{search}%")

    if status_filter in ["Pending", "Completed"]:
        query += " AND status = ?"
        params.append(status_filter)

    if priority_filter in ["Low", "Medium", "High"]:
        query += " AND priority = ?"
        params.append(priority_filter)

    query += " ORDER BY id DESC"
    tasks = conn.execute(query, params).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ?",
        (session["user_id"],)
    ).fetchone()[0]

    completed = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'Completed'",
        (session["user_id"],)
    ).fetchone()[0]

    pending = total - completed
    user = conn.execute(
    "SELECT name, profile_photo FROM users WHERE id = ?",
    (session["user_id"],)
).fetchone()
    conn.close()

    return render_template(
        "index.html",
        tasks=tasks,
        total=total,
        completed=completed,
        pending=pending,
        search=search,
        status_filter=status_filter,
        priority_filter=priority_filter,
        today=date.today().isoformat(),
        profile_name=user["name"] if user else "User",
profile_photo=user["profile_photo"] if user else None
    )


# Add task
@app.route("/add", methods=["POST"])
@login_required
def add_task():
    title = request.form.get("title", "").strip()
    priority = request.form.get("priority", "Medium")
    due_date = request.form.get("due_date", "").strip() or None

    if title and priority in ["Low", "Medium", "High"]:
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO tasks (title, priority, due_date, user_id)
            VALUES (?, ?, ?, ?)
        """, (title, priority, due_date, session["user_id"]))
        conn.commit()
        conn.close()

    return redirect(url_for("home"))


# Complete or reopen task
@app.route("/complete/<int:task_id>", methods=["POST"])
@login_required
def complete_task(task_id):
    conn = get_db_connection()

    conn.execute("""
        UPDATE tasks
        SET status = CASE
            WHEN status = 'Completed' THEN 'Pending'
            ELSE 'Completed'
        END
        WHERE id = ? AND user_id = ?
    """, (task_id, session["user_id"]))

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("home"))


# Delete task
@app.route("/delete/<int:task_id>", methods=["POST"])
@login_required
def delete_task(task_id):
    conn = get_db_connection()

    conn.execute(
        "DELETE FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, session["user_id"])
    )

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("home"))


# Edit task
@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    conn = get_db_connection()

    task = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, session["user_id"])
    ).fetchone()

    if task is None:
        conn.close()
        return redirect(url_for("home"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        priority = request.form.get("priority", "Medium")
        due_date = request.form.get("due_date", "").strip() or None

        if title and priority in ["Low", "Medium", "High"]:
            conn.execute("""
                UPDATE tasks
                SET title = ?, priority = ?, due_date = ?
                WHERE id = ? AND user_id = ?
            """, (title, priority, due_date, task_id, session["user_id"]))

            conn.commit()
            conn.close()
            return redirect(url_for("home"))

    conn.close()
    return render_template("edit_task.html", task=task)


# Calendar page
@app.route("/calendar")
@login_required
def calendar():
    conn = get_db_connection()

    tasks = conn.execute("""
        SELECT * FROM tasks
        WHERE user_id = ?
        AND due_date IS NOT NULL AND due_date != ''
        ORDER BY due_date ASC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "calendar.html",
        tasks=tasks,
        today=date.today().isoformat()
    )


# Analytics page
@app.route("/analytics")
@login_required
def analytics():
    conn = get_db_connection()

    user_id = session["user_id"]

    total = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ?",
        (user_id,)
    ).fetchone()[0]

    completed = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'Completed'",
        (user_id,)
    ).fetchone()[0]

    pending = total - completed

    overdue = conn.execute("""
        SELECT COUNT(*) FROM tasks
        WHERE user_id = ?
        AND status = 'Pending'
        AND due_date IS NOT NULL
        AND due_date < ?
    """, (user_id, date.today().isoformat())).fetchone()[0]

    priorities = conn.execute("""
        SELECT priority, COUNT(*) AS count
        FROM tasks
        WHERE user_id = ?
        GROUP BY priority
    """, (user_id,)).fetchall()

    conn.close()

    percentage = round((completed / total) * 100, 1) if total else 0

    return render_template(
        "analytics.html",
        total=total,
        completed=completed,
        pending=pending,
        overdue=overdue,
        percentage=percentage,
        priorities=priorities
    )


# Notifications page
@app.route("/notifications")
@login_required
def notifications():
    conn = get_db_connection()

    tasks = conn.execute("""
        SELECT * FROM tasks
        WHERE user_id = ?
        AND status = 'Pending'
        AND due_date IS NOT NULL
        AND due_date != ''
        ORDER BY due_date ASC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "notifications.html",
        tasks=tasks,
        today=date.today().isoformat()
    )

@app.route("/upload-profile-photo", methods=["POST"])
@login_required
def upload_profile_photo():

    photo = request.files.get("profile_photo")

    if not photo or not photo.filename:
        flash("Please select a profile photo.", "error")
        return redirect(url_for("home"))

    extension = photo.filename.rsplit(".", 1)[-1].lower()

    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        flash("Only PNG, JPG, JPEG, GIF and WEBP images are allowed.", "error")
        return redirect(url_for("home"))

    filename = (
        f"user_{session['user_id']}_{secrets.token_hex(8)}.{extension}"
    )

    filepath = os.path.join(
        app.config["UPLOAD_FOLDER"],
        filename
    )

    photo.save(filepath)

    relative_path = f"uploads/{filename}"

    conn = get_db_connection()

    conn.execute(
        "UPDATE users SET profile_photo = ? WHERE id = ?",
        (relative_path, session["user_id"])
    )

    conn.commit()
    conn.close()

    flash("Profile photo updated successfully!", "success")

    return redirect(url_for("home"))
# Settings page
@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")



if __name__ == "__main__":

    initialize_database()

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        func=check_task_deadlines,
        trigger="interval",
        seconds=60,
        id="task_deadline_checker",
        replace_existing=True,
        max_instances=1
    )

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        scheduler.start()

    app.run(debug=True)