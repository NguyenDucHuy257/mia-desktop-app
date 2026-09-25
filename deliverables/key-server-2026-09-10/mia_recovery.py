"""MIA contact verification and local-password recovery for the shared key server."""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
from contextlib import closing
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

BASE_DIR = Path(__file__).resolve().parent
EMAIL_RE = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$", re.I)
CODE_RE = re.compile(r"^[0-9]{6}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class RecoveryError(RuntimeError):
    def __init__(self, code: str, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(code)


def normalize_email(value: str) -> str:
    email = str(value or "").strip().casefold()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise RecoveryError("invalid_email")
    return email


def mask_email(value: str) -> str:
    local, domain = value.split("@", 1)
    visible = local[:1]
    return f"{visible}{'*' * max(3, len(local) - 1)}@{domain}"


class RecoveryService:
    def __init__(
        self,
        database: Path | None = None,
        *,
        secret: str | None = None,
        clock: Callable[[], float] = time.time,
        send_mail: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database or Path(os.getenv(
            "MIA_RECOVERY_DB", str(BASE_DIR / "MIA" / "recovery" / "recovery.sqlite3")
        ))
        self.secret = (secret or os.getenv("MIA_RECOVERY_SECRET", "")).encode("utf-8")
        if len(self.secret) < 32:
            raise RecoveryError("recovery_not_configured", 503)
        self.clock = clock
        self.send_mail = send_mail or self._smtp_send
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recovery_bindings (
                    device_hash TEXT PRIMARY KEY, email TEXT NOT NULL,
                    verified_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_challenges (
                    challenge_id TEXT PRIMARY KEY, device_hash TEXT NOT NULL,
                    purpose TEXT NOT NULL, target_email TEXT NOT NULL,
                    code_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at INTEGER, requester_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS recovery_challenge_device_time
                    ON recovery_challenges(device_hash, created_at);
            """)
            db.commit()
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            pass

    def _digest(self, label: str, value: str) -> str:
        return hmac.new(self.secret, f"{label}|{value}".encode(), hashlib.sha256).hexdigest()

    def _device_hash(self, device_id: str) -> str:
        value = str(device_id or "").strip()
        if not value or len(value) > 128:
            raise RecoveryError("invalid_device")
        return self._digest("device", value)

    def _new_challenge(self, device_hash: str, email: str, purpose: str, requester: str) -> dict:
        now = int(self.clock())
        requester_hash = self._digest("requester", str(requester or "unknown"))
        with closing(self._connect()) as db:
            previous = db.execute(
                "SELECT created_at FROM recovery_challenges WHERE device_hash=? AND purpose=? ORDER BY created_at DESC LIMIT 1",
                (device_hash, purpose),
            ).fetchone()
            if previous and now - int(previous["created_at"]) < 60:
                raise RecoveryError("recovery_wait_before_resend", 429)
            recent = db.execute(
                "SELECT COUNT(*) AS value FROM recovery_challenges WHERE (device_hash=? OR requester_hash=?) AND created_at>=?",
                (device_hash, requester_hash, now - 3600),
            ).fetchone()["value"]
            if int(recent) >= 5:
                raise RecoveryError("recovery_rate_limited", 429)
            challenge_id = secrets.token_urlsafe(32)
            code = f"{secrets.randbelow(1_000_000):06d}"
            db.execute(
                "INSERT INTO recovery_challenges VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                (challenge_id, device_hash, purpose, email, self._digest(challenge_id, code), now, now + 600, 0, requester_hash),
            )
            db.commit()
        try:
            self.send_mail(email, code)
        except Exception as error:
            with closing(self._connect()) as db:
                db.execute("DELETE FROM recovery_challenges WHERE challenge_id=?", (challenge_id,))
                db.commit()
            raise RecoveryError("recovery_email_unavailable", 503) from error
        return {"challenge_id": challenge_id, "masked_email": mask_email(email), "expires_in": 600, "resend_after": 60}

    def request_contact(self, device_id: str, email: str, requester: str = "") -> dict:
        return self._new_challenge(self._device_hash(device_id), normalize_email(email), "contact", requester)

    def request_reset(self, device_id: str, requester: str = "") -> dict:
        device_hash = self._device_hash(device_id)
        with closing(self._connect()) as db:
            row = db.execute("SELECT email FROM recovery_bindings WHERE device_hash=?", (device_hash,)).fetchone()
        if not row:
            raise RecoveryError("recovery_email_not_registered", 409)
        return self._new_challenge(device_hash, row["email"], "password_reset", requester)

    def confirm(self, challenge_id: str, code: str, purpose: str, device_id: str) -> dict:
        if not CHALLENGE_RE.fullmatch(str(challenge_id or "")) or not CODE_RE.fullmatch(str(code or "")):
            raise RecoveryError("recovery_code_invalid")
        now = int(self.clock())
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM recovery_challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
            if not row or row["purpose"] != purpose or row["consumed_at"] is not None:
                raise RecoveryError("recovery_code_invalid")
            if not hmac.compare_digest(row["device_hash"], self._device_hash(device_id)):
                raise RecoveryError("recovery_code_invalid")
            if int(row["expires_at"]) < now:
                raise RecoveryError("recovery_code_expired")
            if int(row["attempts"]) >= 5:
                raise RecoveryError("recovery_code_locked", 429)
            if not hmac.compare_digest(row["code_hash"], self._digest(challenge_id, code)):
                db.execute("UPDATE recovery_challenges SET attempts=attempts+1 WHERE challenge_id=?", (challenge_id,))
                db.commit()
                raise RecoveryError("recovery_code_invalid")
            db.execute("UPDATE recovery_challenges SET consumed_at=? WHERE challenge_id=?", (now, challenge_id))
            if purpose == "contact":
                db.execute(
                    "INSERT INTO recovery_bindings(device_hash,email,verified_at,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(device_hash) DO UPDATE SET email=excluded.email, verified_at=excluded.verified_at, updated_at=excluded.updated_at",
                    (row["device_hash"], row["target_email"], now, now),
                )
            db.commit()
        return {"verified": True, "masked_email": mask_email(row["target_email"])}

    @staticmethod
    def _smtp_send(target: str, code: str) -> None:
        host = os.getenv("MIA_SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("MIA_SMTP_PORT", "465"))
        username = os.getenv("MIA_SMTP_USER", "").strip()
        password = os.getenv("MIA_SMTP_APP_PASSWORD", "").replace(" ", "")
        if not username or not password:
            raise RecoveryError("recovery_email_unavailable", 503)
        message = EmailMessage()
        message["Subject"] = f"{code} là mã xác nhận MIA TOOL 2026"
        message["From"] = f"MIA TOOL 2026 <{username}>"
        message["To"] = target
        message.set_content(f"Mã xác nhận MIA TOOL 2026 của bạn là: {code}. Mã có hiệu lực trong 10 phút. Nếu bạn không yêu cầu, hãy bỏ qua email này.")
        message.add_alternative(f"""<!doctype html><html><body style="margin:0;background:#f3f7f4;font-family:Arial,sans-serif;color:#173c29">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="560" style="max-width:100%;background:#fff;border-radius:16px;border:1px solid #dce8e0;box-shadow:0 8px 24px rgba(18,82,49,.08)">
<tr><td style="padding:30px"><div style="font-size:13px;font-weight:700;color:#168447">MIA TOOL 2026</div>
<h1 style="font-size:24px;margin:12px 0">Xác nhận khôi phục mật khẩu</h1><p style="color:#52685b;line-height:1.6">Nhập mã dưới đây trong ứng dụng. Mã có hiệu lực trong 10 phút.</p>
<div style="margin:24px 0;padding:18px;text-align:center;background:#eff8f2;border-radius:12px;font-size:34px;font-weight:800;letter-spacing:9px;color:#126433">{html.escape(code)}</div>
<p style="font-size:13px;color:#718078">MIA không bao giờ yêu cầu bạn gửi lại mã này. Nếu bạn không yêu cầu, hãy bỏ qua email.</p></td></tr></table>
</td></tr></table></body></html>""", subtype="html")
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=context)
                smtp.login(username, password)
                smtp.send_message(message)


_service: RecoveryService | None = None


def recovery_service() -> RecoveryService:
    global _service
    if _service is None:
        _service = RecoveryService()
    return _service
