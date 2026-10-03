from pathlib import Path

import license_admin
import seed_gsoft_legacy


def test_plans_and_scope_are_strict():
    assert license_admin.normalize_plan("VIP") == ("VIP", None, False)
    assert license_admin.normalize_plan("VIP2") == ("VIP2", 2, False)
    assert license_admin.normalize_plan("TEST") == ("TEST", 1, True)
    line = license_admin.build_line(
        "KEYV2-" + "a" * 32 + "-0912345678",
        "TEST1", "31/12/2027", "0912345678", ["0101234567"],
    )
    assert line.endswith("|0101234567")


def test_limited_plan_without_mst_is_dynamic_quota():
    key = "KEYV2-" + "a" * 32 + "-0912345678"
    line = license_admin.build_line(key, "VIP20", "31/12/2027", "0912345678", [])
    assert line.endswith("|VIP20|31/12/2027|0912345678|o")
    assert license_admin.build_line(key, "TEST", "31/12/2027", "x", []).endswith("|TEST|31/12/2027|x|o")
    try:
        license_admin.build_line(key, "VIP1", "31/12/2027", "x", ["0101234567", "0107654321"])
    except ValueError:
        pass
    else:
        raise AssertionError("VIP1 với 2 MST khai báo phải bị từ chối")


def test_old_customer_updates_legacy_and_canonical(tmp_path: Path):
    legacy = "KEY" + "a" * 29
    canonical = "KEYV2-" + "b" * 32 + "-0912345678"
    (tmp_path / "vip.txt").write_text(
        f"{canonical}|VIP|31/12/2026|old|o\n", encoding="utf-8"
    )
    (tmp_path / "legacy_vip.txt").write_text(
        f"{legacy}|VIP|31/12/2026|old|o\n", encoding="utf-8"
    )
    (tmp_path / "legacy_migrations.json").write_text(
        '{"' + legacy + '": {"new_key": "' + canonical + '"}}', encoding="utf-8"
    )
    line = license_admin.build_line(
        legacy, "VIP1", "31/12/2027", "new", ["0101234567"]
    )
    result = license_admin.update_registry(tmp_path, line, existing_only=True)
    assert sorted(result["found"]) == sorted([legacy, canonical])
    assert "|VIP1|31/12/2027|new|0101234567" in (tmp_path / "vip.txt").read_text()
    assert "|VIP1|31/12/2027|new|0101234567" in (tmp_path / "legacy_vip.txt").read_text()


def test_seed_preserves_all_old_rows_and_is_idempotent(tmp_path: Path):
    old_one = "KEY" + "a" * 29
    old_two = "KEY" + "b" * 29
    new_key = "KEYV2-" + "c" * 32 + "-0912345678"
    (tmp_path / "vip.txt").write_text(
        "\n".join([
            "update|https://example.invalid|2.8.0",
            f"{old_one}|VIP|31/12/2027|a|o",
            f"{old_two}|VIP1|31/12/2027|b|0101234567",
            f"{new_key}|VIP|31/12/2027|c|o",
        ]) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "legacy_vip.txt").write_text(
        f"{old_one}|VIP|31/12/2027|a|o\n", encoding="utf-8"
    )

    first = seed_gsoft_legacy.seed(tmp_path)
    second = seed_gsoft_legacy.seed(tmp_path)

    snapshot = (tmp_path / "legacy_vip.txt").read_text(encoding="utf-8")
    assert first["added"] == 1
    assert second["added"] == 0
    assert old_one in snapshot and old_two in snapshot
    assert new_key not in snapshot
    assert "update|" not in snapshot
