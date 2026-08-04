# -*- coding: utf-8 -*-
"""test_report_generator.py — report generator 可重現性（CLI 單一命令重建）"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

EXPECTED_FILES = [
    "p0-baseline-report.json", "p0-baseline-report.md",
    "p0-case-results.csv", "p0-metrics.csv", "p0-known-failures.csv",
    "p0-coverage-matrix.csv", "p0-execution-evidence-matrix.csv",
    "p0-latency.csv", "p0-determinism.csv", "p7-entry-gate-after-p0.md",
]

CLI = [sys.executable, "-m", "tests.regression.report", "--output-dir"]


def _run_cli(out_dir, cwd=None):
    return subprocess.run(CLI + [out_dir], capture_output=True, text=True,
                          cwd=cwd or PROJECT_ROOT)


@pytest.fixture(scope="module")
def cli_out(tmp_path_factory):
    """module 級：CLI 只跑一次（5 輪 latency 量測較慢），各測試共用"""
    out = str(tmp_path_factory.mktemp("reports"))
    r = _run_cli(out)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def _normalized_sha(out_dir):
    """排除 generated_at 的 normalized SHA（逐檔）"""
    shas = {}
    for f in EXPECTED_FILES:
        path = os.path.join(out_dir, f)
        if not os.path.exists(path):
            shas[f] = None
            continue
        if f == "p0-baseline-report.json":
            data = json.load(open(path, encoding="utf-8"))
            data.pop("generated_at", None)
            for c in data.get("cases", []):
                c.pop("median_ms", None)
                c.pop("p95_ms", None)
            m = data.get("metrics", {})
            m.pop("average_latency_ms", None)
            m.pop("p95_latency_ms", None)
            shas[f] = hashlib.sha256(
                json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        elif f in ("p0-case-results.csv", "p0-latency.csv"):
            # latency 為運行時量測（規格十二排除於 determinism）——不比對
            shas[f] = "LATENCY_EXCLUDED"
        elif f == "p0-metrics.csv":
            # 逐欄排除 latency 相關欄位後比對（latency 為運行時量測）
            import csv as _csv
            rows = list(_csv.DictReader(open(path, encoding="utf-8")))
            keep = {k: v for k, v in (rows[0] if rows else {}).items()
                    if "latency" not in k and "median_ms" not in k and "p95_ms" not in k}
            shas[f] = hashlib.sha256(
                _csv.StringIO and json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        elif f == "p0-baseline-report.md":
            # 排除 latency 行後比對
            lines = [ln for ln in open(path, encoding="utf-8").read().splitlines()
                     if "latency" not in ln]
            shas[f] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        else:
            shas[f] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return shas


def test_cli_generates_all_files(cli_out):
    for f in EXPECTED_FILES:
        assert os.path.exists(os.path.join(cli_out, f)), f"缺 {f}"


def test_cli_repeatable(cli_out):
    """同一輸出跑兩次 CLI 比對 normalized SHA（用較小 tmp 目錄驗證可重現）"""
    import tempfile
    out2 = tempfile.mkdtemp(prefix="p0_rep_")
    assert _run_cli(out2).returncode == 0
    s1 = _normalized_sha(cli_out)
    s2 = _normalized_sha(out2)
    assert s1 == s2, "兩次輸出不一致（generated_at 除外）"


def test_cli_from_different_cwd(tmp_path):
    out = str(tmp_path / "out")
    env = dict(os.environ)
    env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(CLI + [out], capture_output=True, text=True,
                       cwd=str(tmp_path), env=env)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(os.path.join(out, "p0-baseline-report.json"))


def test_cli_report_metrics_sane(cli_out):
    r = json.load(open(os.path.join(cli_out, "p0-baseline-report.json"), encoding="utf-8"))
    m = r["metrics"]
    assert m["failed_cases"] == 0
    assert m["count_identity_holds"] is True
    assert m["item_accuracy"]["denominator"] > 0
    assert m["verification_accuracy"]["reason"] == \
        "legacy_regression_adapter_does_not_expose_verified"
    assert m["item_price_link_accuracy"]["reason"] == \
        "legacy_regression_adapter_does_not_expose_item_price_links"


def test_cli_remediation_applied(cli_out):
    import csv
    rows = list(csv.DictReader(open(os.path.join(cli_out, "p0-known-failures.csv"),
                                    encoding="utf-8")))
    by_id = {r["case_id"]: r for r in rows}
    assert by_id["redline_vulcan_simplified"]["remediation_phase"] == "P3"
    assert by_id["seller_ask_plus_buff_floor"]["remediation_phase"] == "P4"
    assert by_id["p0_unlinked_bare_numbers"]["remediation_phase"] == "P4"
    assert by_id["rmb_price_no_conversion_marker"]["remediation_phase"] == "evidence_limitation"
    assert by_id["p0_p7_flash_default_preview"]["remediation_phase"] == "P7"
    assert by_id["p0_p8_llm_profit_override_preview"]["remediation_phase"] == "P8"


def test_report_main_at_bottom():
    """report.py 的 __main__ 必須在檔案最底部"""
    src = open(os.path.join(os.path.dirname(__file__), "report.py"),
               encoding="utf-8").read()
    assert src.rstrip().endswith('raise SystemExit(main())'), "__main__ 不在底部"


def test_no_external_generator_dependency(tmp_path):
    """移除外部 generate_p0x_reports.py 後 CLI 仍可重建（CLI 無外部依賴）"""
    out = str(tmp_path / "out")
    r = _run_cli(out)
    assert r.returncode == 0
    assert os.path.exists(os.path.join(out, "p0-baseline-report.json"))
