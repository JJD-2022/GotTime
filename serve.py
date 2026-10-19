"""Run the built React app and Flask API with a bounded production worker pool."""
import os
os.environ.setdefault("GOTTIME_AUTO_MIGRATE", "0")
from app import app
from waitress import serve

if __name__ == "__main__":
    if app.config["SECRET_KEY"] == "dev-secret-key":
        raise SystemExit("Set a private SECRET_KEY before starting the production server.")
    serve(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")),
          threads=int(os.getenv("WEB_THREADS", "8")), connection_limit=200, channel_timeout=60)
