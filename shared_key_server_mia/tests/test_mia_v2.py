from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from shared_key_server_mia.mia_v2 import verify_mia_key_v2

NOW = datetime(2026, 8, 30, 12, 0, 0)


def key(device_id: str) -> str:
    digest = hashlib.sha256(f"MIA|{device_id}".encode()).hexdigest()
    return f"MIAV2-{digest[:32]}"


def hardware(changed=()) -> dict[str, str]:
    names = ("system_uuid", "bios_serial", "baseboard_serial", "machine_guid", "cpu_id", "disk_serial")
    return {
        name: hashlib.sha256(f"{'changed' if name in changed else 'stable'}:{name}".encode()).hexdigest()
        for name in names
    }


class SharedMiaV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        for tool in ("MIA", "GSOFT", "MIA2", "MIA3", "GBOT", "IDQUICK"):
            folder = self.base / tool
            folder.mkdir(parents=True)
            (folder / "vip.txt").write_text(f"{tool}-baseline|VIP|31/12/2027|metadata\n", encoding="utf-8")
        self.other_before = {
            tool: (self.base / tool / "vip.txt").read_bytes()
            for tool in ("GSOFT", "MIA2", "MIA3", "GBOT", "IDQUICK")
        }

    def tearDown(self):
        self.temp.cleanup()

    def verify(self, device_id: str, *, phone="", legacy_keys=(), signals=None):
        return verify_mia_key_v2(
            self.base,
            key=key(device_id),
            device_id=device_id,
            phone=phone,
            hardware=signals or hardware(),
            legacy_keys=list(legacy_keys),
            now=NOW,
        )

    def assert_other_namespaces_unchanged(self):
        for tool, before in self.other_before.items():
            self.assertEqual((self.base / tool / "vip.txt").read_bytes(), before, tool)
            self.assertEqual(list((self.base / tool).iterdir()), [self.base / tool / "vip.txt"], tool)

    def test_exact_v1_migrates_without_phone_and_preserves_expiry_and_metadata(self):
        legacy = "key" + "a" * 29
        original = f"{legacy}|VIP|31/12/2027|customer-metadata"
        (self.base / "MIA" / "vip.txt").write_text(original + "\n", encoding="utf-8")
        response = self.verify("device-v1", legacy_keys=[legacy])
        self.assertTrue(response["valid"])
        self.assertTrue(response["migrated"])
        self.assertEqual(response["phone_status"], "pending")
        self.assertEqual(response["expires_at"], "31/12/2027")
        lines = (self.base / "MIA" / "vip.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(original, lines)
        self.assertIn(f"{key('device-v1')}|VIP|31/12/2027|customer-metadata", lines)
        self.assert_other_namespaces_unchanged()

    def test_no_legacy_match_requires_phone_and_real_phone_gets_stable_pending_key(self):
        missing = self.verify("new-device")
        self.assertFalse(missing["valid"])
        self.assertEqual(missing["reason"], "phone_required")
        pending = self.verify("new-device", phone="0981234567")
        self.assertFalse(pending["valid"])
        self.assertEqual(pending["key"], key("new-device"))
        self.assertEqual(pending["reason"], "key_not_activated")
        with self.assertRaisesRegex(ValueError, "phone"):
            self.verify("dummy", phone="0000000000")

    def test_manual_activation_binds_then_verifies_same_canonical_device(self):
        device_id = "manual-device"
        (self.base / "MIA" / "vip.txt").write_text(f"{key(device_id)}|VIP|31/12/2027|admin\n", encoding="utf-8")
        first = self.verify(device_id, phone="0981234567")
        second = self.verify(device_id, phone="0981234567")
        self.assertTrue(first["valid"])
        self.assertTrue(second["valid"])
        self.assertEqual(second["device_id"], device_id)
        self.assertEqual(second["key"], key(device_id))

    def test_recovery_accepts_three_of_six_and_rejects_two_of_six(self):
        canonical = "canonical-device"
        (self.base / "MIA" / "vip.txt").write_text(f"{key(canonical)}|VIP|31/12/2027|admin\n", encoding="utf-8")
        self.assertTrue(self.verify(canonical, phone="0981234567")["valid"])

        three = self.verify(
            "temporary-three",
            phone="0981234567",
            signals=hardware(changed=("system_uuid", "bios_serial", "baseboard_serial")),
        )
        self.assertTrue(three["valid"])
        self.assertTrue(three["recovered"])
        self.assertEqual(three["device_id"], canonical)
        self.assertEqual(three["key"], key(canonical))

        two = self.verify(
            "temporary-two",
            phone="0981234567",
            signals=hardware(changed=("system_uuid", "bios_serial", "baseboard_serial", "machine_guid")),
        )
        self.assertFalse(two["valid"])
        self.assertEqual(two["reason"], "key_not_activated")

    def test_observed_phone_legacy_migrates_only_by_exact_candidate(self):
        legacy = "KEY" + "b" * 29 + "0981234567"
        (self.base / "MIA" / "vip.txt").write_text(f"{legacy}|VIP|31/12/2027|0981234567\n", encoding="utf-8")
        response = self.verify("observed-device", phone="0981234567", legacy_keys=[legacy])
        self.assertTrue(response["valid"])
        self.assertTrue(response["migrated"])
        self.assertEqual(response["phone_status"], "verified")

    def test_expired_legacy_is_recognized_but_not_activated(self):
        legacy = "key" + "c" * 29
        (self.base / "MIA" / "vip.txt").write_text(f"{legacy}|VIP|29/08/2026|metadata\n", encoding="utf-8")
        response = self.verify("expired-device", legacy_keys=[legacy])
        self.assertFalse(response["valid"])
        self.assertTrue(response["expired"])
        self.assertEqual(response["expires_at"], "29/08/2026")
        self.assertEqual(response["reason"], "legacy_key_expired")

    def test_all_mutations_are_confined_to_mia_directory(self):
        legacy = "key" + "d" * 29
        (self.base / "MIA" / "vip.txt").write_text(f"{legacy}|VIP|31/12/2027|metadata\n", encoding="utf-8")
        self.verify("isolated-device", legacy_keys=[legacy])
        self.assert_other_namespaces_unchanged()
        bindings = json.loads((self.base / "MIA" / "device_bindings.json").read_text(encoding="utf-8"))
        self.assertEqual(list(bindings), [key("isolated-device")])

    def test_extension_adds_only_mia_dispatch_and_no_server_runtime(self):
        package = Path(__file__).resolve().parents[1]
        source = (package / "mia_v2.py").read_text(encoding="utf-8")
        patch = (package / "shared_server.patch").read_text(encoding="utf-8")
        self.assertNotIn("fastapi", source.lower())
        self.assertNotIn("uvicorn", source.lower())
        self.assertNotIn("sqlite", source.lower())
        self.assertIn('if tool == "MIA"', patch)
        self.assertIn('if tool != "GSOFT"', patch)
        removed_behavior = [line for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]
        self.assertEqual(removed_behavior, [])


if __name__ == "__main__":
    unittest.main()
