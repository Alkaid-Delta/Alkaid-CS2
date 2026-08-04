# -*- coding: utf-8 -*-
"""test_real_analyzer_runner.py — CLI dry-run + repository safety（Phase 6.4C2-B0）"""
import json
import os
import subprocess
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(PROJECT_ROOT, "scripts")

sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402


def _run_cli(*extra, env_extra=None):
    env = dict(os.environ)
    env.pop("EVALUATION_ALLOW_EXTERNAL_ANALYZER", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS,
                                      "run_real_analyzer_evaluation.py"),
         *extra],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
        env=env, timeout=120)


def _load_cli_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_real_analyzer_evaluation_mod",
        os.path.join(SCRIPTS, "run_real_analyzer_evaluation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dry_run_no_real_cases_exit_2():
    r = _run_cli("--dry-run", "--allow-external-analyzer",
                 env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert r.returncode == 2, r.stderr


def test_dry_run_no_real_cases_fixed_output():
    r = _run_cli("--dry-run", "--allow-external-analyzer",
                 env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert "no_eligible_real_cases" in r.stdout
    assert "Traceback" not in r.stdout + r.stderr


def test_dry_run_flag_missing():
    r = _run_cli("--dry-run",
                 env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert r.returncode == 2
    assert "external_analyzer_flag_missing" in r.stdout


def test_dry_run_env_missing():
    r = _run_cli("--dry-run", "--allow-external-analyzer")
    assert r.returncode == 2
    assert "external_analyzer_env_missing" in r.stdout


def test_dry_run_does_not_load_bytes(tmp_path):
    mod = _load_cli_module()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    code = mod.main(["--dry-run", "--allow-external-analyzer",
                     "--run-salt", "test-salt",
                     "--manifest", str(tmp_path / "manifest.json")],
                    local_data_root_override=str(tmp_path / "local_data"))
    assert code == 2  # 無 real case → blocked
    # load_eligible_cases 回傳空 → 無 bytes 載入路徑


def test_dry_run_does_not_make_network_call():
    import socket
    mod = _load_cli_module()

    def _blocked(*a, **k):
        raise OSError("blocked")
    orig = (socket.create_connection, socket.socket, socket.getaddrinfo)
    socket.create_connection = _blocked
    socket.socket = _blocked
    socket.getaddrinfo = _blocked
    try:
        code = mod.main(["--dry-run", "--allow-external-analyzer",
                         "--run-salt", "s"])
        assert code in (0, 2), "CLI 不得因 socket 封鎖而 crash"
    finally:
        (socket.create_connection, socket.socket,
         socket.getaddrinfo) = orig


def test_dry_run_no_sensitive_echo():
    # Phase 6.4C2-B0.4：移除弱式 OR assertion
    # blocked 輸出必須全部是已知固定錯誤碼（無敏感值）
    r = _run_cli("--dry-run", "--allow-external-analyzer",
                 env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    out = r.stdout + r.stderr
    assert "secure-store://" not in out
    assert "C:" not in out
    known_codes = {
        "external_analyzer_flag_missing", "external_analyzer_env_missing",
        "external_analyzer_not_authorized", "no_eligible_real_cases",
        "no_eligible_real_images", "secure_image_loader_unavailable",
        "analyzer_adapter_unavailable",
    }
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines, "blocked 執行必須有輸出"
    for line in lines:
        assert line.startswith("[real-analyzer] "), f"非預期輸出行：{line}"
        code = line[len("[real-analyzer] "):]
        assert code in known_codes, f"輸出含未受控內容：{line}"
    assert r.stderr == "", f"stderr 必須為空：{r.stderr!r}"


def test_audit_output_not_tracked_by_git():
    r = subprocess.run(["git", "ls-files", "local_data"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert r.stdout.strip() == "", "local_data 不得被 Git 追蹤"


def test_local_data_is_gitignored():
    r = subprocess.run(["git", "check-ignore",
                        "local_data/evaluation_analyzer_cache/x.json"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert r.returncode == 0, "local_data/ 必須在 .gitignore"


def test_no_real_images_tracked():
    r = subprocess.run(["git", "ls-files", "tests/fixtures/evaluation_real"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    for f in r.stdout.splitlines():
        assert not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp",
                                       ".gif")), f"圖片不得進 Git：{f}"


def test_no_raw_json_tracked():
    # evaluation_real 的 10 個 JSON 是 6.4C1 已 commit 的 manual_fixture
    # （匿名化、privacy 0 errors、author=anonymous）——非 raw 私人 JSON。
    # 驗證：每個 tracked JSON 的 source 都非 anonymized_real（無 raw real 資料）
    r = subprocess.run(["git", "ls-files", "tests/fixtures/evaluation_real"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    for f in r.stdout.splitlines():
        if f.endswith("manifest.json"):
            continue
        data = json.load(open(os.path.join(PROJECT_ROOT, f),
                              encoding="utf-8"))
        assert data.get("source") != "anonymized_real", \
            f"raw real JSON 不得進 Git：{f}"
        assert data.get("author") in ("anonymous", "synthetic"), \
            f"fixture 必須匿名化：{f}"


def test_no_base64_in_evaluation_real():
    r = subprocess.run(["git", "grep", "-n", "data:image.*base64",
                        "--", "tests/fixtures/evaluation_real"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert r.stdout.strip() == "", "evaluation_real 不得含 base64 圖片"


def test_no_http_urls_in_evaluation_real():
    r = subprocess.run(["git", "grep", "-nE",
                        "https?://|fbcdn|facebook.com",
                        "--", "tests/fixtures/evaluation_real"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert r.stdout.strip() == "", "evaluation_real 不得含 http/fbcdn URL"


def test_production_files_unchanged():
    """Phase-aware allowlist guard（P2.1 方案 B）。

    目前工作樹允許的變更僅限 P2 核准檔案；任何其他 production 檔案
    （含 B2 evaluation/、crawler、bridge、vision、dict）被修改即失敗。
    未來新階段加入時，須由人工更新此 allowlist。
    """
    r = subprocess.run(["git", "diff", "--name-only"],
                       capture_output=True, text=True, cwd=PROJECT_ROOT)
    allowed = {
        # Phase P2 / P2.1 核准檔案
        "alkaid_cs2/services/item_validator.py",
        "alkaid_cs2/parsers/item_parser.py",
        "alkaid_cs2/adapters/legacy_adapter.py",
        "alkaid_cs2/domain/price.py",
        "alkaid_cs2/services/currency.py",
        "alkaid_cs2/adapters/legacy_adapter.py",
        "alkaid_cs2/integration/production_bridge.py",
        "tests/integration/test_currency_hardening.py",
        "tests/integration/test_currency_fail_closed.py",
        "tests/integration/test_v2_currency_handoff.py",
        "tests/unit/test_legacy_adapter.py",
        "tests/unit/test_production_bridge.py",
        # Phase P0：regression baseline fixtures/tests（production 零修改）
        "tests/regression/fixtures/posts.json",
        "tests/regression/fixtures/expected.json",
        "tests/regression/test_golden_posts.py",
        "tests/regression/report.py",
        "tests/regression/test_fixture_schema.py",
        "tests/regression/test_p0_coverage.py",
        "tests/regression/test_metrics.py",
        "tests/regression/test_determinism.py",
        "docs/phase0-regression-baseline.md",
        "docs/phase1-money-currency-hardening.md",
        "analyze_arbitrage.py",
        "tests/unit/test_item_validator.py",
        "tests/integration/test_validation_hard_gate.py",
        "tests/integration/test_controlled_integration.py",
        "tests/integration/test_vision_controlled_integration.py",
        "tests/integration/test_real_analyzer_runner.py",
        "tests/regression/legacy_adapter.py",
        "tests/regression/test_golden_posts.py",
        "tests/regression/fixtures/posts.json",
        "tests/regression/fixtures/expected.json",
        "docs/phase2-validation-hard-gate.md",
    }
    for f in r.stdout.splitlines():
        assert f in allowed, f"非核准檔案被修改：{f}"


# ================================================================
# Phase 6.4C2-B0.1 — Output confinement + test isolation
# ================================================================
SECRET_CASE_ID = "SECRET_CASE_12345"


from contextlib import contextmanager, nullcontext as _nullcontext  # noqa: E402

ENV_FLAG = "EVALUATION_ALLOW_EXTERNAL_ANALYZER"


@contextmanager
def _temporary_env(name: str, value: str):
    """暫時設定 env；結束後完整恢復舊值（Phase 6.4C2-B0.5）。"""
    existed = name in os.environ
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if existed:
            os.environ[name] = old
        else:
            os.environ.pop(name, None)


class _CliResult:
    def __init__(self, code, out, err):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def _run_cli_isolated(tmp_path, *extra, env_extra=None):
    """同 process main() + 程式內 local_data_root_override（不碰 repository）。

    env_extra 透過 monkeypatch.setenv 真正套用（phase 6.4C2-B0.3）；
    未提供 monkeypatch 時用 try/finally 暫時設定並恢復。
    """
    import importlib.util
    with _temporary_env(ENV_FLAG, "1") if env_extra else _nullcontext():
        spec = importlib.util.spec_from_file_location(
            "cli_mod_iso", os.path.join(SCRIPTS,
                                        "run_real_analyzer_evaluation.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        code = mod.main(
            ["--cache-dir", str(tmp_path / "local_data" / "cache"),
             "--audit-dir", str(tmp_path / "local_data" / "runs"),
             "--manifest", str(tmp_path / "manifest.json"),
             "--fixtures-dir", str(tmp_path / "fixtures"),
             *extra],
            local_data_root_override=str(tmp_path / "local_data"))
    return _CliResult(code, "", "")


def test_audit_dir_outside_local_data_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [], "real_case_count": 0, "double_reviewed_real_count": 0, "disputed_real_count": 0}',
        encoding="utf-8")
    env = dict(os.environ)
    env["EVALUATION_ALLOW_EXTERNAL_ANALYZER"] = "1"
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS,
                                      "run_real_analyzer_evaluation.py"),
         "--dry-run", "--allow-external-analyzer",
         "--audit-dir", str(tmp_path / "outside"),
         "--manifest", str(tmp_path / "manifest.json")],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env,
        timeout=120)
    assert r.returncode == 2
    assert "output_path_not_allowed" in r.stdout


def test_cache_dir_outside_local_data_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [], "real_case_count": 0, "double_reviewed_real_count": 0, "disputed_real_count": 0}',
        encoding="utf-8")
    env = dict(os.environ)
    env["EVALUATION_ALLOW_EXTERNAL_ANALYZER"] = "1"
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS,
                                      "run_real_analyzer_evaluation.py"),
         "--dry-run", "--allow-external-analyzer",
         "--cache-dir", str(tmp_path / "outside"),
         "--manifest", str(tmp_path / "manifest.json")],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env,
        timeout=120)
    assert r.returncode == 2
    assert "output_path_not_allowed" in r.stdout


def test_traversal_path_rejected(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        resolve_local_data_subdir,
    )
    root = tmp_path / "local_data"
    root.mkdir()
    with pytest.raises(ValueError):
        resolve_local_data_subdir(root / ".." / "escape", root)


def test_allowed_local_data_subdir_accepted(tmp_path):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        resolve_local_data_subdir,
    )
    root = tmp_path / "local_data"
    sub = root / "evaluation_analyzer_cache"
    sub.mkdir(parents=True)
    got = resolve_local_data_subdir(sub, root)
    assert got == str(sub.resolve())


def test_rejected_path_not_echoed(tmp_path):
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [], "real_case_count": 0, "double_reviewed_real_count": 0, "disputed_real_count": 0}',
        encoding="utf-8")
    env = dict(os.environ)
    env["EVALUATION_ALLOW_EXTERNAL_ANALYZER"] = "1"
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS,
                                      "run_real_analyzer_evaluation.py"),
         "--dry-run", "--allow-external-analyzer",
         "--audit-dir", str(tmp_path / "SECRET_DIR_outside"),
         "--manifest", str(tmp_path / "manifest.json")],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env,
        timeout=120)
    assert "SECRET_DIR" not in r.stdout + r.stderr, "拒絕的路徑不得回顯"


def test_blocked_audit_is_from_current_run(tmp_path):
    # 執行前目錄為空；執行後精確讀取本次 run 的 audit（不用 found[0] 掃全域）
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [], "real_case_count": 0, "double_reviewed_real_count": 0, "disputed_real_count": 0}',
        encoding="utf-8")
    r = _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                          env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert r.returncode == 2
    runs_dir = tmp_path / "local_data" / "runs"
    runs = list(runs_dir.iterdir()) if runs_dir.exists() else []
    assert len(runs) == 1, f"本次 run 應只新增一個 run 目錄：{runs}"
    audit = json.load(open(runs[0] / "audit.json", encoding="utf-8"))
    assert audit["result"] == "blocked"
    assert "no_eligible_real_cases" in audit["fixed_error_codes"]


def _repo_local_snapshot():
    """repository local_data 內容 snapshot（bytes 層級；供前後比較）。"""
    root = os.path.join(PROJECT_ROOT, "local_data")
    snapshot = {}
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                snapshot[p] = (os.path.getsize(p), open(p, "rb").read())
    return snapshot


def _assert_repository_local_data_untouched(tmp_path):
    """共用隔離流程（Phase 6.4C2-B0.3/B2-A.3）：非 test helper。

    抽成 helper 而非 test 呼叫 test：避免 fixture/monkeypatch 邊界錯誤
    （test 直接呼叫 test 時 tmp_path 是呼叫者的，fixture 不會重新解析，
    造成 subprocess 與 cleanup race）。
    """
    # 完整隔離流程（Phase 6.4C2-B0.3）：顯式傳所有路徑 + override
    before = _repo_local_snapshot()
    import importlib.util
    (tmp_path / "fixtures").mkdir(exist_ok=True)
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "cli_rep", os.path.join(SCRIPTS, "run_real_analyzer_evaluation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with _temporary_env(ENV_FLAG, "1"):
        code = mod.main(
            ["--dry-run", "--allow-external-analyzer",
             "--cache-dir", str(tmp_path / "local_data" / "cache"),
             "--audit-dir", str(tmp_path / "local_data" / "runs"),
             "--manifest", str(tmp_path / "manifest.json"),
             "--fixtures-dir", str(tmp_path / "fixtures")],
            local_data_root_override=str(tmp_path / "local_data"))
    # 斷言 1：repository local_data snapshot 前後完全一致
    after = _repo_local_snapshot()
    assert before == after, "repository local_data 不得被測試改寫"
    # 斷言 2：return code == 2（空 manifest no-data gate）
    assert code == 2
    # 斷言 3：tmp/local_data/runs 下精確建立一個 audit
    runs = list((tmp_path / "local_data" / "runs").iterdir())
    assert len(runs) == 1, f"精確一個 audit：{runs}"
    # 斷言 4-5：result == blocked、reasons 含 no_eligible_real_cases
    audit = json.load(open(runs[0] / "audit.json", encoding="utf-8"))
    assert audit["result"] == "blocked"
    assert "no_eligible_real_cases" in audit["fixed_error_codes"]
    # 斷言 6：tmp cache 不存在或為空
    cache = tmp_path / "local_data" / "cache"
    assert not cache.exists() or list(cache.iterdir()) == []

def test_repository_local_data_untouched_by_tests(tmp_path):
    _assert_repository_local_data_untouched(tmp_path)




def test_blocked_run_creates_exactly_one_current_audit(tmp_path):
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    r = _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                          env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert r.returncode == 2
    runs_dir = tmp_path / "local_data" / "runs"
    runs = list(runs_dir.iterdir()) if runs_dir.exists() else []
    assert len(runs) == 1, f"精確一個 run directory：{runs}"
    audit = json.load(open(runs[0] / "audit.json", encoding="utf-8"))
    assert audit["result"] == "blocked"
    assert "no_eligible_real_cases" in audit["fixed_error_codes"]


def test_dry_run_actual_cache_dir_empty(tmp_path):
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    r = _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                          env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert r.returncode == 2
    cache = tmp_path / "local_data" / "cache"
    assert not cache.exists() or list(cache.iterdir()) == [], \
        "dry-run 實際 cache 目錄必須為空"


def test_no_test_reads_historical_audit():
    # 本檔案所有 audit 讀取都從 tmp_path 隔離 root（無 found[0]/歷史掃描）
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    code_lines = [ln for ln in src.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    code = "\n".join(code_lines)
    pattern = "found" + "[0]"
    msg = "不得使用 found" + "[0] 讀取 audit"
    assert pattern not in code, msg
    # 不讀 repository historical audit 的實質證明由 runtime proof 提供：
    # test_isolated_run_never_reads_repository_historical_audit（monkeypatch
    # 攔截 repository runs 路徑的 open/stat，執行隔離 dry-run，呼叫次數為 0）
    # 此處只保留無自指問題的 source 檢查。


def test_sentinel_case_id_not_echoed(tmp_path, capsys):
    # 非空 sentinel 案例（schema-valid、fixture hash 正確）
    import hashlib as _h
    import importlib.util
    case_id = SECRET_CASE_ID
    fixture = {
        "case_id": case_id, "source": "anonymized_real",
        "original_storage_reference": "secure-store://img-1",
        "original_image_hashes": ["b" * 64]}
    (tmp_path / "fixtures").mkdir()
    fp = tmp_path / "fixtures" / f"{case_id}.json"
    fp.write_bytes(json.dumps(fixture, ensure_ascii=False).encode("utf-8"))
    entry = {
        "case_id": case_id, "source": "anonymized_real",
        "source_provenance": "user_supplied_real",
        "authorization_status": "user_supplied",
        "redaction_version": "v1", "privacy_scan_status": "passed",
        "review_status": "double_review",
        "fixture_sha256": _h.sha256(fp.read_bytes()).hexdigest(),
        "image_reference_count": 1, "analyzer_cache_status": "not_run"}
    m = {"schema_version": "evaluation-real-manifest-v1", "cases": [entry],
         "real_case_count": 1, "double_reviewed_real_count": 1,
         "disputed_real_count": 0}
    (tmp_path / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False), encoding="utf-8")
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "cli_sent2", os.path.join(SCRIPTS, "run_real_analyzer_evaluation.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with _temporary_env(ENV_FLAG, "1"):
        code = mod.main(
        ["--dry-run", "--allow-external-analyzer",
         "--run-salt", "fixed-salt",
         "--cache-dir", str(tmp_path / "local_data" / "cache"),
         "--audit-dir", str(tmp_path / "local_data" / "runs"),
         "--manifest", str(tmp_path / "manifest.json"),
         "--fixtures-dir", str(tmp_path / "fixtures")],
        local_data_root_override=str(tmp_path / "local_data"))
    assert code == 0, "authorized dry-run with eligible case → 0"
    # Phase 6.4C2-B0.4：capsys 真正捕捉 stdout/stderr（exact contract）
    captured = capsys.readouterr()
    assert captured.out.strip() == "[real-analyzer] dry_run_valid", \
        f"exact stdout 不符：{captured.out!r}"
    assert captured.err == "", f"stderr 必須為空：{captured.err!r}"
    assert SECRET_CASE_ID not in captured.out, "sentinel 不得在 stdout"
    assert SECRET_CASE_ID not in captured.err, "sentinel 不得在 stderr"
    assert "secure-store://img-1" not in captured.out, "storage ref 不得在 stdout"
    assert "secure-store://img-1" not in captured.err, "storage ref 不得在 stderr"
    # audit / run dir 名 / plan
    runs_dir = tmp_path / "local_data" / "runs"
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1
    raw_audit = (run_dirs[0] / "audit.json").read_text(encoding="utf-8")
    assert case_id not in raw_audit, "sentinel 不得在 audit"
    assert SECRET_CASE_ID not in run_dirs[0].name, "run dir 名不含 sentinel"
    audit = json.loads(raw_audit)
    assert audit["eligible_case_count"] == 1
    assert audit["eligible_image_count"] == 1


def _assert_sentinel_case_id_not_in_plan():
    """共用 sentinel 流程（Phase 6.4C2-B0.3/B2-A.3）：非 test helper。"""
    # plan serialized 不含原 case ID（plan 只存 opaque key）
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        build_execution_plan,
    )
    plan = build_execution_plan(
        eligible_cases=[{"case_id": SECRET_CASE_ID,
                         "storage_reference": "secure-store://img-1",
                         "image_hashes": ["b" * 64]}],
        run_salt="s", adapter_name="fake", dry_run=True, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    d = json.dumps(plan.to_dict())
    assert SECRET_CASE_ID not in d, "plan 不含原 case ID"
    assert all(len(k) == 64 and k.isalnum() for k in plan.case_keys)


def test_sentinel_case_id_not_in_plan():
    _assert_sentinel_case_id_not_in_plan()

def test_eligible_sentinel_uses_only_opaque_key(tmp_path):
    # authorized dry-run：plan.case_count==1、image_count>0、keys 64-hex
    import hashlib as _h
    import importlib.util
    case_id = SECRET_CASE_ID
    fixture = {
        "case_id": case_id, "source": "anonymized_real",
        "original_storage_reference": "secure-store://img-1",
        "original_image_hashes": ["b" * 64]}
    (tmp_path / "fixtures").mkdir()
    fp = tmp_path / "fixtures" / f"{case_id}.json"
    fp.write_bytes(json.dumps(fixture, ensure_ascii=False).encode("utf-8"))
    entry = {
        "case_id": case_id, "source": "anonymized_real",
        "source_provenance": "user_supplied_real",
        "authorization_status": "user_supplied",
        "redaction_version": "v1", "privacy_scan_status": "passed",
        "review_status": "double_review",
        "fixture_sha256": _h.sha256(fp.read_bytes()).hexdigest(),
        "image_reference_count": 1, "analyzer_cache_status": "not_run"}
    m = {"schema_version": "evaluation-real-manifest-v1", "cases": [entry],
         "real_case_count": 1, "double_reviewed_real_count": 1,
         "disputed_real_count": 0}
    (tmp_path / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "cli_sent3", os.path.join(SCRIPTS, "run_real_analyzer_evaluation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with _temporary_env(ENV_FLAG, "1"):
        code = mod.main(
        ["--dry-run", "--allow-external-analyzer",
         "--run-salt", "fixed-salt",
         "--cache-dir", str(tmp_path / "local_data" / "cache"),
         "--audit-dir", str(tmp_path / "local_data" / "runs"),
         "--manifest", str(tmp_path / "manifest.json"),
         "--fixtures-dir", str(tmp_path / "fixtures")],
        local_data_root_override=str(tmp_path / "local_data"))
    assert code == 0
    runs_dir = tmp_path / "local_data" / "runs"
    audit = json.load(open(list(runs_dir.iterdir())[0] / "audit.json",
                           encoding="utf-8"))
    assert audit["eligible_case_count"] == 1
    assert audit["eligible_image_count"] == 1
    assert SECRET_CASE_ID not in json.dumps(audit)


# ================================================================
# Phase 6.4C2-B0.3 — Runtime isolation proof + env flag handling
# ================================================================
def test_isolated_helper_applies_env_flag(tmp_path, monkeypatch):
    # env_extra 真的使 authorization_env_present == true
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    r = _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                          env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert r.returncode == 2
    runs = list((tmp_path / "local_data" / "runs").iterdir())
    audit = json.load(open(runs[0] / "audit.json", encoding="utf-8"))
    assert audit["authorization_env_present"] is True, "env flag 必須真的套用"
    assert audit["authorization_flag_present"] is True


def test_blocked_audit_records_env_present(tmp_path):
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    r = _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                          env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    runs = list((tmp_path / "local_data" / "runs").iterdir())
    audit = json.load(open(runs[0] / "audit.json", encoding="utf-8"))
    assert audit["result"] == "blocked"
    codes = audit["fixed_error_codes"]
    assert "no_eligible_real_cases" in codes
    assert "no_eligible_real_images" in codes
    # reason 集合不保證只有一項（Phase 6.4C2-B0.3 文件一致性）
    assert len(codes) >= 1


def test_env_restored_after_isolated_run(tmp_path, monkeypatch):
    # env_extra 執行後必須恢復（不污染其他測試）
    monkeypatch.delenv(ENV_FLAG, raising=False)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                      env_extra={"EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"})
    assert "EVALUATION_ALLOW_EXTERNAL_ANALYZER" not in os.environ, \
        "env 必須恢復"

# ================================================================
# Phase 6.4C2-B0.4 — Complete filesystem isolation spy
# ================================================================
REPO_LOCAL_DATA = os.path.abspath(os.path.join(PROJECT_ROOT, "local_data"))


def _is_under_repo_local_data(value) -> bool:
    """PathLike 處理：os.fspath() + abspath（不觸發 stat，避免 spy 遞迴）。"""
    try:
        p = os.path.abspath(os.fspath(value))
        return os.path.commonpath([p, REPO_LOCAL_DATA]) == REPO_LOCAL_DATA
    except (TypeError, ValueError, OSError):
        return False


def _install_fs_spy(monkeypatch):
    """攔截 repository local_data 的讀/寫/stat/list/mkdir/remove/rename/replace
    （Phase 6.4C2-B0.5：低階 OS APIs 全攔截）。"""
    import io
    import pathlib
    touches = {
        "open": 0, "read": 0, "write": 0, "stat": 0, "list": 0,
        "mkdir": 0, "remove": 0, "rename": 0, "replace": 0,
    }
    real_open = open
    real_io_open = io.open
    real_os_stat = os.stat
    real_path_open = pathlib.Path.open
    real_read_text = pathlib.Path.read_text
    real_read_bytes = pathlib.Path.read_bytes
    real_write_text = pathlib.Path.write_text
    real_write_bytes = pathlib.Path.write_bytes
    real_iterdir = pathlib.Path.iterdir
    real_glob = pathlib.Path.glob
    real_rglob = pathlib.Path.rglob

    def _forbid(touch_key, api_name):
        def wrapper(*a, **k):
            target = a[0]
            if _is_under_repo_local_data(target):
                touches[touch_key] += 1
                raise AssertionError(
                    "repository_local_data_access_forbidden")
            return api_name(*a, **k)
        return wrapper

    # builtins.open / io.open：讀寫皆攔（file mode 判斷 read/write）
    def spy_open(*a, **k):
        if a and _is_under_repo_local_data(a[0]):
            mode = a[1] if len(a) > 1 and isinstance(a[1], str) else \
                k.get("mode", "r")
            touches["write" if "w" in mode or "a" in mode or "x" in mode
                   else "read"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_open(*a, **k)

    def spy_io_open(*a, **k):
        if a and _is_under_repo_local_data(a[0]):
            mode = a[1] if len(a) > 1 and isinstance(a[1], str) else \
                k.get("mode", "r")
            touches["write" if "w" in mode or "a" in mode or "x" in mode
                   else "read"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_io_open(*a, **k)

    def spy_stat(*a, **k):
        if a and _is_under_repo_local_data(a[0]):
            touches["stat"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_os_stat(*a, **k)

    def spy_path_open(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["read"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_path_open(self, *a, **k)

    def _spy_read(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["read"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_read_text(self, *a, **k)

    def _spy_read_bytes(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["read"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_read_bytes(self, *a, **k)

    def _spy_write(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["write"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_write_text(self, *a, **k)

    def _spy_write_bytes(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["write"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_write_bytes(self, *a, **k)

    def _spy_iterdir(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["list"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_iterdir(self, *a, **k)

    def _spy_glob(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["list"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_glob(self, *a, **k)

    def _spy_rglob(self, *a, **k):
        if _is_under_repo_local_data(self):
            touches["list"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_rglob(self, *a, **k)

    # 低階 OS APIs（Phase 6.4C2-B0.5）
    real_os_open = os.open
    real_os_listdir = os.listdir
    real_os_scandir = os.scandir
    real_os_mkdir = os.mkdir
    real_os_makedirs = os.makedirs
    real_os_remove = os.remove
    real_os_unlink = os.unlink
    real_os_rename = os.rename
    real_os_replace = os.replace
    real_path_mkdir = pathlib.Path.mkdir
    real_path_unlink = pathlib.Path.unlink
    real_path_rename = pathlib.Path.rename
    real_path_replace = pathlib.Path.replace

    def _os_guard(key, real_fn, api_name):
        def wrapper(*a, **k):
            if a and _is_under_repo_local_data(a[0]):
                touches[key] += 1
                raise AssertionError(
                    "repository_local_data_access_forbidden")
            return real_fn(*a, **k)
        wrapper.__name__ = api_name
        return wrapper

    def _guard_two_paths(key, real_fn, api_name):
        """rename/replace 專用：source 與 destination 都檢查
        （Phase 6.4C2-B0.6）。"""
        def wrapper(src, dst, *args, **kwargs):
            if (_is_under_repo_local_data(src)
                    or _is_under_repo_local_data(dst)):
                touches[key] += 1
                raise AssertionError(
                    "repository_local_data_access_forbidden")
            return real_fn(src, dst, *args, **kwargs)
        wrapper.__name__ = api_name
        return wrapper

    def spy_path_rename(self, target, *args, **kwargs):
        if (_is_under_repo_local_data(self)
                or _is_under_repo_local_data(target)):
            touches["rename"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_path_rename(self, target, *args, **kwargs)

    def spy_path_replace(self, target, *args, **kwargs):
        if (_is_under_repo_local_data(self)
                or _is_under_repo_local_data(target)):
            touches["replace"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_path_replace(self, target, *args, **kwargs)

    monkeypatch.setattr(os, "open",
                        _os_guard("open", real_os_open, "os.open"))
    monkeypatch.setattr(os, "listdir",
                        _os_guard("list", real_os_listdir, "os.listdir"))
    monkeypatch.setattr(os, "scandir",
                        _os_guard("list", real_os_scandir, "os.scandir"))
    monkeypatch.setattr(os, "mkdir",
                        _os_guard("mkdir", real_os_mkdir, "os.mkdir"))
    monkeypatch.setattr(os, "makedirs",
                        _os_guard("mkdir", real_os_makedirs, "os.makedirs"))
    monkeypatch.setattr(os, "remove",
                        _os_guard("remove", real_os_remove, "os.remove"))
    monkeypatch.setattr(os, "unlink",
                        _os_guard("remove", real_os_unlink, "os.unlink"))
    monkeypatch.setattr(os, "rename",
                        _guard_two_paths("rename", real_os_rename,
                                         "os.rename"))
    monkeypatch.setattr(os, "replace",
                        _guard_two_paths("replace", real_os_replace,
                                         "os.replace"))
    monkeypatch.setattr(pathlib.Path, "mkdir",
                        _os_guard("mkdir", real_path_mkdir, "Path.mkdir"))
    monkeypatch.setattr(pathlib.Path, "unlink",
                        _os_guard("remove", real_path_unlink, "Path.unlink"))
    monkeypatch.setattr(pathlib.Path, "rename", spy_path_rename)
    monkeypatch.setattr(pathlib.Path, "replace", spy_path_replace)

    monkeypatch.setattr("builtins.open", spy_open)
    monkeypatch.setattr(io, "open", spy_io_open)
    monkeypatch.setattr(os, "stat", spy_stat)
    monkeypatch.setattr(pathlib.Path, "open", spy_path_open)
    monkeypatch.setattr(pathlib.Path, "read_text", _spy_read)
    monkeypatch.setattr(pathlib.Path, "read_bytes", _spy_read_bytes)
    monkeypatch.setattr(pathlib.Path, "write_text", _spy_write)
    monkeypatch.setattr(pathlib.Path, "write_bytes", _spy_write_bytes)
    monkeypatch.setattr(pathlib.Path, "iterdir", _spy_iterdir)
    monkeypatch.setattr(pathlib.Path, "glob", _spy_glob)
    monkeypatch.setattr(pathlib.Path, "rglob", _spy_rglob)
    return touches


def test_isolated_run_never_accesses_repository_local_data(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    # Phase 6.4C2-B0.4：完整 filesystem spy（讀/寫/stat/list 全攔截，
    # PathLike 經 os.fspath 處理；任何 repository local_data 存取即 fail）
    from pathlib import Path as _P
    touches = _install_fs_spy(monkeypatch)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "cli_iso_proof2", os.path.join(SCRIPTS,
                                       "run_real_analyzer_evaluation.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with _temporary_env(ENV_FLAG, "1"):
        code = mod.main(
            ["--dry-run", "--allow-external-analyzer",
             "--cache-dir", str(tmp_path / "local_data" / "cache"),
             "--audit-dir", str(tmp_path / "local_data" / "runs"),
             "--manifest", str(tmp_path / "manifest.json"),
             "--fixtures-dir", str(tmp_path / "fixtures")],
            local_data_root_override=str(tmp_path / "local_data"))
    # 完整隔離 dry-run 結果
    assert code == 2, "空 manifest → exit 2"
    runs = list((tmp_path / "local_data" / "runs").iterdir())
    assert len(runs) == 1, "精確一個 audit"
    audit = json.load(open(runs[0] / "audit.json", encoding="utf-8"))
    assert audit["result"] == "blocked"
    assert "no_eligible_real_cases" in audit["fixed_error_codes"]
    cache = tmp_path / "local_data" / "cache"
    assert not cache.exists() or list(cache.iterdir()) == []
    # repository local_data 零存取（9 counters，Phase 6.4C2-B0.5）
    for key in ("open", "read", "write", "stat", "list",
                "mkdir", "remove", "rename", "replace"):
        assert touches[key] == 0, f"{key}={touches[key]}"


# ================================================================
# Phase 6.4C2-B0.4 — Env restoration（preexisting value preserved）
# ================================================================
def _empty_manifest(tmp_path):
    (tmp_path / "fixtures").mkdir(exist_ok=True)
    (tmp_path / "manifest.json").write_text(
        '{"schema_version": "evaluation-real-manifest-v1", "cases": [],'
        ' "real_case_count": 0, "double_reviewed_real_count": 0,'
        ' "disputed_real_count": 0}', encoding="utf-8")


def test_preexisting_env_value_restored(tmp_path, monkeypatch):
    # Phase 6.4C2-B0.7：前置狀態用 monkeypatch.setenv（pytest 自動恢復）
    monkeypatch.setenv(ENV_FLAG, "old-value")
    _empty_manifest(tmp_path)
    _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                      env_extra={ENV_FLAG: "1"})
    assert os.environ.get(ENV_FLAG) == "old-value", "preexisting env 值必須恢復"


def test_absent_env_remains_absent(tmp_path, monkeypatch):
    # Phase 6.4C2-B0.7：monkeypatch.delenv（即使 host 原本有值也會恢復）
    monkeypatch.delenv(ENV_FLAG, raising=False)
    _empty_manifest(tmp_path)
    _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                      env_extra={ENV_FLAG: "1"})
    assert ENV_FLAG not in os.environ, "執行後 env 必須保持 absent"


# ================================================================
# Phase 6.4C2-B0.5 — Spy positive controls + env helper audit
# ================================================================
def test_repo_local_data_os_open_is_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.open(os.path.join(REPO_LOCAL_DATA, "x.bin"), os.O_RDONLY)
    assert touches["open"] == 1


def test_repo_local_data_listdir_is_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.listdir(REPO_LOCAL_DATA)
    assert touches["list"] == 1


def test_repo_local_data_scandir_is_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.scandir(REPO_LOCAL_DATA)
    assert touches["list"] == 1


def test_repo_local_data_replace_is_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    src = os.path.join(REPO_LOCAL_DATA, "a")
    dst = os.path.join(REPO_LOCAL_DATA, "b")
    with pytest.raises(AssertionError):
        os.replace(src, dst)
    assert touches["replace"] == 1


def test_repo_local_data_unlink_is_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.unlink(os.path.join(REPO_LOCAL_DATA, "x"))
    assert touches["remove"] == 1


def test_external_tmp_path_operations_are_allowed(tmp_path, monkeypatch):
    # 陽性控制：tmp_path 操作不受攔截（spy 只擋 repository local_data）
    touches = _install_fs_spy(monkeypatch)
    d = tmp_path / "sub"
    d.mkdir()
    f = d / "f.txt"
    f.write_text("ok")
    assert f.read_text() == "ok"
    os.replace(f, d / "g.txt")
    (d / "g.txt").unlink()
    os.listdir(tmp_path)
    for key in ("open", "read", "write", "stat", "list",
                "mkdir", "remove", "rename", "replace"):
        assert touches[key] == 0, f"tmp_path 操作不得觸發 {key}"


def test_preexisting_env_survives_repository_snapshot_test(
        tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "pre-existing")
    _empty_manifest(tmp_path)
    _assert_repository_local_data_untouched(tmp_path)
    assert os.environ.get(ENV_FLAG) == "pre-existing", \
        "repository snapshot 測試後 env 必須恢復"


def test_preexisting_env_survives_sentinel_plan_test(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "pre-existing")
    _empty_manifest(tmp_path)
    _assert_sentinel_case_id_not_in_plan()
    assert os.environ.get(ENV_FLAG) == "pre-existing", \
        "sentinel plan 測試後 env 必須恢復"


def test_absent_env_remains_absent_after_all_helpers(tmp_path, monkeypatch):
    # 執行多個 helper 流程後 env 保持 absent
    monkeypatch.delenv(ENV_FLAG, raising=False)
    _empty_manifest(tmp_path)
    _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                      env_extra={ENV_FLAG: "1"})
    with _temporary_env(ENV_FLAG, "1"):
        pass
    assert ENV_FLAG not in os.environ, "所有 helper 後 env 必須保持 absent"


# ================================================================
# Phase 6.4C2-B0.6 — Two-path guard positive controls
# ================================================================
_REPO_FAKE = os.path.join(REPO_LOCAL_DATA, "fake-target.bin")
_TMP_SRC = os.path.join(os.environ.get("TEMP", "/tmp"), "fake-src.bin")


def test_os_rename_external_source_to_repo_destination_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.rename(_TMP_SRC, _REPO_FAKE)
    assert touches["rename"] == 1


def test_os_replace_external_source_to_repo_destination_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.replace(_TMP_SRC, _REPO_FAKE)
    assert touches["replace"] == 1


def test_path_rename_external_source_to_repo_destination_blocked(monkeypatch):
    from pathlib import Path as _P
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        _P(_TMP_SRC).rename(_REPO_FAKE)
    assert touches["rename"] == 1


def test_path_replace_external_source_to_repo_destination_blocked(monkeypatch):
    from pathlib import Path as _P
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        _P(_TMP_SRC).replace(_REPO_FAKE)
    assert touches["replace"] == 1


def test_os_rename_repo_source_to_external_destination_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.rename(_REPO_FAKE, _TMP_SRC)
    assert touches["rename"] == 1


def test_os_replace_repo_source_to_external_destination_blocked(monkeypatch):
    touches = _install_fs_spy(monkeypatch)
    with pytest.raises(AssertionError):
        os.replace(_REPO_FAKE, _TMP_SRC)
    assert touches["replace"] == 1


def test_external_tmp_to_tmp_rename_replace_allowed(tmp_path, monkeypatch):
    # 陽性控制：tmp→tmp 的 rename/replace 正常允許
    from pathlib import Path as _P
    touches = _install_fs_spy(monkeypatch)
    a = tmp_path / "a.txt"
    a.write_text("x")
    b = tmp_path / "b.txt"
    os.replace(a, b)
    c = tmp_path / "c.txt"
    b.rename(c)
    for key in ("rename", "replace"):
        assert touches[key] == 0, f"tmp→tmp 不得觸發 {key}"


# ================================================================
# Phase 6.4C2-B0.6 — Outer env restoration (parameterized flows)
# ================================================================
def _repository_snapshot_flow(tmp_path):
    _assert_repository_local_data_untouched(tmp_path)


def _sentinel_cli_flow(tmp_path):
    _assert_sentinel_case_id_not_in_plan()


def _isolated_helper_flow(tmp_path):
    _empty_manifest(tmp_path)
    _run_cli_isolated(tmp_path, "--dry-run", "--allow-external-analyzer",
                      env_extra={ENV_FLAG: "1"})


@pytest.mark.parametrize("flow", [
    _repository_snapshot_flow, _sentinel_cli_flow, _isolated_helper_flow,
])
def test_flow_preserves_outer_env_when_present(tmp_path, flow, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "outer-value")
    flow(tmp_path)
    assert os.environ.get(ENV_FLAG) == "outer-value", \
        f"{flow.__name__} 後 outer env 必須恢復"


@pytest.mark.parametrize("flow", [
    _repository_snapshot_flow, _sentinel_cli_flow, _isolated_helper_flow,
])
def test_flow_preserves_outer_env_when_absent(tmp_path, flow, monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    flow(tmp_path)
    assert ENV_FLAG not in os.environ, \
        f"{flow.__name__} 後 env 必須保持 absent"


# ================================================================
# Phase 6.4C2-B0.7 — AST env-mutation audit + process isolation proof
# ================================================================
# ================================================================
# Phase 6.4C2-B0.8 — Complete env AST audit + positive controls
# ================================================================
def _find_direct_env_mutations(source: str) -> list:
    """AST 掃描：找出測試函式內直接修改 os.environ 的節點。

    偵測（Phase 6.4C2-B0.8）：
    - os.environ[...] = ...（Assign/AugAssign subscript）
    - del os.environ[...]（Delete subscript）
    - os.environ.pop / update / clear / setdefault
    - os.environ.__setitem__ / __delitem__
    - os.putenv / os.unsetenv

    只允許 _temporary_env helper 本體直接操作（helper 的責任）。
    不使用函式名稱 exempt allowlist。
    """
    import ast
    tree = ast.parse(source)
    # 收集所有函式範圍（含 helper 標記）
    fn_ranges = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            fn_ranges[node.name] = (
                node.lineno, getattr(node, "end_lineno", node.lineno),
                node.name == "_temporary_env")

    findings = []  # (owner_func, lineno, kind)

    def record(lineno, kind):
        owner = None
        for name, (start, end, is_helper) in fn_ranges.items():
            if start <= lineno <= end:
                if not is_helper:
                    owner = name
                break
        if owner:
            findings.append((owner, lineno, kind))

    def is_os_environ_attr(node):
        return (isinstance(node, ast.Attribute)
                and node.attr == "environ"
                and isinstance(node.value, ast.Name)
                and node.value.id == "os")

    # 1) subscript assign / delete：os.environ["X"] = / += / del
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        else:
            continue
        for t in targets:
            if (isinstance(t, ast.Subscript)
                    and is_os_environ_attr(t.value)):
                record(node.lineno, "os.environ[...]")

    # 2) method calls：pop/update/clear/setdefault/__setitem__/__delitem__
    #    + os.putenv / os.unsetenv
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # os.environ.<method>(...)
        if (isinstance(f, ast.Attribute)
                and is_os_environ_attr(f.value)
                and f.attr in ("pop", "update", "clear", "setdefault",
                               "__setitem__", "__delitem__")):
            record(node.lineno, f"os.environ.{f.attr}")
        # os.putenv(...) / os.unsetenv(...)
        if (isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "os"
                and f.attr in ("putenv", "unsetenv")):
            record(node.lineno, f"os.{f.attr}")
    return findings


def _fn_body_source(source: str, fn_name: str) -> str:
    """抽出一段「假函式」source，供 synthetic positive control 使用。"""
    return f"def {fn_name}(monkeypatch):\n" + "\n".join(
        f"    {ln}" for ln in source.splitlines())


def test_catches_environ_assignment():
    src = _fn_body_source('os.environ["X"] = "1"', "f1")
    assert _find_direct_env_mutations(src), "必須抓到 assign"


def test_catches_environ_delete():
    src = _fn_body_source('del os.environ["X"]', "f2")
    assert _find_direct_env_mutations(src), "必須抓到 delete"


def test_catches_environ_pop():
    src = _fn_body_source('os.environ.pop("X", None)', "f3")
    assert _find_direct_env_mutations(src), "必須抓到 pop"


def test_catches_environ_update():
    src = _fn_body_source('os.environ.update({"X": "1"})', "f4")
    assert _find_direct_env_mutations(src), "必須抓到 update"


def test_catches_environ_clear():
    src = _fn_body_source("os.environ.clear()", "f5")
    assert _find_direct_env_mutations(src), "必須抓到 clear"


def test_catches_environ_setdefault():
    src = _fn_body_source('os.environ.setdefault("X", "1")', "f6")
    assert _find_direct_env_mutations(src), "必須抓到 setdefault"


def test_catches_os_putenv():
    src = _fn_body_source('os.putenv("X", "1")', "f7")
    assert _find_direct_env_mutations(src), "必須抓到 putenv"


def test_catches_os_unsetenv():
    src = _fn_body_source('os.unsetenv("X")', "f8")
    assert _find_direct_env_mutations(src), "必須抓到 unsetenv"


def test_allows_monkeypatch_setenv():
    src = _fn_body_source('monkeypatch.setenv("X", "1")', "f9")
    assert _find_direct_env_mutations(src) == [], "setenv 必須允許"


def test_allows_monkeypatch_delenv():
    src = _fn_body_source('monkeypatch.delenv("X", raising=False)', "f10")
    assert _find_direct_env_mutations(src) == [], "delenv 必須允許"


def test_allows_temporary_env_helper_body():
    src = (
        "def _temporary_env(name, value):\n"
        "    old = os.environ.get(name)\n"
        "    os.environ[name] = value\n"
        "    try:\n"
        "        yield\n"
        "    finally:\n"
        "        os.environ.pop(name, None)\n"
    )
    assert _find_direct_env_mutations(src) == [], "helper 本體必須允許"


def test_no_test_function_directly_mutates_env():
    """正式掃描：目前測試檔的測試函式不得直接修改 os.environ。"""
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    findings = _find_direct_env_mutations(src)
    assert findings == [], f"測試函式直接修改 env：{findings}"


# ================================================================
# Phase 6.4C2-B0.8 — Subprocess proof（flaky 收斂）
# ================================================================
def test_env_test_module_does_not_change_parent_process_env(monkeypatch):
    """subprocess proof：parent sentinel 不變（僅證明 process isolation；
    同-process restoration 主要證據是 monkeypatch runtime tests）。"""
    sentinel = "PARENT_SENTINEL_VALUE"
    monkeypatch.setenv(ENV_FLAG, sentinel)
    proof_file = os.path.join(
        "tests", "integration", "test_real_analyzer_runner.py")
    node_ids = [
        f"{proof_file}::test_flow_preserves_outer_env_when_present",
        f"{proof_file}::test_flow_preserves_outer_env_when_absent",
        f"{proof_file}::test_preexisting_env_value_restored",
        f"{proof_file}::test_absent_env_remains_absent",
    ]
    for run_i in range(3):
        with tempfile.TemporaryDirectory(prefix="b08-proof-") as td:
            child_env = dict(os.environ)  # 從 parent copy
            child_env[ENV_FLAG] = sentinel  # 明確保留 sentinel
            child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            child_env["PYTEST_ADDOPTS"] = ""  # 不繼承 parent pytest opts
            r = subprocess.run(
                [sys.executable, "-m", "pytest", *node_ids,
                 "-p", "no:cacheprovider", "-q", "--no-header",
                 "-o", f"cache_dir={os.path.join(td, '.pytest_cache')}"],
                capture_output=True, text=True, cwd=PROJECT_ROOT,
                env=child_env, timeout=180)
            assert r.returncode == 0, \
                f"run {run_i + 1}/3 失敗：stdout 尾端={r.stdout[-300:]!r} " \
                f"stderr 尾端={r.stderr[-300:]!r}"
            assert os.environ.get(ENV_FLAG) == sentinel, \
                f"run {run_i + 1}/3 後 parent sentinel 不變"
    assert os.environ.get(ENV_FLAG) == sentinel, \
        "3 次 subprocess 後 parent sentinel 仍不變"
