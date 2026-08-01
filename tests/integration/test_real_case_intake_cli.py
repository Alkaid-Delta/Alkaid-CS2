"""test_real_case_intake_cli.py — Intake CLI 整合測試（Phase 6.4C2-A）

證明：CLI 離線、不打印 raw_text、dry-run 不寫檔、
synthetic/manual 不能改標 real。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")


def _run_script(name, *args, cwd=None):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, name), *args],
        capture_output=True, text=True, cwd=cwd or PROJECT_ROOT, timeout=120)


# ---------------------------------------------------------------
# 十三、CLI 與離線安全
# ---------------------------------------------------------------
def test_dry_run_writes_nothing(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"raw_text": "售 A 算5000"}), encoding="utf-8")
    out = tmp_path / "out"
    r = _run_script("redact_real_case.py", "--input", str(raw),
                    "--case-id", "real_x", "--source-provenance",
                    "user_supplied_real", "--authorization", "user_supplied",
                    "--redaction-version", "v1", "--output", str(out),
                    "--dry-run")
    assert r.returncode == 0, r.stderr
    assert not out.exists(), "dry-run 不寫檔"


def test_cli_does_not_print_raw_text(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(
        {"raw_text": "售 A 算5000 聯絡 aaa@bbb.com 電話0912345678"}),
        encoding="utf-8")
    r = _run_script("redact_real_case.py", "--input", str(raw),
                    "--case-id", "real_x", "--source-provenance",
                    "user_supplied_real", "--authorization", "user_supplied",
                    "--redaction-version", "v1", "--output",
                    str(tmp_path / "out"), "--dry-run")
    out_text = r.stdout + r.stderr
    assert "aaa@bbb.com" not in out_text, "CLI 不得打印 raw_text 敏感內容"
    assert "0912345678" not in out_text


def test_intake_missing_provenance_exit_2(tmp_path):
    r = _run_script("create_real_case_intake.py", "--case-id", "real_y",
                    "--authorization", "user_supplied",
                    "--redaction-version", "v1",
                    "--storage-reference", "secure-store://y")
    assert r.returncode == 2, "缺少 provenance → exit 2"


def test_intake_prohibited_provenance_exit_2(tmp_path):
    r = _run_script("create_real_case_intake.py", "--case-id", "real_y",
                    "--source-provenance", "agent_generated",
                    "--authorization", "user_supplied",
                    "--redaction-version", "v1",
                    "--storage-reference", "secure-store://y")
    assert r.returncode == 2, "agent_generated → exit 2"


def test_intake_http_storage_ref_exit_2(tmp_path):
    r = _run_script("create_real_case_intake.py", "--case-id", "real_y",
                    "--source-provenance", "user_supplied_real",
                    "--authorization", "user_supplied",
                    "--redaction-version", "v1",
                    "--storage-reference", "https://example.com/raw")
    assert r.returncode == 2, "http storage ref → exit 2"


def test_synthetic_cannot_be_relabeled_real(tmp_path):
    # synthetic provenance 被 CLI 直接拒絕（argparse choices 不允許）→ exit 2
    raw = tmp_path / "syn.json"
    raw.write_text(json.dumps({"raw_text": "售 A 算5000"}), encoding="utf-8")
    r = _run_script("redact_real_case.py", "--input", str(raw),
                    "--case-id", "syn_x", "--source-provenance", "synthetic",
                    "--authorization", "user_supplied",
                    "--redaction-version", "v1", "--output",
                    str(tmp_path / "out"))
    assert r.returncode == 2, "synthetic 不得作為 real provenance（argparse 拒絕）"
    assert not (tmp_path / "out" / "syn_x.draft.json").exists(), \
        "被拒絕時不得寫 draft"


def test_manual_fixture_cannot_be_relabeled_real(tmp_path):
    raw = tmp_path / "manual.json"
    raw.write_text(json.dumps({"raw_text": "售 A 算5000"}), encoding="utf-8")
    r = _run_script("redact_real_case.py", "--input", str(raw),
                    "--case-id", "manual_x", "--source-provenance",
                    "manual_fixture", "--authorization", "user_supplied",
                    "--redaction-version", "v1", "--output",
                    str(tmp_path / "out"))
    assert r.returncode == 2, "manual_fixture 不得作為 real provenance（argparse 拒絕）"
    assert not (tmp_path / "out" / "manual_x.draft.json").exists()


def test_review_workflow_cli_end_to_end(tmp_path):
    # reviewer A/B 各自寫入 → compare → double_review
    ann_a = tmp_path / "ann_a.json"
    ann_b = tmp_path / "ann_b.json"
    common = {"expected_items": [{"name": "AK-47 | Redline",
                                  "wear": "Field-Tested"}],
              "seller_price": "5000", "currency": "TWD", "wear": "Field-Tested",
              "stattrak": False, "item_image_indexes": [0],
              "expected_raw_vision_safe": True,
              "expected_safe_for_production": True, "image_kind": "single",
              "should_create_price": True, "role": "selling"}
    ann_a.write_text(json.dumps(common), encoding="utf-8")
    ann_b.write_text(json.dumps(dict(common)), encoding="utf-8")
    rev_dir = tmp_path / "reviews"
    r1 = _run_script("review_real_case.py", "--case-id", "real_z",
                     "--reviewer", "reviewer_a",
                     "--annotations-json", str(ann_a), "--output", str(rev_dir))
    r2 = _run_script("review_real_case.py", "--case-id", "real_z",
                     "--reviewer", "reviewer_b",
                     "--annotations-json", str(ann_b), "--output", str(rev_dir))
    assert r1.returncode == 0 and r2.returncode == 0
    assert (rev_dir / "reviewer_a.json").exists()
    assert (rev_dir / "reviewer_b.json").exists()
    rc = _run_script("compare_real_case_reviews.py", "--reviews-dir",
                     str(rev_dir), "--json")
    assert rc.returncode == 0, rc.stderr
    out = json.loads(rc.stdout)
    assert out["decision"] == "double_review"


def test_adjudication_cli_requires_disputed(tmp_path):
    rev_dir = tmp_path / "reviews"
    r = _run_script("adjudicate_real_case.py", "--case-id", "real_z",
                    "--adjudicator", "reviewer_c", "--reason", "x",
                    "--reviews-dir", str(rev_dir))
    # reviews 不存在 → compare 讀取失敗 → exit 2
    assert r.returncode in (1, 2)


def test_cli_offline_no_secret_deps():
    # 所有新 CLI 不 import requests/facebook/LLM/vision SDK/網路 subprocess
    for script in ("create_real_case_intake.py", "redact_real_case.py",
                   "review_real_case.py", "compare_real_case_reviews.py",
                   "adjudicate_real_case.py"):
        src = (Path(SCRIPTS) / script).read_text(encoding="utf-8")
        assert "import requests" not in src
        assert "facebook" not in src.lower()
        assert "openai" not in src.lower() and "deepseek" not in src.lower()
        assert "anthropic" not in src.lower()
        assert "urllib" not in src.lower()
        assert "socket" not in src.lower()
        assert "subprocess" not in src
        assert "os.environ" not in src, "不讀取 production secrets"


def test_cli_runs_offline_with_socket_blocked(monkeypatch, tmp_path):
    # Phase 6.4C2-A.3：monkeypatch 不傳入 subprocess → 改為
    # 同一 process import CLI module 並呼叫 main()（socket 確實被封鎖）
    import socket
    import importlib.util

    def _blocked(*args, **kwargs):
        raise OSError("network blocked by test")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "socket",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("network blocked")))
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    spec = importlib.util.spec_from_file_location(
        "redact_real_case_mod",
        os.path.join(SCRIPTS, "redact_real_case.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"raw_text": "售 A 算5000"}), encoding="utf-8")
    out = tmp_path / "out"
    code = mod.main([
        "--input", str(raw), "--case-id", "real_x",
        "--source-provenance", "user_supplied_real",
        "--authorization", "user_supplied", "--redaction-version", "v1",
        "--output", str(out)])
    assert code == 0, f"離線執行失敗"
    assert (out / "real_x.draft.json").exists(), "socket 封鎖下仍成功"


# ================================================================
# Phase 6.4C2-A.4 — Review dry-run parity / exit 2 / image_count
# ================================================================
def _valid_ann():
    return {"expected_items": [{"name": "AK-47 | Redline", "wear": "FT"}],
            "expected_raw_vision_safe": True,
            "expected_safe_for_production": True,
            "seller_price": "5000", "currency": "TWD", "wear": "Field-Tested",
            "stattrak": False, "item_image_indexes": [0],
            "image_kind": "single", "should_create_price": True,
            "role": "selling"}


def test_review_dry_run_empty_annotations_rejected(tmp_path):
    ann = tmp_path / "ann.json"
    ann.write_text(json.dumps({}), encoding="utf-8")
    r = _run_script("review_real_case.py", "--case-id", "real_x",
                    "--reviewer", "reviewer_a", "--annotations-json", str(ann),
                    "--output", str(tmp_path / "out"), "--dry-run")
    assert r.returncode == 2, "空 annotations dry-run 必須拒絕"
    assert "annotations_empty" in r.stderr


def test_review_dry_run_invalid_schema_rejected(tmp_path):
    ann = tmp_path / "ann.json"
    ann.write_text(json.dumps({"expected_items": "bad"}), encoding="utf-8")
    r = _run_script("review_real_case.py", "--case-id", "real_x",
                    "--reviewer", "reviewer_a", "--annotations-json", str(ann),
                    "--output", str(tmp_path / "out"), "--dry-run")
    assert r.returncode == 2
    assert "驗證失敗" in r.stderr


def test_review_dry_run_nested_sensitive_rejected(tmp_path):
    ann = tmp_path / "ann.json"
    ann.write_text(json.dumps(
        {"expected_items": [{"name": "A", "token": "sk-abc"}],
         "expected_raw_vision_safe": True,
         "expected_safe_for_production": True}), encoding="utf-8")
    r = _run_script("review_real_case.py", "--case-id", "real_x",
                    "--reviewer", "reviewer_a", "--annotations-json", str(ann),
                    "--output", str(tmp_path / "out"), "--dry-run")
    assert r.returncode == 2
    assert "auth_key" in r.stderr


def test_review_dry_run_valid_writes_nothing(tmp_path):
    ann = tmp_path / "ann.json"
    ann.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    r = _run_script("review_real_case.py", "--case-id", "real_x",
                    "--reviewer", "reviewer_a", "--annotations-json", str(ann),
                    "--output", str(tmp_path / "out"), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "out" / "reviewer_a.json").exists(), \
        "dry-run 不寫 reviewer 檔"


def test_compare_invalid_review_schema_exit_2(tmp_path):
    # reviewer_a 手動寫入非法 schema → compare exit 2（不 traceback）
    rev = tmp_path / "reviews"
    rev.mkdir()
    (rev / "reviewer_a.json").write_text(json.dumps(
        {"schema_version": "wrong", "case_id": "x", "reviewer_id": "reviewer_a",
         "reviewed_at": "2026-08-01T12:00:00Z", "annotations": _valid_ann()}),
        encoding="utf-8")
    r = _run_script("compare_real_case_reviews.py", "--reviews-dir", str(rev))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr, "不得 traceback"


def test_compare_malformed_json_exit_2(tmp_path):
    rev = tmp_path / "reviews"
    rev.mkdir()
    (rev / "reviewer_a.json").write_text("{not valid json", encoding="utf-8")
    r = _run_script("compare_real_case_reviews.py", "--reviews-dir", str(rev))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_adjudicate_invalid_review_schema_exit_2(tmp_path):
    rev = tmp_path / "reviews"
    rev.mkdir()
    (rev / "reviewer_a.json").write_text(json.dumps(
        {"schema_version": "wrong", "case_id": "x", "reviewer_id": "reviewer_a",
         "reviewed_at": "2026-08-01T12:00:00Z", "annotations": _valid_ann()}),
        encoding="utf-8")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    r = _run_script("adjudicate_real_case.py", "--case-id", "x",
                    "--adjudicator", "reviewer_c", "--reason", "r",
                    "--final-gt-json", str(gt), "--reviews-dir", str(rev))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_adjudicate_malformed_review_json_exit_2(tmp_path):
    rev = tmp_path / "reviews"
    rev.mkdir()
    (rev / "reviewer_a.json").write_text("{bad", encoding="utf-8")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    r = _run_script("adjudicate_real_case.py", "--case-id", "x",
                    "--adjudicator", "reviewer_c", "--reason", "r",
                    "--final-gt-json", str(gt), "--reviews-dir", str(rev))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_cli_validation_failure_no_traceback(tmp_path):
    # 多種 validation failure 全部不 traceback
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"raw_text": "售 A 算5000"}), encoding="utf-8")
    r = _run_script("create_real_case_intake.py", "--input", str(raw),
                    "--case-id", "x", "--source-provenance", "user_supplied_real",
                    "--authorization", "maybe", "--redaction-version", "v1",
                    "--storage-reference", "secure-store://x")
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


# ---- image_count 嚴格型別 ----
def _intake_with_image_count(tmp_path, value):
    raw = tmp_path / "payload.json"
    payload = {"original_storage_reference": "secure-store://ic",
               "image_count": value}
    raw.write_text(json.dumps(payload), encoding="utf-8")
    return _run_script("create_real_case_intake.py", "--input", str(raw),
                       "--case-id", "ic", "--source-provenance",
                       "user_supplied_real", "--authorization",
                       "user_supplied", "--redaction-version", "v1",
                       "--output", str(tmp_path / "out"))


def test_image_count_numeric_string_exit_2(tmp_path):
    r = _intake_with_image_count(tmp_path, "1")
    assert r.returncode == 2, "不接受字串 '1'"
    assert "image_count" in r.stdout + r.stderr


def test_image_count_float_exit_2(tmp_path):
    r = _intake_with_image_count(tmp_path, 1.0)
    assert r.returncode == 2, "不接受 1.0"
    assert "image_count" in r.stdout + r.stderr


def test_image_count_bool_exit_2(tmp_path):
    r = _intake_with_image_count(tmp_path, True)
    assert r.returncode == 2, "不接受 true"
    assert "image_count" in r.stdout + r.stderr


def test_image_count_negative_exit_2(tmp_path):
    r = _intake_with_image_count(tmp_path, -3)
    assert r.returncode == 2, "不接受負數"
    assert "image_count" in r.stdout + r.stderr


def test_image_count_valid_integer_accepted(tmp_path):
    r = _intake_with_image_count(tmp_path, 0)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out" / "ic.intake.json").exists()


# ================================================================
# Phase 6.4C2-A.5 — CLI error containment / notes privacy gate
# ================================================================
def _load_cli_module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"{name}_mod", os.path.join(SCRIPTS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _intake_main(tmp_path, mod, extra=()):
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "secure-store://ec"}), encoding="utf-8")
    args = ["--input", str(raw), "--case-id", "ec",
            "--source-provenance", "user_supplied_real",
            "--authorization", "user_supplied", "--redaction-version", "v1",
            "--output", str(tmp_path / "out"), *extra]
    return mod.main(args)


def test_intake_invalid_redacted_by_exit_2(tmp_path, monkeypatch, capsys):
    # --redacted-by 非法 → constructor ValueError → exit 2（不 traceback）
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--redacted-by", "unknown_user"])
    assert code == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.err and "Traceback" not in out.out
    assert not (tmp_path / "out" / "ec.intake.json").exists()


def test_intake_model_validation_no_traceback(tmp_path, monkeypatch, capsys):
    # model hash validation failure → exit 2（不 traceback、不打印 input）
    mod = _load_cli_module("create_real_case_intake")
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps({
        "original_storage_reference": "secure-store://ec",
        "image_count": 1, "original_image_hashes": ["BADHASH"]}),
        encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.err
    assert "BADHASH" not in out.err, "不得打印完整 input payload"


def test_intake_output_oserror_exit_2(tmp_path, monkeypatch, capsys):
    import pathlib
    mod = _load_cli_module("create_real_case_intake")
    # setup 在 monkeypatch 前完成
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "secure-store://ec"}), encoding="utf-8")

    def _boom(self, *a, **k):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(pathlib.Path, "write_text", _boom)
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.err


def test_review_output_oserror_exit_2(tmp_path, monkeypatch, capsys):
    import pathlib
    mod = _load_cli_module("review_real_case")
    ann = tmp_path / "ann.json"
    ann.write_text(json.dumps(_valid_ann()), encoding="utf-8")

    def _boom(self, *a, **k):
        raise OSError("write failed (simulated)")
    monkeypatch.setattr(pathlib.Path, "write_text", _boom)
    code = mod.main(["--case-id", "rc", "--reviewer", "reviewer_a",
                     "--annotations-json", str(ann),
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.err


def test_compare_output_oserror_exit_2(tmp_path, monkeypatch, capsys):
    import pathlib
    mod = _load_cli_module("compare_real_case_reviews")
    rev = tmp_path / "reviews"
    rev.mkdir()
    (rev / "reviewer_a.json").write_text(json.dumps(
        {"schema_version": "real-review-v1", "case_id": "cc",
         "reviewer_id": "reviewer_a", "reviewed_at": "2026-08-01T12:00:00Z",
         "annotations": _valid_ann()}), encoding="utf-8")
    (rev / "reviewer_b.json").write_text(json.dumps(
        {"schema_version": "real-review-v1", "case_id": "cc",
         "reviewer_id": "reviewer_b", "reviewed_at": "2026-08-01T12:00:00Z",
         "annotations": _valid_ann()}), encoding="utf-8")

    def _boom(self, *a, **k):
        raise OSError("write failed (simulated)")
    monkeypatch.setattr(pathlib.Path, "write_text", _boom)
    code = mod.main(["--reviews-dir", str(rev), "--json",
                     "--output", str(tmp_path / "out.json")])
    assert code == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.err


def test_adjudication_output_oserror_exit_2(tmp_path, monkeypatch, capsys):
    import os as _os
    mod = _load_cli_module("adjudicate_real_case")
    rev = tmp_path / "reviews"
    rev.mkdir()
    ann_a = _valid_ann()
    ann_b = dict(_valid_ann(), seller_price="6000")  # 不一致 → disputed
    (rev / "reviewer_a.json").write_text(json.dumps(
        {"schema_version": "real-review-v1", "case_id": "ac",
         "reviewer_id": "reviewer_a", "reviewed_at": "2026-08-01T12:00:00Z",
         "annotations": ann_a}), encoding="utf-8")
    (rev / "reviewer_b.json").write_text(json.dumps(
        {"schema_version": "real-review-v1", "case_id": "ac",
         "reviewer_id": "reviewer_b", "reviewed_at": "2026-08-01T12:00:00Z",
         "annotations": ann_b}), encoding="utf-8")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    before_a = (rev / "reviewer_a.json").read_text(encoding="utf-8")
    before_b = (rev / "reviewer_b.json").read_text(encoding="utf-8")

    # Phase 6.4C2-A.6：atomic_write_pair 用 os.replace（非 Path.write_text）
    real_replace = _os.replace
    calls = {"n": 0}

    def _flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # commit#2（adjudication）失敗
            raise OSError("simulated replace failure")
        return real_replace(src, dst)
    monkeypatch.setattr(_os, "replace", _flaky)
    code = mod.main(["--case-id", "ac", "--adjudicator", "reviewer_c",
                     "--reason", "r", "--final-gt-json", str(gt),
                     "--reviews-dir", str(rev)])
    assert code == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.err
    assert "atomic_write_failed" in out.err, "固定錯誤碼"
    # reviewer A/B 原始檔未被改寫
    assert (rev / "reviewer_a.json").read_text(encoding="utf-8") == before_a
    assert (rev / "reviewer_b.json").read_text(encoding="utf-8") == before_b
    # 不得留下半套（兩檔皆不存在或皆舊版）
    assert not (rev / "adjudication.json").exists(), "無舊檔時不得留下半套 adjudication"
    assert not (rev / "final_ground_truth.json").exists(), "無舊檔時不得留下半套 GT"


def test_all_cli_write_failures_no_traceback(tmp_path, monkeypatch, capsys):
    # 四支 CLI 寫失敗全部 exit 2、無 traceback（實際呼叫 main）
    import os as _os
    import pathlib
    # 先完成所有 setup（monkeypatch 前）
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "secure-store://ec"}), encoding="utf-8")
    ann = tmp_path / "ann.json"
    ann.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    rev = tmp_path / "reviews2"
    rev.mkdir()
    review_payload = {"schema_version": "real-review-v1", "case_id": "x",
                      "reviewer_id": "reviewer_a",
                      "reviewed_at": "2026-08-01T12:00:00Z",
                      "annotations": _valid_ann()}
    (rev / "reviewer_a.json").write_text(json.dumps(review_payload),
                                         encoding="utf-8")
    # B 不一致 → disputed（adjudication 前置）
    ann_b = dict(_valid_ann(), seller_price="6000")
    review_payload["reviewer_id"] = "reviewer_b"
    review_payload["annotations"] = ann_b
    (rev / "reviewer_b.json").write_text(json.dumps(review_payload),
                                         encoding="utf-8")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")

    monkeypatch.setattr(pathlib.Path, "write_text", _boom_write)
    # create
    mod = _load_cli_module("create_real_case_intake")
    assert mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")]) == 2
    assert "Traceback" not in capsys.readouterr().err
    # review
    mod = _load_cli_module("review_real_case")
    assert mod.main(["--case-id", "x", "--reviewer", "reviewer_a",
                     "--annotations-json", str(ann),
                     "--output", str(tmp_path / "out")]) == 2
    assert "Traceback" not in capsys.readouterr().err
    # compare
    mod = _load_cli_module("compare_real_case_reviews")
    assert mod.main(["--reviews-dir", str(rev), "--json",
                     "--output", str(tmp_path / "o.json")]) == 2
    assert "Traceback" not in capsys.readouterr().err
    # adjudicate（atomic_write_pair 用 os.replace）
    monkeypatch.undo()
    real_replace = _os.replace
    monkeypatch.setattr(_os, "replace",
                        lambda s, d: (_ for _ in ()).throw(
                            OSError("simulated")))
    mod = _load_cli_module("adjudicate_real_case")
    assert mod.main(["--case-id", "x", "--adjudicator", "reviewer_c",
                     "--reason", "r", "--final-gt-json", str(gt),
                     "--reviews-dir", str(rev)]) == 2
    out = capsys.readouterr()
    assert "Traceback" not in out.err
    assert "atomic_write_failed" in out.err


def _boom_write(self, *a, **k):
    raise OSError("simulated write failure")


# ---- Notes privacy gate ----
def test_intake_notes_email_rejected(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--notes", "聯絡 aaa@bbb.com"])
    assert code == 2
    out = capsys.readouterr()
    assert "aaa@bbb.com" not in out.out + out.err, "不得回顯完整 notes"


def test_intake_notes_phone_rejected(tmp_path):
    mod = _load_cli_module("create_real_case_intake")
    assert _intake_main(tmp_path, mod, ["--notes", "電話 0912345678"]) == 2


def test_intake_notes_token_rejected(tmp_path):
    mod = _load_cli_module("create_real_case_intake")
    assert _intake_main(tmp_path, mod, ["--notes", "token sk-abc"]) == 2


def test_intake_notes_http_url_rejected(tmp_path):
    mod = _load_cli_module("create_real_case_intake")
    assert _intake_main(tmp_path, mod,
                        ["--notes", "https://example.com/x"]) == 2


def test_intake_notes_facebook_url_rejected(tmp_path):
    mod = _load_cli_module("create_real_case_intake")
    assert _intake_main(tmp_path, mod,
                        ["--notes", "https://www.facebook.com/abc"]) == 2


def test_intake_notes_local_path_rejected(tmp_path):
    mod = _load_cli_module("create_real_case_intake")
    assert _intake_main(tmp_path, mod,
                        ["--notes", r"C:\Users\user\Desktop\x"]) == 2


def test_intake_safe_notes_accepted(tmp_path):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--notes", "使用者主動提供，圖片共 2 張"])
    assert code == 0
    assert (tmp_path / "out" / "ec.intake.json").exists()


def test_rejected_notes_not_echoed_in_cli_output(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--notes", "電話 0912345678 聯絡 me"])
    assert code == 2
    out = capsys.readouterr()
    assert "0912345678" not in out.out + out.err, "敏感值不得回顯"
    assert "tw_mobile" in out.out + out.err, "只輸出 finding code"


def test_rejected_notes_write_nothing(tmp_path):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--notes", "token sk-abc"])
    assert code == 2
    assert not (tmp_path / "out" / "ec.intake.json").exists(), \
        "notes 被拒時不得寫檔"


# ================================================================
# Phase 6.4C2-A.6 — Constructor error 敏感值淨化
# ================================================================
def test_intake_invalid_redacted_by_value_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--redacted-by", "unknown_user"])
    assert code == 2
    out = capsys.readouterr()
    assert "unknown_user" not in out.out + out.err, "原始值不得回顯"
    assert "manifest_validation_failed" in out.err, "固定錯誤碼"


def test_intake_real_name_redacted_by_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--redacted-by", "王小明"])
    assert code == 2
    out = capsys.readouterr()
    assert "王小明" not in out.out + out.err, "真實姓名不得回顯"
    assert "manifest_validation_failed" in out.err


def test_constructor_error_does_not_echo_sensitive_input(tmp_path, capsys):
    # 非法 hash（含敏感樣式）→ CLI scan 或 constructor error → 原值不回顯
    mod = _load_cli_module("create_real_case_intake")
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps({
        "original_storage_reference": "secure-store://ec",
        "image_count": 1,
        "original_image_hashes": ["SECRETEMAIL@x.com"[:64]]}),
        encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SECRETEMAIL" not in out.out + out.err, "敏感原值不得回顯"
    assert not (tmp_path / "out" / "ec.intake.json").exists()


def test_intake_constructor_error_uses_fixed_code(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--redacted-by", "x y"])
    assert code == 2
    out = capsys.readouterr()
    assert "manifest_validation_failed" in out.err
    assert "x y" not in out.out + out.err


def test_intake_oserror_does_not_echo_sensitive_path(tmp_path, monkeypatch,
                                                     capsys):
    import pathlib
    mod = _load_cli_module("create_real_case_intake")
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "secure-store://ec"}), encoding="utf-8")
    secret_dir = tmp_path / "C:\\SecretUser\\data"

    def _boom(self, *a, **k):
        raise OSError(f"cannot write {secret_dir} (permission)")
    monkeypatch.setattr(pathlib.Path, "write_text", _boom)
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SecretUser" not in out.out + out.err, "敏感路徑不得回顯"
    assert "manifest_write_failed" in out.err, "固定錯誤碼"


def test_intake_error_output_contains_no_repr_value(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod, ["--redacted-by", "abc", "--notes",
                                        "聯絡 aaa@bbb.com"])
    assert code == 2
    out = capsys.readouterr()
    assert "abc" not in out.out + out.err
    assert "aaa@bbb.com" not in out.out + out.err
    assert not (tmp_path / "out" / "ec.intake.json").exists()


# ================================================================
# Phase 6.4C2-A.7 — Full CLI error redaction（adjudicate + intake）
# ================================================================
def _review_file(rev, reviewer, ann, case_id="ac"):
    (rev / f"{reviewer}.json").write_text(json.dumps(
        {"schema_version": "real-review-v1", "case_id": case_id,
         "reviewer_id": reviewer, "reviewed_at": "2026-08-01T12:00:00Z",
         "annotations": ann}), encoding="utf-8")


def test_adjudicate_review_real_name_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("adjudicate_real_case")
    rev = tmp_path / "reviews"
    rev.mkdir()
    _review_file(rev, "reviewer_a", _valid_ann())
    data = json.loads((rev / "reviewer_a.json").read_text(encoding="utf-8"))
    data["reviewer_id"] = "王小明"  # 真實姓名 → schema 失敗
    (rev / "reviewer_a.json").write_text(json.dumps(data), encoding="utf-8")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    code = mod.main(["--case-id", "ac", "--adjudicator", "reviewer_c",
                     "--reason", "r", "--final-gt-json", str(gt),
                     "--reviews-dir", str(rev)])
    assert code == 2
    out = capsys.readouterr()
    assert "王小明" not in out.out + out.err
    assert "review_validation_failed" in out.err


def test_adjudicate_case_id_values_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("adjudicate_real_case")
    rev = tmp_path / "reviews"
    rev.mkdir()
    _review_file(rev, "reviewer_a", _valid_ann(), case_id="real_secret_001")
    _review_file(rev, "reviewer_b", dict(_valid_ann(), seller_price="6000"),
                 case_id="real_secret_001")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    code = mod.main(["--case-id", "other_case", "--adjudicator", "reviewer_c",
                     "--reason", "r", "--final-gt-json", str(gt),
                     "--reviews-dir", str(rev)])
    assert code == 2
    out = capsys.readouterr()
    assert "real_secret_001" not in out.out + out.err, "review case_id 不回顯"
    assert "other_case" not in out.out + out.err, "CLI case_id 不回顯"
    assert "case_id_mismatch" in out.err


def test_adjudicate_review_path_not_echoed(tmp_path, capsys):
    secret_rev = tmp_path / "SecretReviewDir"
    secret_rev.mkdir()
    # malformed JSON → review_validation_failed（路徑不回顯）
    (secret_rev / "reviewer_a.json").write_text("{broken", encoding="utf-8")
    r = _run_script("adjudicate_real_case.py", "--case-id", "ac",
                    "--adjudicator", "reviewer_c", "--reason", "r",
                    "--final-gt-json", str(tmp_path / "gt.json"),
                    "--reviews-dir", str(secret_rev))
    out = r.stdout + r.stderr
    assert "SecretReviewDir" not in out, "review 路徑不回顯"
    assert "review_validation_failed" in r.stderr


def test_adjudicate_final_gt_path_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("adjudicate_real_case")
    rev = tmp_path / "reviews"
    rev.mkdir()
    _review_file(rev, "reviewer_a", _valid_ann())
    _review_file(rev, "reviewer_b", dict(_valid_ann(), seller_price="6000"))
    missing_gt = tmp_path / "SecretGT" / "gt.json"
    code = mod.main(["--case-id", "ac", "--adjudicator", "reviewer_c",
                     "--reason", "r", "--final-gt-json", str(missing_gt),
                     "--reviews-dir", str(rev)])
    assert code == 2
    out = capsys.readouterr()
    assert "SecretGT" not in out.out + out.err
    assert "final_gt_read_failed" in out.err


def test_adjudicate_json_fragment_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("adjudicate_real_case")
    rev = tmp_path / "reviews"
    rev.mkdir()
    _review_file(rev, "reviewer_a", _valid_ann())
    _review_file(rev, "reviewer_b", dict(_valid_ann(), seller_price="6000"))
    bad_gt = tmp_path / "gt.json"
    bad_gt.write_text('{"expected_items": [{"name": "SECRETSKIN', encoding="utf-8")
    code = mod.main(["--case-id", "ac", "--adjudicator", "reviewer_c",
                     "--reason", "r", "--final-gt-json", str(bad_gt),
                     "--reviews-dir", str(rev)])
    assert code == 2
    out = capsys.readouterr()
    assert "SECRETSKIN" not in out.out + out.err, "JSON fragment 不回顯"
    assert "final_gt_invalid_json" in out.err


def test_adjudicate_frontend_errors_use_fixed_codes(tmp_path, capsys):
    mod = _load_cli_module("adjudicate_real_case")
    rev = tmp_path / "reviews"
    rev.mkdir()
    _review_file(rev, "reviewer_a", _valid_ann())
    _review_file(rev, "reviewer_b", dict(_valid_ann(), seller_price="6000"))
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    # malformed review JSON → review_validation_failed（exit 2）
    bad_rev = tmp_path / "bad_reviews"
    bad_rev.mkdir()
    (bad_rev / "reviewer_a.json").write_text("{bad", encoding="utf-8")
    code = mod.main(["--case-id", "ac", "--adjudicator", "reviewer_c",
                     "--reason", "r", "--final-gt-json", str(gt),
                     "--reviews-dir", str(bad_rev)])
    assert code == 2
    out = capsys.readouterr()
    assert "review_validation_failed" in out.err


def test_adjudicate_all_failure_paths_no_traceback(tmp_path, capsys):
    # 多種 failure 路徑全部無 traceback、固定碼
    mod = _load_cli_module("adjudicate_real_case")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps(_valid_ann()), encoding="utf-8")
    rev = tmp_path / "reviews"
    rev.mkdir()
    _review_file(rev, "reviewer_a", _valid_ann())
    _review_file(rev, "reviewer_b", dict(_valid_ann(), seller_price="6000"))
    bad_rev = tmp_path / "bad_reviews"
    bad_rev.mkdir()
    (bad_rev / "reviewer_a.json").write_text("{bad", encoding="utf-8")
    scenarios = [
        # malformed review → review_validation_failed（exit 2）
        (["--case-id", "x", "--adjudicator", "reviewer_c", "--reason", "r",
          "--final-gt-json", str(gt), "--reviews-dir", str(bad_rev)], 2),
        # final GT 不存在 → final_gt_read_failed（exit 2）
        (["--case-id", "ac", "--adjudicator", "reviewer_c", "--reason", "r",
          "--final-gt-json", str(tmp_path / "missing.json"),
          "--reviews-dir", str(rev)], 2),
    ]
    for argv, expected in scenarios:
        code = mod.main(argv)
        assert code == expected
        out = capsys.readouterr()
        assert "Traceback" not in out.out + out.err


# ---- Intake frontend fixed codes ----
def test_intake_input_path_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    secret = tmp_path / "SecretInput" / "data.json"
    code = mod.main(["--input", str(secret), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SecretInput" not in out.out + out.err
    assert "input_not_found" in out.out + out.err


def test_intake_json_fragment_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    raw = tmp_path / "payload.json"
    raw.write_text('{"broken": SECRETFRAGMENT', encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SECRETFRAGMENT" not in out.out + out.err
    assert "input_invalid_json" in out.out + out.err


def test_intake_image_count_value_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "secure-store://ec",
         "image_count": "SECRETCOUNT"}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SECRETCOUNT" not in out.out + out.err
    assert "image_count_invalid_type" in out.out + out.err


def test_intake_hash_value_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "secure-store://ec",
         "image_count": 1,
         "original_image_hashes": ["SECRETHASHVALUE"]}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SECRETHASHVALUE" not in out.out + out.err
    assert "image_hash_validation_failed" in out.out + out.err


def test_intake_storage_reference_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "https://SECRETSTORAGE.example/x"}),
        encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SECRETSTORAGE" not in out.out + out.err
    assert "storage_reference_invalid" in out.out + out.err


def test_intake_frontend_errors_use_fixed_codes(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _intake_main(tmp_path, mod)
    assert code == 0  # 正常路徑成功
    capsys.readouterr()
    # invalid authorization → 固定碼
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": "secure-store://ec"}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "maybe",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out2")])
    assert code == 2
    out = capsys.readouterr()
    assert "maybe" not in out.out + out.err, "原始 authorization 不回顯"
    assert "invalid_authorization" in out.out + out.err


# ================================================================
# Phase 6.4C2-A.8 — Intake success output redaction
# ================================================================
SECRET_CASE = "SecretCase123"
SECRET_REF = "secure-store://secret-resource-999"


def _secret_dry_run(tmp_path, mod):
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": SECRET_REF}), encoding="utf-8")
    return mod.main(["--input", str(raw), "--case-id", SECRET_CASE,
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out"), "--dry-run"])


def test_intake_dry_run_uses_fixed_success_code(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _secret_dry_run(tmp_path, mod)
    assert code == 0
    out = capsys.readouterr()
    assert "dry_run_valid" in out.out, "固定成功碼"


def test_intake_dry_run_does_not_echo_case_id(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _secret_dry_run(tmp_path, mod)
    assert code == 0
    out = capsys.readouterr()
    assert SECRET_CASE not in out.out + out.err, "case_id 不回顯"


def test_intake_dry_run_does_not_echo_storage_reference(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _secret_dry_run(tmp_path, mod)
    assert code == 0
    out = capsys.readouterr()
    assert SECRET_REF not in out.out + out.err, "storage reference 不回顯"


def test_intake_dry_run_does_not_echo_provenance_or_authorization(
        tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _secret_dry_run(tmp_path, mod)
    assert code == 0
    out = capsys.readouterr()
    assert "user_supplied_real" not in out.out + out.err, "provenance 不回顯"
    assert "internal_owned" not in out.out + out.err


def test_intake_dry_run_writes_nothing(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _secret_dry_run(tmp_path, mod)
    assert code == 0
    capsys.readouterr()
    assert not (tmp_path / "out").exists(), "dry-run 不得建立 output directory"
    assert not (tmp_path / "out" / f"{SECRET_CASE}.intake.json").exists()


def test_intake_success_uses_fixed_code(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _secret_dry_run(tmp_path, mod)  # 觸發一次（清 capsys）
    assert code == 0
    capsys.readouterr()
    code = _intake_main(tmp_path, mod)
    assert code == 0
    out = capsys.readouterr()
    assert "manifest_written" in out.out, "正式成功固定碼"


def test_intake_success_does_not_echo_output_path(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    _secret_dry_run(tmp_path, mod)
    capsys.readouterr()
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": SECRET_REF}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", SECRET_CASE,
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr()
    assert str(tmp_path) not in out.out + out.err, "output path 不回顯"
    assert "intake.json" not in out.out + out.err, "manifest filename 不回顯"


def test_intake_success_does_not_echo_case_id(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    _secret_dry_run(tmp_path, mod)
    capsys.readouterr()
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": SECRET_REF}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", SECRET_CASE,
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr()
    assert SECRET_CASE not in out.out + out.err, "case_id 不回顯"


def test_intake_success_writes_manifest(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    _secret_dry_run(tmp_path, mod)
    capsys.readouterr()
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": SECRET_REF}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", SECRET_CASE,
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 0
    capsys.readouterr()
    out_file = tmp_path / "out" / f"{SECRET_CASE}.intake.json"
    assert out_file.exists(), "manifest 正常寫入既定位置"
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["case_id"] == SECRET_CASE
    assert data["original_storage_reference"] == SECRET_REF


def test_intake_success_output_has_no_absolute_path(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    _secret_dry_run(tmp_path, mod)
    capsys.readouterr()
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": SECRET_REF}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", SECRET_CASE,
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr()
    assert "C:" not in out.out + out.err, "無絕對路徑"
    assert "Users" not in out.out + out.err, "無本機使用者名稱"
    assert "\\" not in out.out + out.err, "無路徑分隔符"


# ================================================================
# Phase 6.4C2-A.8.1 — Storage reference fixed code + exact output
# ================================================================
def test_intake_missing_storage_reference_fixed_code(tmp_path, capsys):
    # 無 storage reference（--storage-reference 缺 + payload 無）→ 固定碼
    mod = _load_cli_module("create_real_case_intake")
    code = mod.main(["--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert out.out.strip() == "[intake] ❌ storage_reference_invalid", \
        f"exact output 不符：{out.out!r}"
    assert out.err == ""
    assert "Traceback" not in out.out + out.err
    assert not (tmp_path / "out" / "ec.intake.json").exists()


def test_intake_invalid_storage_reference_fixed_code(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = mod.main(["--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--storage-reference", "https://example.com/x",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert out.out.strip() == "[intake] ❌ storage_reference_invalid"
    assert out.err == ""


def test_intake_storage_reference_error_exact_output(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = mod.main(["--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--storage-reference", "secure-store://",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert out.out.strip() == "[intake] ❌ storage_reference_invalid"
    assert out.err == ""


def test_intake_storage_reference_value_not_echoed(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = mod.main(["--case-id", "ec",
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--storage-reference", "https://SECRETHOST.example/x",
                     "--output", str(tmp_path / "out")])
    assert code == 2
    out = capsys.readouterr()
    assert "SECRETHOST" not in out.out + out.err, "原始值不回顯"
    assert out.out.strip() == "[intake] ❌ storage_reference_invalid"


def test_intake_dry_run_exact_success_output(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    code = _secret_dry_run(tmp_path, mod)
    assert code == 0
    out = capsys.readouterr()
    assert out.out.strip() == "[intake] dry_run_valid", \
        f"exact output 不符：{out.out!r}"
    assert out.err == "", f"stderr 必須為空：{out.err!r}"


def test_intake_dry_run_single_output_line(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    _secret_dry_run(tmp_path, mod)
    out = capsys.readouterr()
    lines = [ln for ln in out.out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"必須只有一行輸出：{lines}"


def test_intake_success_exact_output(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    _secret_dry_run(tmp_path, mod)
    capsys.readouterr()
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": SECRET_REF}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", SECRET_CASE,
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr()
    assert out.out.strip() == "[intake] manifest_written", \
        f"exact output 不符：{out.out!r}"
    assert out.err == "", f"stderr 必須為空：{out.err!r}"


def test_intake_success_single_output_line(tmp_path, capsys):
    mod = _load_cli_module("create_real_case_intake")
    _secret_dry_run(tmp_path, mod)
    capsys.readouterr()
    raw = tmp_path / "payload.json"
    raw.write_text(json.dumps(
        {"original_storage_reference": SECRET_REF}), encoding="utf-8")
    code = mod.main(["--input", str(raw), "--case-id", SECRET_CASE,
                     "--source-provenance", "user_supplied_real",
                     "--authorization", "user_supplied",
                     "--redaction-version", "v1",
                     "--output", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr()
    lines = [ln for ln in out.out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"必須只有一行輸出：{lines}"
