"""Start the Flask panel using Botkeep's assigned HTTP port."""
import os

port = os.environ.get("SERVER_PORT") or os.environ.get("PORT") or "8080"
os.execvp(
    "gunicorn",
    [
        "gunicorn",
        "app:app",
        "--bind",
        f"0.0.0.0:{port}",
        "--workers",
        "1",
        "--threads",
        "4",
        "--timeout",
        "120",
    ],
)
