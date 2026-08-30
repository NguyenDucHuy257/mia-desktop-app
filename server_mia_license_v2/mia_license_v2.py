from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .migration import LegacyRecord, parse_legacy_lines

TOOL = "MIA"
PHONE_RE = re.compile(r"^0[0-9]{9}$")
HASH29_RE = re.compile(r"^[0-9a-f]{29}$")
HASH64_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_ACTIONS = {"verify", "migrate", "activate", "recover", "update_phone"}
HARDWARE_FIELDS = {
    "system_uuid", "bios_serial", "baseboard_serial",
    "machine_guid", "cpu_id", "disk_serial",
}


class LicenseServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class MiaLicenseService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        token_secret: bytes,
        now: Callable[[], datetime] | None = None,
        challenge_ttl_seconds: int = 60,
        token_ttl_seconds: int = 30 * 24 * 60 * 60,
        offline_lease_seconds: int = 72 * 60 * 60,
    ):
        if not isinstance(token_secret, bytes) or len(token_secret) < 32:
            raise ValueError("token_secret must contain at least 32 bytes")
        self.database_path = Path(database_path)
        self.token_secret = token_secret
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self.token_ttl_seconds = token_ttl_seconds
        self.offline_lease_seconds = offline_lease_seconds
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_schema()

    def now(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize_schema(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connection() as connection:
            connection.executescript(schema)
            connection.commit()

    @staticmethod
    def _require_tool(value: Any) -> str:
        if str(value or "").strip().upper() != TOOL:
            raise LicenseServiceError("invalid_tool", "This service accepts tool=MIA only")
        return TOOL

    @staticmethod
    def _phone(value: Any, *, required: bool = False) -> str | None:
        normalized = re.sub(r"[\s.()-]+", "", str(value or "").strip())
        if not normalized:
            if required:
                raise LicenseServiceError("invalid_phone", "A valid phone is required")
            return None
        if not PHONE_RE.fullmatch(normalized) or normalized == "0000000000":
            raise LicenseServiceError("invalid_phone", "Phone must match the configured Vietnam policy")
        return normalized

    @staticmethod
    def _hardware(value: Any) -> dict[str, str]:
        source = value if isinstance(value, dict) else {}
        result = {
            str(name): str(signal).lower()
            for name, signal in source.items()
            if name in HARDWARE_FIELDS and HASH64_RE.fullmatch(str(signal).lower())
        }
        if len(result) < 3:
            raise LicenseServiceError("insufficient_hardware", "At least three hardware signals are required")
        return result

    @staticmethod
    def _fingerprint(public_key_pem: str) -> str:
        return hashlib.sha256(public_key_pem.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_public_key(public_key_pem: str) -> Ed25519PublicKey:
        try:
            key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        except Exception as exc:
            raise LicenseServiceError("invalid_public_key", "Invalid public key") from exc
        if not isinstance(key, Ed25519PublicKey):
            raise LicenseServiceError("invalid_public_key", "Ed25519 public key is required")
        return key

    def _audit(self, connection: sqlite3.Connection, event: str, result: str, *, license_id=None, device_id=None, details=None) -> None:
        safe_details = {k: v for k, v in dict(details or {}).items() if k in {
            "reason", "source", "matches", "baseline", "candidate_count", "schema",
        }}
        connection.execute(
            "INSERT INTO audit_log(tool,event,license_id,device_id,result,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (TOOL, event, license_id, device_id, result, json.dumps(safe_details, sort_keys=True), self.now().isoformat()),
        )

    def create_challenge(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_tool(request.get("tool"))
        action = str(request.get("action") or "").strip()
        if action not in ALLOWED_ACTIONS:
            raise LicenseServiceError("invalid_action", "Unsupported license action")
        public_key = str(request.get("public_key") or "")
        self._load_public_key(public_key)
        fingerprint = str(request.get("device_fingerprint") or "").lower()
        if not HASH64_RE.fullmatch(fingerprint) or fingerprint != self._fingerprint(public_key):
            raise LicenseServiceError("invalid_device_fingerprint", "Public key fingerprint mismatch")
        challenge_id = str(uuid.uuid4())
        challenge = _b64url(secrets.token_bytes(32))
        now = self.now()
        expires_at = now + timedelta(seconds=self.challenge_ttl_seconds)
        with self.connection() as connection, connection:
            connection.execute(
                "INSERT INTO challenges VALUES(?,?,?,?,?,?,?,?,?)",
                (challenge_id, TOOL, action, hashlib.sha256(challenge.encode()).hexdigest(), public_key,
                 fingerprint, expires_at.isoformat(), None, now.isoformat()),
            )
        return {"challenge_id": challenge_id, "challenge": challenge, "expires_in": self.challenge_ttl_seconds}

    def _consume_proof(self, connection: sqlite3.Connection, request: dict[str, Any], action: str) -> tuple[str, str]:
        challenge_id = str(request.get("challenge_id") or "")
        public_key = str(request.get("public_key") or "")
        fingerprint = str(request.get("device_fingerprint") or "").lower()
        signature = str(request.get("signature") or "")
        row = connection.execute(
            "SELECT * FROM challenges WHERE challenge_id=? AND tool=?", (challenge_id, TOOL),
        ).fetchone()
        if not row or row["consumed_at"]:
            raise LicenseServiceError("challenge_invalid", "Challenge is missing or already consumed", status=401)
        if row["action"] != action or row["public_key_pem"] != public_key or row["device_fingerprint"] != fingerprint:
            raise LicenseServiceError("challenge_mismatch", "Challenge proof does not match request", status=401)
        if datetime.fromisoformat(row["expires_at"]) < self.now():
            raise LicenseServiceError("challenge_expired", "Challenge expired", status=401)
        if fingerprint != self._fingerprint(public_key):
            raise LicenseServiceError("invalid_device_fingerprint", "Public key fingerprint mismatch", status=401)
        challenge = str(request.get("challenge") or "")
        if hashlib.sha256(challenge.encode()).hexdigest() != row["challenge_hash"]:
            raise LicenseServiceError("challenge_mismatch", "Challenge value mismatch", status=401)
        try:
            self._load_public_key(public_key).verify(base64.b64decode(signature, validate=True), challenge.encode())
        except (InvalidSignature, ValueError) as exc:
            raise LicenseServiceError("invalid_signature", "Device signature is invalid", status=401) from exc
        connection.execute("UPDATE challenges SET consumed_at=? WHERE challenge_id=?", (self.now().isoformat(), challenge_id))
        return public_key, fingerprint

    def import_legacy_records(self, lines: list[str], *, today: date | None = None) -> dict[str, int]:
        records = parse_legacy_lines(lines, today=today)
        counts: dict[str, int] = {}
        with self.connection() as connection, connection:
            for record in records:
                connection.execute(
                    """INSERT OR IGNORE INTO legacy_licenses
                    (tool,legacy_key_hash,raw_line,schema_name,machine_hash29,phone_guess,expires_at,parse_status,imported_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (TOOL, record.key_hash, record.raw_line, record.schema_name, record.machine_hash29,
                     record.phone_guess, record.expires_at, record.parse_status, self.now().isoformat()),
                )
                counts[record.schema_name] = counts.get(record.schema_name, 0) + 1
        return counts

    @staticmethod
    def _display_key(license_id: str) -> str:
        return "MIAV2-" + license_id.replace("-", "").upper()[:24]

    def _create_license_and_device(
        self, connection: sqlite3.Connection, *, public_key: str, fingerprint: str,
        device_id: str, hardware: dict[str, str], phone: str | None, phone_status: str,
        status: str, expires_at: str | None,
    ) -> tuple[str, str]:
        license_id = str(uuid.uuid4())
        now = self.now().isoformat()
        display_key = self._display_key(license_id)
        connection.execute(
            "INSERT INTO licenses VALUES(?,?,?,?,?,?,?,?,?)",
            (license_id, TOOL, display_key, phone, phone_status, status, expires_at, now, now),
        )
        connection.execute(
            "INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?)",
            (device_id, license_id, TOOL, public_key, fingerprint, json.dumps(hardware, sort_keys=True),
             "active" if status == "active" else "pending", now, now),
        )
        return license_id, display_key

    def _issue_token(self, connection: sqlite3.Connection, license_id: str, device_id: str) -> tuple[str, str, str]:
        now = self.now()
        token_id = str(uuid.uuid4())
        expires_at = now + timedelta(seconds=self.token_ttl_seconds)
        offline_until = now + timedelta(seconds=self.offline_lease_seconds)
        payload = {
            "v": 1, "tool": TOOL, "jti": token_id, "license_id": license_id,
            "device_id": device_id, "exp": int(expires_at.timestamp()),
            "offline_until": int(offline_until.timestamp()),
        }
        encoded = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64url(hmac.new(self.token_secret, encoded.encode(), hashlib.sha256).digest())
        token = encoded + "." + signature
        connection.execute(
            "INSERT INTO license_tokens VALUES(?,?,?,?,?,?,?,?,?)",
            (token_id, TOOL, license_id, device_id, hashlib.sha256(token.encode()).hexdigest(),
             expires_at.isoformat(), offline_until.isoformat(), None, now.isoformat()),
        )
        return token, expires_at.isoformat(), offline_until.isoformat()

    def _decode_token(self, token: str) -> dict[str, Any]:
        try:
            encoded, signature = token.split(".", 1)
            expected = _b64url(hmac.new(self.token_secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(_unb64url(encoded))
        except Exception as exc:
            raise LicenseServiceError("invalid_token", "License token is invalid", status=401) from exc
        if payload.get("tool") != TOOL:
            raise LicenseServiceError("invalid_token", "License token audience mismatch", status=401)
        if not isinstance(payload.get("exp"), int) or payload["exp"] < int(self.now().timestamp()):
            raise LicenseServiceError("token_expired", "License token expired", status=401)
        return payload

    def _active_response(self, connection: sqlite3.Connection, license_row, device_row, *, migrated=False, recovered=False) -> dict[str, Any]:
        token, token_expiry, offline_until = self._issue_token(connection, license_row["license_id"], device_row["device_id"])
        return {
            "valid": True, "migrated": migrated, "recovered": recovered,
            "license_id": license_row["license_id"], "canonical_key": license_row["display_key"],
            "device_id": device_row["device_id"], "phone": license_row["phone"],
            "phone_status": license_row["phone_status"], "expires_at": license_row["expires_at"],
            "license_token": token, "token_expires_at": token_expiry,
            "offline_valid_until": offline_until, "reason": "ok",
        }

    def activate(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_tool(request.get("tool"))
        phone = self._phone(request.get("phone"), required=True)
        hardware = self._hardware(request.get("hardware"))
        device_id = str(request.get("device_id") or "").strip()
        try:
            uuid.UUID(device_id)
        except ValueError as exc:
            raise LicenseServiceError("invalid_device_id", "device_id must be a UUID") from exc
        with self.connection() as connection, connection:
            public_key, fingerprint = self._consume_proof(connection, request, "activate")
            device = connection.execute("SELECT * FROM devices WHERE device_fingerprint=? AND tool=?", (fingerprint, TOOL)).fetchone()
            if device:
                license_row = connection.execute("SELECT * FROM licenses WHERE license_id=? AND tool=?", (device["license_id"], TOOL)).fetchone()
                if license_row["status"] == "active":
                    return self._active_response(connection, license_row, device)
                return {"valid": False, "license_id": license_row["license_id"], "canonical_key": license_row["display_key"], "device_id": device["device_id"], "phone_status": license_row["phone_status"], "reason": "key_not_activated"}
            if connection.execute("SELECT 1 FROM devices WHERE device_id=?", (device_id,)).fetchone():
                raise LicenseServiceError("device_id_conflict", "device_id is already bound", status=409)
            license_id, display_key = self._create_license_and_device(
                connection, public_key=public_key, fingerprint=fingerprint, device_id=device_id,
                hardware=hardware, phone=phone, phone_status="verified", status="pending", expires_at=None,
            )
            self._audit(connection, "activation_requested", "pending", license_id=license_id, device_id=device_id)
            return {"valid": False, "license_id": license_id, "canonical_key": display_key, "device_id": device_id, "phone_status": "verified", "reason": "key_not_activated"}

    def admin_activate(self, license_id: str, expires_at: str) -> None:
        expiry = date.fromisoformat(expires_at)
        if expiry < self.now().date():
            raise LicenseServiceError("invalid_expiry", "Activation expiry must not be in the past")
        with self.connection() as connection, connection:
            changed = connection.execute(
                "UPDATE licenses SET status='active',expires_at=?,updated_at=? WHERE license_id=? AND tool=?",
                (expiry.isoformat(), self.now().isoformat(), license_id, TOOL),
            ).rowcount
            if changed != 1:
                raise LicenseServiceError("license_not_found", "License does not exist", status=404)
            connection.execute("UPDATE devices SET status='active',updated_at=? WHERE license_id=? AND tool=?", (self.now().isoformat(), license_id, TOOL))
            self._audit(connection, "license_activated", "success", license_id=license_id)

    def migrate(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_tool(request.get("tool"))
        hardware = self._hardware(request.get("hardware"))
        phone = self._phone(request.get("phone"))
        device_id = str(request.get("device_id") or "").strip()
        try:
            uuid.UUID(device_id)
        except ValueError as exc:
            raise LicenseServiceError("invalid_device_id", "device_id must be a UUID") from exc
        exact_candidates = [str(value) for value in list(request.get("legacy_keys") or [])[:32]]
        hash_candidates = [str(value).lower() for value in list(request.get("legacy_hashes") or [])[:32] if HASH29_RE.fullmatch(str(value).lower())]
        exact_hashes = []
        for value in exact_candidates:
            embedded = re.fullmatch(r"(?:key|KEY)([0-9a-f]{29})(?:0[0-9]{9})?", value)
            if len(value) <= 160 and embedded and embedded.group(1) in hash_candidates:
                exact_hashes.append(hashlib.sha256(value.encode()).hexdigest())
        if not exact_hashes and not hash_candidates:
            return {"valid": False, "migrated": False, "reason": "no_legacy_match"}
        with self.connection() as connection, connection:
            public_key, fingerprint = self._consume_proof(connection, request, "migrate")
            existing_device = connection.execute("SELECT * FROM devices WHERE device_fingerprint=? AND tool=?", (fingerprint, TOOL)).fetchone()
            if existing_device:
                license_row = connection.execute("SELECT * FROM licenses WHERE license_id=? AND tool=?", (existing_device["license_id"], TOOL)).fetchone()
                if license_row and license_row["status"] == "active":
                    return self._active_response(connection, license_row, existing_device, migrated=True)
            if connection.execute("SELECT 1 FROM devices WHERE device_id=?", (device_id,)).fetchone():
                raise LicenseServiceError("device_id_conflict", "device_id is already bound", status=409)

            exact_rows = []
            if exact_hashes:
                placeholders = ",".join("?" for _ in exact_hashes)
                exact_rows = connection.execute(
                    f"SELECT * FROM legacy_licenses WHERE tool=? AND parse_status='ready' AND legacy_key_hash IN ({placeholders})",
                    (TOOL, *exact_hashes),
                ).fetchall()
            candidates = exact_rows
            source = "exact_legacy_key"
            if len(candidates) != 1 and hash_candidates:
                placeholders = ",".join("?" for _ in hash_candidates)
                candidates = connection.execute(
                    f"SELECT * FROM legacy_licenses WHERE tool=? AND parse_status='ready' AND machine_hash29 IN ({placeholders})",
                    (TOOL, *hash_candidates),
                ).fetchall()
                source = "unique_hash29"
            if len(candidates) == 0:
                return {"valid": False, "migrated": False, "reason": "no_legacy_match"}
            if len(candidates) != 1:
                self._audit(connection, "legacy_migration", "ambiguous", details={"candidate_count": len(candidates)})
                return {"valid": False, "migrated": False, "reason": "legacy_ambiguous"}
            legacy = candidates[0]
            previous = connection.execute("SELECT * FROM legacy_migrations WHERE legacy_id=?", (legacy["legacy_id"],)).fetchone()
            if previous:
                return {"valid": False, "migrated": False, "reason": "legacy_already_migrated"}
            legacy_phone = legacy["phone_guess"]
            if phone and legacy_phone and phone != legacy_phone:
                return {"valid": False, "migrated": False, "reason": "legacy_phone_mismatch"}
            selected_phone = phone or legacy_phone
            phone_status = "verified" if phone else ("legacy" if legacy_phone else "pending")
            license_id, display_key = self._create_license_and_device(
                connection, public_key=public_key, fingerprint=fingerprint, device_id=device_id,
                hardware=hardware, phone=selected_phone, phone_status=phone_status,
                status="active", expires_at=legacy["expires_at"],
            )
            connection.execute(
                "INSERT INTO legacy_migrations VALUES(?,?,?,?,?,?)",
                (legacy["legacy_id"], license_id, device_id, source, self.now().isoformat(), None),
            )
            license_row = connection.execute("SELECT * FROM licenses WHERE license_id=?", (license_id,)).fetchone()
            device_row = connection.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
            self._audit(connection, "legacy_migration", "success", license_id=license_id, device_id=device_id, details={"source": source, "schema": legacy["schema_name"]})
            response = self._active_response(connection, license_row, device_row, migrated=True)
            response["migration_source"] = legacy["schema_name"]
            response["canonical_key"] = display_key
            return response

    def verify(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_tool(request.get("tool"))
        token = str(request.get("license_token") or "")
        payload = self._decode_token(token)
        with self.connection() as connection, connection:
            _public_key, fingerprint = self._consume_proof(connection, request, "verify")
            token_row = connection.execute("SELECT * FROM license_tokens WHERE token_id=? AND tool=?", (payload.get("jti"), TOOL)).fetchone()
            if not token_row or token_row["revoked_at"] or token_row["token_hash"] != hashlib.sha256(token.encode()).hexdigest():
                raise LicenseServiceError("invalid_token", "License token is unknown or revoked", status=401)
            if datetime.fromisoformat(token_row["expires_at"]) < self.now():
                raise LicenseServiceError("token_expired", "License token expired", status=401)
            device = connection.execute("SELECT * FROM devices WHERE device_id=? AND tool=?", (payload.get("device_id"), TOOL)).fetchone()
            if not device or device["device_fingerprint"] != fingerprint or device["status"] != "active":
                raise LicenseServiceError("device_mismatch", "Token is not bound to this device", status=401)
            license_row = connection.execute("SELECT * FROM licenses WHERE license_id=? AND tool=?", (payload.get("license_id"), TOOL)).fetchone()
            if not license_row or license_row["status"] == "revoked":
                return {"valid": False, "reason": "revoked"}
            if not license_row["expires_at"] or date.fromisoformat(license_row["expires_at"]) < self.now().date():
                return {"valid": False, "reason": "expired", "expires_at": license_row["expires_at"]}
            connection.execute("UPDATE license_tokens SET revoked_at=? WHERE token_id=?", (self.now().isoformat(), token_row["token_id"]))
            return self._active_response(connection, license_row, device)

    @staticmethod
    def _hardware_score(saved: dict[str, str], current: dict[str, str]) -> tuple[bool, float, int]:
        baseline = {k: v for k, v in saved.items() if k in HARDWARE_FIELDS and HASH64_RE.fullmatch(str(v))}
        matches = sum(1 for name, value in baseline.items() if current.get(name) == value)
        ratio = matches / len(baseline) if baseline else 0.0
        return len(baseline) >= 3 and matches >= 3 and ratio >= 0.50, ratio, matches

    def recover(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_tool(request.get("tool"))
        phone = self._phone(request.get("phone"), required=True)
        hardware = self._hardware(request.get("hardware"))
        with self.connection() as connection, connection:
            public_key, fingerprint = self._consume_proof(connection, request, "recover")
            existing_fingerprint = connection.execute("SELECT device_id FROM devices WHERE device_fingerprint=?", (fingerprint,)).fetchone()
            if existing_fingerprint:
                raise LicenseServiceError("recovery_identity_conflict", "Recovery identity is already bound", status=409)
            rows = connection.execute(
                """SELECT d.*,l.phone,l.status AS license_status,l.expires_at,l.display_key,l.phone_status
                FROM devices d JOIN licenses l ON l.license_id=d.license_id
                WHERE d.tool=? AND l.tool=? AND l.phone=? AND l.status='active'""",
                (TOOL, TOOL, phone),
            ).fetchall()
            matches = []
            for row in rows:
                ok, ratio, count = self._hardware_score(json.loads(row["hardware_json"]), hardware)
                if ok:
                    matches.append((ratio, count, row))
            matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
            if not matches:
                return {"valid": False, "reason": "recovery_not_matched"}
            if len(matches) > 1 and matches[0][:2] == matches[1][:2]:
                return {"valid": False, "reason": "recovery_ambiguous"}
            ratio, count, device = matches[0]
            connection.execute(
                "UPDATE devices SET public_key_pem=?,device_fingerprint=?,hardware_json=?,updated_at=? WHERE device_id=? AND tool=?",
                (public_key, fingerprint, json.dumps(hardware, sort_keys=True), self.now().isoformat(), device["device_id"], TOOL),
            )
            license_row = connection.execute("SELECT * FROM licenses WHERE license_id=?", (device["license_id"],)).fetchone()
            updated_device = connection.execute("SELECT * FROM devices WHERE device_id=?", (device["device_id"],)).fetchone()
            self._audit(connection, "device_recovery", "success", license_id=device["license_id"], device_id=device["device_id"], details={"matches": count, "baseline": len(json.loads(device["hardware_json"]))})
            return self._active_response(connection, license_row, updated_device, recovered=True)

    def update_phone(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_tool(request.get("tool"))
        phone = self._phone(request.get("phone"), required=True)
        token = str(request.get("license_token") or "")
        payload = self._decode_token(token)
        with self.connection() as connection, connection:
            _public_key, fingerprint = self._consume_proof(connection, request, "update_phone")
            token_row = connection.execute("SELECT * FROM license_tokens WHERE token_id=? AND tool=?", (payload.get("jti"), TOOL)).fetchone()
            if (
                not token_row
                or token_row["revoked_at"]
                or token_row["token_hash"] != hashlib.sha256(token.encode()).hexdigest()
                or token_row["license_id"] != payload.get("license_id")
                or token_row["device_id"] != payload.get("device_id")
            ):
                raise LicenseServiceError("invalid_token", "License token is unknown or revoked", status=401)
            license_id = str(payload.get("license_id") or "")
            device = connection.execute("SELECT * FROM devices WHERE device_id=? AND tool=?", (payload.get("device_id"), TOOL)).fetchone()
            if not device or device["device_fingerprint"] != fingerprint:
                raise LicenseServiceError("device_mismatch", "Token is not bound to this device", status=401)
            license_row = connection.execute("SELECT * FROM licenses WHERE license_id=? AND tool=?", (license_id, TOOL)).fetchone()
            if not license_row or license_row["status"] != "active":
                raise LicenseServiceError("license_inactive", "License is not active", status=403)
            if not license_row["expires_at"] or date.fromisoformat(license_row["expires_at"]) < self.now().date():
                raise LicenseServiceError("expired", "License has expired", status=403)
            connection.execute(
                "UPDATE licenses SET phone=?,phone_status='verified',updated_at=? WHERE license_id=? AND tool=?",
                (phone, self.now().isoformat(), license_id, TOOL),
            )
            license_row = connection.execute("SELECT * FROM licenses WHERE license_id=? AND tool=?", (license_id, TOOL)).fetchone()
            connection.execute("UPDATE license_tokens SET revoked_at=? WHERE token_id=?", (self.now().isoformat(), token_row["token_id"]))
            self._audit(connection, "phone_updated", "success", license_id=license_id, device_id=device["device_id"])
            return self._active_response(connection, license_row, device)
