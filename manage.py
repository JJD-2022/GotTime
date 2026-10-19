"""Explicit schema maintenance and scheduled reminder/missed-session processing."""
import os
import argparse

os.environ["GOTTIME_AUTO_MIGRATE"] = "0"
from app import app, initialize_database, get_db, dispatch_due_session_reminders, mark_missed_sessions


def add_indexes():
    indexes = {
        "sessions": {"ix_learner_status_time": "learner_id, status, scheduled_for", "ix_mentor_status_time": "mentor_id, status, scheduled_for"},
        "messages": {"ix_thread": "sender_id, receiver_id, id", "ix_inbox": "receiver_id, is_read, sender_id"},
        "notifications": {"ix_user_created": "user_id, created_at"},
        "user_skills": {"ix_user_skill": "user_id, skill_id"},
        "session_reminders": {"ix_due_user": "user_id, sent_at, scheduled_at", "ix_due": "sent_at, scheduled_at"},
    }
    with get_db().cursor() as cursor:
        for table, entries in indexes.items():
            cursor.execute(f"SHOW INDEX FROM {table}")
            existing = {row["Key_name"] for row in cursor.fetchall()}
            for name, columns in entries.items():
                if name not in existing:
                    cursor.execute(f"CREATE INDEX {name} ON {table} ({columns})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "jobs"])
    args = parser.parse_args()
    with app.app_context():
        if args.command == "init":
            initialize_database()
            add_indexes()
        else:
            dispatch_due_session_reminders()
            mark_missed_sessions()
