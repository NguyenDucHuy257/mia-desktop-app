from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "deliverables" / "key-server-2026-09-10" / "mia_recovery.py"
SPEC = importlib.util.spec_from_file_location("mia_recovery_test", MODULE_PATH)
assert SPEC and SPEC.loader
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


class RecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1_800_000_000
        self.messages = []
        self.service = RECOVERY.RecoveryService(
            Path(self.temp.name) / "recovery.sqlite3",
            secret="x" * 32,
            clock=lambda: self.now,
            send_mail=lambda email, code: self.messages.append((email, code)),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_verified_contact_can_recover_and_codes_are_single_use(self):
        contact = self.service.request_contact("device-a", "Customer@Example.com", "127.0.0.1")
        self.assertRegex(self.messages[-1][1], r"^[0-9]{6}$")
        self.assertEqual(contact["masked_email"], "c*******@example.com")
        self.assertTrue(self.service.confirm(contact["challenge_id"], self.messages[-1][1], "contact", "device-a")["verified"])

        self.now += 61
        reset = self.service.request_reset("device-a", "127.0.0.1")
        code = self.messages[-1][1]
        self.assertTrue(self.service.confirm(reset["challenge_id"], code, "password_reset", "device-a")["verified"])
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "recovery_code_invalid"):
            self.service.confirm(reset["challenge_id"], code, "password_reset", "device-a")

    def test_wrong_codes_lock_challenge_and_resend_is_limited(self):
        challenge = self.service.request_contact("device-b", "a@example.com", "ip-a")
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "recovery_wait_before_resend"):
            self.service.request_contact("device-b", "a@example.com", "ip-a")
        for _ in range(5):
            with self.assertRaisesRegex(RECOVERY.RecoveryError, "recovery_code_invalid"):
                self.service.confirm(challenge["challenge_id"], "000000", "contact", "device-b")
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "recovery_code_locked"):
            self.service.confirm(challenge["challenge_id"], self.messages[-1][1], "contact", "device-b")

    def test_code_cannot_be_confirmed_from_another_device(self):
        challenge = self.service.request_contact("device-c", "c@example.com", "ip-c")
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "recovery_code_invalid"):
            self.service.confirm(challenge["challenge_id"], self.messages[-1][1], "contact", "device-other")

    def test_unknown_device_and_invalid_email_fail_closed(self):
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "invalid_email"):
            self.service.request_contact("device", "not-an-email")
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "recovery_email_not_registered"):
            self.service.request_reset("unknown-device")


if __name__ == "__main__":
    unittest.main()
