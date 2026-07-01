"""Authentication helpers: password hashing, signed bearer tokens, route guards.

Design notes
------------
* Stateless. Tokens are signed (itsdangerous) so there is no server-side session
  store to keep — which is exactly what we want on Vercel's serverless runtime.
* Passwords are hashed with Werkzeug's PBKDF2 (ships with Flask; no extra dep).
* The admin is a single fixed account (per product requirement). Credentials can be
  overridden with env vars for safety, but default to the values provided.

Set APP_SECRET (or SECRET_KEY) in production so tokens can't be forged. In local dev
a static fallback is used and a warning is printed.
"""

import os
from functools import wraps

from flask import jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

# Fixed admin credentials (overridable via env for deployment hygiene).
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "contact.hf3@gmail.com").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "@Uckhan@6435")

_SECRET = os.environ.get("APP_SECRET") or os.environ.get("SECRET_KEY")
if not _SECRET:
    _SECRET = "dev-insecure-secret-change-me"
    print("[auth] WARNING: APP_SECRET not set — using an insecure dev secret. "
          "Set APP_SECRET in production so auth tokens cannot be forged.")

TOKEN_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_serializer = URLSafeTimedSerializer(_SECRET, salt="resume-tailor-auth")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    return check_password_hash(password_hash, password or "")


def make_token(email: str, role: str) -> str:
    """role is 'user' or 'admin'."""
    return _serializer.dumps({"email": (email or "").strip().lower(), "role": role})


def parse_token(token: str):
    """Return the token payload dict, or None if invalid/expired."""
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return ""


def current_identity():
    """Parse the caller's bearer token into an identity dict, or None."""
    return parse_token(_bearer_token())


def _unauthorized(message: str):
    # The `auth: "required"` flag lets the frontend distinguish "log in again"
    # from ordinary errors and redirect to the login page.
    return jsonify({"error": message, "auth": "required"}), 401


def require_user(fn):
    """Guard: caller must present a valid user OR admin token."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ident = current_identity()
        if not ident or ident.get("role") not in ("user", "admin"):
            return _unauthorized("Please log in to continue.")
        request.identity = ident
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    """Guard: caller must present a valid admin token."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ident = current_identity()
        if not ident or ident.get("role") != "admin":
            return _unauthorized("Admin access required.")
        request.identity = ident
        return fn(*args, **kwargs)
    return wrapper
