import os
import json
import secrets
import hmac
import hashlib
import base64
import binascii
import ssl
from datetime import datetime, timedelta, timezone
from time import monotonic
from functools import wraps
from typing import Any, Dict
from urllib.parse import urlencode

import pymysql
import requests
from payment_security import valid_signature, valid_payment_id, payment_problem
import urllib3
from flask import Flask, flash, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_mail import Mail, Message
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


def load_local_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["MYSQL_HOST"] = os.getenv("MYSQL_HOST", "localhost")
app.config["MYSQL_PORT"] = int(os.getenv("MYSQL_PORT", "3306"))
app.config["MYSQL_SSL_CA"] = os.getenv("MYSQL_SSL_CA", "").strip()
app.config["MYSQL_USER"] = os.getenv("MYSQL_USER", "root")
app.config["MYSQL_PASSWORD"] = os.getenv("MYSQL_PASSWORD", "")
app.config["MYSQL_DB"] = os.getenv("MYSQL_DB", "gottime")
app.config["MYSQL_TIME_ZONE"] = os.getenv("MYSQL_TIME_ZONE", "+05:30")
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "")
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER", "gottime@example.com")
app.config["GOOGLE_OAUTH_VERIFY_SSL"] = os.getenv("GOOGLE_OAUTH_VERIFY_SSL", "true").lower() not in {"0", "false", "no"}
app.config["RAZORPAY_KEY_ID"] = os.getenv("RAZORPAY_KEY_ID", "")
app.config["RAZORPAY_KEY_SECRET"] = os.getenv("RAZORPAY_KEY_SECRET", "")
app.config["RAZORPAY_WEBHOOK_SECRET"] = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
app.config["DONATION_UPI_ID"] = os.getenv("DONATION_UPI_ID", "")
app.config["DONATION_UPI_NAME"] = os.getenv("DONATION_UPI_NAME", "GotTime")

if not app.config["GOOGLE_OAUTH_VERIFY_SSL"]:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

mail = Mail(app)
auth_serializer = URLSafeSerializer(app.config["SECRET_KEY"], salt="gottime-api-auth")

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def make_auth_token(user_id: int) -> str:
    return auth_serializer.dumps({"user_id": int(user_id)})


def user_id_from_auth_token():
    token = request.headers.get("X-GotTime-Auth", "").strip()
    if not token:
        return None
    try:
        payload = auth_serializer.loads(token)
        return int(payload.get("user_id"))
    except (BadSignature, TypeError, ValueError):
        return None


def google_error_redirect(code: str, detail: str = ""):
    frontend = os.getenv("FRONTEND_URL", request.host_url.rstrip("/"))
    params = {"authError": code}
    if detail:
        params["authDetail"] = detail[:220]
    return redirect(f"{frontend}/?{urlencode(params)}")


def finish_google_sign_in(user_info: dict, role: str):
    email = (user_info.get("email") or "").strip().lower()
    if not email:
        return None
    google_birthdate = user_info.get("birthdate") or user_info.get("birthday")
    google_age = age_from_birthdate(google_birthdate)
    if google_age is None:
        google_birthdate = None
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        if user:
            if not user.get("is_active"):
                return None
            if google_birthdate and not user.get("birthdate"):
                cursor.execute(
                    "UPDATE users SET google_id = %s, birthdate = %s, age = %s, is_active = 1 WHERE id = %s",
                    (user_info.get("sub"), google_birthdate, google_age, user["id"]),
                )
            else:
                cursor.execute("UPDATE users SET google_id = %s, is_active = 1 WHERE id = %s", (user_info.get("sub"), user["id"]))
            user_id = user["id"]
        else:
            cursor.execute(
                "INSERT INTO users (username, email, password, role, bio, location, birthdate, age, google_id, is_active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                (
                    user_info.get("given_name") or user_info.get("name") or email.split("@", 1)[0],
                    email,
                    generate_password_hash(secrets.token_urlsafe(16)),
                    role,
                    "Ready to trade hours and learn something new.",
                    "Remote",
                    google_birthdate,
                    google_age or 18,
                    user_info.get("sub") or email,
                ),
            )
            cursor.execute("SELECT LAST_INSERT_ID() as id")
            user_id = cursor.fetchone()["id"]
            cursor.execute("INSERT INTO credits (user_id, balance) VALUES (%s, %s)", (user_id, 10))
            cursor.execute(
                "INSERT INTO transactions (user_id, kind, amount, note) VALUES (%s, 'bonus', 10, 'Welcome credits')",
                (user_id,),
            )
        cursor.execute(
            "INSERT INTO credits (user_id, balance) VALUES (%s, 10) ON DUPLICATE KEY UPDATE balance = balance",
            (user_id,),
        )
    session["user_id"] = user_id
    session.permanent = True
    session.modified = True
    return user_id


def redirect_google_success(user_id: int):
    frontend = os.getenv("FRONTEND_URL", "/")
    query = urlencode({"authToken": make_auth_token(user_id), "authSuccess": "google"})
    return redirect(f"{frontend}/?{query}")


def dev_google_fallback(role: str, reason: str):
    return redirect_google_failure(reason)


def redirect_google_failure(reason):
    # OAuth failures never authenticate a development account.
    return google_error_redirect("google_server")


MENTOR_REVIEWS_FROM = """FROM ratings r
    JOIN sessions rated ON rated.id = r.session_id
      AND rated.mentor_id = r.ratee_id AND rated.learner_id = r.rater_id
    LEFT JOIN skills sk ON sk.id = rated.skill_id
    LEFT JOIN reviews review ON review.id = (
        SELECT MAX(rv.id) FROM reviews rv
        WHERE rv.session_id = r.session_id AND rv.reviewer_id = r.rater_id
          AND rv.reviewee_id = r.ratee_id AND CHAR_LENGTH(TRIM(rv.content)) > 0)
    WHERE rated.status = 'completed' AND r.score BETWEEN 1 AND 5"""


def mentor_rating_stats_select():
    source = MENTOR_REVIEWS_FROM + " AND r.ratee_id = u.id"
    return f"""(SELECT COALESCE(AVG(r.score), 0) {source}) AS rating,
               (SELECT COUNT(*) {source}) AS rating_count,
               (SELECT COUNT(review.id) {source}) AS review_count"""


def minimal_app_payload(error: str = ""):
    user = current_user()
    user_id = user["id"] if user else session.get("user_id")
    mentors = []
    leaderboard = []
    contacts = []
    sessions_data = []
    my_skills = []
    skills_data = []
    badges_data = []
    notifications_data = []
    transactions_data = []
    unread_messages = 0
    daily_quest = current_daily_quest()
    daily_quest_claimed = False
    try:
        profile_photo_select = "profile_photo" if db_column_exists("users", "profile_photo") else "NULL AS profile_photo"
        banner_style_select = "banner_style" if db_column_exists("users", "banner_style") else "'mint' AS banner_style"
        banner_image_select = "banner_image" if db_column_exists("users", "banner_image") else "NULL AS banner_image"
        mentor_profile_photo_select = "u.profile_photo AS profile_photo" if db_column_exists("users", "profile_photo") else "NULL AS profile_photo"
        mentor_banner_style_select = "u.banner_style AS banner_style" if db_column_exists("users", "banner_style") else "'mint' AS banner_style"
        mentor_banner_image_select = "u.banner_image AS banner_image" if db_column_exists("users", "banner_image") else "NULL AS banner_image"
        skill_category_select = "COALESCE(us.category, sk.category) AS category" if db_column_exists("user_skills", "category") else "sk.category"
        skill_duration_select = "us.duration" if db_column_exists("user_skills", "duration") else "60 AS duration"
        skill_availability_select = "us.availability" if db_column_exists("user_skills", "availability") else "'online' AS availability"
        skill_preferred_time_select = "us.preferred_time" if db_column_exists("user_skills", "preferred_time") else "NULL AS preferred_time"
        message_preview_select = "CASE WHEN m.encrypted_content IS NOT NULL THEN 'Encrypted message' ELSE m.content END" if db_column_exists("messages", "encrypted_content") else "m.content"
        streak_select = active_streak_select()
        with get_db().cursor() as cursor:
            dispatch_due_session_reminders(user_id)
            mark_missed_sessions(user_id)
            cursor.execute(
                """SELECT s.*, sk.name AS skill_name, sk.category, us.preferred_time AS preferred_time,
                          COALESCE(us.duration, 60) AS duration,
                          mentor.username AS mentor_name, learner.username AS learner_name
                   FROM sessions s
                   JOIN skills sk ON sk.id = s.skill_id
                   LEFT JOIN user_skills us ON us.user_id = s.mentor_id AND us.skill_id = s.skill_id
                   JOIN users mentor ON mentor.id = s.mentor_id
                   JOIN users learner ON learner.id = s.learner_id
                   WHERE s.learner_id = %s OR s.mentor_id = %s
                   ORDER BY COALESCE(s.scheduled_for, s.created_at) DESC""",
                (user_id, user_id),
            )
            sessions_data = cursor.fetchall()
            sessions_data = enrich_sessions_with_quiz(sessions_data, user_id)
            cursor.execute(
                f"""SELECT u.id, u.username, u.role, u.bio, {mentor_profile_photo_select},
                           {mentor_banner_style_select}, {mentor_banner_image_select},
                           (SELECT COUNT(*) FROM sessions done WHERE (done.learner_id = u.id OR done.mentor_id = u.id)
                            AND done.status = 'completed') AS sessions_completed,
                           {mentor_rating_stats_select()}, {streak_select},
                           us.id AS mentor_skill_id,
                           sk.id AS skill_id,
                           sk.name AS primary_skill,
                           sk.description AS primary_description,
                           sk.name AS skills,
                           COALESCE(us.category, sk.category) AS categories,
                           COALESCE(us.duration, 60) AS duration,
                           COALESCE(us.availability, 'online') AS availability,
                           us.preferred_time AS preferred_time
                    FROM users u
                    JOIN user_skills us ON us.user_id = u.id
                    JOIN skills sk ON sk.id = us.skill_id
                    WHERE u.id != %s AND u.is_active = 1
                    GROUP BY us.id, u.id, sk.id
                    ORDER BY rating DESC, sessions_completed DESC, u.username, us.created_at DESC
                    LIMIT 20""",
                (user_id,),
            )
            mentors = cursor.fetchall()
            cursor.execute(
                f"""SELECT u.id, u.username, u.role, {profile_photo_select},
                           {banner_style_select}, {banner_image_select},
                           COALESCE(c.balance, 0) AS credits, {streak_select},
                           (SELECT COUNT(*) FROM sessions done WHERE (done.learner_id = u.id OR done.mentor_id = u.id)
                            AND done.status = 'completed') AS sessions_completed,
                           {mentor_rating_stats_select()}
                    FROM users u LEFT JOIN credits c ON c.user_id = u.id
                    WHERE u.is_active = 1 ORDER BY credits DESC, sessions_completed DESC LIMIT 10"""
            )
            leaderboard = cursor.fetchall()
            cursor.execute("SELECT id, name, category, description FROM skills ORDER BY name")
            skills_data = cursor.fetchall()
            cursor.execute(
                f"""SELECT us.id, us.skill_id, sk.name, {skill_category_select},
                          sk.description, {skill_duration_select}, {skill_availability_select}, {skill_preferred_time_select}
                   FROM user_skills us JOIN skills sk ON sk.id = us.skill_id
                   WHERE us.user_id = %s ORDER BY us.created_at DESC""",
                (user_id,),
            )
            my_skills = cursor.fetchall()
            cursor.execute(
                f"""SELECT u.id, u.username, u.role, {profile_photo_select},
                          {banner_style_select}, {banner_image_select},
                          (SELECT {message_preview_select} FROM messages m
                           WHERE ((m.sender_id = %s AND m.receiver_id = u.id AND m.deleted_for_sender = 0)
                              OR (m.sender_id = u.id AND m.receiver_id = %s AND m.deleted_for_receiver = 0))
                             AND m.deleted_for_everyone = 0
                           ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message,
                          (SELECT COUNT(*) FROM messages unread
                           WHERE unread.sender_id = u.id
                             AND unread.receiver_id = %s
                             AND unread.is_read = 0
                             AND unread.deleted_for_receiver = 0
                             AND unread.deleted_for_everyone = 0) AS unread_count
                   FROM users u
                   WHERE u.id != %s
                     AND u.is_active = 1
                     AND (EXISTS (
                       SELECT 1 FROM messages existing
                       WHERE ((existing.sender_id = %s AND existing.receiver_id = u.id AND existing.deleted_for_sender = 0)
                          OR (existing.sender_id = u.id AND existing.receiver_id = %s AND existing.deleted_for_receiver = 0))
                         AND existing.deleted_for_everyone = 0
                     ) OR EXISTS (
                       SELECT 1 FROM sessions booked
                       WHERE booked.mentor_id = %s AND booked.learner_id = u.id
                         AND booked.status IN ('accepted', 'scheduled', 'awaiting_confirmation', 'completed', 'missed')
                     ) OR EXISTS (
                       SELECT 1 FROM sessions booked
                       WHERE booked.learner_id = %s AND booked.mentor_id = u.id
                         AND booked.status IN ('accepted', 'scheduled', 'awaiting_confirmation', 'completed', 'missed')
                     ))
                   ORDER BY u.username""",
                (user_id, user_id, user_id, user_id, user_id, user_id, user_id, user_id),
            )
            contacts = cursor.fetchall()
            cursor.execute(
                """SELECT COUNT(*) AS count FROM messages
                   WHERE receiver_id = %s
                     AND is_read = 0
                     AND deleted_for_receiver = 0
                     AND deleted_for_everyone = 0""",
                (user_id,),
            )
            unread_messages = cursor.fetchone()["count"]
            cursor.execute(
                """SELECT id, type, message, is_read, created_at
                   FROM notifications
                   WHERE user_id = %s
                   ORDER BY created_at DESC
                   LIMIT 20""",
                (user_id,),
            )
            notifications_data = cursor.fetchall()
            cursor.execute(
                """SELECT id, kind, amount, related_session_id, note, created_at FROM transactions
                   WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT 50""",
                (user_id,),
            )
            transactions_data = cursor.fetchall()
            if db_table_exists("user_badges"):
                cursor.execute(
                    """SELECT b.id, b.name, b.category, b.description, ub.earned_at
                       FROM user_badges ub JOIN badges b ON b.id = ub.badge_id
                       WHERE ub.user_id = %s ORDER BY ub.earned_at DESC""",
                    (user_id,),
                )
                badges_data = [display_badge(row) for row in cursor.fetchall()]
            if db_table_exists("xp_events"):
                daily_quest_claimed = has_daily_quest_claim(user_id, daily_quest)
    except Exception as exc:
        app.logger.exception("Minimal app payload also hit a non-fatal issue: %s", exc)
        raise
    user = current_user()
    quest_progress = daily_quest["target"] if daily_quest_claimed else daily_quest_progress(user_id, daily_quest)
    payload = {
        "user": user,
        "sessions": sessions_data,
        "mentors": mentors,
        "skills": skills_data,
        "mySkills": my_skills,
        "notifications": notifications_data,
        "transactions": transactions_data,
        "badges": badges_data,
        "dailyQuest": daily_quest,
        "dailyQuestClaimed": daily_quest_claimed,
        "dailyQuestProgress": quest_progress,
        "leaderboard": leaderboard,
        "contacts": contacts,
        "unreadMessages": unread_messages,
    }
    if error:
        payload["debugError"] = error
    return payload
    try:
        payload = auth_serializer.loads(token)
        return int(payload.get("user_id"))
    except (BadSignature, TypeError, ValueError):
        return None


def allowed_image_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def get_db():
    if "db" not in g:
        # A configured CA enables verified TLS. Invalid/missing certificates must
        # fail rather than silently falling back to an unencrypted connection.
        tls = None
        if app.config["MYSQL_SSL_CA"]:
            tls = ssl.create_default_context(cafile=app.config["MYSQL_SSL_CA"])
        g.db = pymysql.connect(
            host=app.config["MYSQL_HOST"],
            port=app.config["MYSQL_PORT"],
            ssl=tls,
            user=app.config["MYSQL_USER"],
            password=app.config["MYSQL_PASSWORD"],
            database=app.config["MYSQL_DB"],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=5,
            read_timeout=15,
            write_timeout=15,
        )
        with g.db.cursor() as cursor:
            cursor.execute("SET time_zone = %s", (app.config["MYSQL_TIME_ZONE"],))
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
    return g.db


def atomic(view):
    """Commit a whole operation, including its ledger and notifications, or none."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        db = get_db()
        db.begin()
        try:
            result = view(*args, **kwargs)
            status = result[1] if isinstance(result, tuple) else getattr(result, 'status_code', 200)
            if status >= 400:
                db.rollback()
            else:
                db.commit()
            return result
        except Exception:
            db.rollback()
            raise
    return wrapped


def app_now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)


def lock_participants(cursor, learner_id, mentor_id):
    # All booking paths acquire the same locks, even across different workers.
    cursor.execute("SELECT id, is_active FROM users WHERE id IN (%s, %s) ORDER BY id FOR UPDATE", (learner_id, mentor_id))
    return cursor.fetchall()


def session_overlap(cursor, learner_id, mentor_id, scheduled_at, duration=60, exclude_id=0):
    cursor.execute("""SELECT s.id, s.learner_id, s.mentor_id, s.scheduled_for,
        COALESCE(us.duration,60) AS duration, sk.name AS skill_name FROM sessions s
        LEFT JOIN user_skills us ON us.user_id = s.mentor_id AND us.skill_id = s.skill_id
        LEFT JOIN skills sk ON sk.id = s.skill_id
        WHERE (s.learner_id IN (%s,%s) OR s.mentor_id IN (%s,%s))
        AND s.status IN ('pending','accepted','scheduled','awaiting_confirmation') AND s.id != %s
        AND s.scheduled_for < %s
        AND DATE_ADD(s.scheduled_for, INTERVAL COALESCE(us.duration,60) MINUTE) > %s
        ORDER BY (s.learner_id = %s OR s.mentor_id = %s) DESC, s.scheduled_for, s.id LIMIT 1""",
        (learner_id, mentor_id, learner_id, mentor_id, exclude_id,
         scheduled_at + timedelta(minutes=int(duration or 60)), scheduled_at, learner_id, learner_id))
    return cursor.fetchone()


def overlap_message(conflict, user_id, other_role="mentor"):
    # Only describe sessions the current user participates in.
    if user_id in (conflict["learner_id"], conflict["mentor_id"]):
        start = conflict["scheduled_for"]
        end = start + timedelta(minutes=int(conflict["duration"] or 60))
        return (f"You already have {conflict['skill_name'] or 'a session'} on "
                f"{start:%d %b %Y, %I:%M %p} to {end:%d %b %Y, %I:%M %p} (IST). "
                "Choose a time outside this session.")
    return f"The {other_role} already has a session during that time. Choose another time."


def booking_problem(cursor, learner_id, mentor_id, scheduled_at, duration=60, exclude_id=0):
    if int(learner_id) == int(mentor_id):
        return "You cannot book yourself."
    accounts = lock_participants(cursor, learner_id, mentor_id)
    if len(accounts) != 2 or not all(row["is_active"] for row in accounts):
        return "Both session participants must have active accounts."
    cursor.execute("SELECT balance FROM credits WHERE user_id = %s", (learner_id,))
    balance = (cursor.fetchone() or {}).get("balance", 0)
    cursor.execute("""SELECT COUNT(*) AS n FROM sessions WHERE learner_id = %s
        AND status IN ('pending','accepted','scheduled','awaiting_confirmation') AND id != %s""", (learner_id, exclude_id))
    if balance <= cursor.fetchone()["n"]:
        return "You need an available credit; your remaining credits are committed to other sessions."
    conflict = session_overlap(cursor, learner_id, mentor_id, scheduled_at, duration, exclude_id)
    if conflict:
        return overlap_message(conflict, session["user_id"], "mentor" if session["user_id"] == learner_id else "learner")
    return None


def guardian_requirement(user, mode):
    age = age_from_birthdate(str(user.get("birthdate") or ""))
    if age is None:
        return "Add your birthdate before attending a session."
    threshold = 16 if mode == "offline" else 13
    if age < threshold:
        return f"Because you are under {threshold}, a parent or guardian must be present for this {mode} session."
    return ""


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    try:
        conn = get_db()
        with open(os.path.join(app.root_path, "schema.sql"), "r", encoding="utf-8") as handle:
            statements = [stmt.strip() for stmt in handle.read().split(";") if stmt.strip()]
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            cursor.execute(
                "ALTER TABLE users MODIFY role ENUM('learner','mentor','both','admin') DEFAULT 'both'"
            )
            cursor.execute("UPDATE users SET role = 'both' WHERE role IN ('learner', 'mentor')")
        seed_sample_data()
    except Exception as exc:
        app.logger.warning("Database initialization skipped: %s", exc)


def display_badge(badge):
    # Keep the historical database identity so earned badges are never reset or duplicated.
    if badge["name"] == "10 Hours Taught":
        return {**badge, "name": "First Session Taught",
                "description": "Completed your first session as a mentor."}
    return badge


def seed_sample_data():
    conn = get_db()
    with conn.cursor() as cursor:
        default_badges = (
            ("First Session Completed", "learning", "Completed your first session."),
            ("Skill Hunter", "learning", "Completed 5 learning sessions."),
            ("10 Hours Taught", "teaching", "Completed your first session as a mentor."),
            ("Guide Master", "teaching", "Completed 5 teaching sessions."),
            ("Skill Sharer", "skill", "Added your first teachable skill."),
            ("Conversation Starter", "social", "Started your first skill chat."),
            ("Calendar Captain", "planning", "Scheduled your first session."),
            ("Time Trader", "master", "Completed 10 total sessions."),
            ("Curiosity Spark", "xp", "Reached 50 total XP on GotTime."),
            ("Knowledge Seeker", "xp", "Reached 250 XP on GotTime."),
            ("Time Legend", "xp", "Reached 500 XP on GotTime."),
            ("First Daily Quest", "quest", "Completed your first daily quest."),
        )
        for name, category, description in default_badges:
            cursor.execute("SELECT id FROM badges WHERE name = %s", (name,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO badges (name, category, description) VALUES (%s, %s, %s)",
                    (name, category, description),
                )


def ensure_message_action_columns():
    conn = get_db()
    columns = {
        "encrypted_content": "ALTER TABLE messages ADD COLUMN encrypted_content TEXT NULL AFTER content",
        "edited_at": "ALTER TABLE messages ADD COLUMN edited_at TIMESTAMP NULL DEFAULT NULL",
        "deleted_for_sender": "ALTER TABLE messages ADD COLUMN deleted_for_sender TINYINT(1) DEFAULT 0",
        "deleted_for_receiver": "ALTER TABLE messages ADD COLUMN deleted_for_receiver TINYINT(1) DEFAULT 0",
        "deleted_for_everyone": "ALTER TABLE messages ADD COLUMN deleted_for_everyone TINYINT(1) DEFAULT 0",
    }
    with conn.cursor() as cursor:
        for name, statement in columns.items():
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'messages'
                     AND COLUMN_NAME = %s""",
                (name,),
            )
            if cursor.fetchone()["count"] == 0:
                cursor.execute(statement)


def ensure_user_crypto_column():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*) AS count
               FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = 'users'
                 AND COLUMN_NAME = 'public_key'"""
        )
        if cursor.fetchone()["count"] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN public_key TEXT NULL AFTER banner_image")


def ensure_session_tracking_columns():
    conn = get_db()
    columns = {
        "learner_joined_at": "ALTER TABLE sessions ADD COLUMN learner_joined_at DATETIME NULL",
        "mentor_joined_at": "ALTER TABLE sessions ADD COLUMN mentor_joined_at DATETIME NULL",
        "learner_left_at": "ALTER TABLE sessions ADD COLUMN learner_left_at DATETIME NULL",
        "mentor_left_at": "ALTER TABLE sessions ADD COLUMN mentor_left_at DATETIME NULL",
    }
    with conn.cursor() as cursor:
        for name, statement in columns.items():
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'sessions'
                     AND COLUMN_NAME = %s""",
                (name,),
            )
            if cursor.fetchone()["count"] == 0:
                cursor.execute(statement)


def ensure_session_status_values():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """ALTER TABLE sessions
               MODIFY status ENUM('pending','accepted','scheduled','awaiting_confirmation','completed','cancelled','missed')
               DEFAULT 'pending'"""
        )


def ensure_user_birthdate_column():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*) AS count
               FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = 'users'
                 AND COLUMN_NAME = 'birthdate'"""
        )
        if cursor.fetchone()["count"] == 0:
            cursor.execute("ALTER TABLE users ADD COLUMN birthdate DATE NULL AFTER profile_photo")


def ensure_user_banner_columns():
    conn = get_db()
    columns = {
        "banner_style": "ALTER TABLE users ADD COLUMN banner_style VARCHAR(40) DEFAULT 'mint' AFTER profile_photo",
        "banner_image": "ALTER TABLE users ADD COLUMN banner_image VARCHAR(255) NULL AFTER banner_style",
    }
    with conn.cursor() as cursor:
        for name, statement in columns.items():
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'users'
                     AND COLUMN_NAME = %s""",
                (name,),
            )
            if cursor.fetchone()["count"] == 0:
                cursor.execute(statement)


def ensure_user_skill_columns():
    conn = get_db()
    columns = {
        "duration": "ALTER TABLE user_skills ADD COLUMN duration INT DEFAULT 60 AFTER category",
        "availability": "ALTER TABLE user_skills ADD COLUMN availability ENUM('online','offline','both') DEFAULT 'online' AFTER duration",
        "preferred_time": "ALTER TABLE user_skills ADD COLUMN preferred_time VARCHAR(120) NULL AFTER availability",
    }
    with conn.cursor() as cursor:
        for name, statement in columns.items():
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'user_skills'
                     AND COLUMN_NAME = %s""",
                (name,),
            )
            if cursor.fetchone()["count"] == 0:
                cursor.execute(statement)


def ensure_skill_columns():
    conn = get_db()
    columns = {
        "category": "ALTER TABLE skills ADD COLUMN category VARCHAR(100) NULL AFTER name",
        "description": "ALTER TABLE skills ADD COLUMN description TEXT NULL AFTER category",
        "created_by": "ALTER TABLE skills ADD COLUMN created_by INT NULL AFTER description",
        "created_at": "ALTER TABLE skills ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER created_by",
    }
    with conn.cursor() as cursor:
        for name, statement in columns.items():
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'skills'
                     AND COLUMN_NAME = %s""",
                (name,),
            )
            if cursor.fetchone()["count"] == 0:
                cursor.execute(statement)


def ensure_user_progress_columns():
    conn = get_db()
    columns = {
        "xp": "ALTER TABLE users ADD COLUMN xp INT DEFAULT 0 AFTER age",
        "streak": "ALTER TABLE users ADD COLUMN streak INT DEFAULT 0 AFTER xp",
        "last_visit_on": "ALTER TABLE users ADD COLUMN last_visit_on DATE NULL AFTER streak",
        "sessions_completed": "ALTER TABLE users ADD COLUMN sessions_completed INT DEFAULT 0 AFTER streak",
    }
    with conn.cursor() as cursor:
        for name, statement in columns.items():
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = 'users'
                     AND COLUMN_NAME = %s""",
                (name,),
            )
            if cursor.fetchone()["count"] == 0:
                cursor.execute(statement)


def ensure_xp_system():
    conn = get_db()
    with conn.cursor() as cursor:
        ensure_user_progress_columns()
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS xp_events (
                 id INT AUTO_INCREMENT PRIMARY KEY,
                 user_id INT NOT NULL,
                 event_key VARCHAR(80) NOT NULL,
                 amount INT DEFAULT 0,
                 event_date DATE NOT NULL,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE KEY unique_user_event_day (user_id, event_key, event_date),
                 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
               )"""
        )


def ensure_session_quiz_tables():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS session_quiz_questions (
                 id INT AUTO_INCREMENT PRIMARY KEY,
                 session_id INT NOT NULL,
                 question TEXT NOT NULL,
                 option_a VARCHAR(255) NOT NULL,
                 option_b VARCHAR(255) NOT NULL,
                 option_c VARCHAR(255) NOT NULL,
                 option_d VARCHAR(255) NOT NULL,
                 correct_option CHAR(1) NOT NULL,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
               )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS session_quiz_attempts (
                 id INT AUTO_INCREMENT PRIMARY KEY,
                 session_id INT NOT NULL,
                 learner_id INT NOT NULL,
                 score INT DEFAULT 0,
                 total INT DEFAULT 0,
                 answers_json TEXT,
                 submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE KEY unique_session_learner_quiz (session_id, learner_id),
                 FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                 FOREIGN KEY (learner_id) REFERENCES users(id) ON DELETE CASCADE
               )"""
        )


def ensure_session_reminder_tables():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS session_reminders (
                 id INT AUTO_INCREMENT PRIMARY KEY,
                 session_id INT NOT NULL,
                 user_id INT NOT NULL,
                 reminder_key VARCHAR(40) NOT NULL,
                 scheduled_at DATETIME NOT NULL,
                 message TEXT NOT NULL,
                 sent_at DATETIME NULL DEFAULT NULL,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE KEY unique_session_user_reminder (session_id, user_id, reminder_key),
                 FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
               )"""
        )


def ensure_payment_tables():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS credit_payments (
                 id INT AUTO_INCREMENT PRIMARY KEY,
                 user_id INT NOT NULL,
                 package_id VARCHAR(40) NOT NULL,
                 credits INT NOT NULL,
                 amount_paise INT NOT NULL,
                 currency VARCHAR(8) DEFAULT 'INR',
                 provider VARCHAR(40) DEFAULT 'razorpay',
                 provider_order_id VARCHAR(120) UNIQUE,
                 provider_payment_id VARCHAR(120),
                 status VARCHAR(30) DEFAULT 'created',
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 paid_at DATETIME NULL DEFAULT NULL,
                 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
               )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS donation_payments (
                 id INT AUTO_INCREMENT PRIMARY KEY,
                 user_id INT NULL,
                 donor_name VARCHAR(160),
                 donor_email VARCHAR(160),
                 amount_paise INT NOT NULL,
                 currency VARCHAR(8) DEFAULT 'INR',
                 provider VARCHAR(40) DEFAULT 'razorpay',
                 provider_order_id VARCHAR(120) UNIQUE,
                 provider_payment_id VARCHAR(120),
                 status VARCHAR(30) DEFAULT 'created',
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 paid_at DATETIME NULL DEFAULT NULL,
                 FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
               )"""
        )


def enrich_sessions_with_quiz(sessions_data, user_id: int):
    if not sessions_data:
        return sessions_data
    ids = [item["id"] for item in sessions_data if item.get("id")]
    if not ids:
        return sessions_data
    placeholders = ",".join(["%s"] * len(ids))
    counts = {}
    attempts = {}
    with get_db().cursor() as cursor:
        cursor.execute(
            f"""SELECT session_id, COUNT(*) AS count
                FROM session_quiz_questions
                WHERE session_id IN ({placeholders})
                GROUP BY session_id""",
            tuple(ids),
        )
        counts = {row["session_id"]: row["count"] for row in cursor.fetchall()}
        cursor.execute(
            f"""SELECT session_id, score, total, submitted_at
                FROM session_quiz_attempts
                WHERE learner_id = %s AND session_id IN ({placeholders})""",
            tuple([user_id, *ids]),
        )
        attempts = {row["session_id"]: row for row in cursor.fetchall()}
    for item in sessions_data:
        quiz_count = int(counts.get(item["id"], 0) or 0)
        attempt = attempts.get(item["id"])
        item["quiz_question_count"] = quiz_count
        item["has_quiz"] = quiz_count > 0
        item["quiz_submitted"] = bool(attempt)
        item["quiz_score"] = attempt["score"] if attempt else None
        item["quiz_total"] = attempt["total"] if attempt else None
    return sessions_data


def initialize_database():
    init_db()
    try:
        ensure_message_action_columns()
    except Exception as exc:
        app.logger.warning("Message action migration skipped: %s", exc)
    try:
        ensure_user_crypto_column()
    except Exception as exc:
        app.logger.warning("User crypto migration skipped: %s", exc)
    try:
        ensure_session_tracking_columns()
    except Exception as exc:
        app.logger.warning("Session tracking migration skipped: %s", exc)
    try:
        ensure_session_status_values()
    except Exception as exc:
        app.logger.warning("Session status migration skipped: %s", exc)
    try:
        ensure_user_birthdate_column()
    except Exception as exc:
        app.logger.warning("User birthdate migration skipped: %s", exc)
    try:
        ensure_user_banner_columns()
    except Exception as exc:
        app.logger.warning("User banner migration skipped: %s", exc)
    try:
        ensure_user_progress_columns()
    except Exception as exc:
        app.logger.warning("User progress migration skipped: %s", exc)
    try:
        ensure_skill_columns()
    except Exception as exc:
        app.logger.warning("Skill migration skipped: %s", exc)
    try:
        ensure_user_skill_columns()
    except Exception as exc:
        app.logger.warning("User skill migration skipped: %s", exc)
    try:
        ensure_xp_system()
    except Exception as exc:
        app.logger.warning("XP system migration skipped: %s", exc)
    try:
        ensure_session_quiz_tables()
    except Exception as exc:
        app.logger.warning("Session quiz migration skipped: %s", exc)
    try:
        ensure_session_reminder_tables()
    except Exception as exc:
        app.logger.warning("Session reminder migration skipped: %s", exc)
    try:
        ensure_payment_tables()
    except Exception as exc:
        app.logger.warning("Payment migration skipped: %s", exc)


def normalize_role(role: str | None) -> str:
    return "both"


def age_from_birthdate(value: str | None):
    if not value:
        return None
    try:
        born = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = app_now().date()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age if 0 <= age <= 120 else None


def ensure_credit_row(user_id: int):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT id, balance FROM credits WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO credits (user_id, balance) VALUES (%s, %s)", (user_id, 0))
            return 0
        return row["balance"]


def credit_packages():
    return {
        "starter": {"id": "starter", "credits": 5, "price": 99, "name": "Starter top-up"},
        "boost": {"id": "boost", "credits": 12, "price": 199, "name": "Skill boost"},
        "pro": {"id": "pro", "credits": 30, "price": 449, "name": "Deep learner"},
    }


def create_notification(user_id: int, kind: str, message: str):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO notifications (user_id, type, message) VALUES (%s, %s, %s)",
            (user_id, kind, message),
        )


def parse_session_datetime(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def parse_preferred_time_range(value: str | None):
    text = str(value or "").strip()
    if "-" not in text:
        return None
    start_text, end_text = [part.strip() for part in text.split("-", 1)]
    try:
        start_hours, start_minutes = [int(part) for part in start_text.split(":", 1)]
        end_hours, end_minutes = [int(part) for part in end_text.split(":", 1)]
    except (TypeError, ValueError):
        return None
    if not (0 <= start_hours <= 23 and 0 <= end_hours <= 23 and 0 <= start_minutes <= 59 and 0 <= end_minutes <= 59):
        return None
    return (start_hours * 60 + start_minutes, end_hours * 60 + end_minutes)


def is_time_inside_preferred_range(scheduled_at, preferred_time: str | None) -> bool:
    time_range = parse_preferred_time_range(preferred_time)
    if not scheduled_at or not time_range:
        return True
    selected = scheduled_at.hour * 60 + scheduled_at.minute
    start, end = time_range
    if start <= end:
        return start <= selected <= end
    return selected >= start or selected <= end


def format_session_reminder_time(value):
    if isinstance(value, str):
        value = parse_session_datetime(value)
    if not value:
        return "the scheduled time"
    return value.strftime("%d %b, %I:%M %p")


def schedule_session_reminders(session_row):
    scheduled_at = parse_session_datetime(str(session_row.get("scheduled_for") or ""))
    if not scheduled_at:
        return
    mentor_name = session_row.get("mentor_name") or "your mentor"
    learner_name = session_row.get("learner_name") or "your learner"
    skill_name = session_row.get("skill_name") or "your GotTime session"
    time_text = format_session_reminder_time(scheduled_at)
    reminder_offsets = [
        ("two_days", timedelta(days=2), "in 2 days"),
        ("one_day", timedelta(days=1), "tomorrow"),
        ("one_hour", timedelta(hours=1), "in 1 hour"),
        ("five_minutes", timedelta(minutes=5), "in 5 minutes"),
    ]
    rows = []
    for key, offset, label in reminder_offsets:
        due_at = scheduled_at - offset
        if due_at <= app_now():
            continue
        rows.append((session_row["id"], session_row["learner_id"], key, due_at, f"Reminder: {skill_name} with {mentor_name} starts {label} ({time_text})."))
        rows.append((session_row["id"], session_row["mentor_id"], key, due_at, f"Reminder: {skill_name} with {learner_name} starts {label} ({time_text})."))
    with get_db().cursor() as cursor:
        cursor.execute("DELETE FROM session_reminders WHERE session_id = %s AND sent_at IS NULL", (session_row["id"],))
        if rows:
            cursor.executemany(
                """INSERT INTO session_reminders
                   (session_id, user_id, reminder_key, scheduled_at, message)
                   VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE scheduled_at = VALUES(scheduled_at), message = VALUES(message), sent_at = NULL""",
                rows,
            )


@atomic
def dispatch_due_session_reminders(user_id: int | None = None):
    with get_db().cursor() as cursor:
        if user_id:
            cursor.execute(
                """SELECT id, user_id, message FROM session_reminders
                   WHERE sent_at IS NULL AND scheduled_at <= NOW() AND user_id = %s
                   ORDER BY scheduled_at ASC LIMIT 20 FOR UPDATE""",
                (user_id,),
            )
        else:
            cursor.execute(
                """SELECT id, user_id, message FROM session_reminders
                   WHERE sent_at IS NULL AND scheduled_at <= NOW()
                   ORDER BY scheduled_at ASC LIMIT 50 FOR UPDATE"""
            )
        rows = cursor.fetchall()
        for row in rows:
            cursor.execute(
                "INSERT INTO notifications (user_id, type, message) VALUES (%s, 'session', %s)",
                (row["user_id"], row["message"]),
            )
            cursor.execute("UPDATE session_reminders SET sent_at = NOW() WHERE id = %s", (row["id"],))


@atomic
def mark_missed_sessions(user_id: int | None = None):
    with get_db().cursor() as cursor:
        params: list[Any] = []
        user_filter = ""
        if user_id:
            user_filter = "AND (s.learner_id = %s OR s.mentor_id = %s)"
            params.extend([user_id, user_id])
        cursor.execute(
            f"""SELECT s.id, s.learner_id, s.mentor_id, s.scheduled_for,
                       sk.name AS skill_name, COALESCE(us.duration, 60) AS duration
                FROM sessions s
                JOIN skills sk ON sk.id = s.skill_id
                LEFT JOIN user_skills us ON us.user_id = s.mentor_id AND us.skill_id = s.skill_id
                WHERE s.status IN ('accepted', 'scheduled')
                  AND s.learner_joined_at IS NULL
                  AND s.scheduled_for IS NOT NULL
                  AND DATE_ADD(s.scheduled_for, INTERVAL COALESCE(us.duration, 60) MINUTE) < NOW()
                  {user_filter}""",
            tuple(params),
        )
        missed = cursor.fetchall()
        for item in missed:
            cursor.execute("UPDATE sessions SET status = 'missed' WHERE id = %s AND status IN ('accepted', 'scheduled') AND learner_joined_at IS NULL", (item["id"],))
            if cursor.rowcount == 0:
                continue
            cursor.execute("INSERT INTO credits (user_id, balance) VALUES (%s, 0) ON DUPLICATE KEY UPDATE balance = balance", (item["learner_id"],))
            cursor.execute("UPDATE credits SET balance = balance - 1 WHERE user_id = %s", (item["learner_id"],))
            cursor.execute(
                "INSERT INTO transactions (user_id, kind, amount, related_session_id, note) VALUES (%s, 'spent', 1, %s, 'Missed session penalty')",
                (item["learner_id"], item["id"]),
            )
            create_notification(item["learner_id"], "session", f"You missed {item['skill_name']}. One time credit was deducted.")
            create_notification(item["mentor_id"], "session", f"{item['skill_name']} was marked missed. No teaching credit was added.")


_schema_cache = {}


def cached_schema_check(check):
    @wraps(check)
    def wrapped(*args):
        key = (app.config["MYSQL_DB"], check.__name__, *args)
        cached = _schema_cache.get(key)
        if cached and monotonic() - cached[0] < 60:
            return cached[1]
        result = check(*args)
        if result:
            _schema_cache[key] = (monotonic(), result)
        return result
    return wrapped


@cached_schema_check
def db_column_exists(table: str, column: str) -> bool:
    try:
        with get_db().cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = %s
                     AND COLUMN_NAME = %s""",
                (table, column),
            )
            return cursor.fetchone()["count"] > 0
    except Exception as exc:
        app.logger.warning("Column check failed for %s.%s: %s", table, column, exc)
        return False


@cached_schema_check
def db_table_exists(table: str) -> bool:
    try:
        with get_db().cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM INFORMATION_SCHEMA.TABLES
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = %s""",
                (table,),
            )
            return cursor.fetchone()["count"] > 0
    except Exception as exc:
        app.logger.warning("Table check failed for %s: %s", table, exc)
        return False


DAILY_QUESTS = (
    {
        "key": "mentor_spotlight",
        "title": "Mentor scout",
        "description": "Check out 1 mentor profile today.",
        "target": 1,
        "kind": "mentor_views",
        "xp": 10,
    },
    {
        "key": "book_one",
        "title": "Make a learning plan",
        "description": "Book 1 session today. The session itself can be on a later date.",
        "target": 1,
        "kind": "book_sessions",
        "xp": 25,
    },
    {
        "key": "complete_one",
        "title": "Show up and grow",
        "description": "Complete 1 session today as a learner or mentor. Both sides must confirm.",
        "target": 1,
        "kind": "complete_sessions",
        "xp": 50,
    },
    {
        "key": "mentor_explorer",
        "title": "Curiosity collector",
        "description": "Explore 2 different mentor profiles today.",
        "target": 2,
        "kind": "mentor_views",
        "xp": 20,
    },
    {
        "key": "book_two",
        "title": "Plan your next steps",
        "description": "Book 2 different sessions today. They can take place on later dates.",
        "target": 2,
        "kind": "book_sessions",
        "xp": 40,
    },
    {
        "key": "complete_two",
        "title": "Double the learning",
        "description": "Complete 2 sessions today as a learner or mentor. Both sides must confirm each one.",
        "target": 2,
        "kind": "complete_sessions",
        "xp": 100,
    },
)


def today_ist():
    return app_now().date()


def active_streak_select():
    if not db_column_exists("users", "last_visit_on"):
        return "0 AS streak"
    today = today_ist()
    yesterday = today - timedelta(days=1)
    # Dates are generated by the server, never supplied by the client.
    return (f"CASE WHEN u.last_visit_on BETWEEN '{yesterday.isoformat()}' AND '{today.isoformat()}' "
            "THEN COALESCE(u.streak, 0) ELSE 0 END AS streak")


def recorded_streak_days(last_visit, streak, today, start=None, end=None):
    """Recover the consecutive dates encoded by the server's saved streak counter."""
    if not last_visit or last_visit > today:
        return set()
    count = max(1, int(streak or 0))
    first = last_visit - timedelta(days=min(count - 1, last_visit.toordinal() - 1))
    if start:
        first = max(first, start)
    last = min(last_visit, end - timedelta(days=1)) if end else last_visit
    return {first + timedelta(days=offset) for offset in range(max(0, (last - first).days + 1))}


def ensure_activity_days_table():
    with get_db().cursor() as cursor:
        cursor.execute("""CREATE TABLE IF NOT EXISTS user_activity_days (
            user_id INT NOT NULL,
            activity_date DATE NOT NULL,
            PRIMARY KEY (user_id, activity_date),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""")


def record_app_visit(user_id):
    """Count one authenticated workspace visit per calendar day, atomically."""
    # Keep existing deployments usable until the explicit schema upgrade runs.
    if not db_column_exists("users", "last_visit_on"):
        return
    if not db_table_exists("user_activity_days"):
        ensure_activity_days_table()
    return record_daily_activity(user_id)


@atomic
def record_daily_activity(user_id):
    today = today_ist()
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id, streak, last_visit_on, is_active FROM users WHERE id = %s FOR UPDATE", (user_id,))
        person = cursor.fetchone()
        if not person or not person["is_active"]:
            return
        # Preserve the existing consecutive run before a later visit can reset it.
        known_days = recorded_streak_days(person["last_visit_on"], person["streak"], today)
        if known_days:
            cursor.execute("""SELECT activity_date FROM user_activity_days WHERE user_id = %s
                              AND activity_date BETWEEN %s AND %s""", (user_id, min(known_days), max(known_days)))
            missing_days = known_days - {row["activity_date"] for row in cursor.fetchall()}
            if missing_days:
                cursor.executemany("INSERT IGNORE INTO user_activity_days (user_id, activity_date) VALUES (%s, %s)",
                                   [(user_id, day) for day in sorted(missing_days)])
        cursor.execute("""INSERT IGNORE INTO user_activity_days (user_id, activity_date)
                          SELECT id, %s FROM users WHERE id = %s AND is_active = 1""", (today, user_id))
        cursor.execute(
            """UPDATE users
               SET streak = CASE WHEN last_visit_on = %s THEN COALESCE(streak, 0) + 1 ELSE 1 END,
                   last_visit_on = %s
               WHERE id = %s AND is_active = 1
                 AND (last_visit_on IS NULL OR last_visit_on < %s)""",
            (today - timedelta(days=1), today, user_id, today),
        )


def current_daily_quest():
    today = today_ist()
    quest = dict(DAILY_QUESTS[today.toordinal() % len(DAILY_QUESTS)])
    quest["event_key"] = f"daily_quest_{quest['key']}"
    quest["date"] = today.isoformat()
    quest["resets_at"] = f"{(today + timedelta(days=1)).isoformat()}T00:00:00+05:30"
    return quest


def daily_quest_progress(user_id, quest, mentor_ids=()):
    """Use persisted bookings/completion ledgers, never client-supplied counts."""
    start = datetime.fromisoformat(quest["date"])
    end = start + timedelta(days=1)
    with get_db().cursor() as cursor:
        if quest["kind"] == "mentor_views":
            if not isinstance(mentor_ids, (list, tuple)) or len(mentor_ids) > 100:
                return 0
            ids = set()
            for mid in mentor_ids:
                value = str(mid)
                if value.isascii() and value.isdigit() and len(value) <= 10 and 0 < int(value) <= 2147483647:
                    ids.add(int(value))
            ids.discard(user_id)
            if not ids:
                return 0
            placeholders = ",".join(["%s"] * len(ids))
            cursor.execute(
                f"""SELECT COUNT(DISTINCT u.id) AS count FROM users u
                    JOIN user_skills us ON us.user_id = u.id
                    WHERE u.is_active = 1 AND u.id IN ({placeholders})""", tuple(ids),
            )
        elif quest["kind"] == "book_sessions":
            cursor.execute(
                """SELECT COUNT(*) AS count FROM sessions
                   WHERE learner_id = %s AND created_at >= %s AND created_at < %s
                     AND status NOT IN ('cancelled', 'missed')""", (user_id, start, end),
            )
        elif quest["kind"] == "complete_sessions":
            # The reward ledger timestamps the confirmed completion, not the booking date.
            cursor.execute(
                """SELECT COUNT(DISTINCT s.id) AS count FROM sessions s
                   JOIN transactions t ON t.related_session_id = s.id
                   WHERE t.user_id = %s AND (s.learner_id = %s OR s.mentor_id = %s)
                     AND s.status = 'completed'
                     AND t.note IN ('Learning session', 'Teaching reward')
                     AND t.created_at >= %s AND t.created_at < %s""",
                (user_id, user_id, user_id, start, end),
            )
        else:
            return 0
        return min(int(quest["target"]), cursor.fetchone()["count"])


def has_daily_quest_claim(user_id, quest):
    # Include older quest keys so a deployment/rotation change cannot pay twice in one day.
    with get_db().cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*) AS count FROM xp_events
               WHERE user_id = %s AND event_date = %s AND LEFT(event_key, 11) = 'daily_quest'""",
            (user_id, quest["date"]),
        )
        return cursor.fetchone()["count"] > 0


def award_progress_badges(user_id):
    """Award total-XP milestones and the one-time quest badge under the user lock.

    Call inside a transaction. Old quest XP counts too; never rewrite the ledger.
    """
    unlocked = []
    with get_db().cursor() as cursor:
        cursor.execute("SELECT COALESCE(xp, 0) AS xp FROM users WHERE id = %s FOR UPDATE", (user_id,))
        user = cursor.fetchone()
        if not user:
            return unlocked
        cursor.execute(
            """SELECT EXISTS(SELECT 1 FROM xp_events
               WHERE user_id = %s AND LEFT(event_key, 11) = 'daily_quest') AS completed""", (user_id,),
        )
        completed_quest = bool(cursor.fetchone()["completed"])
        earned_names = [name for threshold, name in ((50, "Curiosity Spark"), (250, "Knowledge Seeker"), (500, "Time Legend"))
                       if user["xp"] >= threshold]
        if completed_quest:
            earned_names.append("First Daily Quest")
            cursor.execute("SELECT id FROM badges WHERE name = 'First Daily Quest'")
            if not cursor.fetchone():
                # Existing deployments skip startup seeding. Serialize the one-time
                # catalog addition on an existing badge row, then recheck after locking.
                cursor.execute("SELECT id FROM badges ORDER BY id LIMIT 1 FOR UPDATE")
                cursor.fetchone()
                cursor.execute("SELECT id FROM badges WHERE name = 'First Daily Quest'")
                if not cursor.fetchone():
                    cursor.execute("INSERT INTO badges (name, category, description) VALUES ('First Daily Quest', 'quest', 'Completed your first daily quest.')")
        if not earned_names:
            return unlocked
        cursor.execute("SELECT id, name FROM badges")
        lookup = {row["name"]: row["id"] for row in cursor.fetchall()}
        cursor.execute("SELECT badge_id FROM user_badges WHERE user_id = %s", (user_id,))
        existing = {row["badge_id"] for row in cursor.fetchall()}
        for name in earned_names:
            badge_id = lookup.get(name)
            if badge_id and badge_id not in existing:
                cursor.execute("INSERT INTO user_badges (user_id, badge_id) VALUES (%s, %s)", (user_id, badge_id))
                existing.add(badge_id)
                unlocked.append(name)
                create_notification(user_id, "badge", f"You unlocked the {name} badge.")
    return unlocked


@atomic
def reconcile_progress_badges(user_id):
    # Also repair milestones withheld by the previous quest-XP exclusion.
    return award_progress_badges(user_id)


def award_badges(user_id: int):
    unlocked = []
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) as count FROM sessions WHERE learner_id = %s AND status = 'completed'", (user_id,))
        completed_sessions = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM sessions WHERE mentor_id = %s AND status = 'completed'", (user_id,))
        taught_sessions = cursor.fetchone()["count"]
        total_sessions = completed_sessions + taught_sessions
        cursor.execute("SELECT COUNT(*) as count FROM user_skills WHERE user_id = %s", (user_id,))
        skill_count = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM sessions WHERE (learner_id = %s OR mentor_id = %s) AND scheduled_for IS NOT NULL", (user_id, user_id))
        scheduled_sessions = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM messages WHERE sender_id = %s", (user_id,))
        sent_messages = cursor.fetchone()["count"]
        cursor.execute(
            """SELECT COUNT(DISTINCT CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END) AS count
               FROM messages
               WHERE sender_id = %s OR receiver_id = %s""",
            (user_id, user_id, user_id),
        )
        chat_people = cursor.fetchone()["count"]
        cursor.execute("SELECT balance FROM credits WHERE user_id = %s", (user_id,))
        credit_row = cursor.fetchone()
        balance = credit_row["balance"] if credit_row else 0

        cursor.execute("SELECT id, name FROM badges")
        badge_lookup = {row["name"]: row["id"] for row in cursor.fetchall()}
        cursor.execute("SELECT badge_id FROM user_badges WHERE user_id = %s", (user_id,))
        existing_ids = {row["badge_id"] for row in cursor.fetchall()}

        if completed_sessions >= 1 and badge_lookup.get("First Session Completed") and badge_lookup["First Session Completed"] not in existing_ids:
            cursor.execute("INSERT INTO user_badges (user_id, badge_id) VALUES (%s, %s)", (user_id, badge_lookup["First Session Completed"]))
            existing_ids.add(badge_lookup["First Session Completed"])
            unlocked.append("First Session Completed")
            create_notification(user_id, "badge", "You unlocked the First Session Completed badge.")
        milestone_badges = (
            (completed_sessions, 5, "Skill Hunter"),
            (taught_sessions, 1, "10 Hours Taught"),
            (taught_sessions, 5, "Guide Master"),
            (skill_count, 1, "Skill Sharer"),
            (sent_messages, 1, "Conversation Starter"),
            (scheduled_sessions, 1, "Calendar Captain"),
            (total_sessions, 10, "Time Trader"),
        )
        for value, threshold, badge_name in milestone_badges:
            badge_id = badge_lookup.get(badge_name)
            if badge_id and value >= threshold and badge_id not in existing_ids:
                cursor.execute("INSERT INTO user_badges (user_id, badge_id) VALUES (%s, %s)", (user_id, badge_id))
                existing_ids.add(badge_id)
                display_name = display_badge({"name": badge_name})["name"]
                unlocked.append(display_name)
                create_notification(user_id, "badge", f"You unlocked the {display_name} badge.")
    unlocked.extend(award_progress_badges(user_id))
    return unlocked


def sync_reversible_badges(user_id: int):
    try:
        with get_db().cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(DISTINCT LOWER(sk.name)) AS count
                   FROM user_skills us JOIN skills sk ON sk.id = us.skill_id
                   WHERE us.user_id = %s""",
                (user_id,),
            )
            skill_count = cursor.fetchone()["count"]
            if skill_count == 0:
                cursor.execute(
                    """DELETE ub FROM user_badges ub
                       JOIN badges b ON b.id = ub.badge_id
                       WHERE ub.user_id = %s AND b.name = 'Skill Sharer'""",
                    (user_id,),
                )
    except Exception as exc:
        app.logger.warning("Reversible badge sync skipped: %s", exc)


def award_xp(user_id: int, amount: int, event_key: str, message: str, *, event_date=None):
    today = event_date or today_ist()
    conn = get_db()
    with conn.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO xp_events (user_id, event_key, amount, event_date) VALUES (%s, %s, %s, %s)",
                (user_id, event_key, amount, today),
            )
        except pymysql.err.IntegrityError:
            cursor.execute("SELECT COALESCE(xp, 0) AS xp FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            return {"awarded": False, "xp": row["xp"] if row else 0, "unlockedBadges": []}
        cursor.execute("UPDATE users SET xp = COALESCE(xp, 0) + %s WHERE id = %s", (amount, user_id))
        cursor.execute("SELECT COALESCE(xp, 0) AS xp FROM users WHERE id = %s", (user_id,))
        xp = cursor.fetchone()["xp"]
    create_notification(user_id, "xp", message)
    unlocked = award_progress_badges(user_id) if event_key.startswith("daily_quest") else award_badges(user_id)
    return {"awarded": True, "xp": xp, "unlockedBadges": unlocked}


def password_matches(stored_password: str | None, candidate: str) -> bool:
    """Accept normal Werkzeug hashes, plus legacy plain local passwords once."""
    if not stored_password:
        return False
    try:
        if check_password_hash(stored_password, candidate):
            return True
    except (ValueError, TypeError):
        pass
    return stored_password == candidate


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def api_login_required(view):
    """JSON-friendly authentication guard used by the React client."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.headers.get("X-GotTime-Auth"):
            token_user_id = user_id_from_auth_token()
            if not token_user_id:
                return jsonify({"error": "Please sign in again."}), 401
            session["user_id"] = token_user_id
        elif "user_id" not in session:
            token_user_id = user_id_from_auth_token()
            if not token_user_id:
                return jsonify({"error": "Please sign in to continue."}), 401
            session["user_id"] = token_user_id
            session.modified = True
        with get_db().cursor() as cursor:
            cursor.execute("SELECT is_active FROM users WHERE id = %s", (session["user_id"],))
            account = cursor.fetchone()
        if not account or not account["is_active"]:
            return jsonify({"error": "This account is unavailable."}), 401
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_user():
    user = None
    notifications = []
    if "user_id" in session:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
            user = cursor.fetchone()
            cursor.execute("SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 5", (session["user_id"],))
            notifications = cursor.fetchall()
    return dict(user=user, notifications=notifications)


@app.route("/")
def index():
    react_index = os.path.join(app.root_path, "static", "react", "index.html")
    if os.path.exists(react_index):
        return send_from_directory(os.path.dirname(react_index), "index.html")
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = normalize_role(request.form.get("role", "learner"))
        age = request.form.get("age", type=int)
        bio = request.form.get("bio", "").strip()
        location = request.form.get("location", "").strip()

        if not username or not email or not password:
            flash("Please fill in the required fields.", "danger")
            return redirect(url_for("register"))

        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                flash("An account already exists for that email.", "warning")
                return redirect(url_for("register"))
            hashed = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (username, email, password, role, bio, location, age) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (username, email, hashed, role, bio, location, age),
            )
            cursor.execute("SELECT LAST_INSERT_ID() as id")
            new_user_id = cursor.fetchone()["id"]
            cursor.execute("INSERT INTO credits (user_id, balance) VALUES (%s, %s)", (new_user_id, 10))
            cursor.execute(
                "INSERT INTO transactions (user_id, kind, amount, note) VALUES (%s, 'bonus', 10, 'Welcome credits')",
                (new_user_id,),
            )
            flash("Welcome aboard! Your account is ready.", "success")
            session["user_id"] = new_user_id
            return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session.permanent = remember
            flash("Welcome back.", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("index"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
        if user:
            token = secrets.token_urlsafe(24)
            expires = datetime.utcnow() + timedelta(hours=1)
            conn = get_db()
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET reset_token = %s, reset_expires = %s WHERE id = %s",
                    (token, expires.strftime("%Y-%m-%d %H:%M:%S"), user["id"]),
                )
            try:
                msg = Message("GotTime? Password Reset", recipients=[email])
                msg.body = f"Use this link to reset your password: {request.host_url}reset-password/{token}"
                mail.send(msg)
            except Exception as exc:
                app.logger.warning("Email send failed: %s", exc)
            flash("If that email exists, a reset link was sent.", "info")
        else:
            flash("If that email exists, a reset link was sent.", "info")
        return redirect(url_for("login"))
    return render_template("auth.html", mode="forgot")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE reset_token = %s", (token,))
        user = cursor.fetchone()
    if not user or not user["reset_expires"] or datetime.utcnow() > user["reset_expires"]:
        flash("This reset link is invalid or expired.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return render_template("reset_password.html", token=token)
        hashed = generate_password_hash(password)
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET password = %s, reset_token = NULL, reset_expires = NULL WHERE id = %s",
                (hashed, user["id"]),
            )
        flash("Your password has been reset. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)


@app.route("/auth/google")
def google_login():
    session.pop("user_id", None)
    session.modified = True
    requested_role = normalize_role(request.args.get("role"))
    if os.getenv("DEV_GOOGLE_BYPASS", "false").lower() in {"1", "true", "yes", "on"}:
        return dev_google_fallback(requested_role, "Local development Google bypass is enabled.")
    try:
        google_client_id = os.getenv("GOOGLE_CLIENT_ID")
        if not google_client_id:
            flash("Google OAuth is not configured yet. Add your credentials to enable it.", "warning")
            return google_error_redirect("google_not_configured")
        state = auth_serializer.dumps({"role": requested_role, "nonce": secrets.token_urlsafe(12)})
        params = {
            "client_id": google_client_id,
            "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI", request.host_url + "auth/google/callback"),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "select_account",
        }
        auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        return redirect(auth_url)
    except Exception as exc:
        app.logger.exception("Google sign-in start failed: %s", exc)
        return dev_google_fallback(requested_role, f"Google start failed: {type(exc).__name__}: {exc}")


@app.route("/auth/google/callback")
def google_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    try:
        state_payload = auth_serializer.loads(state or "")
    except (BadSignature, TypeError, ValueError):
        state_payload = None
    if not code or not state_payload:
        flash("Google sign-in was cancelled or failed.", "danger")
        return google_error_redirect("google_state")
    role = normalize_role(state_payload.get("role", "learner"))

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        flash("Google OAuth is not configured yet.", "warning")
        return google_error_redirect("google_not_configured")

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": os.getenv("GOOGLE_REDIRECT_URI", request.host_url + "auth/google/callback"),
        "grant_type": "authorization_code",
    }
    try:
        token_response = requests.post(token_url, data=data, timeout=10, verify=app.config["GOOGLE_OAUTH_VERIFY_SSL"])
        token_json = token_response.json()
    except requests.RequestException as exc:
        app.logger.exception("Google token exchange failed: %s", exc)
        return google_error_redirect("google_network", f"Token exchange failed: {type(exc).__name__}: {exc}")
    access_token = token_json.get("access_token")
    if not access_token:
        flash("Google returned no access token.", "danger")
        return google_error_redirect("google_token", token_json.get("error_description") or token_json.get("error") or "No access token in Google response.")

    try:
        user_info = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
            verify=app.config["GOOGLE_OAUTH_VERIFY_SSL"],
        ).json()
    except requests.RequestException as exc:
        app.logger.exception("Google userinfo fetch failed: %s", exc)
        return google_error_redirect("google_network", f"User info failed: {type(exc).__name__}: {exc}")
    email = user_info.get("email")
    if not email:
        flash("Google account email could not be retrieved.", "danger")
        return google_error_redirect("google_email")

    try:
        user_id = finish_google_sign_in(user_info, role)
    except Exception as exc:
        app.logger.exception("Google account save failed: %s", exc)
        return google_error_redirect("google_server", f"{type(exc).__name__}: {exc}")
    if not user_id:
        return google_error_redirect("google_email")
    flash("Signed in with Google.", "success")
    return redirect_google_success(user_id)


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
        user = cursor.fetchone()
        cursor.execute("SELECT balance FROM credits WHERE user_id = %s", (session["user_id"],))
        credit_row = cursor.fetchone()
        credit_balance = credit_row["balance"] if credit_row else 0
        cursor.execute("SELECT * FROM sessions WHERE learner_id = %s OR mentor_id = %s ORDER BY created_at DESC LIMIT 6", (session["user_id"], session["user_id"],))
        sessions = cursor.fetchall()
        cursor.execute("SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 6", (session["user_id"],))
        notifications = cursor.fetchall()
        cursor.execute("SELECT * FROM user_badges ub JOIN badges b ON ub.badge_id = b.id WHERE ub.user_id = %s", (session["user_id"],))
        badges = [display_badge(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT 8", (session["user_id"],))
        transactions = cursor.fetchall()
    return render_template(
        "dashboard.html",
        user=user,
        credit_balance=credit_balance,
        sessions=sessions,
        notifications=notifications,
        badges=badges,
        transactions=transactions,
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
        user = cursor.fetchone()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        bio = request.form.get("bio", "").strip()
        location = request.form.get("location", "").strip()
        age = request.form.get("age", type=int) or user["age"]
        profile_photo = None
        if "profile_photo" in request.files:
            file = request.files["profile_photo"]
            if file and file.filename:
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                profile_photo = filename
        conn = get_db()
        with conn.cursor() as cursor:
            if profile_photo:
                cursor.execute(
                    "UPDATE users SET username = %s, bio = %s, location = %s, age = %s, profile_photo = %s WHERE id = %s",
                    (username, bio, location, age, profile_photo, session["user_id"]),
                )
            else:
                cursor.execute(
                    "UPDATE users SET username = %s, bio = %s, location = %s, age = %s WHERE id = %s",
                    (username, bio, location, age, session["user_id"]),
                )
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=user)


@app.route("/skills", methods=["GET", "POST"])
@login_required
def skills():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM skills ORDER BY created_at DESC")
        skills_list = cursor.fetchall()
        cursor.execute("SELECT us.*, s.name as skill_name FROM user_skills us JOIN skills s ON us.skill_id = s.id WHERE us.user_id = %s", (session["user_id"],))
        user_skills = cursor.fetchall()
    if request.method == "POST":
        skill_name = request.form.get("skill_name", "").strip()
        category = request.form.get("category", "").strip()
        duration = request.form.get("duration", type=int) or 60
        availability = request.form.get("availability", "online")
        if skill_name:
            conn = get_db()
            with conn.cursor() as cursor:
                cursor.execute("INSERT INTO skills (name, category, description, created_by) VALUES (%s, %s, %s, %s)", (skill_name, category, "Added from GotTime?", session["user_id"]))
                cursor.execute("SELECT LAST_INSERT_ID() as id")
                skill_id = cursor.fetchone()["id"]
                cursor.execute("INSERT INTO user_skills (user_id, skill_id, category, duration, availability) VALUES (%s, %s, %s, %s, %s)", (session["user_id"], skill_id, category, duration, availability))
                flash("Skill added successfully.", "success")
        return redirect(url_for("skills"))
    return render_template("skills.html", skills=skills_list, user_skills=user_skills)


@app.route("/skills/delete/<int:skill_id>", methods=["POST"])
@login_required
def delete_skill(skill_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM user_skills WHERE user_id = %s AND skill_id = %s", (session["user_id"], skill_id))
        cursor.execute("DELETE FROM skills WHERE id = %s AND created_by = %s", (skill_id, session["user_id"]))
    flash("Skill removed.", "info")
    return redirect(url_for("skills"))


@app.route("/sessions", methods=["GET", "POST"])
@login_required
def sessions():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE id != %s AND is_active = 1 ORDER BY username", (session["user_id"],))
        mentors = cursor.fetchall()
        cursor.execute("SELECT * FROM skills ORDER BY name")
        skill_options = cursor.fetchall()
        cursor.execute("SELECT s.*, u.username as mentor_name, sk.name as skill_name FROM sessions s JOIN users u ON s.mentor_id = u.id JOIN skills sk ON s.skill_id = sk.id WHERE s.learner_id = %s OR s.mentor_id = %s ORDER BY s.created_at DESC", (session["user_id"], session["user_id"],))
        session_list = cursor.fetchall()
    if request.method == "POST":
        mentor_id = request.form.get("mentor_id", type=int)
        skill_id = request.form.get("skill_id", type=int)
        scheduled_for = request.form.get("scheduled_for")
        mode = request.form.get("mode", "online")
        notes = request.form.get("notes", "")
        if mentor_id and skill_id and scheduled_for:
            conn = get_db()
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sessions (learner_id, mentor_id, skill_id, scheduled_for, mode, notes, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (session["user_id"], mentor_id, skill_id, scheduled_for, mode, notes, "pending"),
                )
            create_notification(mentor_id, "session", "A new session request is waiting for your review.")
            flash("Session request sent.", "success")
        else:
            flash("Please complete the form before booking.", "warning")
        return redirect(url_for("sessions"))
    return render_template("sessions.html", mentors=mentors, skills=skill_options, sessions=session_list)


@app.route("/sessions/<int:session_id>/accept", methods=["POST"])
@login_required
def accept_session(session_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        session_row = cursor.fetchone()
        if session_row and session_row["mentor_id"] == session["user_id"]:
            cursor.execute("UPDATE sessions SET status = 'accepted' WHERE id = %s", (session_id,))
            create_notification(session_row["learner_id"], "session", "Your session request was accepted.")
            flash("Request accepted.", "success")
        else:
            flash("You cannot manage that request.", "danger")
    return redirect(url_for("sessions"))


@app.route("/sessions/<int:session_id>/reject", methods=["POST"])
@login_required
def reject_session(session_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        session_row = cursor.fetchone()
        if session_row and session_row["mentor_id"] == session["user_id"]:
            cursor.execute("UPDATE sessions SET status = 'cancelled' WHERE id = %s", (session_id,))
            create_notification(session_row["learner_id"], "session", "Your session request was declined.")
            flash("Request declined.", "info")
        else:
            flash("You cannot manage that request.", "danger")
    return redirect(url_for("sessions"))


@app.route("/sessions/<int:session_id>/schedule", methods=["POST"])
@login_required
def schedule_session(session_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        session_row = cursor.fetchone()
        if session_row and session_row["mentor_id"] == session["user_id"]:
            cursor.execute("UPDATE sessions SET status = 'scheduled' WHERE id = %s", (session_id,))
            create_notification(session_row["learner_id"], "session", "Your session is now scheduled.")
            flash("Session scheduled.", "success")
        else:
            flash("Only the mentor can schedule this session.", "danger")
    return redirect(url_for("sessions"))


@app.route("/sessions/<int:session_id>/mark-complete", methods=["POST"])
@login_required
def mark_complete(session_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        session_row = cursor.fetchone()
        if session_row and session_row["mentor_id"] == session["user_id"]:
            cursor.execute("UPDATE sessions SET status = 'awaiting_confirmation' WHERE id = %s", (session_id,))
            create_notification(session_row["learner_id"], "session", "Please confirm that the session was completed.")
            flash("Completion request sent to the learner.", "success")
        else:
            flash("Only the mentor can request completion.", "danger")
    return redirect(url_for("sessions"))


@app.route("/sessions/<int:session_id>/confirm-complete", methods=["POST"])
@login_required
def confirm_complete(session_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        session_row = cursor.fetchone()
        if session_row and session_row["learner_id"] == session["user_id"] and session_row["status"] == "awaiting_confirmation":
            learner_balance = ensure_credit_row(session_row["learner_id"])
            if learner_balance >= 1:
                cursor.execute("UPDATE sessions SET status = 'completed' WHERE id = %s", (session_id,))
                cursor.execute("UPDATE credits SET balance = balance - 1 WHERE user_id = %s", (session_row["learner_id"],))
                cursor.execute("UPDATE credits SET balance = balance + 1 WHERE user_id = %s", (session_row["mentor_id"],))
                cursor.execute(
                    "INSERT INTO transactions (user_id, kind, amount, related_session_id, note) VALUES (%s, %s, %s, %s, %s)",
                    (session_row["learner_id"], "spent", 1, session_id, "Credit spent for a session"),
                )
                cursor.execute(
                    "INSERT INTO transactions (user_id, kind, amount, related_session_id, note) VALUES (%s, %s, %s, %s, %s)",
                    (session_row["mentor_id"], "earned", 1, session_id, "Credit earned for teaching"),
                )
                cursor.execute("UPDATE users SET sessions_completed = sessions_completed + 1 WHERE id = %s", (session_row["learner_id"],))
                create_notification(session_row["mentor_id"], "credit", "A new credit was awarded for your teaching contribution.")
                create_notification(session_row["learner_id"], "credit", "Your session is complete and your credits were updated.")
                award_badges(session_row["learner_id"])
                award_badges(session_row["mentor_id"])
                flash("Session confirmed and credits transferred.", "success")
            else:
                flash("You need at least one credit to confirm this session.", "warning")
        else:
            flash("Only the learner can confirm completion.", "danger")
    return redirect(url_for("sessions"))


@app.route("/messages", methods=["GET", "POST"])
@app.route("/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def messages(user_id=None):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE id != %s ORDER BY username", (session["user_id"],))
        users = cursor.fetchall()
        if user_id:
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            contact = cursor.fetchone()
            cursor.execute(
                "SELECT m.*, u.username FROM messages m JOIN users u ON m.sender_id = u.id WHERE (sender_id = %s AND receiver_id = %s) OR (sender_id = %s AND receiver_id = %s) ORDER BY m.created_at ASC",
                (session["user_id"], user_id, user_id, session["user_id"]),
            )
            thread = cursor.fetchall()
            cursor.execute("UPDATE messages SET is_read = 1 WHERE sender_id = %s AND receiver_id = %s", (user_id, session["user_id"]))
        else:
            contact = None
            thread = []
    if request.method == "POST":
        recipient_id = request.form.get("recipient_id", type=int)
        content = request.form.get("content", "").strip()
        if recipient_id and content:
            conn = get_db()
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO messages (sender_id, receiver_id, content) VALUES (%s, %s, %s)",
                    (session["user_id"], recipient_id, content),
                )
            create_notification(recipient_id, "message", "You have a new message in GotTime?.")
            flash("Message sent.", "success")
            return redirect(url_for("messages", user_id=recipient_id))
        flash("Message content is required.", "warning")
    return render_template("messages.html", users=users, contact=contact, thread=thread)


@app.route("/notifications")
@login_required
def notifications():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC", (session["user_id"],))
        items = cursor.fetchall()
        cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s", (session["user_id"],))
    return render_template("notifications.html", notifications=items)


@app.route("/leaderboard")
@login_required
def leaderboard():
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT u.username, u.role, c.balance FROM users u JOIN credits c ON u.id = c.user_id ORDER BY c.balance DESC LIMIT 5")
        credit_leaders = cursor.fetchall()
        cursor.execute("SELECT u.username, u.role, u.sessions_completed FROM users u ORDER BY u.sessions_completed DESC LIMIT 5")
        mentor_leaders = cursor.fetchall()
        cursor.execute("SELECT u.username, u.role, u.sessions_completed FROM users u ORDER BY u.sessions_completed DESC LIMIT 5")
        learner_leaders = cursor.fetchall()
    return render_template("leaderboard.html", credit_leaders=credit_leaders, mentor_leaders=mentor_leaders, learner_leaders=learner_leaders)


@app.route("/report-user/<int:user_id>", methods=["POST"])
@login_required
def report_user(user_id):
    reason = request.form.get("reason", "Inappropriate behavior")
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO reports (reporter_id, reported_user_id, reason) VALUES (%s, %s, %s)", (session["user_id"], user_id, reason))
    flash("Report submitted to the admins.", "warning")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/block-user/<int:user_id>", methods=["POST"])
@login_required
def block_user(user_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("INSERT INTO blocked_users (user_id, blocked_user_id) VALUES (%s, %s)", (session["user_id"], user_id))
    flash("User added to your moderation list.", "info")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/admin")
@login_required
def admin():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
        users = cursor.fetchall()
        cursor.execute("SELECT * FROM reports ORDER BY created_at DESC")
        reports = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) as count FROM users")
        user_count = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM sessions")
        session_count = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM notifications")
        notification_count = cursor.fetchone()["count"]
    return render_template("admin.html", users=users, reports=reports, user_count=user_count, session_count=session_count, notification_count=notification_count)


@app.route("/admin/remove-user/<int:user_id>", methods=["POST"])
@login_required
def remove_user(user_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT role FROM users WHERE id = %s", (session["user_id"],))
        current_user = cursor.fetchone()
        if current_user and current_user["role"] != "admin":
            flash("Only admins can remove accounts.", "danger")
            return redirect(url_for("admin"))
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    flash("Account removed.", "success")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# React application API
# ---------------------------------------------------------------------------

def current_user():
    token_header = request.headers.get("X-GotTime-Auth", "").strip()
    token_user_id = user_id_from_auth_token()
    if token_user_id:
        session["user_id"] = token_user_id
        session.modified = True
    elif token_header:
        return None
    elif request.path.startswith("/api/") and "user_id" not in session:
        return None
    elif "user_id" not in session:
        return None
    profile_photo_select = "u.profile_photo" if db_column_exists("users", "profile_photo") else "NULL AS profile_photo"
    xp_select = "COALESCE(u.xp, 0) AS xp" if db_column_exists("users", "xp") else "0 AS xp"
    banner_style_select = "u.banner_style" if db_column_exists("users", "banner_style") else "'mint' AS banner_style"
    banner_image_select = "u.banner_image" if db_column_exists("users", "banner_image") else "NULL AS banner_image"
    birthdate_select = "DATE_FORMAT(u.birthdate, '%%Y-%%m-%%d') AS birthdate" if db_column_exists("users", "birthdate") else "NULL AS birthdate"
    needs_birthdate_select = "u.birthdate IS NULL AS needs_birthdate" if db_column_exists("users", "birthdate") else "0 AS needs_birthdate"
    age_select = "TIMESTAMPDIFF(YEAR, u.birthdate, CURDATE()) AS age" if db_column_exists("users", "birthdate") else "NULL AS age"
    sessions_completed_select = """(SELECT COUNT(*) FROM sessions done
        WHERE (done.learner_id = u.id OR done.mentor_id = u.id)
          AND done.status = 'completed') AS sessions_completed"""
    streak_select = active_streak_select()
    with get_db().cursor() as cursor:
        cursor.execute(
            f"""SELECT u.id, u.username, u.email, u.role, u.bio, u.location,
                      {profile_photo_select}, {banner_style_select}, {banner_image_select},
                      {birthdate_select},
                      {needs_birthdate_select},
                      {age_select}, {xp_select}, {streak_select}, {sessions_completed_select}, u.created_at,
                      COALESCE(c.balance, 0) AS credits,
                      {mentor_rating_stats_select()}
               FROM users u LEFT JOIN credits c ON c.user_id = u.id
               WHERE u.id = %s AND u.is_active = 1""",
            (session["user_id"],),
        )
        return cursor.fetchone()


def auth_user_payload(row: dict | None):
    if not row:
        return None
    try:
        fresh = current_user()
        if fresh:
            return fresh
    except Exception as exc:
        app.logger.warning("Falling back to minimal auth user payload: %s", exc)
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "email": row.get("email"),
        "role": row.get("role", "learner"),
        "bio": row.get("bio"),
        "location": row.get("location"),
        "profile_photo": row.get("profile_photo"),
        "banner_style": row.get("banner_style", "mint"),
        "banner_image": row.get("banner_image"),
        "birthdate": row.get("birthdate"),
        "needs_birthdate": not bool(row.get("birthdate")),
        "age": age_from_birthdate(str(row.get("birthdate") or "")),
        "xp": row.get("xp") or 0,
        "sessions_completed": row.get("sessions_completed") or 0,
        "created_at": row.get("created_at"),
        "credits": 0,
    }


@app.get("/api/auth/me")
def api_me():
    try:
        user = current_user()
    except Exception as exc:
        app.logger.warning("Auth check failed; returning minimal signed-in user: %s", exc)
        user = None
        if "user_id" in session:
            with get_db().cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE id = %s AND is_active = 1", (session["user_id"],))
                user = auth_user_payload(cursor.fetchone())
    return jsonify({"user": user, "authToken": make_auth_token(user["id"]) if user else None})


@app.post("/api/auth/login")
def api_login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    with get_db().cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        if not user or not user.get("is_active") or not password_matches(user.get("password"), password):
            return jsonify({"error": "That email and password do not match."}), 401
        if user.get("password") == password:
            cursor.execute("UPDATE users SET password = %s WHERE id = %s", (generate_password_hash(password), user["id"]))
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = bool(payload.get("remember"))
    return jsonify({"user": auth_user_payload(user), "authToken": make_auth_token(user["id"]), "message": "Welcome back!"})


@app.post("/api/auth/admin-login")
def api_admin_login():
    payload = request.get_json(silent=True) or {}
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not email or not password:
        return jsonify({"error": "Enter your admin email and password."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
    if not user or not password_matches(user.get("password"), password):
        return jsonify({"error": "That admin email and password do not match."}), 401
    if user.get("role") != "admin":
        return jsonify({"error": "That account is not an admin."}), 403
    if int(user.get("is_active", 1) or 0) != 1:
        return jsonify({"error": "This admin account is inactive."}), 403
    session["user_id"] = user["id"]
    session.permanent = True
    session.modified = True
    return jsonify({"user": auth_user_payload(user), "authToken": make_auth_token(user["id"]), "message": "Admin signed in."})


@app.post("/api/auth/google-profile")
def api_google_profile_login():
    return jsonify({"error": "Use verified Google sign-in at /auth/google."}), 410


@app.post("/api/auth/register")
@atomic
def api_register():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username", "").strip()
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    role = normalize_role(payload.get("role", "learner"))
    if not username or not email or len(password) < 8:
        return jsonify({"error": "Add your name, email, and a password of at least 8 characters."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            return jsonify({"error": "An account already exists for that email."}), 409
        cursor.execute(
            """INSERT INTO users (username, email, password, role, bio, location, age)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                username,
                email,
                generate_password_hash(password),
                role,
                "Ready to trade hours and learn something new.",
                payload.get("location", "").strip(),
                payload.get("age") or 18,
            ),
        )
        cursor.execute("SELECT LAST_INSERT_ID() AS id")
        user_id = cursor.fetchone()["id"]
        cursor.execute("INSERT INTO credits (user_id, balance) VALUES (%s, 10)", (user_id,))
        cursor.execute(
            """INSERT INTO transactions (user_id, kind, amount, note)
               VALUES (%s, 'bonus', 10, 'Welcome credits')""",
            (user_id,),
        )
    session["user_id"] = user_id
    session.modified = True
    with get_db().cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        new_user = cursor.fetchone()
    return jsonify({"user": auth_user_payload(new_user), "authToken": make_auth_token(user_id), "message": "Your learning adventure starts now."}), 201


@app.post("/api/auth/birthdate")
@api_login_required
def api_birthdate():
    payload = request.get_json(silent=True) or {}
    birthdate = (payload.get("birthdate") or "").strip()
    age = age_from_birthdate(birthdate)
    if age is None:
        return jsonify({"error": "Enter a valid birthdate."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("UPDATE users SET birthdate = %s, age = %s WHERE id = %s", (birthdate, age, session["user_id"]))
    verified_user = current_user()
    return jsonify({"user": verified_user, "authToken": make_auth_token(verified_user["id"]) if verified_user else None, "message": "Age verified."})


@app.post("/api/auth/logout")
def api_logout():
    session.clear()
    return jsonify({"message": "Signed out."})


def admin_required_json():
    user = current_user()
    if not user:
        return None, (jsonify({"error": "Please sign in as an admin."}), 401)
    if user.get("role") != "admin":
        return user, (jsonify({"error": "Admin access is required."}), 403)
    return user, None


@app.get("/api/admin")
@api_login_required
def api_admin_dashboard():
    admin_user, error = admin_required_json()
    if error:
        return error
    profile_photo_select = "u.profile_photo" if db_column_exists("users", "profile_photo") else "NULL AS profile_photo"
    with get_db().cursor() as cursor:
        cursor.execute(
            f"""SELECT u.id, u.username, u.email, u.role, u.is_active,
                      {profile_photo_select}, u.sessions_completed, u.created_at,
                      COALESCE(c.balance, 0) AS credits
               FROM users u
               LEFT JOIN credits c ON c.user_id = u.id
               ORDER BY u.created_at DESC"""
        )
        users = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS count FROM users WHERE is_active = 1")
        user_count = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) AS count FROM sessions")
        session_count = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) AS count FROM notifications")
        notification_count = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) AS count FROM reports")
        report_count = cursor.fetchone()["count"]
        cursor.execute(
            """SELECT r.id, r.reason, r.created_at,
                      reporter.username AS reporter_name,
                      reported.username AS reported_name
               FROM reports r
               LEFT JOIN users reporter ON reporter.id = r.reporter_id
               LEFT JOIN users reported ON reported.id = r.reported_user_id
               ORDER BY r.created_at DESC
               LIMIT 20"""
        )
        reports = cursor.fetchall()
    return jsonify({
        "admin": admin_user,
        "stats": {
            "users": user_count,
            "sessions": session_count,
            "notifications": notification_count,
            "reports": report_count,
        },
        "users": users,
        "reports": reports,
    })


@app.post("/api/admin/remove-user/<int:user_id>")
@api_login_required
def api_admin_remove_user(user_id):
    admin_user, error = admin_required_json()
    if error:
        return error
    if user_id == admin_user["id"]:
        return jsonify({"error": "You cannot deactivate your own admin account."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id, role FROM users WHERE id = %s", (user_id,))
        target = cursor.fetchone()
        if not target:
            return jsonify({"error": "That account was not found."}), 404
        if target.get("role") == "admin":
            return jsonify({"error": "Admin accounts cannot be deactivated here."}), 403
        cursor.execute("UPDATE users SET is_active = 0 WHERE id = %s", (user_id,))
    return jsonify({"message": "Account deactivated."})


@app.get("/api/app")
@api_login_required
def api_app_data():
    signed_in = current_user()
    if signed_in and signed_in.get("needs_birthdate"):
        return jsonify({"error": "Birthdate verification is required.", "needs_birthdate": True}), 428
    try:
        record_app_visit(session["user_id"])
        reconcile_progress_badges(session["user_id"])
        return jsonify(minimal_app_payload())
    except Exception:
        app.logger.exception("App data failed")
        return jsonify({"error": "Could not load your workspace. Please retry."}), 503


@app.get("/api/users/<int:user_id>/completed-sessions")
@api_login_required
def api_profile_completed_sessions(user_id):
    try:
        page = int(request.args.get("page", "1"))
        if not 1 <= page <= 100000:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Choose a valid session page."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id, username FROM users WHERE id = %s AND is_active = 1", (user_id,))
        person = cursor.fetchone()
        if not person:
            return jsonify({"error": "This profile is unavailable."}), 404
        cursor.execute("""SELECT COUNT(*) AS total FROM sessions
                          WHERE (learner_id = %s OR mentor_id = %s) AND status = 'completed'""", (user_id, user_id))
        total = cursor.fetchone()["total"]
        cursor.execute("""SELECT s.id, COALESCE(sk.name, 'Skill session') AS skill_name,
                          DATE_FORMAT(s.scheduled_for, '%%Y-%%m-%%d') AS session_date,
                          CASE WHEN s.mentor_id = %s THEN 'Taught' ELSE 'Learned' END AS role,
                          CASE WHEN %s = %s THEN other.username ELSE NULL END AS counterpart_name
                          FROM sessions s LEFT JOIN skills sk ON sk.id = s.skill_id
                          LEFT JOIN users other ON other.id = CASE WHEN s.mentor_id = %s THEN s.learner_id ELSE s.mentor_id END
                          WHERE (s.learner_id = %s OR s.mentor_id = %s) AND s.status = 'completed'
                          ORDER BY COALESCE(s.scheduled_for, s.created_at) DESC, s.id DESC LIMIT 20 OFFSET %s""",
                       (user_id, session["user_id"], user_id, user_id, user_id, user_id, (page - 1) * 20))
        return jsonify({"user": person, "sessions": cursor.fetchall(), "total": total,
                        "page": page, "has_more": page * 20 < total})


@app.get("/api/users/<int:user_id>/activity")
@api_login_required
def api_profile_activity(user_id):
    today = today_ist()
    month = request.args.get("month", today.strftime("%Y-%m"))
    try:
        start = datetime.strptime(month, "%Y-%m").date()
        if start.strftime("%Y-%m") != month or not 2000 <= start.year <= 2100:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Choose a valid calendar month."}), 400
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_visit_select = "u.last_visit_on" if db_column_exists("users", "last_visit_on") else "NULL AS last_visit_on"
    with get_db().cursor() as cursor:
        cursor.execute(f"SELECT u.id, u.username, u.streak AS recorded_streak, {active_streak_select()}, {last_visit_select} FROM users u WHERE u.id = %s AND u.is_active = 1", (user_id,))
        person = cursor.fetchone()
        if not person:
            return jsonify({"error": "This profile is unavailable."}), 404
        days = set()
        if db_table_exists("user_activity_days"):
            cursor.execute("""SELECT activity_date FROM user_activity_days WHERE user_id = %s
                              AND activity_date >= %s AND activity_date < %s AND activity_date <= %s""",
                           (user_id, start, end, today))
            days.update(row["activity_date"] for row in cursor.fetchall())
        days.update(recorded_streak_days(person["last_visit_on"], person["recorded_streak"], today, start, end))
        return jsonify({"month": month, "today": today.isoformat(), "active_days": sorted(day.isoformat() for day in days),
                        "streak": person["streak"], "history_note": "Active-day history is recorded from this update. Older visits may not appear."})


def ensure_missed_notice_table():
    # Additive, idempotent bootstrap for deployed apps without a migration runner.
    # This runs before the claim transaction so DDL cannot commit its writes early.
    with get_db().cursor() as cursor:
        cursor.execute("""CREATE TABLE IF NOT EXISTS missed_session_notices (
            session_id INT NOT NULL,
            user_id INT NOT NULL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (session_id, user_id),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""")


@atomic
def claim_missed_notices(user_id, session_ids, previously_seen):
    claimed = []
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
        placeholders = ",".join(["%s"] * len(session_ids))
        cursor.execute(
            f"""SELECT id FROM sessions WHERE id IN ({placeholders}) AND status = 'missed'
                AND (learner_id = %s OR mentor_id = %s) ORDER BY id""",
            (*session_ids, user_id, user_id),
        )
        for item in cursor.fetchall():
            cursor.execute("INSERT IGNORE INTO missed_session_notices (session_id, user_id) VALUES (%s, %s)",
                           (item["id"], user_id))
            if cursor.rowcount == 1 and item["id"] not in previously_seen:
                claimed.append(item["id"])
    return jsonify({"session_ids": claimed})


@app.post("/api/sessions/missed-notices/claim")
@api_login_required
def api_claim_missed_notices():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Provide the missed session IDs."}), 400
    ids, seen = payload.get("session_ids"), payload.get("previously_seen", [])
    for values in (ids, seen):
        if not isinstance(values, list) or len(values) > 500 or any(type(value) is not int or value <= 0 for value in values):
            return jsonify({"error": "Provide up to 500 valid session IDs."}), 400
    if not ids:
        return jsonify({"session_ids": []})
    if not db_table_exists("missed_session_notices"):
        try:
            ensure_missed_notice_table()
        except Exception:
            app.logger.exception("Could not initialize missed-session notice tracking")
            return jsonify({"error": "Missed-session notice tracking is temporarily unavailable."}), 503
    return claim_missed_notices(session["user_id"], sorted(set(ids)), set(seen))


@app.get("/api/mentors/<int:mentor_id>/reviews")
@api_login_required
def api_mentor_reviews(mentor_id):
    try:
        page = int(request.args.get("page", "1"))
        if not 1 <= page <= 100000:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "Choose a valid review page."}), 400
    page_size = 20
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id, username FROM users WHERE id = %s AND is_active = 1", (mentor_id,))
        mentor = cursor.fetchone()
        if not mentor:
            return jsonify({"error": "This mentor is unavailable."}), 404
        source = MENTOR_REVIEWS_FROM + " AND r.ratee_id = %s"
        cursor.execute(f"SELECT COALESCE(AVG(r.score), 0) AS rating, COUNT(*) AS rating_count, COUNT(review.id) AS review_count {source}", (mentor_id,))
        summary = cursor.fetchone()
        cursor.execute(
            f"""SELECT r.id, r.score, COALESCE(sk.name, 'Skill session') AS skill_name,
                       DATE_FORMAT(rated.scheduled_for, '%%Y-%%m-%%d') AS session_date,
                       review.content AS feedback
                {source} ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s""",
            (mentor_id, page_size, (page - 1) * page_size),
        )
        return jsonify({"mentor": mentor, "summary": summary, "reviews": cursor.fetchall(),
                        "page": page, "has_more": page * page_size < summary["rating_count"]})


@app.get("/api/birthdays/today")
@api_login_required
def api_birthdays_today():
    today = today_ist()
    with get_db().cursor() as cursor:
        cursor.execute("""SELECT id, username FROM users
            WHERE is_active = 1 AND id <> %s AND birthdate <= %s
              AND MONTH(birthdate) = %s AND DAY(birthdate) = %s
            ORDER BY username, id""", (session["user_id"], today, today.month, today.day))
        return jsonify({"date": today.isoformat(), "people": cursor.fetchall()})


def ensure_community_feedback_table():
    # Add only the missing table; never reinitialize the application's database.
    with get_db().cursor() as cursor:
        cursor.execute("""CREATE TABLE IF NOT EXISTS community_feedback (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""")


def feedback_story(cursor, feedback_id):
    cursor.execute(
        """SELECT f.id, f.message, f.created_at, u.username AS name
           FROM community_feedback f JOIN users u ON u.id = f.user_id
           WHERE f.id = %s AND u.is_active = 1""", (feedback_id,),
    )
    return cursor.fetchone()


@app.route("/api/feedback", methods=["GET", "POST"])
@api_login_required
def api_community_feedback():
    if not db_table_exists("community_feedback"):
        try:
            ensure_community_feedback_table()
        except Exception:
            app.logger.exception("Could not initialize community feedback storage")
            return jsonify({"error": "Feedback is temporarily unavailable. Please try again shortly."}), 503
    if request.method == "GET":
        with get_db().cursor() as cursor:
            cursor.execute(
                """SELECT f.id, f.message, f.created_at, u.username AS name
                   FROM community_feedback f JOIN users u ON u.id = f.user_id
                   WHERE u.is_active = 1 ORDER BY f.id DESC LIMIT 50"""
            )
            return jsonify({"stories": cursor.fetchall()})
    payload = request.get_json(silent=True)
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000:
        return jsonify({"error": "Enter feedback between 1 and 2,000 characters."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("INSERT INTO community_feedback (user_id, message) VALUES (%s, %s)",
                       (session["user_id"], message.strip()))
        story = feedback_story(cursor, cursor.lastrowid)
    return jsonify({"story": story}), 201


@app.patch("/api/profile")
@api_login_required
def api_update_profile():
    payload = request.get_json(silent=True) or {}
    with get_db().cursor() as cursor:
        cursor.execute(
            """UPDATE users SET username = %s, bio = %s, location = %s, age = %s
               WHERE id = %s""",
            (
                payload.get("username", "").strip(),
                payload.get("bio", "").strip(),
                payload.get("location", "").strip(),
                payload.get("age") or 18,
                session["user_id"],
            ),
        )
    return jsonify({"user": current_user(), "message": "Profile updated."})


@app.post("/api/profile/photo")
@api_login_required
def api_upload_profile_photo():
    file = request.files.get("profile_photo")
    if not file or not file.filename:
        return jsonify({"error": "Choose a profile photo first."}), 400
    if not allowed_image_file(file.filename):
        return jsonify({"error": "Upload a PNG, JPG, GIF, or WebP image."}), 400

    original = secure_filename(file.filename)
    extension = original.rsplit(".", 1)[1].lower()
    filename = f"profile_{session['user_id']}_{secrets.token_hex(8)}.{extension}"
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    with get_db().cursor() as cursor:
        cursor.execute("UPDATE users SET profile_photo = %s WHERE id = %s", (filename, session["user_id"]))
    return jsonify({"user": current_user(), "message": "Profile photo updated."})


@app.post("/api/profile/banner")
@api_login_required
def api_update_profile_banner():
    allowed_styles = {"mint", "violet", "peach", "blue", "yellow"}
    style = (request.form.get("banner_style") or "").strip().lower()
    file = request.files.get("banner_image")

    if file and file.filename:
        if not allowed_image_file(file.filename):
            return jsonify({"error": "Upload a PNG, JPG, GIF, or WebP banner."}), 400
        original = secure_filename(file.filename)
        extension = original.rsplit(".", 1)[1].lower()
        filename = f"banner_{session['user_id']}_{secrets.token_hex(8)}.{extension}"
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        with get_db().cursor() as cursor:
            cursor.execute(
                "UPDATE users SET banner_image = %s, banner_style = %s WHERE id = %s",
                (filename, style if style in allowed_styles else "custom", session["user_id"]),
            )
        return jsonify({"user": current_user(), "message": "Banner uploaded."})

    if style not in allowed_styles:
        return jsonify({"error": "Choose one of the default banner designs."}), 400

    with get_db().cursor() as cursor:
        cursor.execute(
            "UPDATE users SET banner_style = %s, banner_image = NULL WHERE id = %s",
            (style, session["user_id"]),
        )
    return jsonify({"user": current_user(), "message": "Banner updated."})


@app.post("/api/quests/daily-curiosity")
@api_login_required
@atomic
def api_claim_daily_curiosity():
    payload = request.get_json(silent=True) or {}
    quest = current_daily_quest()
    if not isinstance(payload, dict) or payload.get("quest_key") != quest["key"] or payload.get("quest_date") != quest["date"]:
        return jsonify({"awarded": False, "error": "The daily quest has changed. Refresh to see today's quest."}), 409
    user_id = session["user_id"]
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
    if has_daily_quest_claim(user_id, quest):
        return jsonify({"awarded": False, "dailyQuest": quest, "dailyQuestClaimed": True,
                        "user": current_user(), "message": "Daily quest already completed today."})
    progress = daily_quest_progress(user_id, quest, payload.get("mentor_ids", []))
    if progress < int(quest["target"]):
        return jsonify({"awarded": False, "error": "Finish today’s quest first."}), 400
    result = award_xp(
        session["user_id"],
        int(quest["xp"]),
        quest["event_key"],
        f"Daily quest complete: +{quest['xp']} XP for {quest['title']}.",
        event_date=quest["date"],
    )
    return jsonify({
        **result,
        "dailyQuest": quest,
        "dailyQuestClaimed": True,
        "user": current_user(),
        "message": f"+{quest['xp']} XP earned. Daily quest complete!" if result["awarded"] else "Daily quest already completed today.",
    })


@app.post("/api/quests/daily-reset")
@api_login_required
def api_reset_daily_quest():
    return jsonify({"error": "Daily quests reset automatically at midnight IST."}), 403


class PaymentVerificationError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


@app.errorhandler(PaymentVerificationError)
def payment_verification_error(error):
    return jsonify({"error": str(error)}), error.status


def payment_keys(*, require_webhook=False):
    key_id = app.config["RAZORPAY_KEY_ID"]
    key_secret = app.config["RAZORPAY_KEY_SECRET"]
    if not key_id or not key_secret:
        raise PaymentVerificationError("Payments are not configured.", 503)
    if require_webhook and key_id.startswith("rzp_live_") and not app.config["RAZORPAY_WEBHOOK_SECRET"]:
        raise PaymentVerificationError("Live payments are awaiting webhook configuration.", 503)
    return key_id, key_secret


def fetch_razorpay_payment(payment_id):
    if not valid_payment_id(payment_id):
        raise PaymentVerificationError("Invalid payment ID.", 400)
    keys = payment_keys()
    try:
        response = requests.get(
            f"https://api.razorpay.com/v1/payments/{payment_id}",
            auth=keys, timeout=(2, 2),
        )
        response.raise_for_status()
        payment = response.json()
    except (requests.RequestException, ValueError):
        app.logger.warning("Razorpay payment lookup unavailable")
        raise PaymentVerificationError(
            "Payment confirmation is temporarily unavailable. Do not pay again; check your wallet shortly.", 503
        ) from None
    if not isinstance(payment, dict) or payment.get("id") != payment_id:
        raise PaymentVerificationError("Unexpected payment response from provider.", 502)
    return payment


PAYMENT_TABLES = ("credit_payments", "donation_payments")


def find_payment_order(table, order_id, owner_id=None):
    if table not in PAYMENT_TABLES:
        raise ValueError("Invalid payment table")
    with get_db().cursor() as cursor:
        cursor.execute(f"SELECT * FROM {table} WHERE provider_order_id = %s", (order_id,))
        order = cursor.fetchone()
    if order and (owner_id is None or order["user_id"] == owner_id):
        return order
    return None


@atomic
def finalize_razorpay_payment(table, payment, owner_id=None):
    """Both delivery paths serialize on the same persisted order before granting anything."""
    if table not in PAYMENT_TABLES:
        raise ValueError("Invalid payment table")
    with get_db().cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM {table} WHERE provider_order_id = %s FOR UPDATE",
            (payment.get("order_id"),),
        )
        order = cursor.fetchone()
        if not order or (owner_id is not None and order["user_id"] != owner_id):
            raise PaymentVerificationError("Payment order was not found.", 404)
        problem = payment_problem(payment, order)
        if problem:
            raise PaymentVerificationError(problem)
        if order["status"] == "paid":
            if order["provider_payment_id"] != payment["id"]:
                raise PaymentVerificationError("Order already has a different payment.")
            return {"paid": True, "message": "Payment already verified."}
        if order["status"] != "created":
            raise PaymentVerificationError("This order requires manual payment review.")
        if payment.get("status") in ("created", "authorized"):
            return {
                "paid": False, "pending": True,
                "message": "Payment is awaiting capture. Do not pay again; check back shortly.",
            }
        if (payment.get("status") != "captured" or payment.get("captured") is not True
                or payment.get("amount_refunded") != 0):
            raise PaymentVerificationError("Payment is not captured or has been refunded. No credits were added.")
        cursor.execute(
            f"UPDATE {table} SET status = 'paid', provider_payment_id = %s, paid_at = NOW() WHERE id = %s",
            (payment["id"], order["id"]),
        )
        user_id = order["user_id"]
        if table == "credit_payments":
            cursor.execute(
                "INSERT INTO credits (user_id, balance) VALUES (%s, 0) ON DUPLICATE KEY UPDATE balance = balance",
                (user_id,),
            )
            cursor.execute("UPDATE credits SET balance = balance + %s WHERE user_id = %s",
                           (order["credits"], user_id))
            cursor.execute(
                "INSERT INTO transactions (user_id, kind, amount, note) VALUES (%s, 'bonus', %s, %s)",
                (user_id, order["credits"], f"Paid credit pack - ₹{int(order['amount_paise']) // 100}"),
            )
            create_notification(user_id, "credit", f"{order['credits']} paid credits added to your wallet.")
            return {"paid": True, "credits": order["credits"],
                    "message": f"Payment successful. {order['credits']} credits added."}
        amount = int(order["amount_paise"]) // 100
        if user_id is not None:
            create_notification(user_id, "donation", f"Thank you for donating ₹{amount} to support GotTime.")
        return {"paid": True, "message": f"Thank you for your ₹{amount} donation."}


def verify_checkout_payment(table):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise PaymentVerificationError("Payment verification details are missing.", 400)
    order_id = payload.get("razorpay_order_id")
    payment_id = payload.get("razorpay_payment_id")
    if not isinstance(payload.get("razorpay_signature"), str) or not payload["razorpay_signature"]:
        raise PaymentVerificationError("Payment signature is missing.", 400)
    if not isinstance(order_id, str) or not order_id or len(order_id) > 120 or not valid_payment_id(payment_id):
        raise PaymentVerificationError("Invalid payment verification details.", 400)
    _, secret = payment_keys()
    order = find_payment_order(table, order_id, session["user_id"])
    if not order:
        raise PaymentVerificationError("Payment order was not found.", 404)
    signed_body = f"{order['provider_order_id']}|{payment_id}".encode("utf-8")
    if not valid_signature(secret, signed_body, payload.get("razorpay_signature")):
        raise PaymentVerificationError("Payment signature verification failed.", 400)
    payment = fetch_razorpay_payment(payment_id)
    # Never let a valid payment for some other local order fulfill that order through this callback.
    problem = payment_problem(payment, order)
    if problem:
        raise PaymentVerificationError(problem)
    result = finalize_razorpay_payment(table, payment, session["user_id"])
    if table == "credit_payments":
        result["user"] = current_user()
    return jsonify(result), 200 if result["paid"] else 202


@app.post("/api/payments/razorpay/webhook")
def api_razorpay_webhook():
    secret = app.config["RAZORPAY_WEBHOOK_SECRET"]
    if not secret:
        raise PaymentVerificationError("Webhook is not configured.", 503)
    if request.content_length is not None and request.content_length > 65536:
        return jsonify({"error": "Webhook too large."}), 413
    raw = request.get_data()
    if len(raw) > 65536:
        return jsonify({"error": "Webhook too large."}), 413
    if not valid_signature(secret, raw, request.headers.get("X-Razorpay-Signature")):
        return jsonify({"error": "Invalid webhook signature."}), 400
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError()
        if event.get("event") not in ("payment.captured", "order.paid"):
            return jsonify({"received": True, "ignored": True})
        entity = event["payload"]["payment"]["entity"]
        payment_id, order_id = entity["id"], entity["order_id"]
        if not valid_payment_id(payment_id) or not isinstance(order_id, str) or len(order_id) > 120:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        return jsonify({"error": "Invalid webhook payload."}), 400
    table = next((name for name in PAYMENT_TABLES if find_payment_order(name, order_id)), None)
    if table is None:
        # The same merchant account can receive orders from other applications.
        return jsonify({"received": True, "ignored": True})
    payment = fetch_razorpay_payment(payment_id)
    if payment.get("order_id") != order_id:
        raise PaymentVerificationError("Webhook order does not match payment.")
    result = finalize_razorpay_payment(table, payment)
    if not result["paid"]:
        # Non-2xx asks Razorpay to retry; never acknowledge an unfulfilled capture.
        return jsonify(result), 503
    return jsonify({"received": True})


@app.post("/api/credits/purchase")
@api_login_required
def api_purchase_credits():
    packages = credit_packages()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Choose a valid credit package."}), 400
    package_id = str(payload.get("packageId") or "").strip().lower()
    package = packages.get(package_id)
    if not package:
        return jsonify({"error": "Choose a valid credit package."}), 400
    key_id, key_secret = payment_keys(require_webhook=True)
    user_id = session["user_id"]
    amount_paise = int(package["price"]) * 100
    receipt = f"gt_{user_id}_{package_id}_{secrets.token_hex(5)}"
    try:
        response = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(key_id, key_secret),
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {
                    "user_id": str(user_id),
                    "package_id": package_id,
                    "credits": str(package["credits"]),
                },
            },
            timeout=15,
        )
        order = response.json() if response.status_code < 400 else {}
    except (requests.RequestException, ValueError):
        app.logger.exception("Razorpay order create failed")
        return jsonify({"error": "Could not start payment. Please try again shortly."}), 500
    if response.status_code == 401:
        return jsonify({"error": "Payment provider authentication failed. Please contact support."}), 401
    if response.status_code >= 400:
        return jsonify({"error": "Razorpay could not create the order. Please try again shortly."}), 500
    if (not isinstance(order, dict) or not order.get("id")
            or order.get("amount") != amount_paise or order.get("currency") != "INR"):
        return jsonify({"error": "Razorpay could not create the expected order."}), 502
    ensure_payment_tables()
    with get_db().cursor() as cursor:
        cursor.execute(
            """INSERT INTO credit_payments
               (user_id, package_id, credits, amount_paise, currency, provider_order_id, status)
               VALUES (%s, %s, %s, %s, 'INR', %s, 'created')""",
            (user_id, package_id, package["credits"], amount_paise, order["id"]),
        )
    return jsonify({
        "provider": "razorpay",
        "keyId": key_id,
        "order": {
            "id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
        },
        "package": package,
        "user": current_user(),
    })


@app.post("/api/credits/verify")
@api_login_required
def api_verify_credit_payment():
    return verify_checkout_payment("credit_payments")


@app.get("/api/donations/upi")
@api_login_required
def api_donation_upi():
    response = jsonify({
        "upiId": (app.config.get("DONATION_UPI_ID") or "").strip(),
        "payeeName": (app.config.get("DONATION_UPI_NAME") or "").strip() or "GotTime",
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/donations/create")
@api_login_required
def api_create_donation():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Enter a valid donation amount."}), 400
    try:
        amount_rupees = int(payload.get("amount") or 0)
    except (TypeError, ValueError):
        amount_rupees = 0
    if amount_rupees < 10:
        return jsonify({"error": "Donation amount must be at least ₹10."}), 400
    key_id, key_secret = payment_keys(require_webhook=True)
    user = current_user()
    user_id = session["user_id"]
    amount_paise = amount_rupees * 100
    receipt = f"don_{user_id}_{secrets.token_hex(6)}"
    try:
        response = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(key_id, key_secret),
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {
                    "purpose": "GotTime public donation",
                    "user_id": str(user_id),
                },
            },
            timeout=15,
        )
        order = response.json() if response.status_code < 400 else {}
    except (requests.RequestException, ValueError):
        app.logger.exception("Razorpay donation order create failed")
        return jsonify({"error": "Could not start donation. Please try again shortly."}), 500
    if response.status_code == 401:
        return jsonify({"error": "Payment provider authentication failed. Please contact support."}), 401
    if response.status_code >= 400:
        return jsonify({"error": "Razorpay could not create the donation order. Please try again shortly."}), 500
    if (not isinstance(order, dict) or not order.get("id")
            or order.get("amount") != amount_paise or order.get("currency") != "INR"):
        return jsonify({"error": "Razorpay could not create the expected donation order."}), 502
    ensure_payment_tables()
    with get_db().cursor() as cursor:
        cursor.execute(
            """INSERT INTO donation_payments
               (user_id, donor_name, donor_email, amount_paise, currency, provider_order_id, status)
               VALUES (%s, %s, %s, %s, 'INR', %s, 'created')""",
            (user_id, user.get("username"), user.get("email"), amount_paise, order["id"]),
        )
    return jsonify({
        "provider": "razorpay",
        "keyId": key_id,
        "order": {
            "id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
        },
        "amount": amount_rupees,
        "user": user,
    })


@app.post("/api/donations/verify")
@api_login_required
def api_verify_donation():
    return verify_checkout_payment("donation_payments")


@app.post("/api/dev/reset-gamification")
@api_login_required
def api_reset_gamification():
    user_id = session["user_id"]
    ensure_xp_system()
    with get_db().cursor() as cursor:
        cursor.execute("UPDATE users SET xp = 0, streak = 0, last_visit_on = NULL WHERE id = %s", (user_id,))
        cursor.execute("DELETE FROM user_badges WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM xp_events WHERE user_id = %s", (user_id,))
        cursor.execute(
            "DELETE FROM notifications WHERE user_id = %s AND type IN ('xp', 'badge')",
            (user_id,),
        )
    return jsonify({
        "message": "XP, streak, daily quest claims, and badges reset for this user.",
        "user": current_user(),
        "dailyQuestClaimed": False,
    })


@app.post("/api/skills")
@api_login_required
def api_add_skill():
    payload = request.get_json(silent=True) or {}
    name = payload.get("name", "").strip()
    if not name:
        return jsonify({"error": "Give your skill a name."}), 400
    category = (payload.get("category") or "Other").strip() or "Other"
    description = payload.get("description", "")
    duration = payload.get("duration") or 60
    availability = payload.get("availability", "online")
    preferred_time = (payload.get("preferred_time") or "").strip()
    user_skill_id = payload.get("id")
    skill_id = payload.get("skill_id")
    try:
        ensure_skill_columns()
        ensure_user_skill_columns()
        with get_db().cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(DISTINCT LOWER(sk.name)) AS count
                   FROM user_skills us JOIN skills sk ON sk.id = us.skill_id
                   WHERE us.user_id = %s""",
                (session["user_id"],),
            )
            had_skills = cursor.fetchone()["count"] > 0
            existing_rows = []
            if user_skill_id or skill_id:
                cursor.execute(
                    """SELECT us.id, us.skill_id
                       FROM user_skills us
                       JOIN skills sk ON sk.id = us.skill_id
                       WHERE us.user_id = %s
                         AND (%s IS NULL OR us.id = %s)
                         AND (%s IS NULL OR us.skill_id = %s)
                       ORDER BY us.id""",
                    (session["user_id"], user_skill_id, user_skill_id, skill_id, skill_id),
                )
                existing_rows = cursor.fetchall()
            if not existing_rows:
                cursor.execute(
                    """SELECT us.id, us.skill_id
                       FROM user_skills us
                       JOIN skills sk ON sk.id = us.skill_id
                       WHERE us.user_id = %s AND LOWER(sk.name) = LOWER(%s)
                       ORDER BY us.id""",
                    (session["user_id"], name),
                )
                existing_rows = cursor.fetchall()
            if existing_rows:
                keep = existing_rows[0]
                cursor.execute(
                    "UPDATE skills SET name = %s, category = %s, description = %s WHERE id = %s",
                    (name, category, description, keep["skill_id"]),
                )
                cursor.execute(
                    "UPDATE user_skills SET category = %s, duration = %s, availability = %s, preferred_time = %s WHERE id = %s",
                    (category, duration, availability, preferred_time, keep["id"]),
                )
                duplicate_skill_ids = [row["skill_id"] for row in existing_rows[1:]]
                if duplicate_skill_ids:
                    placeholders = ",".join(["%s"] * len(duplicate_skill_ids))
                    cursor.execute(
                        f"DELETE FROM user_skills WHERE user_id = %s AND skill_id IN ({placeholders})",
                        tuple([session["user_id"], *duplicate_skill_ids]),
                    )
                    cursor.execute(
                        f"DELETE FROM skills WHERE created_by = %s AND id IN ({placeholders})",
                        tuple([session["user_id"], *duplicate_skill_ids]),
                    )
                new_skill = {
                    "id": keep["id"],
                    "skill_id": keep["skill_id"],
                    "name": name,
                    "category": category,
                    "description": description,
                    "duration": duration,
                    "availability": availability,
                    "preferred_time": preferred_time,
                }
                unlocked_badges = award_badges(session["user_id"])
                reward_badge = "Skill Sharer" if (not had_skills or "Skill Sharer" in unlocked_badges) else None
                return jsonify({"message": "Skill updated in your deck.", "skill": new_skill, "unlockedBadges": unlocked_badges, "rewardBadge": reward_badge}), 200
            cursor.execute(
                "INSERT INTO skills (name, category, description, created_by) VALUES (%s, %s, %s, %s)",
                (name, category, description, session["user_id"]),
            )
            cursor.execute("SELECT LAST_INSERT_ID() AS id")
            skill_id = cursor.fetchone()["id"]
            cursor.execute(
                """INSERT INTO user_skills (user_id, skill_id, category, duration, availability, preferred_time)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    session["user_id"],
                    skill_id,
                    category,
                    duration,
                    availability,
                    preferred_time,
                ),
            )
            cursor.execute("SELECT LAST_INSERT_ID() AS id")
            user_skill_id = cursor.fetchone()["id"]
        new_skill = {
            "id": user_skill_id,
            "skill_id": skill_id,
            "name": name,
            "category": category,
            "description": description,
            "duration": duration,
            "availability": availability,
            "preferred_time": preferred_time,
        }
        unlocked_badges = award_badges(session["user_id"])
        reward_badge = "Skill Sharer" if (not had_skills or "Skill Sharer" in unlocked_badges) else None
        return jsonify({"message": "Skill added to your teaching deck.", "skill": new_skill, "unlockedBadges": unlocked_badges, "rewardBadge": reward_badge}), 201
    except Exception as exc:
        app.logger.exception("Add skill failed")
        detail = str(exc)
        if app.debug:
            return jsonify({"error": f"Could not add skill: {detail}"}), 500
        return jsonify({"error": "Could not add skill right now."}), 500


@app.delete("/api/skills/<int:skill_id>")
@api_login_required
def api_remove_skill(skill_id):
    try:
        with get_db().cursor() as cursor:
            cursor.execute(
                """SELECT sk.name FROM user_skills us
                   JOIN skills sk ON sk.id = us.skill_id
                   WHERE us.user_id = %s AND (us.skill_id = %s OR us.id = %s)
                   ORDER BY us.id LIMIT 1""",
                (session["user_id"], skill_id, skill_id),
            )
            row = cursor.fetchone()
            if not row:
                return jsonify({"error": "That skill is already removed."}), 404
            cursor.execute(
                """DELETE us FROM user_skills us
                   JOIN skills sk ON sk.id = us.skill_id
                   WHERE us.user_id = %s AND LOWER(sk.name) = LOWER(%s)""",
                (session["user_id"], row["name"]),
            )
            cursor.execute(
                """DELETE sk FROM skills sk
                   LEFT JOIN user_skills us ON us.skill_id = sk.id
                   WHERE us.id IS NULL AND sk.created_by = %s AND LOWER(sk.name) = LOWER(%s)""",
                (session["user_id"], row["name"]),
            )
            cursor.execute(
                """SELECT COUNT(DISTINCT LOWER(sk.name)) AS count
                   FROM user_skills us JOIN skills sk ON sk.id = us.skill_id
                   WHERE us.user_id = %s""",
                (session["user_id"],),
            )
            remaining_skills = cursor.fetchone()["count"]
            if remaining_skills == 0:
                cursor.execute(
                    """DELETE ub FROM user_badges ub
                       JOIN badges b ON b.id = ub.badge_id
                       WHERE ub.user_id = %s AND b.name = 'Skill Sharer'""",
                    (session["user_id"],),
                )
        return jsonify({"message": "Skill removed."})
    except Exception as exc:
        app.logger.exception("Remove skill failed")
        if app.debug:
            return jsonify({"error": f"Could not remove skill: {exc}"}), 500
        return jsonify({"error": "Could not remove skill right now."}), 500


@app.post("/api/sessions/availability")
@api_login_required
def api_session_availability():
    """Read-only preview. The booking transaction still rechecks under locks."""
    payload = request.get_json(silent=True) or {}
    try:
        mentor_id = int(payload.get("mentorId"))
        skill_id = int(payload.get("skillId"))
    except (TypeError, ValueError):
        return jsonify({"error": "Choose a valid mentor and skill."}), 400
    scheduled_at = parse_session_datetime(payload.get("scheduledFor"))
    if not scheduled_at or scheduled_at <= app_now():
        return jsonify({"error": "Choose a future date and time."}), 400
    if mentor_id == session["user_id"]:
        return jsonify({"error": "You cannot book yourself."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("""SELECT us.duration FROM user_skills us
            JOIN users u ON u.id = us.user_id AND u.is_active = 1
            WHERE us.user_id = %s AND us.skill_id = %s ORDER BY us.id DESC LIMIT 1""",
            (mentor_id, skill_id))
        skill = cursor.fetchone()
        if not skill:
            return jsonify({"error": "Choose a valid mentor skill."}), 400
        conflict = session_overlap(cursor, session["user_id"], mentor_id, scheduled_at, skill["duration"])
    return jsonify({"available": conflict is None,
                    "message": overlap_message(conflict, session["user_id"]) if conflict else ""})


@app.post("/api/sessions")
@api_login_required
@atomic
def api_book_session():
    payload = request.get_json(silent=True) or {}
    try:
        mentor_id = int(payload.get("mentorId"))
        skill_id = int(payload.get("skillId"))
    except (TypeError, ValueError):
        return jsonify({"error": "Choose a valid mentor and skill."}), 400
    scheduled_for = payload.get("scheduledFor")
    mode = payload.get("mode", "online")
    if mode not in {"online", "offline"}:
        return jsonify({"error": "Choose online or offline mode."}), 400
    if not mentor_id or not skill_id or not scheduled_for:
        return jsonify({"error": "Choose a mentor, skill, and time."}), 400
    scheduled_at = parse_session_datetime(scheduled_for)
    if not scheduled_at:
        return jsonify({"error": "Choose a valid session date and time."}), 400
    if scheduled_at <= app_now():
        return jsonify({"error": "Choose a future date and time."}), 400
    user = current_user()
    if not user or age_from_birthdate(str(user.get("birthdate") or "")) is None:
        return jsonify({"error": "Add your birthdate before booking."}), 428
    guardian_notice = guardian_requirement(user, mode)
    guardian_confirmed = payload.get("guardianConfirmed") is True
    if guardian_notice and not guardian_confirmed:
        return jsonify({"error": "Confirm parent or guardian presence before booking this session."}), 400
    notes = "Booked from mentor card\n" + str(payload.get("notes") or "").strip()
    if guardian_notice and guardian_notice not in notes:
        notes = f"{notes}\n\n{guardian_notice}".strip()
    if guardian_notice and "Guardian confirmation received." not in notes:
        notes = f"{notes}\nGuardian confirmation received.".strip()
    with get_db().cursor() as cursor:
        cursor.execute("SELECT COALESCE(balance, 0) AS credits FROM credits WHERE user_id = %s", (session["user_id"],))
        credit_row = cursor.fetchone()
        if int((credit_row or {}).get("credits") or 0) < 1:
            return jsonify({"error": "You need at least 1 credit to book a mentor."}), 402
        cursor.execute(
            """SELECT preferred_time, availability, duration FROM user_skills
               WHERE user_id = %s AND skill_id = %s
               ORDER BY id DESC LIMIT 1""",
            (mentor_id, skill_id),
        )
        mentor_skill = cursor.fetchone()
        if not mentor_skill:
            return jsonify({"error": "Choose a valid mentor skill."}), 400
        if mentor_skill["availability"] not in {mode, "both"}:
            return jsonify({"error": "That mentor does not offer this session mode."}), 400
        problem = booking_problem(cursor, session["user_id"], mentor_id, scheduled_at, mentor_skill["duration"])
        if problem:
            return jsonify({"error": problem}), 409
        if not is_time_inside_preferred_range(scheduled_at, mentor_skill.get("preferred_time")):
            return jsonify({"error": "Choose a time inside the mentor's available range."}), 400
        cursor.execute(
            """INSERT INTO sessions
               (learner_id, mentor_id, skill_id, scheduled_for, mode, notes, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending')""",
            (session["user_id"], mentor_id, skill_id, scheduled_at.strftime("%Y-%m-%d %H:%M:%S"), mode, notes),
        )
    create_notification(mentor_id, "session", "A new session quest is waiting for your review.")
    return jsonify({"message": "Session quest sent!", "guardianNotice": guardian_notice}), 201


@app.post("/api/sessions/<int:session_id>/schedule")
@api_login_required
@atomic
def api_schedule_session_time(session_id):
    payload = request.get_json(silent=True) or {}
    scheduled_for = payload.get("scheduledFor")
    scheduled_at = parse_session_datetime(scheduled_for)
    if not scheduled_at:
        return jsonify({"error": "Choose a valid session date and time."}), 400
    if scheduled_at <= app_now():
        return jsonify({"error": "Choose a future date and time."}), 400
    with get_db().cursor() as cursor:
        cursor.execute(
            """SELECT s.*, sk.name AS skill_name, us.preferred_time AS preferred_time, COALESCE(us.duration,60) AS duration,
                      mentor.username AS mentor_name, learner.username AS learner_name
               FROM sessions s
               JOIN skills sk ON sk.id = s.skill_id
               LEFT JOIN user_skills us ON us.user_id = s.mentor_id AND us.skill_id = s.skill_id
               JOIN users mentor ON mentor.id = s.mentor_id
               JOIN users learner ON learner.id = s.learner_id
               WHERE s.id = %s""",
            (session_id,),
        )
        item = cursor.fetchone()
        if not item or item["mentor_id"] != session["user_id"]:
            return jsonify({"error": "Only the mentor can set this session time."}), 403
        lock_participants(cursor, item["learner_id"], item["mentor_id"])
        cursor.execute("SELECT status FROM sessions WHERE id = %s FOR UPDATE", (session_id,))
        item["status"] = cursor.fetchone()["status"]
        if item["status"] not in {"pending", "accepted", "scheduled"}:
            return jsonify({"error": "This session can no longer be rescheduled."}), 400
        if not str(item.get("notes") or "").startswith("Scheduled from chat") and not is_time_inside_preferred_range(scheduled_at, item.get("preferred_time")):
            return jsonify({"error": "Choose a time inside your available range for this skill."}), 400
        problem = booking_problem(cursor, item["learner_id"], item["mentor_id"], scheduled_at, item["duration"], exclude_id=session_id)
        if problem:
            return jsonify({"error": problem}), 409
        cursor.execute(
            "UPDATE sessions SET scheduled_for = %s, status = 'scheduled', learner_joined_at = NULL, mentor_joined_at = NULL, learner_left_at = NULL, mentor_left_at = NULL WHERE id = %s",
            (scheduled_at.strftime("%Y-%m-%d %H:%M:%S"), session_id),
        )
        item["scheduled_for"] = scheduled_at.strftime("%Y-%m-%d %H:%M:%S")
        item["status"] = "scheduled"
    schedule_session_reminders(item)
    time_text = format_session_reminder_time(scheduled_at)
    create_notification(item["learner_id"], "session", f"Your mentor scheduled {item['skill_name']} for {time_text}.")
    create_notification(item["mentor_id"], "session", f"You scheduled {item['skill_name']} for {time_text}.")
    return jsonify({"message": f"Session scheduled for {time_text}.", "session": item})


@app.post("/api/messages/<int:contact_id>/schedule")
@api_login_required
@atomic
def api_schedule_session_from_chat(contact_id):
    payload = request.get_json(silent=True) or {}
    scheduled_for = payload.get("scheduledFor")
    scheduled_at = parse_session_datetime(scheduled_for)
    if not scheduled_at:
        return jsonify({"error": "Choose a valid session date and time."}), 400
    if scheduled_at <= app_now():
        return jsonify({"error": "Choose a future date and time."}), 400
    mentor_id = session["user_id"]
    learner_id = contact_id
    with get_db().cursor() as cursor:
        lock_participants(cursor, learner_id, mentor_id)
        cursor.execute("SELECT id FROM users WHERE id = %s AND is_active = 1", (learner_id,))
        if not cursor.fetchone():
            return jsonify({"error": "That learner account was not found."}), 404
        cursor.execute(
            """SELECT us.skill_id, sk.name AS skill_name, us.preferred_time AS preferred_time, COALESCE(us.duration,60) AS duration
               FROM user_skills us JOIN skills sk ON sk.id = us.skill_id
               WHERE us.user_id = %s
               ORDER BY us.created_at DESC LIMIT 1""",
            (mentor_id,),
        )
        skill = cursor.fetchone()
        if not skill:
            return jsonify({"error": "Add a skill first, then you can schedule learners from chat."}), 400
        cursor.execute(
            """SELECT s.*, sk.name AS skill_name, us.preferred_time AS preferred_time, COALESCE(us.duration,60) AS duration,
                      mentor.username AS mentor_name, learner.username AS learner_name
               FROM sessions s
               JOIN skills sk ON sk.id = s.skill_id
               LEFT JOIN user_skills us ON us.user_id = s.mentor_id AND us.skill_id = s.skill_id
               JOIN users mentor ON mentor.id = s.mentor_id
               JOIN users learner ON learner.id = s.learner_id
               WHERE s.mentor_id = %s AND s.learner_id = %s
                 AND s.status IN ('pending', 'accepted', 'scheduled')
               ORDER BY COALESCE(s.scheduled_for, s.created_at) DESC
               LIMIT 1""",
            (mentor_id, learner_id),
        )
        item = cursor.fetchone()
        problem = booking_problem(cursor, learner_id, mentor_id, scheduled_at,
                                  item["duration"] if item else skill["duration"], exclude_id=item["id"] if item else 0)
        if problem:
            return jsonify({"error": problem}), 409
        if item:
            if not str(item.get("notes") or "").startswith("Scheduled from chat") and not is_time_inside_preferred_range(scheduled_at, item.get("preferred_time")):
                return jsonify({"error": "This booking must stay inside the mentor's available range."}), 400
            cursor.execute(
                "UPDATE sessions SET scheduled_for = %s, status = 'scheduled', learner_joined_at = NULL, mentor_joined_at = NULL, learner_left_at = NULL, mentor_left_at = NULL WHERE id = %s",
                (scheduled_at.strftime("%Y-%m-%d %H:%M:%S"), item["id"]),
            )
        else:
            cursor.execute(
                """INSERT INTO sessions (learner_id, mentor_id, skill_id, scheduled_for, mode, notes, status)
                   VALUES (%s, %s, %s, %s, 'online', 'Scheduled from chat', 'scheduled')""",
                (learner_id, mentor_id, skill["skill_id"], scheduled_at.strftime("%Y-%m-%d %H:%M:%S")),
            )
            new_id = cursor.lastrowid
            cursor.execute(
                """SELECT s.*, sk.name AS skill_name,
                          mentor.username AS mentor_name, learner.username AS learner_name
                   FROM sessions s
                   JOIN skills sk ON sk.id = s.skill_id
                   JOIN users mentor ON mentor.id = s.mentor_id
                   JOIN users learner ON learner.id = s.learner_id
                   WHERE s.id = %s""",
                (new_id,),
            )
            item = cursor.fetchone()
        item["scheduled_for"] = scheduled_at.strftime("%Y-%m-%d %H:%M:%S")
        item["status"] = "scheduled"
    schedule_session_reminders(item)
    time_text = format_session_reminder_time(scheduled_at)
    create_notification(learner_id, "session", f"Your mentor scheduled {item['skill_name']} for {time_text}.")
    create_notification(mentor_id, "session", f"You scheduled {item['skill_name']} for {time_text}.")
    return jsonify({"message": f"Session scheduled for {time_text}.", "session": item})


@app.post("/api/sessions/<int:session_id>/<action>")
@api_login_required
@atomic
def api_session_action(session_id, action):
    with get_db().cursor() as cursor:
        cursor.execute("SELECT learner_id, mentor_id FROM sessions WHERE id = %s", (session_id,))
        participants = cursor.fetchone()
        if not participants or session["user_id"] not in {participants["learner_id"], participants["mentor_id"]}:
            return jsonify({"error": "Session not found."}), 404
        lock_participants(cursor, participants["learner_id"], participants["mentor_id"])
    if action == "join":
        with get_db().cursor() as cursor:
            cursor.execute("SELECT * FROM sessions WHERE id = %s FOR UPDATE", (session_id,))
            item = cursor.fetchone()
            if not item or session["user_id"] not in {item["learner_id"], item["mentor_id"]}:
                return jsonify({"error": "You cannot join this session."}), 403
            if item["status"] not in {"accepted", "scheduled", "awaiting_confirmation"}:
                return jsonify({"error": "This meeting is not ready yet."}), 400
            signed_in = current_user()
            if not signed_in or age_from_birthdate(str(signed_in.get("birthdate") or "")) is None:
                return jsonify({"error": "Add your birthdate before joining."}), 428
            notice = guardian_requirement(signed_in, item["mode"])
            if notice and (request.get_json(silent=True) or {}).get("guardianConfirmed") is not True:
                return jsonify({"error": notice, "guardianRequired": True}), 403
            column = "learner_joined_at" if session["user_id"] == item["learner_id"] else "mentor_joined_at"
            cursor.execute(f"UPDATE sessions SET {column} = COALESCE({column}, NOW()) WHERE id = %s", (session_id,))
        return jsonify({"message": "Meeting opened."})
    if action == "leave":
        with get_db().cursor() as cursor:
            cursor.execute("SELECT * FROM sessions WHERE id = %s FOR UPDATE", (session_id,))
            item = cursor.fetchone()
            if not item or session["user_id"] not in {item["learner_id"], item["mentor_id"]}:
                return jsonify({"error": "You cannot close this session."}), 403
            if item["status"] not in {"accepted", "scheduled", "awaiting_confirmation"}:
                return jsonify({"error": "This meeting is not active."}), 400
            joined_column = "learner_joined_at" if session["user_id"] == item["learner_id"] else "mentor_joined_at"
            if not item.get(joined_column):
                return jsonify({"error": "Join the session before ending it."}), 400
            column = "learner_left_at" if session["user_id"] == item["learner_id"] else "mentor_left_at"
            cursor.execute(f"UPDATE sessions SET {column} = COALESCE({column}, NOW()) WHERE id = %s", (session_id,))
            if session["user_id"] == item["mentor_id"]:
                create_notification(item["learner_id"], "session", "Your mentor ended the meeting. Please confirm when you are done.")
        return jsonify({"message": "Meeting closed."})
    transitions = {
        "accept": ("accepted", "mentor_id", "Your session quest was accepted."),
        "decline": ("cancelled", "mentor_id", "Your session request was declined."),
        "schedule": ("scheduled", "mentor_id", "Your session is locked in."),
        "complete": ("awaiting_confirmation", "mentor_id", "Confirm that your session is complete."),
    }
    if action == "confirm":
        with get_db().cursor() as cursor:
            cursor.execute("SELECT * FROM sessions WHERE id = %s FOR UPDATE", (session_id,))
            item = cursor.fetchone()
        if not item or item["learner_id"] != session["user_id"] or item["status"] != "awaiting_confirmation":
            return jsonify({"error": "This session cannot be confirmed."}), 403
        if not item.get("learner_joined_at") or not item.get("mentor_joined_at"):
            return jsonify({"error": "Both learner and mentor must open the meeting link before rewards unlock."}), 400
        if ensure_credit_row(item["learner_id"]) < 1:
            return jsonify({"error": "You need one time credit to complete this session."}), 400
        with get_db().cursor() as cursor:
            cursor.execute("UPDATE sessions SET status = 'completed' WHERE id = %s", (session_id,))
            cursor.execute("UPDATE credits SET balance = balance - 1 WHERE user_id = %s AND balance >= 1", (item["learner_id"],))
            if cursor.rowcount != 1:
                return jsonify({"error": "You need one time credit to complete this session."}), 409
            cursor.execute("INSERT INTO credits (user_id, balance) VALUES (%s, 0) ON DUPLICATE KEY UPDATE balance = balance", (item["mentor_id"],))
            cursor.execute("UPDATE credits SET balance = balance + 1 WHERE user_id = %s", (item["mentor_id"],))
            cursor.execute(
                "INSERT INTO transactions (user_id, kind, amount, related_session_id, note) VALUES (%s, 'spent', 1, %s, 'Learning session')",
                (item["learner_id"], session_id),
            )
            cursor.execute(
                "INSERT INTO transactions (user_id, kind, amount, related_session_id, note) VALUES (%s, 'earned', 1, %s, 'Teaching reward')",
                (item["mentor_id"], session_id),
            )
            cursor.execute("UPDATE users SET sessions_completed = sessions_completed + 1 WHERE id IN (%s, %s)", (item["learner_id"], item["mentor_id"]))
        award_badges(item["learner_id"])
        award_badges(item["mentor_id"])
        create_notification(item["mentor_id"], "credit", "You earned one time credit. Nicely taught!")
        create_notification(item["mentor_id"], "badge", "Session complete — your teaching achievements were updated.")
        create_notification(item["learner_id"], "badge", "Session complete — your learning achievements were updated.")
        return jsonify({
            "message": "Quest complete — credits transferred!",
            "reward": True,
            "needsRating": True,
            "ratingSession": {
                "sessionId": item["id"],
                "mentorId": item["mentor_id"],
            },
        })
    if action not in transitions:
        return jsonify({"error": "Unknown session action."}), 404
    next_status, owner_field, notice = transitions[action]
    with get_db().cursor() as cursor:
        cursor.execute("SELECT * FROM sessions WHERE id = %s FOR UPDATE", (session_id,))
        item = cursor.fetchone()
        if not item or item[owner_field] != session["user_id"]:
            return jsonify({"error": "You cannot manage this session."}), 403
        allowed = {"accept": {"pending"}, "decline": {"pending"}, "schedule": {"accepted"}, "complete": {"accepted", "scheduled"}}
        if item["status"] not in allowed[action]:
            return jsonify({"error": "This session has already changed. Refresh and try again."}), 409
        if action == "complete" and (not item.get("learner_joined_at") or not item.get("mentor_joined_at")):
            return jsonify({"error": "Both learner and mentor must open the meeting link before finishing."}), 400
        cursor.execute("UPDATE sessions SET status = %s WHERE id = %s", (next_status, session_id))
    if action in {"accept", "schedule"}:
        schedule_session_reminders(item)
    create_notification(item["learner_id"], "session", notice)
    award_badges(item["learner_id"])
    award_badges(item["mentor_id"])
    return jsonify({"message": notice})


def session_for_quiz(session_id: int):
    with get_db().cursor() as cursor:
        suffix = " FOR UPDATE" if request.method == "POST" else ""
        cursor.execute("SELECT * FROM sessions WHERE id = %s" + suffix, (session_id,))
        return cursor.fetchone()


@app.post("/api/sessions/<int:session_id>/quiz")
@api_login_required
@atomic
def api_save_session_quiz(session_id):
    item = session_for_quiz(session_id)
    if not item or item["mentor_id"] != session["user_id"]:
        return jsonify({"error": "Only the mentor can create a quiz for this session."}), 403
    if item["status"] not in {"pending", "accepted", "scheduled"}:
        return jsonify({"error": "Quiz can only be attached before the session is finished."}), 400
    payload = request.get_json(silent=True) or {}
    questions = payload.get("questions") or []
    if not isinstance(questions, list) or not 5 <= len(questions) <= 10:
        return jsonify({"error": "Create between 5 and 10 MCQ questions."}), 400
    clean_questions = []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            return jsonify({"error": "Each question must contain text and options."}), 400
        text = str(question.get("question") or "").strip()
        options = question.get("options") or []
        correct = str(question.get("correct") or "").upper().strip()
        if correct in {"0", "1", "2", "3"}:
            correct = "ABCD"[int(correct)]
        if not text or not isinstance(options, list) or len(options) != 4 or correct not in {"A", "B", "C", "D"}:
            return jsonify({"error": f"Question {index} needs text, 4 options, and a correct answer."}), 400
        clean_options = [str(option or "").strip() for option in options]
        if any(not option for option in clean_options):
            return jsonify({"error": f"Question {index} has an empty option."}), 400
        clean_questions.append((text, *clean_options, correct))
    with get_db().cursor() as cursor:
        cursor.execute("DELETE FROM session_quiz_attempts WHERE session_id = %s", (session_id,))
        cursor.execute("DELETE FROM session_quiz_questions WHERE session_id = %s", (session_id,))
        cursor.executemany(
            """INSERT INTO session_quiz_questions
               (session_id, question, option_a, option_b, option_c, option_d, correct_option)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [(session_id, *question) for question in clean_questions],
        )
    create_notification(item["learner_id"], "session", "Your mentor attached a quiz to your upcoming session.")
    return jsonify({"message": "Quiz attached to this session.", "questionCount": len(clean_questions)})


@app.get("/api/sessions/<int:session_id>/quiz")
@api_login_required
def api_get_session_quiz(session_id):
    item = session_for_quiz(session_id)
    if not item or session["user_id"] not in {item["learner_id"], item["mentor_id"]}:
        return jsonify({"error": "Quiz not found."}), 404
    is_mentor = item["mentor_id"] == session["user_id"]
    with get_db().cursor() as cursor:
        cursor.execute(
            """SELECT id, question, option_a, option_b, option_c, option_d, correct_option
               FROM session_quiz_questions
               WHERE session_id = %s
               ORDER BY id""",
            (session_id,),
        )
        rows = cursor.fetchall()
        cursor.execute(
            "SELECT score, total, submitted_at, answers_json FROM session_quiz_attempts WHERE session_id = %s AND learner_id = %s",
            (session_id, item["learner_id"]),
        )
        attempt = cursor.fetchone()
        if attempt:
            attempt["answers"] = json.loads(attempt.pop("answers_json") or "{}")
    questions = []
    for row in rows:
        payload = {
            "id": row["id"],
            "question": row["question"],
            "options": [row["option_a"], row["option_b"], row["option_c"], row["option_d"]],
        }
        if is_mentor or attempt:
            payload["correct"] = row["correct_option"]
        questions.append(payload)
    return jsonify({"questions": questions, "attempt": attempt, "canSubmit": item["learner_id"] == session["user_id"] and not attempt and item["status"] in {"awaiting_confirmation", "completed"}})


@app.post("/api/sessions/<int:session_id>/quiz/submit")
@api_login_required
@atomic
def api_submit_session_quiz(session_id):
    item = session_for_quiz(session_id)
    if not item or item["learner_id"] != session["user_id"]:
        return jsonify({"error": "Only the learner can submit this quiz."}), 403
    if item["status"] not in {"awaiting_confirmation", "completed"}:
        return jsonify({"error": "Quiz unlocks at the end of the session."}), 400
    answers = (request.get_json(silent=True) or {}).get("answers") or {}
    if not isinstance(answers, dict):
        return jsonify({"error": "Choose an answer for every question."}), 400
    with get_db().cursor() as cursor:
        cursor.execute(
            """SELECT id, correct_option FROM session_quiz_questions
               WHERE session_id = %s ORDER BY id""",
            (session_id,),
        )
        questions = cursor.fetchall()
        if not questions:
            return jsonify({"error": "No quiz has been attached to this session yet."}), 404
        cursor.execute("SELECT id FROM session_quiz_attempts WHERE session_id = %s AND learner_id = %s", (session_id, session["user_id"]))
        if cursor.fetchone():
            return jsonify({"error": "You already submitted this quiz."}), 400
        score = 0
        normalized_answers = {}
        for question in questions:
            answer = str(answers.get(str(question["id"])) or answers.get(question["id"]) or "").upper().strip()
            if answer in {"0", "1", "2", "3"}:
                answer = "ABCD"[int(answer)]
            if answer not in {"A", "B", "C", "D"}:
                return jsonify({"error": "Answer every question before submitting."}), 400
            normalized_answers[str(question["id"])] = answer
            if answer == question["correct_option"]:
                score += 1
        total = len(questions)
        cursor.execute(
            """INSERT INTO session_quiz_attempts (session_id, learner_id, score, total, answers_json)
               VALUES (%s, %s, %s, %s, %s)""",
            (session_id, session["user_id"], score, total, json.dumps(normalized_answers)),
        )
    create_notification(item["mentor_id"], "session", f"Your learner submitted the quiz: {score}/{total}.")
    return jsonify({"message": f"Quiz submitted! Score: {score}/{total}", "score": score, "total": total})


@app.post("/api/sessions/<int:session_id>/rating")
@api_login_required
def api_rate_session(session_id):
    payload = request.get_json(silent=True) or {}
    try:
        score = int(payload.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    feedback = (payload.get("feedback") or "").strip()
    if score < 1 or score > 5:
        return jsonify({"error": "Choose a rating from 1 to 5 stars."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        item = cursor.fetchone()
        if not item or item["learner_id"] != session["user_id"] or item["status"] != "completed":
            return jsonify({"error": "Only the learner can rate a completed session."}), 403
        cursor.execute(
            "SELECT id FROM ratings WHERE session_id = %s AND rater_id = %s AND ratee_id = %s",
            (session_id, session["user_id"], item["mentor_id"]),
        )
        existing_rating = cursor.fetchone()
        if existing_rating:
            cursor.execute("UPDATE ratings SET score = %s WHERE id = %s", (score, existing_rating["id"]))
        else:
            cursor.execute(
                "INSERT INTO ratings (session_id, rater_id, ratee_id, score) VALUES (%s, %s, %s, %s)",
                (session_id, session["user_id"], item["mentor_id"], score),
            )
        cursor.execute(
            "SELECT id FROM reviews WHERE session_id = %s AND reviewer_id = %s AND reviewee_id = %s",
            (session_id, session["user_id"], item["mentor_id"]),
        )
        existing_review = cursor.fetchone()
        if feedback:
            if existing_review:
                cursor.execute("UPDATE reviews SET content = %s WHERE id = %s", (feedback, existing_review["id"]))
            else:
                cursor.execute(
                    "INSERT INTO reviews (session_id, reviewer_id, reviewee_id, content) VALUES (%s, %s, %s, %s)",
                    (session_id, session["user_id"], item["mentor_id"], feedback),
                )
        elif existing_review:
            cursor.execute("DELETE FROM reviews WHERE id = %s", (existing_review["id"],))
        create_notification(item["mentor_id"], "rating", f"You received a {score}-star rating.")
    return jsonify({"message": "Thanks — your mentor rating was saved."})


@app.get("/api/messages/<int:contact_id>")
@api_login_required
def api_message_thread(contact_id):
    encrypted_select = "m.encrypted_content" if db_column_exists("messages", "encrypted_content") else "NULL AS encrypted_content"
    with get_db().cursor() as cursor:
        cursor.execute(
            f"""SELECT m.id, m.sender_id, m.receiver_id, m.content, m.is_read, {encrypted_select},
                      DATE_FORMAT(m.edited_at, '%%Y-%%m-%%dT%%H:%%i:%%s+05:30') AS edited_at,
                      DATE_FORMAT(m.created_at, '%%Y-%%m-%%dT%%H:%%i:%%s+05:30') AS created_at
               FROM messages m
               WHERE ((m.sender_id = %s AND m.receiver_id = %s)
                  OR (m.sender_id = %s AND m.receiver_id = %s))
                 AND m.deleted_for_everyone = 0
                 AND NOT (m.sender_id = %s AND m.deleted_for_sender = 1)
                 AND NOT (m.receiver_id = %s AND m.deleted_for_receiver = 1)
               ORDER BY m.created_at, m.id""",
            (session["user_id"], contact_id, contact_id, session["user_id"], session["user_id"], session["user_id"]),
        )
        items = cursor.fetchall()
        cursor.execute("UPDATE messages SET is_read = 1 WHERE sender_id = %s AND receiver_id = %s", (contact_id, session["user_id"]))
        cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s AND type = 'message'", (session["user_id"],))
    return jsonify({"messages": items})


@app.get("/api/messages/key/<int:contact_id>")
@api_login_required
def api_message_public_key(contact_id):
    with get_db().cursor() as cursor:
        cursor.execute("SELECT id, username, public_key FROM users WHERE id = %s AND is_active = 1", (contact_id,))
        user = cursor.fetchone()
    if not user:
        return jsonify({"error": "User not found."}), 404
    return jsonify({"id": user["id"], "username": user["username"], "publicKey": user.get("public_key")})


@app.post("/api/messages/public-key")
@api_login_required
def api_save_public_key():
    public_key = (request.get_json(silent=True) or {}).get("publicKey")
    if not isinstance(public_key, dict) or not public_key.get("x") or not public_key.get("y"):
        return jsonify({"error": "Valid public key required."}), 400
    with get_db().cursor() as cursor:
        cursor.execute("UPDATE users SET public_key = %s WHERE id = %s", (json.dumps(public_key), session["user_id"]))
    return jsonify({"message": "Message encryption key saved."})


@app.post("/api/messages/<int:contact_id>")
@api_login_required
def api_send_message(contact_id):
    payload = request.get_json(silent=True) or {}
    content = payload.get("content", "").strip()
    encrypted_content = payload.get("encryptedContent")
    if not content:
        return jsonify({"error": "Write a message first."}), 400
    stored_content = content
    encrypted_json = None
    if isinstance(encrypted_content, dict):
        encrypted_json = json.dumps(encrypted_content)
        stored_content = "[encrypted message]"
    else:
        return jsonify({"error": "Message encryption is required. Ask the other user to open GotTime once, then try again."}), 400
    with get_db().cursor() as cursor:
        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, content, encrypted_content) VALUES (%s, %s, %s, %s)",
            (session["user_id"], contact_id, stored_content, encrypted_json),
        )
    create_notification(contact_id, "message", "You have a new message.")
    award_badges(session["user_id"])
    return jsonify({"message": "Sent."}), 201


@app.patch("/api/messages/item/<int:message_id>")
@api_login_required
def api_edit_message(message_id):
    payload = request.get_json(silent=True) or {}
    content = payload.get("content", "").strip()
    encrypted_content = payload.get("encryptedContent")
    if not content:
        return jsonify({"error": "Message cannot be empty."}), 400
    stored_content = content
    encrypted_json = None
    if isinstance(encrypted_content, dict):
        encrypted_json = json.dumps(encrypted_content)
        stored_content = "[encrypted message]"
    else:
        return jsonify({"error": "Message encryption is required. Ask the other user to open GotTime once, then try again."}), 400
    with get_db().cursor() as cursor:
        cursor.execute(
            """SELECT id FROM messages
               WHERE id = %s AND sender_id = %s AND deleted_for_everyone = 0""",
            (message_id, session["user_id"]),
        )
        if not cursor.fetchone():
            return jsonify({"error": "You can only edit your own message."}), 403
        cursor.execute(
            "UPDATE messages SET content = %s, encrypted_content = %s, edited_at = CURRENT_TIMESTAMP WHERE id = %s AND is_read = 0 AND deleted_for_everyone = 0",
            (stored_content, encrypted_json, message_id),
        )
        if cursor.rowcount != 1:
            return jsonify({"error": "Seen or deleted messages cannot be edited."}), 409
    return jsonify({"message": "Message edited."})


@app.post("/api/messages/item/<int:message_id>/encrypt")
@api_login_required
def api_encrypt_legacy_message(message_id):
    encrypted_content = (request.get_json(silent=True) or {}).get("encryptedContent")
    if not isinstance(encrypted_content, dict):
        return jsonify({"error": "Encrypted message payload required."}), 400
    with get_db().cursor() as cursor:
        cursor.execute(
            """SELECT sender_id, receiver_id
               FROM messages
               WHERE id = %s AND deleted_for_everyone = 0""",
            (message_id,),
        )
        item = cursor.fetchone()
        if not item or session["user_id"] not in {item["sender_id"], item["receiver_id"]}:
            return jsonify({"error": "Message not found."}), 404
        cursor.execute(
            """UPDATE messages SET content = '[encrypted message]', encrypted_content = %s
               WHERE id = %s AND encrypted_content IS NULL AND deleted_for_everyone = 0""",
            (json.dumps(encrypted_content), message_id),
        )
        if cursor.rowcount != 1:
            return jsonify({"error": "Message changed. Reload the conversation."}), 409
    return jsonify({"message": "Legacy message encrypted."})


@app.post("/api/messages/item/<int:message_id>/recover")
@api_login_required
def api_recover_message(message_id):
    payload = request.get_json(silent=True) or {}
    copy = payload.get("recoveryCopy")
    expected = payload.get("expectedEncryptedContent")
    if not isinstance(copy, dict) or not isinstance(expected, str):
        return jsonify({"error": "Encrypted recovery copy required."}), 400
    # Accept ciphertext only; never store the readable text or private keys.
    required = {"recipientId", "senderPublicKey", "receiverPublicKey", "iv", "data"}
    if set(copy) != required:
        return jsonify({"error": "Invalid recovery copy."}), 400
    for key_name in ("senderPublicKey", "receiverPublicKey"):
        key = copy[key_name]
        if (not isinstance(key, dict) or "d" in key or key.get("kty") != "EC"
                or key.get("crv") != "P-256" or not isinstance(key.get("x"), str)
                or not isinstance(key.get("y"), str) or len(key["x"]) != 43 or len(key["y"]) != 43):
            return jsonify({"error": "Valid public keys required."}), 400
    try:
        if not isinstance(copy["data"], str) or len(copy["data"]) > 60000:
            raise ValueError()
        if len(base64.b64decode(copy["iv"], validate=True)) != 12 or len(base64.b64decode(copy["data"], validate=True)) < 16:
            raise ValueError()
    except (TypeError, ValueError, binascii.Error):
        return jsonify({"error": "Invalid encrypted message data."}), 400
    with get_db().cursor() as cursor:
        cursor.execute(
            """SELECT sender_id, receiver_id, encrypted_content FROM messages
               WHERE id = %s AND deleted_for_everyone = 0
               AND ((sender_id = %s AND deleted_for_sender = 0)
                 OR (receiver_id = %s AND deleted_for_receiver = 0))""",
            (message_id, session["user_id"], session["user_id"]),
        )
        item = cursor.fetchone()
        if not item:
            return jsonify({"error": "Message not found."}), 404
        peer_id = item["receiver_id"] if item["sender_id"] == session["user_id"] else item["sender_id"]
        if copy["recipientId"] != peer_id:
            return jsonify({"error": "Recovery is only available to the other participant."}), 403
        if item["encrypted_content"] != expected:
            return jsonify({"error": "Message changed. Reload before recovering."}), 409
        cursor.execute("SELECT id, public_key FROM users WHERE id IN (%s, %s)", (session["user_id"], peer_id))
        registered = {row["id"]: json.loads(row["public_key"] or "null") for row in cursor.fetchall()}
        for uid, key_name in ((session["user_id"], "senderPublicKey"), (peer_id, "receiverPublicKey")):
            key = registered.get(uid) or {}
            if any(key.get(part) != copy[key_name].get(part) for part in ("kty", "crv", "x", "y")):
                return jsonify({"error": "Message keys changed. Reload before recovering."}), 409
        try:
            encrypted = json.loads(expected)
            if not isinstance(encrypted, dict) or not isinstance(encrypted.get("copies"), dict):
                raise ValueError()
            recovered = encrypted.setdefault("recoveryCopies", {})
            if not isinstance(recovered, dict):
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify({"error": "Original encrypted message is invalid."}), 400
        public = copy["receiverPublicKey"]
        copy_id = f"{peer_id}:{public['x']}.{public['y']}"
        if copy_id in recovered:
            return jsonify({"message": "Recovery copy already exists."})
        if len(recovered) >= 20:
            return jsonify({"error": "Recovery device limit reached."}), 409
        recovered[copy_id] = copy
        # Compare-and-swap protects against concurrent edits, deletions and recovery.
        cursor.execute(
            """UPDATE messages SET encrypted_content = %s
               WHERE id = %s AND encrypted_content = %s AND deleted_for_everyone = 0
               AND ((sender_id = %s AND deleted_for_sender = 0)
                 OR (receiver_id = %s AND deleted_for_receiver = 0))""",
            (json.dumps(encrypted), message_id, expected, session["user_id"], session["user_id"]),
        )
        if cursor.rowcount != 1:
            return jsonify({"error": "Message changed. Reload before recovering."}), 409
    return jsonify({"message": "Encrypted recovery copy saved."})


@app.delete("/api/messages/item/<int:message_id>")
@api_login_required
def api_delete_message(message_id):
    scope = (request.get_json(silent=True) or {}).get("scope", "me")
    with get_db().cursor() as cursor:
        cursor.execute("SELECT sender_id, receiver_id FROM messages WHERE id = %s", (message_id,))
        item = cursor.fetchone()
        if not item or session["user_id"] not in {item["sender_id"], item["receiver_id"]}:
            return jsonify({"error": "Message not found."}), 404
        if scope == "everyone":
            if item["sender_id"] != session["user_id"]:
                return jsonify({"error": "Only the sender can delete for everyone."}), 403
            cursor.execute("UPDATE messages SET deleted_for_everyone = 1 WHERE id = %s", (message_id,))
            return jsonify({"message": "Message deleted for everyone."})
        if item["sender_id"] == session["user_id"]:
            cursor.execute("UPDATE messages SET deleted_for_sender = 1 WHERE id = %s", (message_id,))
        else:
            cursor.execute("UPDATE messages SET deleted_for_receiver = 1 WHERE id = %s", (message_id,))
    return jsonify({"message": "Message deleted for you."})


@app.post("/api/notifications/read")
@api_login_required
def api_read_notifications():
    with get_db().cursor() as cursor:
        cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s", (session["user_id"],))
    return jsonify({"message": "Notifications cleared."})


@app.before_request
def retired_session_forms():
    if request.method == "POST" and (request.path == "/sessions" or request.path.startswith("/sessions/")):
        return jsonify({"error": "Use the current GotTime app to manage sessions."}), 410


@app.errorhandler(pymysql.err.IntegrityError)
def database_conflict(error):
    if error.args and error.args[0] == 1062:
        return jsonify({"error": "This record already exists. Refresh and try again."}), 409
    app.logger.exception("Database integrity error")
    return jsonify({"error": "That change could not be saved."}), 400


if os.getenv("GOTTIME_AUTO_MIGRATE", "1") == "1":
    with app.app_context():
        initialize_database()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", host="0.0.0.0", port=5000)
