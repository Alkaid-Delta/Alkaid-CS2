"""test_real_case_intake.py — Intake Manifest 驗證測試（Phase 6.4C2-A）"""
import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.intake_models import (  # noqa: E402
    RealCaseIntakeManifest,
    can_mark_anonymized_real,
    validate_authorization,
    validate_provenance,
    validate_secure_store_reference,
)


def _valid_manifest(**kw):
    base = dict(
        intake_id="intake-abc123",
        case_id="real_001",
        source_type="post",
        source_provenance="user_supplied_real",
        consent_or_authorization="user_supplied",
        original_storage_reference="secure-store://case-001-raw",
        redaction_version="v1.0",
    )
    base.update(kw)
    return RealCaseIntakeManifest(**base)


# ---------------------------------------------------------------
# 十一、Privacy 與資料治理測試
# ---------------------------------------------------------------
def test_missing_provenance_rejected():
    with pytest.raises(ValueError, match="source_provenance"):
        _valid_manifest(source_provenance="")


def test_agent_generated_provenance_rejected():
    with pytest.raises(ValueError, match="source_provenance"):
        _valid_manifest(source_provenance="agent_generated")


def test_synthetic_provenance_rejected():
    with pytest.raises(ValueError, match="source_provenance"):
        _valid_manifest(source_provenance="synthetic")


def test_missing_authorization_rejected():
    with pytest.raises(ValueError, match="consent_or_authorization"):
        _valid_manifest(consent_or_authorization="")


def test_unknown_authorization_rejected():
    with pytest.raises(ValueError, match="consent_or_authorization"):
        _valid_manifest(consent_or_authorization="maybe")


def test_http_source_reference_rejected():
    with pytest.raises(ValueError, match="secure-store"):
        _valid_manifest(original_storage_reference="https://example.com/raw")


def test_absolute_local_path_rejected():
    with pytest.raises(ValueError, match="secure-store"):
        _valid_manifest(original_storage_reference=r"C:\Users\user\Desktop\raw.json")


def test_safe_opaque_secure_store_reference_accepted():
    m = _valid_manifest(original_storage_reference="secure-store://case-001-raw")
    assert m.original_storage_reference == "secure-store://case-001-raw"


def test_unknown_manifest_fields_rejected():
    with pytest.raises(ValueError, match="未知欄位"):
        RealCaseIntakeManifest.from_dict(
            {**_valid_manifest().to_dict(), "raw_text": "secret"})


def test_can_mark_requires_all_gates():
    ok, reasons = can_mark_anonymized_real(
        provenance="user_supplied_real", authorization="user_supplied",
        redaction_version="v1", privacy_error_count=0,
        has_image_bytes=False, has_http=False, has_private_fields=False)
    assert ok is True and reasons == []


def test_can_mark_fails_with_errors():
    ok, reasons = can_mark_anonymized_real(
        provenance="agent_generated", authorization="user_supplied",
        redaction_version="v1", privacy_error_count=1,
        has_image_bytes=True, has_http=True, has_private_fields=True)
    assert ok is False
    assert "provenance_invalid" in reasons
    assert "privacy_errors_present" in reasons
    assert "image_bytes_present" in reasons
    assert "http_url_present" in reasons


def test_validate_helpers():
    assert validate_provenance("internal_owned_source")
    assert not validate_provenance("inferred_real")
    assert validate_authorization("owner_authorized")
    assert not validate_authorization("")
    assert validate_secure_store_reference("secure-store://a-b_1")
    assert not validate_secure_store_reference("secure-store://UPPER")
    assert not validate_secure_store_reference("facebook.com/x")


# ================================================================
# Phase 6.4C2-A §11 — evaluation_real manifest.json
# ================================================================
EMPTY_MANIFEST = {
    "schema_version": "evaluation-real-manifest-v1",
    "cases": [],
    "real_case_count": 0,
    "double_reviewed_real_count": 0,
    "disputed_real_count": 0,
}


def test_empty_manifest_valid():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    ok, reasons = validate_real_manifest(dict(EMPTY_MANIFEST), [])
    assert ok is True, reasons


def test_current_manifest_real_count_zero():
    import json as _json
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    manifest = _json.loads(
        open(os.path.join(PROJECT_ROOT, "tests", "fixtures",
                          "evaluation_real", "manifest.json"),
             encoding="utf-8").read())
    assert manifest["real_case_count"] == 0
    ok, reasons = validate_real_manifest(manifest, [])
    assert ok is True, reasons


def test_manual_fixtures_not_listed_as_real():
    # 10 個 manual_fixture 不得寫入 manifest cases
    import json as _json
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    real_dir = os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation_real")
    cases = load_evaluation_directory(real_dir)  # 10 manual_fixture（manifest 跳過）
    assert len(cases) == 10
    manifest = _json.loads(open(os.path.join(real_dir, "manifest.json"),
                                encoding="utf-8").read())
    assert manifest["cases"] == [], "manual_fixture 不得寫入 manifest cases"
    ok, reasons = validate_real_manifest(manifest, cases)
    assert ok is True, reasons


def test_manifest_count_mismatch_rejected():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST, real_case_count=3)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("real_case_count_mismatch" in r for r in reasons)


def _full_entry(**kw):
    entry = {
        "case_id": "r1",
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
    entry.update(kw)
    return entry


def test_manifest_raw_text_rejected():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST,
               cases=[_full_entry(raw_text="售 A 算5000")],
               real_case_count=1)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("unknown_fields" in r or "privacy:" in r for r in reasons), reasons


def test_manifest_http_url_rejected():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST,
               cases=[_full_entry(image_reference="https://fbcdn.net/x")],
               real_case_count=1)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("unknown_fields" in r or "privacy:" in r for r in reasons), reasons


def test_manifest_bytes_rejected():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST,
               cases=[_full_entry(bytes=b"\x89PNG")],
               real_case_count=1)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("unknown_fields" in r or "privacy:" in r for r in reasons), reasons


def test_manifest_entry_requires_source_anonymized_real():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST,
               cases=[_full_entry(source="manual_fixture")],
               real_case_count=1)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("source_not_anonymized_real" in r for r in reasons)


def test_missing_manifest_means_intake_not_ready():
    from alkaid_cs2.evaluation.intake_models import intake_ready
    ok, reasons = intake_ready(None, [])
    assert ok is False
    assert "manifest_missing" in reasons


def test_valid_manifest_plus_workflow_sets_intake_ready():
    from alkaid_cs2.evaluation.intake_models import intake_ready
    ok, reasons = intake_ready(dict(EMPTY_MANIFEST), [])
    assert ok is True, reasons
    assert reasons == []


def test_default_report_does_not_claim_intake_ready():
    # default 執行不傳 intake_ready → None（不得宣稱 ready）
    from alkaid_cs2.evaluation.report import generate_evaluation_report
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    from alkaid_cs2.evaluation.scoring import score_case
    case = EvaluationCase(
        case_id="s1", source=EvaluationSource.SYNTHETIC, author="synthetic",
        link="fixture://s1", raw_text="售 A 算5000",
        expected_safe_for_production=True)
    preds = {"legacy": [], "text_v2": [], "vision_raw": [],
             "vision_production": []}
    results = {k: [] for k in preds}
    for name in preds:
        p = EvaluationPrediction(case_id="s1", parser_name=name,
                                 parse_status="parsed", source="v2",
                                 latency_ms=1.0)
        preds[name].append(p)
        results[name].append(score_case(case, name, p, expected_safe=True))
    r = generate_evaluation_report([case], preds, results)
    assert r.get("intake_ready") is None, \
        "default report 不得宣稱 intake ready（None）"
    assert r["intake_ready"] is not True
    assert "real_dataset_intake_ready" not in r["readiness_reasons"], \
        "intake_ready=None 時不得加入 reason"


# ================================================================
# Phase 6.4C2-A.2 — 兩道 gate / hash / report intake-ready
# ================================================================
def test_single_review_ingest_but_not_readiness():
    from alkaid_cs2.evaluation.intake_validation import (
        can_enter_real_readiness, can_ingest_as_anonymized_real,
    )
    ok, _ = can_ingest_as_anonymized_real(
        provenance="user_supplied_real", authorization="user_supplied",
        redaction_version="v1", privacy_error_count=0,
        has_image_bytes=False, has_http=False, has_private_fields=False)
    assert ok is True, "single_review 仍可 ingest"
    ok2, reasons2 = can_enter_real_readiness(review_status="single_review")
    assert ok2 is False
    assert any("review_status_not_double" in r for r in reasons2)


def test_disputed_ingest_but_not_readiness():
    from alkaid_cs2.evaluation.intake_validation import can_enter_real_readiness
    ok, _ = can_enter_real_readiness(review_status="disputed")
    assert ok is False


def test_double_review_enters_readiness():
    from alkaid_cs2.evaluation.intake_validation import can_enter_real_readiness
    ok, reasons = can_enter_real_readiness(review_status="double_review")
    assert ok is True and reasons == []


def test_unknown_review_status_rejected():
    from alkaid_cs2.evaluation.intake_validation import can_enter_real_readiness
    ok, reasons = can_enter_real_readiness(review_status="almost_done")
    assert ok is False
    assert any("invalid_review_status" in r for r in reasons)


def test_invalid_image_hash_rejected():
    from alkaid_cs2.evaluation.intake_validation import validate_image_hashes
    errors = validate_image_hashes(
        original_image_hashes=["ZZZ"], redacted_image_hashes=[], image_count=1)
    assert any("original_invalid_hash" in e for e in errors)


def test_duplicate_image_hash_rejected():
    from alkaid_cs2.evaluation.intake_validation import validate_image_hashes
    h = "a" * 64
    errors = validate_image_hashes(
        original_image_hashes=[h, h], redacted_image_hashes=[], image_count=2)
    assert any("original_duplicate_hash" in e for e in errors)


def test_hash_count_must_match_image_count():
    from alkaid_cs2.evaluation.intake_validation import validate_image_hashes
    h = "b" * 64
    errors = validate_image_hashes(
        original_image_hashes=[h], redacted_image_hashes=[], image_count=3)
    assert any("original_hash_count_mismatch" in e for e in errors)
    errors2 = validate_image_hashes(
        original_image_hashes=[], redacted_image_hashes=[], image_count=0)
    assert errors2 == []


def test_redaction_drops_preloaded_ground_truth():
    from alkaid_cs2.evaluation.redaction import redact_real_case_input
    raw = {"raw_text": "售 A 算5000",
           "expected_items": [{"name": "AK-47 | Redline", "wear": "FT"}],
           "expected_post_intent": "selling",
           "expected_safe_for_production": True,
           "expected_raw_vision_safe": False,
           "seller_price": "5000", "currency": "TWD", "wear": "Field-Tested",
           "stattrak": False, "role": "selling",
           "should_create_price": True, "item_image_indexes": [0],
           "image_kind": "single"}
    draft, _ = redact_real_case_input(
        raw, case_id="r1", redaction_version="v1",
        provenance="user_supplied_real", authorization="user_supplied")
    for banned in ("expected_items", "expected_post_intent",
                   "expected_safe_for_production", "expected_raw_vision_safe",
                   "seller_price", "currency", "wear", "stattrak", "role",
                   "should_create_price", "item_image_indexes", "image_kind"):
        assert banned not in draft, f"draft 不得保留預載 GT：{banned}"


def test_intake_ready_true_adds_reason():
    # intake_ready=True → _readiness_reasons 加入 real_dataset_intake_ready
    from alkaid_cs2.evaluation.report import _readiness_reasons
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    cases = load_evaluation_directory(FIXTURES_DIR)
    reasons_true = _readiness_reasons(cases, {}, {}, 0, intake_ready=True)
    assert "real_dataset_intake_ready" in reasons_true
    reasons_none = _readiness_reasons(cases, {}, {}, 0, intake_ready=None)
    assert "real_dataset_intake_ready" not in reasons_none
    reasons_false = _readiness_reasons(cases, {}, {}, 0, intake_ready=False)
    assert "real_dataset_intake_ready" not in reasons_false


# ================================================================
# Phase 6.4C2-A.3 — SHA-256 豁免 / manifest counts / model hash
# ================================================================
def test_valid_nonempty_manifest_entry_passes():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    entry = _full_entry()
    manifest = dict(EMPTY_MANIFEST, cases=[entry],
                    real_case_count=1, double_reviewed_real_count=1,
                    disputed_real_count=0)
    ok, reasons = validate_real_manifest(manifest, None)
    assert ok is True, reasons


def test_sha256_not_base64_false_positive():
    # 64 位 hex hash 不得被 generic base64 heuristic 誤判
    from alkaid_cs2.evaluation.intake_validation import scan_redaction_issues
    h = "a" * 64
    findings = scan_redaction_issues({"fixture_sha256": h,
                                      "original_image_hashes": [h],
                                      "reviewer_inputs_hash": h})
    assert not any(f.code == "base64_like" for f in findings), \
        "合法 SHA-256 不得被 base64 heuristic 誤判"


def test_invalid_fixture_sha256_rejected():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST,
               cases=[_full_entry(fixture_sha256="not-a-hash")],
               real_case_count=1)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("fixture_sha256_invalid" in r for r in reasons), reasons


def test_empty_manifest_nonzero_double_rejected():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST, double_reviewed_real_count=3)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("double_reviewed_count_mismatch" in r for r in reasons)


def test_empty_manifest_nonzero_disputed_rejected():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    bad = dict(EMPTY_MANIFEST, disputed_real_count=2)
    ok, reasons = validate_real_manifest(bad, [])
    assert ok is False
    assert any("disputed_count_mismatch" in r for r in reasons)


def test_manifest_counts_derived_from_entries():
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    e1 = _full_entry(case_id="r1", review_status="double_review")
    e2 = _full_entry(case_id="r2", review_status="disputed")
    ok, reasons = validate_real_manifest(
        dict(EMPTY_MANIFEST, cases=[e1, e2], real_case_count=2,
             double_reviewed_real_count=1, disputed_real_count=1), None)
    assert ok is True, reasons
    bad = dict(EMPTY_MANIFEST, cases=[e1, e2], real_case_count=2,
               double_reviewed_real_count=2, disputed_real_count=0)
    ok2, reasons2 = validate_real_manifest(bad, [])
    assert ok2 is False
    assert any("double_reviewed_count_mismatch" in r for r in reasons2)


def test_loaded_fixture_and_manifest_count_mismatch_rejected():
    # 載入的 EvaluationCase 與 manifest counts 交叉檢查
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    from alkaid_cs2.evaluation.models import (
        EvaluationCase, EvaluationSource, GroundTruthReviewStatus,
    )
    loaded = [
        EvaluationCase(case_id="r1", source=EvaluationSource.ANONYMIZED_REAL,
                       author="anonymous", link="redacted://r1",
                       raw_text="售 A 算5000",
                       expected_safe_for_production=True,
                       redaction_version="v1",
                       ground_truth_review_status="double_review")]
    entry = _full_entry(case_id="r1", review_status="double_review")
    ok, reasons = validate_real_manifest(
        dict(EMPTY_MANIFEST, cases=[entry], real_case_count=1,
             double_reviewed_real_count=1, disputed_real_count=0), loaded)
    assert ok is True, reasons
    # manifest 說 0 但載入 1 → mismatch
    ok2, reasons2 = validate_real_manifest(
        dict(EMPTY_MANIFEST, cases=[], real_case_count=0,
             double_reviewed_real_count=0, disputed_real_count=0), loaded)
    assert ok2 is False
    assert any("loaded_fixture_count_mismatch" in r for r in reasons2), reasons2


# ================================================================
# Phase 6.4C2-A.4 — Manifest loaded dataset 交叉檢查（list|None）
# ================================================================
def test_manifest_nonempty_loaded_real_empty_rejected():
    # manifest 有 real entry、loaded real=[] → 必須 rejected
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    entry = _full_entry(case_id="r1", review_status="double_review")
    ok, reasons = validate_real_manifest(
        dict(EMPTY_MANIFEST, cases=[entry], real_case_count=1,
             double_reviewed_real_count=1, disputed_real_count=0), [])
    assert ok is False
    assert any("loaded_fixture_count_mismatch" in r for r in reasons), reasons


def test_manifest_empty_loaded_real_empty_accepted():
    # 空 manifest、loaded real=[] → accepted
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    ok, reasons = validate_real_manifest(dict(EMPTY_MANIFEST), [])
    assert ok is True, reasons


def test_manifest_empty_with_manual_fixtures_accepted():
    # 只有 manual fixtures、real manifest empty → accepted
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    manual = load_evaluation_directory(  # 10 manual_fixture
        os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation_real"))
    assert len(manual) == 10
    ok, reasons = validate_real_manifest(dict(EMPTY_MANIFEST), manual)
    assert ok is True, reasons


def test_model_invalid_hash_rejected():
    from alkaid_cs2.evaluation.intake_models import RealCaseIntakeManifest
    with pytest.raises(ValueError, match="image hash 驗證失敗"):
        RealCaseIntakeManifest(
            intake_id="i1", case_id="r1", source_type="post",
            source_provenance="user_supplied_real",
            consent_or_authorization="user_supplied",
            original_storage_reference="secure-store://r1",
            redaction_version="v1", image_count=1,
            original_image_hashes=["BADHASH"])


def test_model_duplicate_hash_rejected():
    from alkaid_cs2.evaluation.intake_models import RealCaseIntakeManifest
    h = "c" * 64
    with pytest.raises(ValueError, match="duplicate"):
        RealCaseIntakeManifest(
            intake_id="i2", case_id="r2", source_type="post",
            source_provenance="user_supplied_real",
            consent_or_authorization="user_supplied",
            original_storage_reference="secure-store://r2",
            redaction_version="v1", image_count=2,
            original_image_hashes=[h, h])


def test_redacted_hashes_round_trip(tmp_path):
    # create_real_case_intake 保存 original/redacted hashes（不丟棄）
    import json as _json
    h1 = "d" * 64
    h2 = "e" * 64
    raw = tmp_path / "payload.json"
    raw.write_text(_json.dumps({
        "original_storage_reference": "secure-store://r3",
        "original_image_hashes": [h1],
        "redacted_image_hashes": [h2],
        "image_count": 1,
    }), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "create_real_case_intake.py"),
         "--input", str(raw), "--case-id", "r3",
         "--source-provenance", "user_supplied_real",
         "--authorization", "user_supplied", "--redaction-version", "v1",
         "--output", str(tmp_path / "out")],
        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=120)
    assert r.returncode == 0, r.stderr
    manifest = _json.loads((tmp_path / "out" / "r3.intake.json").read_text(
        encoding="utf-8"))
    assert manifest["original_image_hashes"] == [h1], "original hashes 保存"
    assert manifest["redacted_image_hashes"] == [h2], "redacted hashes 保存"
    assert manifest["image_count"] == 1


FIXTURES_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation")
SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")


# ================================================================
# Phase 6.4C2-A.8.1 — Field-scoped secure-store exemption
# ================================================================
def _scan(payload):
    from alkaid_cs2.evaluation.intake_validation import scan_redaction_issues
    return [f for f in scan_redaction_issues(payload) if f.severity == "error"]


def test_valid_secure_store_secret_segment_accepted():
    # 受控欄位 + 合法值 → auth_keyword 豁免
    fs = _scan({"original_storage_reference": "secure-store://secret-resource-1"})
    assert not any(f.code == "auth_keyword" for f in fs)


def test_valid_secure_store_token_segment_accepted():
    fs = _scan({"original_storage_reference": "secure-store://token-holder-2"})
    assert not any(f.code == "auth_keyword" for f in fs)


def test_valid_secure_store_password_segment_accepted():
    fs = _scan({"original_storage_reference": "secure-store://password-vault-3"})
    assert not any(f.code == "auth_keyword" for f in fs)


def test_invalid_secure_store_empty_id_rejected():
    # 空 opaque ID → 專用 validator 拒絕（職責分離，規格六）
    from alkaid_cs2.evaluation.intake_validation import (
        validate_secure_store_reference,
    )
    assert not validate_secure_store_reference("secure-store://")


def test_invalid_secure_store_uppercase_rejected():
    from alkaid_cs2.evaluation.intake_validation import (
        validate_secure_store_reference,
    )
    assert not validate_secure_store_reference("secure-store://SecretToken-X")


def test_invalid_secure_store_special_character_rejected():
    from alkaid_cs2.evaluation.intake_validation import (
        validate_secure_store_reference,
    )
    assert not validate_secure_store_reference("secure-store://ab@cd")


def test_http_storage_reference_rejected():
    fs = _scan({"original_storage_reference": "https://example.com/x"})
    assert any(f.code == "http_url" for f in fs), "HTTP 照常拒絕"


def test_local_path_storage_reference_rejected():
    fs = _scan({"original_storage_reference": "C:/Users/user/secret"})
    assert any(f.code == "local_path" for f in fs), "本機路徑照常拒絕"


def test_notes_secure_store_secret_still_rejected():
    # 非受控欄位：即使值符合 secure-store 格式仍觸發 auth_keyword
    fs = _scan({"notes": "secure-store://secret-password-token"})
    assert any(f.code == "auth_keyword" for f in fs), "notes 不豁免"


def test_arbitrary_field_secure_store_token_rejected():
    fs = _scan({"arbitrary_field": "secure-store://token-value"})
    assert any(f.code == "auth_keyword" for f in fs), "任意欄位不豁免"


def test_nested_metadata_secure_store_password_rejected():
    fs = _scan({"metadata": {"description": "secure-store://api_key-secret"}})
    assert any(f.code == "auth_keyword" for f in fs), "nested metadata 不豁免"


def test_exemption_only_applies_to_storage_reference_fields():
    fs = _scan({"other_reference": "secure-store://secret-thing"})
    assert any(f.code == "auth_keyword" for f in fs), "非 allowlist 欄位不豁免"


def test_field_named_reference_not_automatically_exempt():
    # 欄位名含 "reference" 但不在 allowlist → 不豁免
    fs = _scan({"my_reference_field": "secure-store://token-stash"})
    assert any(f.code == "auth_keyword" for f in fs)


def test_storage_reference_like_value_in_expected_items_rejected():
    fs = _scan({"expected_items": [{"name": "secure-store://password-box"}]})
    assert any(f.code == "auth_keyword" for f in fs), "expected_items 內不豁免"
