from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from shared_key_server_mia.mia_v2 import verify_mia_key_v2

NOW = datetime(2026, 8, 30, 12, 0, 0)
PHONE = "0987654321"
DEVICE_ID = "11111111-2222-3333-4444-555555555555"
LEGACY_KEY = "keycd9706d13cb09a723cfd576dcdc99"
EXPECTED_KEY = "KEYV2-20cd0a15bc1ab172b385707877c0f82b-0987654321"


def key(device_id: str, phone: str = PHONE) -> str:
    digest = hashlib.sha256(f"MIA|{device_id}".encode()).hexdigest()
    return f"KEYV2-{digest[:32]}-{phone}"


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

    def verify(self, device_id: str, *, phone=PHONE, legacy_keys=(), signals=None):
        return verify_mia_key_v2(
            self.base,
            key=key(device_id, phone),
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

    def test_exact_prompt_fixture_migrates_and_preserves_complete_metadata(self):
        self.assertEqual(key(DEVICE_ID), EXPECTED_KEY)
        original = f"{LEGACY_KEY}|v|31/12/2028|ABC COMPANY|o|note old"
        (self.base / "MIA" / "vip.txt").write_text(original + "\n", encoding="utf-8")
        response = self.verify(DEVICE_ID, legacy_keys=[LEGACY_KEY])
        self.assertTrue(response["valid"])
        self.assertTrue(response["migrated"])
        self.assertEqual(response["phone_status"], "verified")
        self.assertEqual(response["expires_at"], "31/12/2028")
        lines = (self.base / "MIA" / "vip.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(original, lines)
        self.assertIn(f"{EXPECTED_KEY}|v|31/12/2028|ABC COMPANY|o|note old", lines)
        legacy_lines = (self.base / "MIA" / "legacy_vip.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(original, legacy_lines)
        bindings = json.loads((self.base / "MIA" / "device_bindings.json").read_text(encoding="utf-8"))
        migrations = json.loads((self.base / "MIA" / "legacy_migrations.json").read_text(encoding="utf-8"))
        self.assertIn(EXPECTED_KEY, bindings)
        self.assertEqual(migrations[LEGACY_KEY]["new_key"], EXPECTED_KEY)
        self.assert_other_namespaces_unchanged()

    def test_server_requires_phone_and_real_phone_gets_stable_pending_key(self):
        with self.assertRaisesRegex(ValueError, "phone"):
            self.verify("new-device", phone="")
        pending = self.verify("new-device")
        self.assertFalse(pending["valid"])
        self.assertEqual(pending["key"], key("new-device"))
        self.assertEqual(pending["reason"], "key_not_activated")
        with self.assertRaisesRegex(ValueError, "phone"):
            self.verify("dummy", phone="0000000000")

    def test_manual_activation_binds_then_verifies_same_canonical_device(self):
        device_id = "manual-device"
        (self.base / "MIA" / "vip.txt").write_text(f"{key(device_id)}|VIP|31/12/2027|admin\n", encoding="utf-8")
        first = self.verify(device_id)
        second = self.verify(device_id)
        self.assertTrue(first["valid"])
        self.assertTrue(second["valid"])
        self.assertEqual(second["device_id"], device_id)
        self.assertEqual(second["key"], key(device_id))

    def test_recovery_accepts_three_of_six_and_rejects_two_of_six(self):
        canonical = "canonical-device"
        (self.base / "MIA" / "vip.txt").write_text(f"{key(canonical)}|VIP|31/12/2027|admin\n", encoding="utf-8")
        self.assertTrue(self.verify(canonical)["valid"])

        three = self.verify(
            "temporary-three",
            signals=hardware(changed=("system_uuid", "bios_serial", "baseboard_serial")),
        )
        self.assertTrue(three["valid"])
        self.assertTrue(three["recovered"])
        self.assertEqual(three["device_id"], canonical)
        self.assertEqual(three["key"], key(canonical))

        two = self.verify(
            "temporary-two",
            signals=hardware(changed=("system_uuid", "bios_serial", "baseboard_serial", "machine_guid")),
        )
        self.assertFalse(two["valid"])
        self.assertEqual(two["reason"], "key_not_activated")

    def test_hardware_thresholds_six_through_three_pass_and_two_fails(self):
        canonical = "threshold-canonical"
        (self.base / "MIA" / "vip.txt").write_text(f"{key(canonical)}|VIP|31/12/2028|synthetic\n", encoding="utf-8")
        self.assertTrue(self.verify(canonical)["valid"])
        names = ("system_uuid", "bios_serial", "baseboard_serial", "machine_guid", "cpu_id", "disk_serial")
        for expected_matches in (6, 5, 4, 3):
            changed = names[:6 - expected_matches]
            result = self.verify(f"temporary-{expected_matches}", signals=hardware(changed=changed))
            self.assertTrue(result["valid"], expected_matches)
            self.assertTrue(result["recovered"], expected_matches)
            self.assertEqual(result["hardware_matches"], expected_matches)
            self.assertEqual(result["key"], key(canonical))
        rejected = self.verify("temporary-2", signals=hardware(changed=names[:4]))
        self.assertFalse(rejected["valid"])
        self.assertEqual(rejected["reason"], "key_not_activated")

    def test_wrong_phone_and_different_machine_cannot_recover(self):
        canonical = "protected-canonical"
        (self.base / "MIA" / "vip.txt").write_text(f"{key(canonical)}|VIP|31/12/2028|synthetic\n", encoding="utf-8")
        self.assertTrue(self.verify(canonical)["valid"])
        wrong_phone = self.verify("wrong-phone-device", phone="0911111111")
        self.assertFalse(wrong_phone["valid"])
        self.assertEqual(wrong_phone["reason"], "key_not_activated")
        different_machine = self.verify("different-machine", signals=hardware(changed=(
            "system_uuid", "bios_serial", "baseboard_serial", "machine_guid", "cpu_id", "disk_serial",
        )))
        self.assertFalse(different_machine["valid"])
        self.assertEqual(different_machine["reason"], "key_not_activated")

    def test_ambiguous_recovery_and_malformed_payload_fail_closed(self):
        first, second = "ambiguous-one", "ambiguous-two"
        vip = self.base / "MIA" / "vip.txt"
        vip.write_text(
            f"{key(first)}|VIP|31/12/2028|one\n{key(second)}|VIP|31/12/2028|two\n",
            encoding="utf-8",
        )
        self.assertTrue(self.verify(first)["valid"])
        self.assertTrue(self.verify(second)["valid"])
        ambiguous = self.verify("ambiguous-temporary")
        self.assertFalse(ambiguous["valid"])
        self.assertEqual(ambiguous["reason"], "recovery_ambiguous")
        with self.assertRaisesRegex(ValueError, "key"):
            verify_mia_key_v2(
                self.base, key="bad", device_id=DEVICE_ID, phone=PHONE,
                hardware=hardware(), legacy_keys=[], now=NOW,
            )
        with self.assertRaisesRegex(ValueError, "hardware"):
            verify_mia_key_v2(
                self.base, key=EXPECTED_KEY, device_id=DEVICE_ID, phone=PHONE,
                hardware={"disk_serial": "a" * 64}, legacy_keys=[], now=NOW,
            )

    def test_key_is_stable_and_phone_change_requires_separate_server_policy(self):
        values = {key(DEVICE_ID) for _ in range(100)}
        self.assertEqual(values, {EXPECTED_KEY})
        other = key(DEVICE_ID, "0911111111")
        self.assertNotEqual(other, EXPECTED_KEY)
        self.assertTrue(other.endswith("-0911111111"))

    def test_observed_phone_legacy_migrates_only_by_exact_candidate(self):
        legacy = "KEY" + "b" * 29 + PHONE
        (self.base / "MIA" / "vip.txt").write_text(f"{legacy}|VIP|31/12/2027|synthetic\n", encoding="utf-8")
        response = self.verify("observed-device", legacy_keys=[legacy])
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
