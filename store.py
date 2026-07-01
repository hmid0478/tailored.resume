"""Persistent storage layer.

Two backends behind one interface:

  * RedisStore     — Upstash Redis over its stateless HTTPS REST API. This is the
                     production backend on Vercel (serverless can't keep DB sockets
                     open, and Upstash REST is a plain request/response per command).
  * JSONFileStore  — a local JSON file. Used automatically when no Redis creds are
                     present, so the app runs locally with zero setup. On Vercel this
                     falls back to /tmp (ephemeral — fine for a quick look, NOT for
                     real multi-user use), so set the Upstash env vars in production.

Selection is automatic (see `get_store`). Set either the Upstash Marketplace vars
(UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN) or the Vercel KV vars
(KV_REST_API_URL / KV_REST_API_TOKEN) to activate Redis.

Data model
----------
  user:{email}     -> JSON  {email, password_hash, created_at}
  users            -> SET   of lowercased emails
  resumes:{email}  -> HASH  {resume_id: JSON resume record}

Emails are always normalized to lowercase + stripped before use as keys.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone

import requests


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ─────────────────────────────────────────────
# Upstash Redis (REST) backend
# ─────────────────────────────────────────────

class RedisStore:
    """Talks to Upstash Redis via its REST API (one HTTPS call per command)."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _cmd(self, *args):
        # Upstash accepts the command as a JSON array in the POST body and
        # returns {"result": ...} (or {"error": ...}).
        resp = self._session.post(self.url, json=[str(a) for a in args], timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(f"Upstash error: {payload['error']}")
        return payload.get("result") if isinstance(payload, dict) else None

    # ── users ──
    def get_user(self, email: str):
        email = _norm_email(email)
        raw = self._cmd("GET", f"user:{email}")
        if not raw:
            return None
        return json.loads(raw)

    def create_user(self, email: str, password_hash: str) -> dict:
        email = _norm_email(email)
        if self.get_user(email):
            raise ValueError("A user with that email already exists.")
        record = {"email": email, "password_hash": password_hash, "created_at": _now_iso()}
        self._cmd("SET", f"user:{email}", json.dumps(record))
        self._cmd("SADD", "users", email)
        return record

    def list_users(self) -> list:
        emails = self._cmd("SMEMBERS", "users") or []
        out = []
        for e in emails:
            u = self.get_user(e)
            if u:
                out.append({"email": u["email"], "created_at": u.get("created_at", "")})
        out.sort(key=lambda u: u.get("created_at", ""))
        return out

    def delete_user(self, email: str) -> bool:
        email = _norm_email(email)
        existed = bool(self.get_user(email))
        self._cmd("DEL", f"user:{email}")
        self._cmd("SREM", "users", email)
        self._cmd("DEL", f"resumes:{email}")
        return existed

    # ── resumes ──
    def add_resume(self, email: str, record: dict) -> dict:
        email = _norm_email(email)
        rid = record.get("id") or _new_id()
        record["id"] = rid
        record.setdefault("created_at", _now_iso())
        self._cmd("HSET", f"resumes:{email}", rid, json.dumps(record))
        return record

    def list_resumes(self, email: str) -> list:
        email = _norm_email(email)
        flat = self._cmd("HGETALL", f"resumes:{email}") or []
        # HGETALL returns [field1, val1, field2, val2, ...]
        out = []
        for i in range(0, len(flat) - 1, 2):
            try:
                out.append(json.loads(flat[i + 1]))
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return out

    def get_resume(self, email: str, resume_id: str):
        email = _norm_email(email)
        raw = self._cmd("HGET", f"resumes:{email}", resume_id)
        if not raw:
            return None
        return json.loads(raw)

    def delete_resume(self, email: str, resume_id: str) -> bool:
        email = _norm_email(email)
        removed = self._cmd("HDEL", f"resumes:{email}", resume_id)
        return bool(removed)


# ─────────────────────────────────────────────
# Local JSON-file backend (dev / fallback)
# ─────────────────────────────────────────────

class JSONFileStore:
    """A tiny file-backed store. Not for production scale, but perfect for local dev.

    Every operation reads+writes the whole file under a lock. Fine for a handful of
    users; do not use as the Vercel backend (see module docstring).
    """

    def __init__(self, path: str | None = None):
        if path is None:
            if os.environ.get("VERCEL"):
                path = "/tmp/rt_store.json"
            else:
                base = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".data")
                os.makedirs(base, exist_ok=True)
                path = os.path.join(base, "rt_store.json")
        self.path = path
        self._lock = threading.Lock()
        if not os.path.exists(self.path):
            self._write({"users": {}, "resumes": {}})

    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            data = {}
        data.setdefault("users", {})
        data.setdefault("resumes", {})
        return data

    def _write(self, data: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ── users ──
    def get_user(self, email: str):
        email = _norm_email(email)
        with self._lock:
            return self._read()["users"].get(email)

    def create_user(self, email: str, password_hash: str) -> dict:
        email = _norm_email(email)
        with self._lock:
            data = self._read()
            if email in data["users"]:
                raise ValueError("A user with that email already exists.")
            record = {"email": email, "password_hash": password_hash, "created_at": _now_iso()}
            data["users"][email] = record
            self._write(data)
            return record

    def list_users(self) -> list:
        with self._lock:
            users = self._read()["users"].values()
        out = [{"email": u["email"], "created_at": u.get("created_at", "")} for u in users]
        out.sort(key=lambda u: u.get("created_at", ""))
        return out

    def delete_user(self, email: str) -> bool:
        email = _norm_email(email)
        with self._lock:
            data = self._read()
            existed = email in data["users"]
            data["users"].pop(email, None)
            data["resumes"].pop(email, None)
            self._write(data)
            return existed

    # ── resumes ──
    def add_resume(self, email: str, record: dict) -> dict:
        email = _norm_email(email)
        rid = record.get("id") or _new_id()
        record["id"] = rid
        record.setdefault("created_at", _now_iso())
        with self._lock:
            data = self._read()
            data["resumes"].setdefault(email, {})[rid] = record
            self._write(data)
        return record

    def list_resumes(self, email: str) -> list:
        email = _norm_email(email)
        with self._lock:
            bucket = self._read()["resumes"].get(email, {})
        out = list(bucket.values())
        out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return out

    def get_resume(self, email: str, resume_id: str):
        email = _norm_email(email)
        with self._lock:
            return self._read()["resumes"].get(email, {}).get(resume_id)

    def delete_resume(self, email: str, resume_id: str) -> bool:
        email = _norm_email(email)
        with self._lock:
            data = self._read()
            bucket = data["resumes"].get(email, {})
            existed = resume_id in bucket
            bucket.pop(resume_id, None)
            self._write(data)
            return existed


# ─────────────────────────────────────────────
# Backend selection
# ─────────────────────────────────────────────

_STORE = None


def get_store():
    """Return a singleton store, choosing Redis if creds are present, else local file."""
    global _STORE
    if _STORE is not None:
        return _STORE

    url = (os.environ.get("UPSTASH_REDIS_REST_URL")
           or os.environ.get("KV_REST_API_URL"))
    token = (os.environ.get("UPSTASH_REDIS_REST_TOKEN")
             or os.environ.get("KV_REST_API_TOKEN"))

    if url and token:
        _STORE = RedisStore(url, token)
        print("[store] Using Upstash Redis backend.")
    else:
        _STORE = JSONFileStore()
        where = "/tmp (ephemeral!)" if os.environ.get("VERCEL") else _STORE.path
        print(f"[store] Using local JSON file backend at {where}. "
              f"Set UPSTASH_REDIS_REST_URL/TOKEN for persistent multi-user storage.")
    return _STORE
