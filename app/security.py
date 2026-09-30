import hmac
import secrets

from flask import abort, request, session


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf() -> None:
    """Prefer a request header so multipart bodies are not parsed just for CSRF."""
    submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    stored = session.get("_csrf_token", "")
    if not stored or not hmac.compare_digest(stored, submitted):
        abort(400)
