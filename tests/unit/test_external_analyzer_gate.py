# -*- coding: utf-8 -*-
"""test_external_analyzer_gate.py — Authorization gate + plan（Phase 6.4C2-B0）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

from alkaid_cs2.evaluation.external_analyzer_runner import (  # noqa: E402
    ERROR_ADAPTER_UNAVAILABLE,
    ERROR_ENV_MISSING,
    ERROR_FLAG_MISSING,
    ERROR_LOADER_UNAVAILABLE,
    ERROR_NOT_AUTHORIZED,
    ERROR_NO_ELIGIBLE_IMAGES,
    ERROR_NO_REAL_CASES,
    build_execution_plan,
    can_run_external_analyzer,
)


def _full_kwargs(**over):
    kw = dict(cli_allowed=True, env_allowed=True,
              anonymized_real_case_count=1, eligible_real_image_count=1,
              secure_loader_available=True, adapter_available=True)
    kw.update(over)
    return kw


def test_external_analyzer_flag_missing_rejected():
    ok, reasons = can_run_external_analyzer(**_full_kwargs(cli_allowed=False))
    assert not ok
    assert ERROR_FLAG_MISSING in reasons


def test_external_analyzer_env_missing_rejected():
    ok, reasons = can_run_external_analyzer(**_full_kwargs(env_allowed=False))
    assert not ok
    assert ERROR_ENV_MISSING in reasons


def test_both_authorizations_required():
    ok, reasons = can_run_external_analyzer(
        **_full_kwargs(cli_allowed=False, env_allowed=False))
    assert not ok
    assert ERROR_FLAG_MISSING in reasons and ERROR_ENV_MISSING in reasons


def test_no_real_cases_rejected():
    ok, reasons = can_run_external_analyzer(
        **_full_kwargs(anonymized_real_case_count=0))
    assert not ok
    assert ERROR_NO_REAL_CASES in reasons
    assert ERROR_NOT_AUTHORIZED in reasons


def test_no_eligible_images_rejected():
    ok, reasons = can_run_external_analyzer(
        **_full_kwargs(eligible_real_image_count=0))
    assert not ok
    assert ERROR_NO_ELIGIBLE_IMAGES in reasons


def test_loader_unavailable_rejected():
    ok, reasons = can_run_external_analyzer(
        **_full_kwargs(secure_loader_available=False))
    assert not ok
    assert ERROR_LOADER_UNAVAILABLE in reasons


def test_adapter_unavailable_rejected():
    ok, reasons = can_run_external_analyzer(
        **_full_kwargs(adapter_available=False))
    assert not ok
    assert ERROR_ADAPTER_UNAVAILABLE in reasons


def test_all_conditions_met_allowed():
    ok, reasons = can_run_external_analyzer(**_full_kwargs())
    assert ok and reasons == []


def test_baseline_always_blocked():
    # 目前 baseline：anonymized_real=0 → 一定 blocked
    ok, reasons = can_run_external_analyzer(**_full_kwargs(
        anonymized_real_case_count=0, eligible_real_image_count=0))
    assert not ok
    assert ERROR_NO_REAL_CASES in reasons


def test_plan_uses_opaque_case_keys():
    plan = build_execution_plan(
        eligible_cases=[{"case_id": "real_secret_001",
                         "storage_reference": "secure-store://secret-x",
                         "image_hashes": ["a" * 64]}],
        run_salt="salt-1", adapter_name="fake-analyzer",
        dry_run=True, authorized=False, created_at="2026-08-01T00:00:00Z")
    d = plan.to_dict()
    # 不保存原 case ID / storage reference
    assert "real_secret_001" not in json_dumps(d)
    assert "secret-x" not in json_dumps(d)
    assert all(len(k) == 64 and k.isalnum() for k in plan.case_keys)


def test_plan_deterministic_with_salt():
    cases = [{"case_id": "c1", "storage_reference": "secure-store://x",
              "image_hashes": ["b" * 64, "c" * 64]}]
    p1 = build_execution_plan(eligible_cases=cases, run_salt="s",
                              adapter_name="a", dry_run=True,
                              authorized=True, created_at="t")
    p2 = build_execution_plan(eligible_cases=cases, run_salt="s",
                              adapter_name="a", dry_run=True,
                              authorized=True, created_at="t")
    assert p1.case_keys == p2.case_keys
    assert p1.image_indexes == [0, 1]
    assert p1.image_count == 2


def test_different_salt_different_keys():
    cases = [{"case_id": "c1", "storage_reference": "secure-store://x",
              "image_hashes": ["b" * 64]}]
    p1 = build_execution_plan(eligible_cases=cases, run_salt="s1",
                              adapter_name="a", dry_run=True,
                              authorized=True, created_at="t")
    p2 = build_execution_plan(eligible_cases=cases, run_salt="s2",
                              adapter_name="a", dry_run=True,
                              authorized=True, created_at="t")
    assert p1.case_keys != p2.case_keys


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj)


# ================================================================
# Phase 6.4C2-B0.1 — Manifest-driven eligibility + DI
# ================================================================
import json  # noqa: E402
import os as _os  # noqa: E402


def _write_manifest(tmp_path, cases):
    m = {"schema_version": "evaluation-real-manifest-v1", "cases": cases,
         "real_case_count": len([c for c in cases
                                 if c.get("source") == "anonymized_real"]),
         "double_reviewed_real_count": len([c for c in cases
                                            if c.get("review_status")
                                            == "double_review"]),
         "disputed_real_count": len([c for c in cases
                                     if c.get("review_status")
                                     == "disputed"])}
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return p


def _write_fixture(tmp_path, case_id, hashes=("b" * 64,)):
    p = tmp_path / f"{case_id}.json"
    p.write_text(json.dumps(
        {"case_id": case_id, "source": "anonymized_real",
         "original_storage_reference": "secure-store://img-1",
         "original_image_hashes": list(hashes)}, ensure_ascii=False),
        encoding="utf-8")
    import hashlib as _h
    return _h.sha256(p.read_bytes()).hexdigest()


def _valid_entry(**over):
    e = {
        "case_id": "real_001",
        "source": "anonymized_real",
        "source_provenance": "user_supplied_real",
        "authorization_status": "user_supplied",
        "redaction_version": "v1",
        "privacy_scan_status": "passed",
        "review_status": "double_review",
        "fixture_sha256": "a" * 64,
        "image_reference_count": 1,
        "analyzer_cache_status": "not_run",
    }
    e.update(over)
    return e


def test_empty_manifest_returns_no_eligible_cases(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    m = _write_manifest(tmp_path, [])
    cases, errors = load_eligible_cases_from_manifest(m, tmp_path)
    assert cases == [] and errors == []


def test_invalid_manifest_exit_2(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    # 空 manifest 路徑（不存在）
    cases, errors = load_eligible_cases_from_manifest(
        tmp_path / "nope.json", tmp_path)
    assert cases == [] and errors == ["manifest_missing"]


def test_malformed_manifest_exit_2(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    p = tmp_path / "manifest.json"
    p.write_text("{broken", encoding="utf-8")
    cases, errors = load_eligible_cases_from_manifest(p, tmp_path)
    assert cases == [] and errors == ["manifest_invalid_json"]


def test_manual_fixture_not_eligible(tmp_path):
    # manual_fixture 根本不能寫入 manifest（entry schema 要求 source=
    # anonymized_real）→ manifest_validation_failed（比「不 eligible」更嚴格）
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    m = _write_manifest(tmp_path, [_valid_entry(source="manual_fixture")])
    cases, errors = load_eligible_cases_from_manifest(m, tmp_path)
    assert cases == []
    assert errors == ["manifest_validation_failed"]


def test_disputed_case_not_readiness_eligible(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    m = _write_manifest(tmp_path,
                        [_valid_entry(review_status="disputed")])
    cases, errors = load_eligible_cases_from_manifest(m, tmp_path)
    assert cases == [] and errors == []
    _write_fixture(tmp_path, "real_001")  # fixture 存在也不影響（disputed 不合資格）


def test_double_reviewed_real_case_planned(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    sha = _write_fixture(tmp_path, "real_001")
    m = _write_manifest(tmp_path, [_valid_entry(fixture_sha256=sha)])
    cases, errors = load_eligible_cases_from_manifest(m, tmp_path)
    assert errors == []
    assert len(cases) == 1
    assert cases[0].review_status == "double_review"
    assert cases[0].image_hashes == ["b" * 64]


def test_loader_does_not_read_image_bytes(tmp_path, monkeypatch):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    sha = _write_fixture(tmp_path, "real_001")
    m = _write_manifest(tmp_path, [_valid_entry(fixture_sha256=sha)])
    img = tmp_path / "img-1.bin"
    img.write_bytes(b"secret-image-bytes")
    cases, errors = load_eligible_cases_from_manifest(m, tmp_path)
    assert errors == [] and len(cases) == 1
    # loader 回傳的 image_hashes 是 hash（無 bytes 內容）
    assert all(len(h) == 64 for h in cases[0].image_hashes)


def test_dry_run_loader_factory_not_called(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import run_dry_plan
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return object()
    cases = [{"case_id": "real_001", "storage_reference": "secure-store://x",
              "image_hashes": ["b" * 64]}]
    code, errors, plan = run_dry_plan(
        cli_allowed=True, env_allowed=True, eligible_cases=cases,
        run_salt="s", adapter_name="fake", cache_dir=tmp_path,
        audit_dir=tmp_path, loader_factory=factory, adapter_factory=factory)
    assert calls["n"] == 0, "dry-run 不得呼叫 factory"


def test_dry_run_loader_load_not_called(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import run_dry_plan
    loads = {"n": 0}

    class SpyLoader:
        def load(self, ref, h):
            loads["n"] += 1
            return b"x"
    cases = [{"case_id": "real_001", "storage_reference": "secure-store://x",
              "image_hashes": ["b" * 64]}]
    run_dry_plan(cli_allowed=True, env_allowed=True, eligible_cases=cases,
                 run_salt="s", adapter_name="fake", cache_dir=tmp_path,
                 audit_dir=tmp_path,
                 loader_factory=lambda: SpyLoader(), adapter_factory=None)
    assert loads["n"] == 0, "dry-run 不得 load bytes"


def test_dry_run_adapter_factory_not_called(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import run_dry_plan
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return object()
    cases = [{"case_id": "real_001", "storage_reference": "secure-store://x",
              "image_hashes": ["b" * 64]}]
    run_dry_plan(cli_allowed=True, env_allowed=True, eligible_cases=cases,
                 run_salt="s", adapter_name="fake", cache_dir=tmp_path,
                 audit_dir=tmp_path, loader_factory=None,
                 adapter_factory=factory)
    assert calls["n"] == 0, "dry-run 不得建立 adapter"


def test_dry_run_adapter_analyze_not_called(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import run_dry_plan
    analyzes = {"n": 0}

    class SpyAdapter:
        analyzer_name = "spy"
        analyzer_version = "0"

        def analyze_image(self, *a, **k):
            analyzes["n"] += 1
            return {}
    cases = [{"case_id": "real_001", "storage_reference": "secure-store://x",
              "image_hashes": ["b" * 64]}]
    run_dry_plan(cli_allowed=True, env_allowed=True, eligible_cases=cases,
                 run_salt="s", adapter_name="fake", cache_dir=tmp_path,
                 audit_dir=tmp_path, loader_factory=None,
                 adapter_factory=lambda: SpyAdapter())
    assert analyzes["n"] == 0, "dry-run 不得 analyze"

# ================================================================
# Phase 6.4C2-B0.2 — Fixed local-data boundary + fixture integrity
# ================================================================
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import subprocess  # noqa: E402

_HERE = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(_HERE, "scripts")


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "cli_b02", os.path.join(SCRIPTS_DIR,
                                "run_real_analyzer_evaluation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_has_no_local_data_root_argument():
    src = open(os.path.join(SCRIPTS_DIR,
                            "run_real_analyzer_evaluation.py"),
               encoding="utf-8").read()
    assert "--local-data-root" not in src, "不得有公開 --local-data-root CLI"


def test_production_root_is_repository_local_data():
    mod = _load_cli()
    root = os.path.join(_HERE, "local_data")
    assert os.path.abspath(mod.LOCAL_DATA) == os.path.abspath(root)


def test_cli_cannot_redefine_allowed_root():
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR,
                                      "run_real_analyzer_evaluation.py"),
         "--dry-run", "--local-data-root", "x"],
        capture_output=True, text=True, cwd=_HERE, timeout=120)
    assert r.returncode == 2
    assert "unrecognized" in r.stderr


def test_external_absolute_output_rejected(tmp_path):
    mod = _load_cli()
    code = mod.main(["--dry-run", "--allow-external-analyzer",
                     "--audit-dir", str(tmp_path / "outside"),
                     "--manifest", str(tmp_path / "manifest.json")])
    assert code == 2  # production root 固定 repository local_data


def test_repository_tests_output_rejected():
    mod = _load_cli()
    code = mod.main(["--dry-run", "--allow-external-analyzer",
                     "--audit-dir", "tests/evaluation/reports/audit"])
    assert code == 2  # tests/ 不在 local_data root


def test_injected_test_root_accepted_only_via_main_parameter(tmp_path):
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    mod = _load_cli()
    code = mod.main(["--dry-run", "--allow-external-analyzer",
                     "--cache-dir", str(tmp_path / "local_data" / "cache"),
                     "--audit-dir", str(tmp_path / "local_data" / "runs"),
                     "--manifest", str(tmp_path / "manifest.json")],
                    local_data_root_override=str(tmp_path / "local_data"))
    assert code == 2  # 空 manifest → no eligible（root 注入成功，非 path 錯誤）
    runs = tmp_path / "local_data" / "runs"
    assert runs.exists() and any(runs.iterdir()), "注入 root 應接受並寫 audit"


# ---- Fixture integrity binding ----
def _write_integrity_case(tmp_path, case_id="real_001", **case_over):
    fixture = {
        "case_id": case_id,
        "source": "anonymized_real",
        "original_storage_reference": "secure-store://img-1",
        "original_image_hashes": ["b" * 64],
    }
    fixture.update(case_over)
    p = tmp_path / f"{case_id}.json"
    p.write_bytes(json.dumps(fixture, ensure_ascii=False).encode("utf-8"))
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _integrity_manifest(tmp_path, fixture_sha, **entry_over):
    entry = {
        "case_id": "real_001",
        "source": "anonymized_real",
        "source_provenance": "user_supplied_real",
        "authorization_status": "user_supplied",
        "redaction_version": "v1",
        "privacy_scan_status": "passed",
        "review_status": "double_review",
        "fixture_sha256": fixture_sha,
        "image_reference_count": 1,
        "analyzer_cache_status": "not_run",
    }
    entry.update(entry_over)
    m = {"schema_version": "evaluation-real-manifest-v1", "cases": [entry],
         "real_case_count": 1, "double_reviewed_real_count": 1,
         "disputed_real_count": 0}
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return p


def _load_eligible(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        load_eligible_cases_from_manifest,
    )
    return load_eligible_cases_from_manifest(
        tmp_path / "manifest.json", tmp_path)


def test_fixture_hash_mismatch_rejected(tmp_path):
    _write_integrity_case(tmp_path)
    _integrity_manifest(tmp_path, "f" * 64)  # 錯的 hash
    cases, errors = _load_eligible(tmp_path)
    assert cases == []
    assert errors == ["manifest_fixture_hash_mismatch"]


def test_fixture_case_id_mismatch_rejected(tmp_path):
    # 真正測 case_id mismatch（Phase 6.4C2-B0.3）：
    # fixture 檔名 real_001.json、內容 case_id=other_case、
    # manifest entry case_id=real_001、fixture_sha256 依 raw bytes 正確計算
    fixture = {
        "case_id": "other_case",  # 與檔名/entry 不一致
        "source": "anonymized_real",
        "original_storage_reference": "secure-store://img-1",
        "original_image_hashes": ["b" * 64],
    }
    fp = tmp_path / "real_001.json"
    fp.write_bytes(json.dumps(fixture, ensure_ascii=False).encode("utf-8"))
    import hashlib as _h
    sha = _h.sha256(fp.read_bytes()).hexdigest()
    _integrity_manifest(tmp_path, sha, case_id="real_001")
    cases, errors = _load_eligible(tmp_path)
    assert cases == []
    assert errors == ["manifest_fixture_case_id_mismatch"]


def test_fixture_missing_rejected(tmp_path):
    # 獨立測試：fixture 檔不存在（與 case-ID mismatch 區分）
    _integrity_manifest(tmp_path, "a" * 64)
    cases, errors = _load_eligible(tmp_path)
    assert cases == []
    assert errors == ["manifest_case_fixture_missing"]


def test_fixture_source_mismatch_rejected(tmp_path):
    sha = _write_integrity_case(tmp_path, source="manual_fixture")
    _integrity_manifest(tmp_path, sha)
    cases, errors = _load_eligible(tmp_path)
    assert cases == []
    assert errors == ["manifest_fixture_source_mismatch"]


def test_invalid_fixture_image_hash_rejected(tmp_path):
    sha = _write_integrity_case(
        tmp_path, original_image_hashes=["NOT-A-HASH"])
    _integrity_manifest(tmp_path, sha)
    cases, errors = _load_eligible(tmp_path)
    assert cases == []
    assert errors == ["manifest_image_hash_invalid"]


def test_fixture_privacy_issue_rejected(tmp_path):
    sha = _write_integrity_case(tmp_path, raw_text="\u8054\u7d61 aaa@bbb.com")
    _integrity_manifest(tmp_path, sha)
    cases, errors = _load_eligible(tmp_path)
    assert cases == []
    assert errors == ["manifest_fixture_privacy_failed"]


def test_fixture_integrity_success_eligible(tmp_path):
    sha = _write_integrity_case(tmp_path)
    _integrity_manifest(tmp_path, sha)
    cases, errors = _load_eligible(tmp_path)
    assert errors == []
    assert len(cases) == 1
    assert cases[0].source_case_id == "real_001"
    assert cases[0].image_hashes == ["b" * 64]


def test_integrity_failure_returns_no_partial_cases(tmp_path):
    # 兩筆：第一筆合法、第二筆 hash mismatch → 全部拒絕（無部分案例）
    _write_integrity_case(tmp_path, case_id="real_001")
    _write_integrity_case(tmp_path, case_id="real_002")
    entry1 = {
        "case_id": "real_001", "source": "anonymized_real",
        "source_provenance": "user_supplied_real",
        "authorization_status": "user_supplied",
        "redaction_version": "v1", "privacy_scan_status": "passed",
        "review_status": "double_review",
        "fixture_sha256": hashlib.sha256(
            (tmp_path / "real_001.json").read_bytes()).hexdigest(),
        "image_reference_count": 1, "analyzer_cache_status": "not_run"}
    entry2 = dict(entry1, case_id="real_002", fixture_sha256="f" * 64)
    m = {"schema_version": "evaluation-real-manifest-v1",
         "cases": [entry1, entry2], "real_case_count": 2,
         "double_reviewed_real_count": 2, "disputed_real_count": 0}
    (tmp_path / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False), encoding="utf-8")
    cases, errors = _load_eligible(tmp_path)
    assert cases == [] and errors == ["manifest_fixture_hash_mismatch"], \
        "任一 integrity failure → 不產生部分 eligible cases"
