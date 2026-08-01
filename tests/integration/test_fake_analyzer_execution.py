# -*- coding: utf-8 -*-
"""test_fake_analyzer_execution.py — Phase 6.4C2-B1 端到端 fake execution"""
import hashlib
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from alkaid_cs2.evaluation.analyzer_audit import (  # noqa: E402
    AUDIT_SCHEMA_VERSION_V2,
)
from alkaid_cs2.evaluation.external_analyzer_adapter import (  # noqa: E402
    FakeExternalAnalyzerAdapter,
)
from alkaid_cs2.evaluation.external_analyzer_runner import (  # noqa: E402
    EligibleAnalyzerCase, build_execution_plan,
    execute_external_analyzer_plan,
)
from alkaid_cs2.evaluation.secure_image_loader import (  # noqa: E402
    InMemorySecureImageLoader,
)

FAKE_BYTES = b"fake-image-001"
FAKE_SHA = hashlib.sha256(FAKE_BYTES).hexdigest()


def _case(case_id="case-A", refs=("secure-store://img-1",),
          hashes=(FAKE_SHA,)):
    return EligibleAnalyzerCase(
        source_case_id=case_id, storage_references=list(refs),
        image_hashes=list(hashes), review_status="double_review",
        privacy_scan_status="passed", source="anonymized_real")


def _full_env(tmp_path):
    root = tmp_path / "local_data"
    cache = root / "cache"
    runs = root / "runs"
    cache.mkdir(parents=True, exist_ok=True)
    runs.mkdir(parents=True, exist_ok=True)
    return str(root), str(cache), str(runs)


FAKE_ANALYZER_NAME = "fake-analyzer"
FAKE_ANALYZER_VERSION = "0.1.0"


def _run_engine(tmp_path, *, case=None, adapter=None, loader=None,
                analyzer_version=FAKE_ANALYZER_VERSION, run_salt="salt-1",
                analyzer_name=FAKE_ANALYZER_NAME,
                adapter_name=FAKE_ANALYZER_NAME):
    case = case or _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt=run_salt,
        adapter_name=adapter_name, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _full_env(tmp_path)
    return execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case],
        loader=loader if loader is not None else InMemorySecureImageLoader(
            {"secure-store://img-1": FAKE_BYTES}),
        adapter=adapter if adapter is not None else FakeExternalAnalyzerAdapter(),
        cache_dir=cache, audit_dir=runs, allowed_root=root,
        analyzer_name=analyzer_name, analyzer_version=analyzer_version), \
        root, cache, runs


def test_end_to_end_fake_execution(tmp_path):
    s, root, cache, runs = _run_engine(tmp_path)
    assert s.status == "completed"
    assert s.succeeded_image_count == 1
    assert s.cache_write_count == 1
    # cache 檔 + v2 audit
    assert len(os.listdir(cache)) == 1
    audits = os.listdir(runs)
    assert len(audits) == 1
    a = json.load(open(os.path.join(runs, audits[0], "audit.json"),
                       encoding="utf-8"))
    assert a["schema_version"] == AUDIT_SCHEMA_VERSION_V2
    assert a["result"] == "completed"
    assert a["cache_miss_count"] == 1


def test_sentinel_not_in_outputs(tmp_path):
    # 非空 sentinel case：stdout 不適用（engine 無 print）；驗證 audit/cache
    case = _case(case_id="SECRET_CASE_12345")
    s, root, cache, runs = _run_engine(tmp_path, case=case)
    assert s.status == "completed"
    for f in os.listdir(runs):
        raw = open(os.path.join(runs, f, "audit.json"),
                   encoding="utf-8").read()
        assert "SECRET_CASE_12345" not in raw, "sentinel 不得在 audit"
        assert "secure-store://" not in raw, "storage ref 不得在 audit"
    for f in os.listdir(cache):
        raw = open(os.path.join(cache, f), encoding="utf-8").read()
        assert "SECRET_CASE_12345" not in raw
        assert "secure-store://" not in raw
        assert "fake-image-001" not in raw, "bytes 內容不得進 cache"


def test_engine_never_writes_repository_local_data(tmp_path, monkeypatch):
    # 完整 filesystem spy：執行期間 repository local_data 零存取
    import pathlib
    repo_runs = os.path.join(PROJECT_ROOT, "local_data")
    touches = {"n": 0}
    real_open = open
    real_stat = os.stat

    def spy_open(*a, **k):
        if a and isinstance(a[0], str) and repo_runs in a[0]:
            touches["n"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_open(*a, **k)

    def spy_stat(*a, **k):
        if a and isinstance(a[0], str) and repo_runs in a[0]:
            touches["n"] += 1
            raise AssertionError("repository_local_data_access_forbidden")
        return real_stat(*a, **k)

    monkeypatch.setattr("builtins.open", spy_open)
    monkeypatch.setattr(os, "stat", spy_stat)
    s, root, cache, runs = _run_engine(tmp_path)
    assert s.status == "completed"
    assert touches["n"] == 0, "engine 不得存取 repository local_data"


def test_network_and_sdk_zero_tolerance(tmp_path, monkeypatch):
    # socket / urllib / http.client 全部封鎖下 engine 正常執行
    import socket
    import urllib.request
    import http.client
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)
    s, root, cache, runs = _run_engine(tmp_path)
    assert s.status == "completed"
    assert calls["n"] == 0, "執行期間不得有任何網路呼叫"


def test_production_sdk_not_importable_path():
    # engine/adapter/loader 原始碼不得有 production SDK import
    import re
    files = [
        os.path.join(PROJECT_ROOT, "alkaid_cs2", "evaluation",
                     "external_analyzer_runner.py"),
        os.path.join(PROJECT_ROOT, "alkaid_cs2", "evaluation",
                     "external_analyzer_adapter.py"),
        os.path.join(PROJECT_ROOT, "alkaid_cs2", "evaluation",
                     "secure_image_loader.py"),
        os.path.join(PROJECT_ROOT, "alkaid_cs2", "evaluation",
                     "analyzer_cache.py"),
    ]
    banned = ("requests", "httpx", "aiohttp", "openai", "anthropic",
              "deepseek", "google.generativeai")
    for f in files:
        src = open(f, encoding="utf-8").read()
        for b in banned:
            for m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", src,
                                 re.MULTILINE):
                mod = m.group(1)
                if mod == b or mod.startswith(b + "."):
                    pytest.fail(f"{os.path.basename(f)} 不得 import {b}")


def test_engine_reads_no_env_secrets(tmp_path):
    # 執行期間不得讀取 API key/token/cookie env
    import builtins
    calls = {"n": 0}
    real_getenv = os.getenv
    real_environ_get = os.environ.get

    def spy_getenv(k, *a):
        if any(s in k.upper() for s in ("KEY", "TOKEN", "COOKIE", "SECRET")):
            calls["n"] += 1
        return real_getenv(k, *a)

    def spy_environ_get(k, *a):
        if any(s in k.upper() for s in ("KEY", "TOKEN", "COOKIE", "SECRET")):
            calls["n"] += 1
        return real_environ_get(k, *a)

    os.getenv = spy_getenv
    os.environ.get = spy_environ_get
    try:
        s, root, cache, runs = _run_engine(tmp_path)
        assert s.status == "completed"
        assert calls["n"] == 0, "engine 不得讀取 secrets env"
    finally:
        os.getenv = real_getenv
        os.environ.get = real_environ_get


def test_no_real_data_safe_stop_unchanged():
    # repository 正式 manifest anonymized_real=0 → CLI dry-run 仍 exit 2
    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "scripts",
                                      "run_real_analyzer_evaluation.py"),
         "--dry-run", "--allow-external-analyzer"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
        env={**os.environ, "EVALUATION_ALLOW_EXTERNAL_ANALYZER": "1"},
        timeout=60)
    assert r.returncode == 2
    assert "no_eligible_real_cases" in r.stdout
    assert "no_eligible_real_images" in r.stdout


def test_deterministic_rerun(tmp_path):
    # 相同輸入（同 run_salt）+ 全新 cache 目錄 → 語意完全一致
    import tempfile
    td1 = tempfile.mkdtemp(prefix="b1-det-")
    td2 = tempfile.mkdtemp(prefix="b1-det-")

    def run_in(td):
        root = os.path.join(td, "local_data")
        cache = os.path.join(root, "cache")
        runs = os.path.join(root, "runs")
        os.makedirs(cache, exist_ok=True)
        os.makedirs(runs, exist_ok=True)
        case = _case()
        plan = build_execution_plan(
            eligible_cases=[case.as_dict()], run_salt="salt-X",
            adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
            created_at="2026-08-01T00:00:00Z")
        return execute_external_analyzer_plan(
            plan=plan, eligible_cases=[case], cache_dir=cache,
            audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
            analyzer_version=FAKE_ANALYZER_VERSION,
            loader=InMemorySecureImageLoader(
                {"secure-store://img-1": FAKE_BYTES}),
            adapter=FakeExternalAnalyzerAdapter())

    s1 = run_in(td1)
    s2 = run_in(td2)
    for k in ("planned_image_count", "processed_image_count",
              "attempted_image_count", "succeeded_image_count",
              "cache_miss_count", "cache_write_count", "status"):
        assert s1.to_dict()[k] == s2.to_dict()[k], f"{k} 不一致"
    # 同目錄重跑 → cache hit（cache 重用語意）
    root, cache, runs = _full_env(tmp_path)
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-X",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    a = execute_external_analyzer_plan(
        loader=InMemorySecureImageLoader({"secure-store://img-1": FAKE_BYTES}),
        adapter=FakeExternalAnalyzerAdapter(), **kw)
    b = execute_external_analyzer_plan(
        loader=InMemorySecureImageLoader({"secure-store://img-1": FAKE_BYTES}),
        adapter=FakeExternalAnalyzerAdapter(), **kw)
    assert a.cache_miss_count == 1 and b.cache_hit_count == 1
    # 兩次 normalized result 一致
    rec = json.load(open(os.path.join(cache, os.listdir(cache)[0]),
                         encoding="utf-8"))
    assert rec["normalized_result"]["items"][0]["name"] == "fake-item-001"
