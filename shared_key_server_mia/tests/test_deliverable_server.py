from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTH_PATH = ROOT / "deliverables" / "key-server-2026-09-10" / "auth.py"
SPEC = importlib.util.spec_from_file_location("mia_deliverable_auth", AUTH_PATH)
assert SPEC and SPEC.loader
AUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTH)

APP_PATH = AUTH_PATH.with_name("app.py")
sys.path.insert(0, str(APP_PATH.parent))
APP_SPEC = importlib.util.spec_from_file_location("mia_deliverable_app", APP_PATH)
assert APP_SPEC and APP_SPEC.loader
APP = importlib.util.module_from_spec(APP_SPEC)
PREVIOUS_AUTH = sys.modules.get("auth")
sys.modules["auth"] = AUTH
try:
    APP_SPEC.loader.exec_module(APP)
finally:
    if PREVIOUS_AUTH is None:
        sys.modules.pop("auth", None)
    else:
        sys.modules["auth"] = PREVIOUS_AUTH
RECOVERY = sys.modules["mia_recovery"]

DEVICE_ID = "ec3fc9c7-e2a1-4260-b2a9-2014d003a76d"
MST = "0109067059"


def key(phone: str, device_id: str = DEVICE_ID) -> str:
    digest = hashlib.sha256(f"MIA|{device_id}".encode("utf-8")).hexdigest()
    return f"KEYV2-{digest[:32]}-{phone}"


def hardware(changed=()) -> dict[str, str]:
    fields = (
        "system_uuid", "bios_serial", "baseboard_serial",
        "machine_guid", "cpu_id", "disk_serial",
    )
    return {
        field: hashlib.sha256(
            f"{'changed' if field in changed else 'stable'}:{field}".encode("utf-8")
        ).hexdigest()
        for field in fields
    }


class DeliverableServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        for tool in ("MIA", "MIA2", "MIA3", "GBOT", "IDQUICK", "GSOFT"):
            (self.base / tool).mkdir(parents=True)
            (self.base / tool / "vip.txt").write_text("", encoding="utf-8")
        self.original_base = AUTH.BASE_DIR
        AUTH.BASE_DIR = self.base

    def tearDown(self):
        AUTH.BASE_DIR = self.original_base
        self.temp.cleanup()

    def verify(self, phone: str, *, signals=None, device_id=DEVICE_ID,
               current_version="4.0.8", legacy_keys=()):
        return AUTH.verify_key_v2(
            "MIA", key=key(phone, device_id), device_id=device_id,
            phone=phone, hardware=signals or hardware(), legacy_keys=list(legacy_keys),
            current_version=current_version,
        )

    def test_mia_migration_replaces_legacy_in_all_mia_registries(self):
        phone = "0900001090"
        legacy = "KEY" + "a" * 29 + phone
        original = f"{legacy}|VIP|31/12/2099|{phone}|o|legacy"
        for tool in ("MIA", "MIA2", "MIA3"):
            (self.base / tool / "vip.txt").write_text(original + "\n", encoding="utf-8")

        response = self.verify(phone, legacy_keys=[legacy])

        self.assertTrue(response["valid"])
        canonical = key(phone)
        for tool in ("MIA", "MIA2", "MIA3"):
            rows = (self.base / tool / "vip.txt").read_text(encoding="utf-8").splitlines()
            self.assertFalse(any(row.startswith(legacy + "|") for row in rows), tool)
            self.assertEqual(sum(row.startswith(canonical + "|") for row in rows), 1, tool)

    def test_mia_existing_migration_repairs_missing_row_and_keeps_first_duplicate(self):
        phone = "0900001091"
        legacy = "KEY" + "b" * 29 + phone
        original = f"{legacy}|VIP|31/12/2099|{phone}|o|legacy"
        mia = self.base / "MIA"
        mia.joinpath("vip.txt").write_text(original + "\n", encoding="utf-8")
        self.assertTrue(self.verify(phone, legacy_keys=[legacy])["valid"])

        canonical = key(phone)
        preferred = f"{canonical}|VIP|30/11/2030|{phone}|o|first"
        duplicate = f"{canonical}|VIP|31/12/2031|other|{MST}|second"
        mia.joinpath("vip.txt").write_text(
            original + "\n" + preferred + "\n" + duplicate + "\n",
            encoding="utf-8",
        )

        response = self.verify(phone, legacy_keys=[legacy])

        self.assertTrue(response["valid"])
        self.assertEqual(response["expires_at"], "30/11/2030")
        rows = mia.joinpath("vip.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual([row for row in rows if row.startswith(canonical + "|")], [preferred])
        self.assertFalse(any(row.startswith(legacy + "|") for row in rows))

        mia.joinpath("vip.txt").write_text("", encoding="utf-8")
        repaired = self.verify(phone, legacy_keys=[legacy])
        self.assertTrue(repaired["valid"])
        self.assertEqual(
            sum(row.startswith(canonical + "|") for row in mia.joinpath("vip.txt").read_text(encoding="utf-8").splitlines()),
            1,
        )

    def test_requested_vip1_test1_vip_and_test_matrix(self):
        rows = [
            ("VIP1", "0900001001", MST, False, 1, [MST], None, None),
            ("TEST1", "0900001002", MST, True, 1, [MST], "2026-08-01", "2026-08-31"),
            ("VIP", "0900001003", "o", False, None, [], None, None),
            ("TEST", "0900001004", MST, True, 1, [MST], "2026-08-01", "2026-08-31"),
        ]
        lines = [
            f"{key(phone)}|{plan}|31/12/2099|SECURITY-QA|{scope}"
            for plan, phone, scope, *_expected in rows
        ]
        (self.base / "MIA" / "vip.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        for plan, phone, _scope, trial, maximum, allowed, date_from, date_to in rows:
            with self.subTest(plan=plan):
                response = self.verify(phone)
                self.assertTrue(response["valid"])
                self.assertEqual(response["reason"], "ok")
                self.assertEqual(response["entitlements"], {
                    "version": 1, "plan": plan, "trial": trial,
                    "max_tax_codes": maximum, "allowed_tax_codes": allowed,
                    "date_from": date_from, "date_to": date_to,
                })

    def test_fastapi_endpoint_forwards_the_complete_desktop_contract(self):
        phone = "0900001050"
        (self.base / "MIA" / "vip.txt").write_text(
            f"{key(phone)}|TEST|31/12/2099|SECURITY-QA|{MST}\n",
            encoding="utf-8",
        )
        response = APP.verify_key_v2_endpoint(APP.KeyV2Req(
            tool="MIA", key=key(phone), device_id=DEVICE_ID, phone=phone,
            hardware=hardware(), legacy_keys=[], mst=MST,
            date_from="2026-08-01", date_to="2026-08-31",
            current_version="4.0.8",
        ))
        self.assertTrue(response["valid"])
        self.assertTrue(response["authorized"])
        self.assertEqual(response["entitlements"]["plan"], "TEST")
        self.assertTrue(response["mst_authorized"])
        self.assertTrue(response["date_authorized"])

    def test_recovery_actions_reuse_verify_key_v2_without_changing_default_verify(self):
        phone = "0900001051"
        (self.base / "MIA" / "vip.txt").write_text(
            f"{key(phone)}|VIP|31/12/2099|SECURITY-QA|o\n", encoding="utf-8",
        )
        messages = []
        recovery = RECOVERY.RecoveryService(
            self.base / "recovery.sqlite3", secret="r" * 32,
            send_mail=lambda email, code: messages.append((email, code)),
        )
        original = APP.recovery_service
        APP.recovery_service = lambda: recovery
        payload = dict(
            tool="MIA", key=key(phone), device_id=DEVICE_ID, phone=phone,
            hardware=hardware(), current_version="4.0.8",
        )
        try:
            ordinary = APP.verify_key_v2_endpoint(APP.KeyV2Req(**payload))
            self.assertTrue(ordinary["valid"])

            challenge = APP.verify_key_v2_endpoint(APP.KeyV2Req(
                **payload, action="contact_request", email="owner@example.com",
            ))
            confirmed = APP.verify_key_v2_endpoint(APP.KeyV2Req(
                **payload, action="contact_confirm",
                challenge_id=challenge["challenge_id"], code=messages[-1][1],
            ))
            self.assertTrue(confirmed["verified"])
        finally:
            APP.recovery_service = original

    def test_invalid_recovery_license_keeps_client_error_instead_of_becoming_500(self):
        request = APP.KeyV2Req(
            tool="MIA", key="invalid", device_id="deployment-test",
            phone="0981234567", hardware={}, action="password_reset_request",
        )
        with self.assertRaises(APP.HTTPException) as raised:
            APP.verify_key_v2_endpoint(request)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail["code"], "recovery_license_invalid")
        self.assertRegex(raised.exception.detail["request_id"], r"^[0-9a-f]{16}$")

    def test_limited_policies_require_an_explicit_scope(self):
        for plan in ("VIP1", "TEST1", "TEST"):
            for scope in ("", "o", f"{MST},0111380276"):
                with self.subTest(plan=plan, scope=scope):
                    phone = "0900001010"
                    (self.base / "MIA" / "vip.txt").write_text(
                        f"{key(phone)}|{plan}|31/12/2099|SECURITY-QA|{scope}\n",
                        encoding="utf-8",
                    )
                    response = self.verify(phone)
                    self.assertFalse(response["valid"])
                    self.assertEqual(response["reason"], "license_policy_invalid")

    def test_vip1_row_authorizes_only_its_fifth_field_mst(self):
        phone = "0977030925"
        allowed_mst = "0240590043"
        (self.base / "MIA" / "vip.txt").write_text(
            f"{key(phone)}|VIP1|09/09/2031|{phone}|{allowed_mst}|\n",
            encoding="utf-8",
        )

        allowed = AUTH.verify_key_v2(
            "MIA", key=key(phone), device_id=DEVICE_ID,
            phone=phone, hardware=hardware(), legacy_keys=[], mst=allowed_mst,
            current_version="4.0.8",
        )
        self.assertTrue(allowed["valid"])
        self.assertTrue(allowed["authorized"])
        self.assertEqual(allowed["entitlements"]["allowed_tax_codes"], [allowed_mst])

        denied = AUTH.verify_key_v2(
            "MIA", key=key(phone), device_id=DEVICE_ID,
            phone=phone, hardware=hardware(), legacy_keys=[], mst="0240590044",
            current_version="4.0.8",
        )
        self.assertFalse(denied["valid"])
        self.assertFalse(denied["authorized"])
        self.assertEqual(denied["reason"], "mst_not_authorized")

    def test_limited_mia_policy_requires_fixed_desktop_version(self):
        for index, plan in enumerate(("VIP1", "TEST1", "TEST"), start=5):
            phone = f"090000100{index}"
            (self.base / "MIA" / "vip.txt").write_text(
                f"{key(phone)}|{plan}|09/09/2031|{phone}|0240590043|\n",
                encoding="utf-8",
            )
            for current_version in ("", "4.0.7", "MIA 4.0.7"):
                with self.subTest(plan=plan, current_version=current_version):
                    response = self.verify(phone, current_version=current_version)
                    self.assertFalse(response["valid"])
                    self.assertFalse(response["authorized"])
                    self.assertEqual(response["reason"], "client_update_required")
            self.assertTrue(self.verify(phone, current_version="4.0.8")["valid"])

    def test_version_floors_only_apply_to_limited_mia_and_gsoft_clients(self):
        phone = "0977030925"
        (self.base / "MIA" / "vip.txt").write_text(
            f"{key(phone)}|VIP|09/09/2031|{phone}|o|\n",
            encoding="utf-8",
        )
        self.assertTrue(self.verify(phone, current_version="4.0.7")["valid"])

        device_id = "gsoft-version-floor-regression"
        digest = hashlib.sha256(f"GSOFT|{device_id}".encode("utf-8")).hexdigest()
        gsoft_key = f"KEYV2-{digest[:32]}-{phone}"
        (self.base / "GSOFT" / "vip.txt").write_text(
            f"{gsoft_key}|VIP1|09/09/2031|{phone}|0240590043|\n",
            encoding="utf-8",
        )
        response = AUTH.verify_key_v2(
            "GSOFT", key=gsoft_key, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], current_version="4.0.7",
        )
        self.assertTrue(response["valid"])

        old_taxsoft = AUTH.verify_key_v2(
            "GSOFT", key=gsoft_key, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], current_version="2.7.0",
        )
        self.assertFalse(old_taxsoft["valid"])
        self.assertEqual(old_taxsoft["reason"], "client_update_required")
        self.assertTrue(AUTH.verify_key_v2(
            "GSOFT", key=gsoft_key, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], current_version="2.8.0",
        )["valid"])

        scoped_vip_phone = "0900001009"
        scoped_vip_key = AUTH._new_key("GSOFT", device_id, scoped_vip_phone)
        (self.base / "GSOFT" / "vip.txt").write_text(
            f"{scoped_vip_key}|VIP|09/09/2031|Taxsoft|0240590043|\n",
            encoding="utf-8",
        )
        scoped_old = AUTH.verify_key_v2(
            "GSOFT", key=scoped_vip_key, device_id=device_id,
            phone=scoped_vip_phone, hardware=hardware(), legacy_keys=[],
            current_version="2.7.0",
        )
        self.assertFalse(scoped_old["valid"])
        self.assertEqual(scoped_old["reason"], "client_update_required")

    def test_gsoft_vip1_requires_field_five_and_authorizes_only_that_mst(self):
        phone = "0977030925"
        device_id = "gsoft-taxsoft-vip1-device"
        digest = hashlib.sha256(f"GSOFT|{device_id}".encode("utf-8")).hexdigest()
        gsoft_key = f"KEYV2-{digest[:32]}-{phone}"
        gsoft = self.base / "GSOFT"
        gsoft.joinpath("vip.txt").write_text(
            f"{gsoft_key}|VIP1|09/09/2031|{phone}|0240590043|\n",
            encoding="utf-8",
        )

        allowed = AUTH.verify_key_v2(
            "GSOFT", key=gsoft_key, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], mst="0240590043",
            current_version="2.8.0",
        )
        self.assertTrue(allowed["valid"])
        self.assertTrue(allowed["authorized"])

        denied = AUTH.verify_key_v2(
            "GSOFT", key=gsoft_key, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], mst="0240590044",
            current_version="2.8.0",
        )
        self.assertFalse(denied["valid"])
        self.assertEqual(denied["reason"], "mst_not_authorized")

        gsoft.joinpath("vip.txt").write_text(
            f"{gsoft_key}|VIP1|09/09/2031|{phone}|o|\n",
            encoding="utf-8",
        )
        malformed = AUTH.verify_key_v2(
            "GSOFT", key=gsoft_key, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], mst="0240590043",
            current_version="2.8.0",
        )
        self.assertFalse(malformed["valid"])
        self.assertEqual(malformed["reason"], "license_policy_invalid")

    def test_gsoft_vip1_test1_vip_and_test_policy_matrix(self):
        device_id = "gsoft-four-policy-matrix"
        rows = [
            ("VIP1", "0900001101", MST, False, 1),
            ("TEST1", "0900001102", MST, True, 1),
            ("VIP", "0900001103", "o", False, None),
            ("TEST", "0900001104", MST, True, 1),
        ]
        lines = []
        keys = {}
        for plan, phone, scope, _trial, _limit in rows:
            canonical = AUTH._new_key("GSOFT", device_id, phone)
            keys[phone] = canonical
            lines.append(f"{canonical}|{plan}|31/12/2099|Taxsoft-QA|{scope}")
        (self.base / "GSOFT" / "vip.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8",
        )

        for plan, phone, _scope, trial, limit in rows:
            with self.subTest(plan=plan):
                response = AUTH.verify_key_v2(
                    "GSOFT", key=keys[phone], device_id=device_id, phone=phone,
                    hardware=hardware(), legacy_keys=[], mst=MST,
                    date_from="2026-08-01", date_to="2026-08-31",
                    current_version="2.8.0",
                )
                self.assertTrue(response["valid"])
                self.assertTrue(response["authorized"])
                self.assertEqual(response["license_policy"], plan)
                self.assertEqual(response["entitlements"]["trial"], trial)
                self.assertEqual(response["entitlements"]["max_tax_codes"], limit)

        denied_date = AUTH.verify_key_v2(
            "GSOFT", key=keys["0900001104"], device_id=device_id,
            phone="0900001104", hardware=hardware(), legacy_keys=[], mst=MST,
            date_from="2026-09-01", date_to="2026-09-30",
            current_version="2.8.0",
        )
        self.assertFalse(denied_date["valid"])
        self.assertEqual(denied_date["reason"], "test_month_restricted")

    def test_corrupt_gsoft_state_cannot_rebind_an_activated_key(self):
        phone = "0900001090"
        device_id = "gsoft-corrupt-state-device"
        canonical = AUTH._new_key("GSOFT", device_id, phone)
        gsoft = self.base / "GSOFT"
        gsoft.joinpath("vip.txt").write_text(
            f"{canonical}|VIP|31/12/2099|Taxsoft|o\n", encoding="utf-8",
        )
        self.assertTrue(AUTH.verify_key_v2(
            "GSOFT", key=canonical, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], current_version="2.8.0",
        )["valid"])

        gsoft.joinpath("device_bindings.json").write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Corrupt GSOFT license state"):
            AUTH.verify_key_v2(
                "GSOFT", key=canonical, device_id=device_id, phone=phone,
                hardware=hardware(), legacy_keys=[], current_version="2.8.0",
            )

    def test_corrupt_mia_state_cannot_rebind_a_whitelisted_key(self):
        phone = "0900001020"
        mia = self.base / "MIA"
        mia.joinpath("vip.txt").write_text(
            f"{key(phone)}|VIP|31/12/2099|SECURITY-QA|o\n",
            encoding="utf-8",
        )
        self.assertTrue(self.verify(phone)["valid"])
        mia.joinpath("device_bindings.json").write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Corrupt MIA license state"):
            self.verify(phone)

    def test_malformed_binding_and_oversized_device_id_fail_closed(self):
        phone = "0900001030"
        mia = self.base / "MIA"
        canonical = key(phone)
        mia.joinpath("vip.txt").write_text(
            f"{canonical}|VIP|31/12/2099|SECURITY-QA|o\n",
            encoding="utf-8",
        )
        mia.joinpath("device_bindings.json").write_text(
            json.dumps({canonical: {}}), encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "Corrupt MIA license binding"):
            self.verify(phone)
        with self.assertRaisesRegex(ValueError, "device_id"):
            self.verify(phone, device_id="x" * 129)

    def test_bound_key_rejects_two_of_six_hardware_matches(self):
        phone = "0900001040"
        (self.base / "MIA" / "vip.txt").write_text(
            f"{key(phone)}|VIP|31/12/2099|SECURITY-QA|o\n",
            encoding="utf-8",
        )
        self.assertTrue(self.verify(phone)["valid"])
        changed = ("system_uuid", "bios_serial", "baseboard_serial", "machine_guid")
        response = self.verify(phone, signals=hardware(changed))
        self.assertFalse(response["valid"])
        self.assertEqual(response["reason"], "hardware_mismatch_below_50_percent")

    def test_other_legacy_tool_registries_remain_byte_for_byte_read_only(self):
        for tool in ("MIA2", "MIA3", "GBOT", "IDQUICK", "GSOFT"):
            with self.subTest(tool=tool):
                content = f"{tool}-legacy-line|v|31/12/2099|contact|o\n"
                registry = self.base / tool / "vip.txt"
                registry.write_text(content, encoding="utf-8")
                before = registry.read_bytes()
                self.assertEqual(AUTH.check_key(tool), content)
                self.assertEqual(registry.read_bytes(), before)

    def test_gsoft_interim_key_upgrade_behavior_is_preserved(self):
        device_id = "gsoft-compatibility-device"
        phone = "0900001060"
        interim = AUTH._interim_v2_key("GSOFT", device_id)
        expected = AUTH._new_key("GSOFT", device_id, phone)
        vip = self.base / "GSOFT" / "vip.txt"
        vip.write_text(f"{interim}|VIP|31/12/2099|Taxsoft|o\n", encoding="utf-8")

        first = AUTH.verify_key_v2(
            "GSOFT", key=interim, device_id=device_id, phone="",
            hardware=hardware(), legacy_keys=[],
        )
        self.assertTrue(first["valid"])
        upgraded = AUTH.verify_key_v2(
            "GSOFT", key=expected, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[],
        )
        self.assertTrue(upgraded["valid"])
        self.assertEqual(upgraded["key"], expected)
        self.assertEqual(upgraded["reason"], "interim_v2_upgraded")

    def test_gsoft_bare_test_requires_explicit_field_five_scope(self):
        device_id = "gsoft-test-policy-device"
        phone = "0900001070"
        canonical = AUTH._new_key("GSOFT", device_id, phone)
        (self.base / "GSOFT" / "vip.txt").write_text(
            f"{canonical}|TEST|31/12/2099|Taxsoft|o\n", encoding="utf-8",
        )
        response = AUTH.verify_key_v2(
            "GSOFT", key=canonical, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[], mst=MST,
            date_from="2026-08-01", date_to="2026-08-31",
            current_version="2.8.0",
        )
        self.assertFalse(response["valid"])
        self.assertEqual(response["reason"], "license_policy_invalid")
        self.assertFalse((self.base / "GSOFT" / "mst_bindings.txt").exists())

    def test_gsoft_keeps_long_device_ids_but_fails_closed_on_bad_state(self):
        device_id = "g" * 129
        phone = "0900001080"
        canonical = AUTH._new_key("GSOFT", device_id, phone)
        gsoft = self.base / "GSOFT"
        gsoft.joinpath("vip.txt").write_text(
            f"{canonical}|VIP|31/12/2099|Taxsoft|o\n", encoding="utf-8",
        )
        self.assertTrue(AUTH.verify_key_v2(
            "GSOFT", key=canonical, device_id=device_id, phone=phone,
            hardware=hardware(), legacy_keys=[],
        )["valid"])
        gsoft.joinpath("device_bindings.json").write_text(
            json.dumps({canonical: []}), encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "Corrupt GSOFT license binding"):
            AUTH.verify_key_v2(
                "GSOFT", key=canonical, device_id=device_id, phone=phone,
                hardware=hardware(), legacy_keys=[],
            )
        with self.assertRaisesRegex(ValueError, "^Missing device_id$"):
            AUTH.verify_key_v2(
                "GSOFT", key="irrelevant", device_id="", phone=phone,
                hardware=hardware(), legacy_keys=[],
            )
        gsoft.joinpath("device_bindings.json").write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Corrupt GSOFT license state"):
            AUTH.verify_key_v2(
                "GSOFT", key=canonical, device_id=device_id, phone=phone,
                hardware=hardware(), legacy_keys=[],
            )


if __name__ == "__main__":
    unittest.main()
