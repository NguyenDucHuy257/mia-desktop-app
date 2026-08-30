from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server_mia_license_v2.mia_license_v2 import LicenseServiceError, MiaLicenseService


NOW = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)


def identity():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private, public, hashlib.sha256(public.encode()).hexdigest()


def hardware(changed=()):
    names = ("system_uuid", "bios_serial", "baseboard_serial", "machine_guid", "cpu_id", "disk_serial")
    return {
        name: hashlib.sha256(("changed:" if name in changed else "stable:") .encode() + name.encode()).hexdigest()
        for name in names
    }


class MiaLicenseV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = MiaLicenseService(
            Path(self.temp.name) / "license.db",
            token_secret=b"test-secret-that-is-at-least-32-bytes-long",
            now=lambda: NOW,
        )

    def tearDown(self):
        self.temp.cleanup()

    def proof(self, action, private, public, fingerprint):
        challenge = self.service.create_challenge({
            "tool": "MIA", "action": action,
            "public_key": public, "device_fingerprint": fingerprint,
        })
        return {
            "tool": "MIA", "challenge_id": challenge["challenge_id"],
            "challenge": challenge["challenge"], "public_key": public,
            "device_fingerprint": fingerprint,
            "signature": base64.b64encode(private.sign(challenge["challenge"].encode())).decode(),
        }

    def migrate(self, *, legacy_line, exact=True, phone=None, hash29=None):
        self.service.import_legacy_records([legacy_line], today=date(2026, 8, 30))
        private, public, fingerprint = identity()
        request = self.proof("migrate", private, public, fingerprint)
        key = legacy_line.split("|", 1)[0]
        embedded_hash = key[3:32] if len(key) >= 32 and key[:3] in {"key", "KEY"} else None
        request.update({
            "device_id": str(uuid.uuid4()), "phone": phone, "hardware": hardware(),
            "legacy_keys": [key] if exact else [],
            "legacy_hashes": [hash29 or embedded_hash] if (hash29 or embedded_hash) else [],
        })
        return self.service.migrate(request), (private, public, fingerprint)

    def test_v1_without_phone_migrates_before_new_customer_flow_and_preserves_expiry(self):
        key = "key" + "a" * 29
        response, _ = self.migrate(legacy_line=f"{key}|VIP|31/12/2027|legacy-note")
        self.assertTrue(response["valid"])
        self.assertTrue(response["migrated"])
        self.assertEqual(response["phone_status"], "pending")
        self.assertIsNone(response["phone"])
        self.assertEqual(response["expires_at"], "2027-12-31")
        self.assertTrue(response["canonical_key"].startswith("MIAV2-"))

    def test_observed_v2_phone_candidate_recovers_phone_without_form(self):
        hash29 = "b" * 29
        phone = "0912345678"
        key = "KEY" + hash29 + phone
        response, _ = self.migrate(legacy_line=f"{key}|VIP|31/12/2027|{phone}", phone=phone)
        self.assertTrue(response["valid"])
        self.assertEqual(response["phone"], phone)
        self.assertEqual(response["phone_status"], "verified")

    def test_unique_hash_migrates_but_ambiguous_hash_requires_verification(self):
        hash29 = "c" * 29
        self.service.import_legacy_records([
            f"key{hash29}|VIP|31/12/2027|note",
            f"KEY{hash29}0912345678|VIP|31/12/2027|0912345678",
        ], today=date(2026, 8, 30))
        private, public, fingerprint = identity()
        request = self.proof("migrate", private, public, fingerprint)
        request.update({"device_id": str(uuid.uuid4()), "phone": None, "hardware": hardware(), "legacy_keys": [], "legacy_hashes": [hash29]})
        response = self.service.migrate(request)
        self.assertFalse(response["valid"])
        self.assertEqual(response["reason"], "legacy_ambiguous")

    def test_unsupported_and_malformed_legacy_rows_never_auto_migrate(self):
        key = "key" + "d" * 64
        response, _ = self.migrate(legacy_line=f"{key}|VIP|not-a-date|0912345678")
        self.assertFalse(response["valid"])
        self.assertEqual(response["reason"], "no_legacy_match")

    def test_challenge_is_one_time_and_replay_fails(self):
        private, public, fingerprint = identity()
        request = self.proof("activate", private, public, fingerprint)
        request.update({"device_id": str(uuid.uuid4()), "phone": "0912345678", "hardware": hardware()})
        self.service.activate(request)
        with self.assertRaisesRegex(LicenseServiceError, "already consumed"):
            self.service.activate(request)

    def test_invalid_signature_and_expired_challenge_are_rejected(self):
        private, public, fingerprint = identity()
        invalid = self.proof("activate", private, public, fingerprint)
        invalid["signature"] = base64.b64encode(b"\0" * 64).decode()
        invalid.update({"device_id": str(uuid.uuid4()), "phone": "0912345678", "hardware": hardware()})
        with self.assertRaisesRegex(LicenseServiceError, "signature"):
            self.service.activate(invalid)

        clock = [NOW]
        expired_service = MiaLicenseService(
            Path(self.temp.name) / "expired-challenge.db",
            token_secret=b"test-secret-that-is-at-least-32-bytes-long",
            now=lambda: clock[0],
        )
        challenge = expired_service.create_challenge({
            "tool": "MIA", "action": "activate",
            "public_key": public, "device_fingerprint": fingerprint,
        })
        clock[0] = NOW + timedelta(seconds=61)
        expired = {
            "tool": "MIA", "challenge_id": challenge["challenge_id"],
            "challenge": challenge["challenge"], "public_key": public,
            "device_fingerprint": fingerprint,
            "signature": base64.b64encode(private.sign(challenge["challenge"].encode())).decode(),
            "device_id": str(uuid.uuid4()), "phone": "0912345678", "hardware": hardware(),
        }
        with self.assertRaisesRegex(LicenseServiceError, "expired"):
            expired_service.activate(expired)

    def test_dummy_phone_is_rejected(self):
        private, public, fingerprint = identity()
        request = self.proof("activate", private, public, fingerprint)
        request.update({"device_id": str(uuid.uuid4()), "phone": "0000000000", "hardware": hardware()})
        with self.assertRaisesRegex(LicenseServiceError, "Phone"):
            self.service.activate(request)

    def test_activation_is_stable_and_verify_requires_bound_ed25519_identity(self):
        private, public, fingerprint = identity()
        device_id = str(uuid.uuid4())
        first = self.proof("activate", private, public, fingerprint)
        first.update({"device_id": device_id, "phone": "0912345678", "hardware": hardware()})
        pending = self.service.activate(first)
        self.assertEqual(pending["reason"], "key_not_activated")
        self.service.admin_activate(pending["license_id"], "2027-12-31")
        second = self.proof("activate", private, public, fingerprint)
        second.update({"device_id": device_id, "phone": "0912345678", "hardware": hardware()})
        active = self.service.activate(second)
        self.assertTrue(active["valid"])
        self.assertEqual(active["canonical_key"], pending["canonical_key"])

        verify = self.proof("verify", private, public, fingerprint)
        verify["license_token"] = active["license_token"]
        refreshed = self.service.verify(verify)
        self.assertTrue(refreshed["valid"])

        other_private, other_public, other_fingerprint = identity()
        copied = self.proof("verify", other_private, other_public, other_fingerprint)
        copied["license_token"] = refreshed["license_token"]
        with self.assertRaisesRegex(LicenseServiceError, "not bound"):
            self.service.verify(copied)

    def test_recovery_accepts_three_of_six_and_rejects_two_of_six(self):
        private, public, fingerprint = identity()
        device_id = str(uuid.uuid4())
        activate = self.proof("activate", private, public, fingerprint)
        activate.update({"device_id": device_id, "phone": "0912345678", "hardware": hardware()})
        pending = self.service.activate(activate)
        self.service.admin_activate(pending["license_id"], "2027-12-31")

        recovery_private, recovery_public, recovery_fingerprint = identity()
        three = self.proof("recover", recovery_private, recovery_public, recovery_fingerprint)
        three.update({
            "phone": "0912345678",
            "hardware": hardware(changed=("system_uuid", "bios_serial", "baseboard_serial")),
        })
        recovered = self.service.recover(three)
        self.assertTrue(recovered["valid"])
        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["device_id"], device_id)

        third_private, third_public, third_fingerprint = identity()
        two = self.proof("recover", third_private, third_public, third_fingerprint)
        two_of_six = hardware(changed=("system_uuid", "bios_serial", "baseboard_serial"))
        for name in ("system_uuid", "bios_serial", "baseboard_serial", "machine_guid"):
            two_of_six[name] = hashlib.sha256(f"second-change:{name}".encode()).hexdigest()
        two.update({
            "phone": "0912345678",
            "hardware": two_of_six,
        })
        rejected = self.service.recover(two)
        self.assertFalse(rejected["valid"])
        self.assertEqual(rejected["reason"], "recovery_not_matched")

    def test_phone_update_does_not_rotate_license_or_device_identity(self):
        key = "key" + "e" * 29
        response, identity_values = self.migrate(legacy_line=f"{key}|VIP|31/12/2027|note")
        private, public, fingerprint = identity_values
        update = self.proof("update_phone", private, public, fingerprint)
        update.update({"license_token": response["license_token"], "phone": "0999999999"})
        changed = self.service.update_phone(update)
        self.assertEqual(changed["license_id"], response["license_id"])
        self.assertEqual(changed["device_id"], response["device_id"])
        self.assertEqual(changed["canonical_key"], response["canonical_key"])
        self.assertEqual(changed["phone"], "0999999999")

        replay_private = self.proof("update_phone", private, public, fingerprint)
        replay_private.update({"license_token": response["license_token"], "phone": "0988888888"})
        with self.assertRaisesRegex(LicenseServiceError, "revoked"):
            self.service.update_phone(replay_private)

    def test_other_tool_namespace_is_rejected_without_database_mutation(self):
        private, public, fingerprint = identity()
        with self.assertRaisesRegex(LicenseServiceError, "tool=MIA"):
            self.service.create_challenge({
                "tool": "GSOFT", "action": "verify",
                "public_key": public, "device_fingerprint": fingerprint,
            })
        with self.service.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM licenses").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
