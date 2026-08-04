# -*- coding: utf-8 -*-
"""test_determinism.py — P0 determinism：離線 case 5 輪輸出 hash 唯一性"""
import hashlib
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import analyze_arbitrage as aa  # noqa: E402
from tests.regression.legacy_adapter import extract_legacy  # noqa: E402

POSTS = json.load(open(os.path.join(os.path.dirname(__file__),
                                    "fixtures", "posts.json"), encoding="utf-8"))

# 離線 case（字典命中、無 LLM/網路依賴）
OFFLINE_IDS = [
    "simple_single_twd", "p0_bundle_total_price", "p0_float_value_not_price",
    "p0_rmb_single_conversion", "p0_usd_single_conversion",
    "p0_mode_off_legacy", "p0_mode_shadow", "p0_mode_v2_only",
    "stat_trak_ak", "knife_star_prefix",
]

EXCLUDE_FIELDS = {"timestamp", "latency", "trace_id", "run_id", "generated_at"}


def _norm(result: dict) -> str:
    """標準化輸出（排除時間/latency/run-id 欄位）"""
    keep = {k: v for k, v in result.items() if k not in EXCLUDE_FIELDS}
    return json.dumps(keep, sort_keys=True, ensure_ascii=False)


def test_offline_cases_deterministic_5_rounds():
    """離線 case 5 輪 → normalized hash 唯一數 = 1"""
    for cid in OFFLINE_IDS:
        hashes = set()
        for _ in range(5):
            r = extract_legacy(next(p for p in POSTS if p["id"] == cid)["text"])
            hashes.add(hashlib.sha256(_norm(r).encode("utf-8")).hexdigest())
        assert len(hashes) == 1, f"{cid}: {len(hashes)} unique hashes"


def test_all_fixture_ids_resolve():
    for p in POSTS:
        assert p["id"] == p["case_id"], p["id"]


def test_output_normalization_excludes_noise():
    assert "timestamp" in EXCLUDE_FIELDS
    assert "latency" in EXCLUDE_FIELDS
