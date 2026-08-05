# -*- coding: utf-8 -*-
"""test_mode_trace_execution.py — P6-R1-E5-R2 boundary snapshot harness

- snapshot_post/diff_snapshots：通用 snapshot diff（key/value-hash——不只看 underscore）
- path-boundary snapshots：process/structured/legacy 邊界（legacy 邊界 = process_level wrapper 明確標記）
- mutation 由 boundary snapshot diff 計算（不依最後 event）
- source 從 bridge_return event 聚合（非 closure）
- network guard：requests + socket（+ project lookup seam）——network_attempt 進 event log
- 16 negative self-tests（驗證工具能力——不寫入正式 PASS traces）
"""
import sys
import os
import json
import io
import contextlib
import tempfile
import hashlib
import socket
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import analyze_arbitrage as aa
import alkaid_cs2.integration.production_bridge as pb
from alkaid_cs2.integration.production_bridge import ProductionParseResult
from alkaid_cs2.domain.market_candidate import MarketCandidate
from alkaid_cs2.domain.item_candidate import ItemRole
from alkaid_cs2.domain.price import Money
from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.price_candidate import PriceType

TRACE_OUT = os.environ.get("P6_TRACE_OUT", os.path.join(tempfile.gettempdir(), "p6-e5r2-traces"))
SCHEMA_VERSION = "P6-E5-R3-1"


# ---------------- 通用 snapshot diff（純函式） ----------------
def snapshot_post(post):
    """保存可 JSON 序列化的 key -> normalized value + value hash。"""
    snap = {}
    for k, v in post.items():
        if isinstance(v, Decimal):
            nv = str(v)
        elif isinstance(v, (dict, list)):
            nv = json.dumps(v, sort_keys=True, default=str)
        else:
            nv = v
        try:
            vh = hashlib.sha256(json.dumps(nv, sort_keys=True, default=str).encode()).hexdigest()
        except TypeError:
            nv = str(nv)
            vh = hashlib.sha256(nv.encode()).hexdigest()
        snap[k] = {"value": nv, "hash": vh}
    return snap


def diff_snapshots(before, after):
    """通用 diff：added/removed/changed keys + unchanged_count（value hash 比較）。"""
    b_keys = set(before)
    a_keys = set(after)
    added = sorted(a_keys - b_keys)
    removed = sorted(b_keys - a_keys)
    changed = sorted(k for k in (a_keys & b_keys) if before[k]["hash"] != after[k]["hash"])
    unchanged = len(a_keys & b_keys) - len(changed)
    return {"added_keys": added, "removed_keys": removed, "changed_keys": changed,
            "unchanged_count": unchanged}


def snap_hash(snap):
    return hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()


def _cand(mhn="AK-47 | Redline (Field-Tested)", amt=5000):
    m = Money(amount=Decimal(str(amt)), currency=Currency.TWD)
    return MarketCandidate(item_index=0, market_hash_name=mhn, verified=True,
                           verified_by="trusted_dictionary_exact", item_role=ItemRole.SELLING,
                           price_index=0, price_type=PriceType.SELLER_ASK,
                           original_money=m, original_currency="TWD",
                           price_image_index=0, associated_item_index=0)


def _data():
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    return {"market_hash_name": "AK-47 | Redline (Field-Tested)", "seller_price": 5000,
            "confidence": "high", "verified": True, "verified_by": "trusted_dictionary_exact",
            "validation_error": None, "original": m, "original_price": Decimal("5000"),
            "currency": "TWD"}


class NetworkGuard:
    """觀測 network boundary（requests + socket + project lookup seam）——一旦呼叫：
    network_attempt event + raise AssertionError（不真連外）。"""
    def __init__(self, monkeypatch, emit):
        self.emit = emit
        self._mp = monkeypatch
        self._install()

    def _install(self):
        import requests as _req

        def _blocked(*a, **k):
            self.emit("network_attempt", "none", boundary="requests", target=str(a[0])[:40] if a else "")
            raise AssertionError("network guard: requests blocked (P6-R1-E5-R2)")

        self._mp.setattr(_req, "get", _blocked)
        self._mp.setattr(_req, "post", _blocked)

        def _blocked_socket(*a, **k):
            self.emit("network_attempt", "none", boundary="socket", target=str(a[:2]))
            raise AssertionError("network guard: socket blocked (P6-R1-E5-R2)")

        self._mp.setattr(socket, "socket", _blocked_socket)
        self._mp.setattr(socket, "create_connection", _blocked_socket)


def run_trace(monkeypatch, name, env_mode, candidates, data, blocked=False,
              result_source_override=None, structured_fake_mutator=None,
              legacy_fake_mutator=None, out_dir=None):
    """執行 process_posts 並以 event log + boundary snapshots 觀測。"""
    source = result_source_override if result_source_override is not None else "v2"
    result = ProductionParseResult(data=data, source=source, blocked=blocked,
                                   structured_candidates=candidates)
    events = []
    ev_idx = [0]
    bridge_returns = []

    def ev(etype, dispatch="none", **extra):
        ev_idx[0] += 1
        events.append({"order": ev_idx[0], "event": etype, "dispatch_path": dispatch, **extra})

    orig_sc = aa.process_structured_market_candidates
    aa._dispatch_ctx = ["none"]
    post = {"id": "p", "author": "A", "url": "http://x", "content": "售 AK-47 红 5000", "images": []}
    snap_before_process = snapshot_post(post)
    snap_before_structured = None
    snap_after_structured = None
    snap_before_legacy = None
    snap_after_legacy = None
    structured_active = [False]

    def parse_wrap(*a, **k):
        ev("bridge_called", "none")
        returned = result
        ev("bridge_return", "none", observed_source=getattr(returned, "source", None),
           returned_object_id=id(returned))
        bridge_returns.append(getattr(returned, "source", None))
        return returned

    def sc_wrap(*a, **k):
        nonlocal snap_before_structured, snap_after_structured
        snap_before_structured = snapshot_post(post)
        ev("structured_consumer_enter", "structured")
        structured_active[0] = True
        aa._dispatch_ctx[0] = "structured"
        try:
            outs, deals = orig_sc(*a, **k)
        finally:
            aa._dispatch_ctx[0] = "none"
            structured_active[0] = False
        ev("structured_consumer_exit", "structured", deals=len(deals))
        snap_after_structured = snapshot_post(post)
        if structured_fake_mutator is not None:
            structured_fake_mutator(post)
            snap_after_structured = snapshot_post(post)
        return outs, deals

    def lookup_wrap(mh):
        dispatch = "structured" if structured_active[0] else "legacy"
        ev("lookup_attempt", dispatch, market=mh)
        r = {"market_hash_name": mh, "price_twd": 100, "volume": 10}
        ev("lookup_success", dispatch, market=mh)
        return r

    def analyze_wrap(p, buff):
        dispatch = "structured" if structured_active[0] else "legacy"
        ev("analysis_attempt", dispatch, market=buff.get("market_hash_name"))
        ev("analysis_result", dispatch, market=buff.get("market_hash_name"))
        if legacy_fake_mutator is not None and dispatch == "legacy":
            legacy_fake_mutator(post)
        return {"deal": "x", "skin_name": "ItemA", "author": "A", "link": "http://x"}

    def upload_wrap(d):
        dispatch = "structured" if structured_active[0] else "legacy"
        ev("upload", dispatch, deal=d.get("skin_name"))

    monkeypatch.setattr(pb, "parse_post_for_production", parse_wrap)
    monkeypatch.setattr(aa, "process_structured_market_candidates", sc_wrap)
    monkeypatch.setattr(aa, "lookup_buff_price", lookup_wrap)
    monkeypatch.setattr(aa, "analyze_arbitrage", analyze_wrap)
    monkeypatch.setattr(aa, "upload_to_cloud", upload_wrap)
    monkeypatch.setattr(aa, "load_state", lambda: {})
    monkeypatch.setattr(aa, "mark_processed", lambda ids, st: ev("processed_id", "none", ids=len(ids)))
    monkeypatch.setattr(aa, "save_state", lambda st: None)
    monkeypatch.setattr(aa, "save_deal_to_history", lambda d: None)
    monkeypatch.setattr(aa, "print_deal_report", lambda d: None)
    monkeypatch.setattr(aa, "extract_skin_info", lambda t: None)
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", env_mode)
    guard = NetworkGuard(monkeypatch, ev)
    if env_mode == "off":
        ev("legacy_mode_selected", "legacy", observed_source="legacy", reason="mode_off")
    exit_code = 0
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            aa.process_posts([post])
    except Exception as exc:  # pragma: no cover
        exit_code = 1
    snap_after_process = snapshot_post(post)
    ev("post_after_process", "none")

    # legacy 邊界：legacy_boundary_precision = process_level（無 production seam wrapper——誠實標記）
    has_structured = any(e["event"] == "structured_consumer_enter" for e in events)
    has_legacy = any(e["dispatch_path"] == "legacy" for e in events)
    if has_structured and snap_before_structured is not None and snap_after_structured is not None:
        sd = diff_snapshots(snap_before_structured, snap_after_structured)
        structured_added, structured_removed, structured_changed = sd["added_keys"], sd["removed_keys"], sd["changed_keys"]
    else:
        structured_added = structured_removed = structured_changed = []
    if has_legacy:
        # legacy boundary = process-level（誠實契約）：snap_before_legacy = before_process、
        # snap_after_legacy = after_process——content 直接保存、diff 由 content 直接計算。
        # 僅在 dispatch 互斥成立（無 structured events）時有效——validator 強制。
        snap_before_legacy = snap_before_process
        snap_after_legacy = snap_after_process
        ld = diff_snapshots(snap_before_legacy, snap_after_legacy)
        legacy_added, legacy_removed, legacy_changed = ld["added_keys"], ld["removed_keys"], ld["changed_keys"]
    else:
        snap_before_legacy = snap_after_legacy = None
        legacy_added = legacy_removed = legacy_changed = []
    pd = diff_snapshots(snap_before_process, snap_after_process)

    # off source：由 legacy_mode_selected event 聚合（非 env_mode 推論）
    if bridge_returns:
        observed_source = bridge_returns[-1]
    else:
        legacy_sel = [e for e in events if e["event"] == "legacy_mode_selected"]
        observed_source = legacy_sel[-1].get("observed_source") if legacy_sel else None
    trace = {
        "schema_version": SCHEMA_VERSION,
        "mode": name,
        "production_bridge_called": len([e for e in events if e["event"] == "bridge_called"]),
        "observed_result_source": observed_source,
        "observed_dispatch_path": "structured" if has_structured else ("legacy" if has_legacy else "none"),
        "result_blocked": blocked,
        "legacy_data_present": data is not None,
        "structured_total": len(candidates or []),
        "structured_eligible": len([x for x in (candidates or []) if not x.blocked and x.verified]),
        "structured_consumer_invocation_count": len([e for e in events if e["event"] == "structured_consumer_enter"]),
        "structured_lookup_attempt_count": len([e for e in events if e["event"] == "lookup_attempt" and e["dispatch_path"] == "structured"]),
        "structured_lookup_success_count": len([e for e in events if e["event"] == "lookup_success" and e["dispatch_path"] == "structured"]),
        "structured_analysis_attempt_count": len([e for e in events if e["event"] == "analysis_attempt" and e["dispatch_path"] == "structured"]),
        "structured_deal_count": len([e for e in events if e["event"] == "analysis_result" and e["dispatch_path"] == "structured"]),
        "structured_upload_count": len([e for e in events if e["event"] == "upload" and e["dispatch_path"] == "structured"]),
        "legacy_lookup_attempt_count": len([e for e in events if e["event"] == "lookup_attempt" and e["dispatch_path"] == "legacy"]),
        "legacy_lookup_success_count": len([e for e in events if e["event"] == "lookup_success" and e["dispatch_path"] == "legacy"]),
        "legacy_analysis_attempt_count": len([e for e in events if e["event"] == "analysis_attempt" and e["dispatch_path"] == "legacy"]),
        "legacy_deal_count": len([e for e in events if e["event"] == "analysis_result" and e["dispatch_path"] == "legacy"]),
        "legacy_upload_count": len([e for e in events if e["event"] == "upload" and e["dispatch_path"] == "legacy"]),
        "total_deal_count": len([e for e in events if e["event"] == "analysis_result"]),
        "processed_id_count": len([e for e in events if e["event"] == "processed_id"]),
        "post_before_process_hash": snap_hash(snap_before_process),
        "snap_before_process": snap_before_process,
        "snap_after_process": snap_after_process,
        "post_before_structured_hash": snap_hash(snap_before_structured) if snap_before_structured else None,
        "snap_before_structured": snap_before_structured,
        "post_after_structured_hash": snap_hash(snap_after_structured) if snap_after_structured else None,
        "snap_after_structured": snap_after_structured,
        "post_before_legacy_hash": snap_hash(snap_before_legacy) if snap_before_legacy else None,
        "snap_before_legacy": snap_before_legacy,
        "post_after_legacy_hash": snap_hash(snap_after_legacy) if snap_after_legacy else None,
        "snap_after_legacy": snap_after_legacy,
        "post_after_process_hash": snap_hash(snap_after_process),
        "structured_added_keys": structured_added,
        "structured_removed_keys": structured_removed,
        "structured_changed_keys": structured_changed,
        "legacy_added_keys": legacy_added,
        "legacy_removed_keys": legacy_removed,
        "legacy_changed_keys": legacy_changed,
        "process_added_keys": pd["added_keys"],
        "process_removed_keys": pd["removed_keys"],
        "process_changed_keys": pd["changed_keys"],
        "structured_post_mutation_keys": sorted(set(structured_added + structured_removed + structured_changed)),
        "legacy_post_mutation_keys": sorted(set(legacy_added + legacy_removed + legacy_changed)),
        "legacy_boundary_precision": "process_level" if has_legacy else None,
        "network_guard_installed": True,
        "network_call_count": len([e for e in events if e["event"] == "network_attempt"]),
        "event_log": events,
        "trace_generated_by": "tests/integration/test_mode_trace_execution.py",
        "trace_test_node": f"test_mode_trace_execution::trace_{name}",
        "trace_command": f"process_posts mode={env_mode} candidates={len(candidates or [])} data={'present' if data else 'None'}",
        "exit_code": exit_code,
    }
    out_dir = out_dir or TRACE_OUT
    os.makedirs(out_dir, exist_ok=True)
    json.dump(trace, open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return trace


# ---------- 六份正式 traces ----------
def test_trace_off(monkeypatch):
    t = run_trace(monkeypatch, "off", "off", [], None)
    assert t["exit_code"] == 0 and t["network_call_count"] == 0
    assert t["production_bridge_called"] == 0
    assert t["observed_result_source"] == "legacy"


def test_trace_shadow(monkeypatch):
    t = run_trace(monkeypatch, "shadow", "shadow", [_cand()], _data())
    assert t["exit_code"] == 0 and t["network_call_count"] == 0
    assert t["structured_consumer_invocation_count"] == 0
    assert t["legacy_lookup_attempt_count"] == 1


def test_trace_safe_eligible(monkeypatch):
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None)
    assert t["exit_code"] == 0 and t["network_call_count"] == 0
    assert t["structured_consumer_invocation_count"] == 1
    assert t["legacy_lookup_attempt_count"] == 0
    assert t["structured_post_mutation_keys"] == []


def test_trace_safe_no_eligible(monkeypatch):
    t = run_trace(monkeypatch, "safe-no-eligible", "safe", [], _data())
    assert t["exit_code"] == 0 and t["network_call_count"] == 0
    assert t["legacy_lookup_attempt_count"] == 1


def test_trace_v2_only_eligible(monkeypatch):
    t = run_trace(monkeypatch, "v2-only-eligible", "v2_only", [_cand()], None)
    assert t["exit_code"] == 0 and t["network_call_count"] == 0
    assert t["structured_consumer_invocation_count"] == 1
    assert t["legacy_lookup_attempt_count"] == 0


def test_trace_v2_only_empty(monkeypatch):
    t = run_trace(monkeypatch, "v2-only-empty", "v2_only", [], None)
    assert t["exit_code"] == 0 and t["network_call_count"] == 0
    assert t["structured_lookup_attempt_count"] == 0
    assert t["legacy_lookup_attempt_count"] == 0
    assert t["total_deal_count"] == 0


# ---------- snapshot diff 純函式 self-tests ----------
def test_snapshot_diff_detects_added_key():
    b = snapshot_post({"a": 1})
    a = snapshot_post({"a": 1, "b": 2})
    d = diff_snapshots(b, a)
    assert d["added_keys"] == ["b"] and d["removed_keys"] == [] and d["changed_keys"] == []


def test_snapshot_diff_detects_removed_key():
    b = snapshot_post({"a": 1, "b": 2})
    a = snapshot_post({"a": 1})
    d = diff_snapshots(b, a)
    assert d["removed_keys"] == ["b"] and d["added_keys"] == []


def test_snapshot_diff_detects_changed_value():
    b = snapshot_post({"a": 1})
    a = snapshot_post({"a": 2})
    d = diff_snapshots(b, a)
    assert d["changed_keys"] == ["a"] and d["unchanged_count"] == 0


def test_snapshot_diff_detects_nested_change():
    b = snapshot_post({"a": {"x": 1}})
    a = snapshot_post({"a": {"x": 2}})
    d = diff_snapshots(b, a)
    assert d["changed_keys"] == ["a"]


def test_snapshot_diff_no_change():
    b = snapshot_post({"a": 1, "b": [1, 2]})
    a = snapshot_post({"a": 1, "b": [1, 2]})
    d = diff_snapshots(b, a)
    assert d["added_keys"] == [] and d["removed_keys"] == [] and d["changed_keys"] == [] and d["unchanged_count"] == 2


# ---------- boundary mutation self-tests ----------
def test_structured_boundary_detects_mutation(monkeypatch, tmp_path):
    def mutator(post):
        post["_seller_price"] = 999
        post["extra_key"] = "x"
    t = run_trace(monkeypatch, "neg-structured-mutation", "safe", [_cand()], None,
                  structured_fake_mutator=mutator, out_dir=str(tmp_path))
    assert "_seller_price" in t["structured_post_mutation_keys"]
    assert "extra_key" in t["structured_added_keys"]


def test_legacy_boundary_detects_mutation(monkeypatch, tmp_path):
    def mutator(post):
        post["_seller_price"] = 777
    t = run_trace(monkeypatch, "neg-legacy-mutation", "safe", [], _data(),
                  legacy_fake_mutator=mutator, out_dir=str(tmp_path))
    assert "_seller_price" in t["legacy_post_mutation_keys"]


def test_source_mode_mismatch_is_preserved(monkeypatch, tmp_path):
    """safe fixture 回傳 shadow_legacy source → trace 忠實保存（validator 將拒絕 SOURCE_MODE_MISMATCH）"""
    t = run_trace(monkeypatch, "neg-source-mismatch", "safe", [_cand()], None,
                  result_source_override="shadow_legacy", out_dir=str(tmp_path))
    assert t["observed_result_source"] == "shadow_legacy", "source 被推論/覆寫"


def test_network_attempt_enters_event_log(monkeypatch, tmp_path):
    """guard 阻擋的 network boundary → network_attempt event 進 log + count=1"""
    from tests.integration.test_mode_trace_execution import NetworkGuard
    evs = []
    guard = NetworkGuard(monkeypatch, lambda e, d, **k: evs.append(e))
    import requests
    with pytest.raises(AssertionError):
        requests.get("http://example.com")
    assert evs == ["network_attempt"]
    t = run_trace(monkeypatch, "neg-network-event", "safe", [], _data(), out_dir=str(tmp_path))
    assert t["network_call_count"] == 0


def test_network_guard_blocks_requests(monkeypatch):
    guard = NetworkGuard(monkeypatch, lambda e, d, **k: None)
    import requests
    with pytest.raises(AssertionError):
        requests.get("http://example.com")
    assert True


def test_network_guard_blocks_socket(monkeypatch):
    guard = NetworkGuard(monkeypatch, lambda e, d, **k: None)
    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 80))
    assert True


# ---------- validator negative self-tests ----------
def _run_validator(validator, trace_dir, summary):
    import subprocess
    r = subprocess.run(["python", validator, "--trace-dir", trace_dir, "--summary", summary],
                       capture_output=True, text=True)
    return r


def test_validator_rejects_snapshot_hash_mismatch(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub（P6_TRACE_VALIDATOR 未設）")
    t = run_trace(monkeypatch, "neg-hash", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["post_after_process_hash"] = "deadbeef"
    json.dump(t, open(os.path.join(tmp_path, "neg-hash.json"), "w", encoding="utf-8"))
    r = _run_validator(v, str(tmp_path), "none")
    assert r.returncode != 0, "validator 未拒絕 snapshot hash mismatch"


def test_validator_rejects_mutation_diff_mismatch(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub（P6_TRACE_VALIDATOR 未設）")
    t = run_trace(monkeypatch, "neg-mutdiff", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["structured_post_mutation_keys"] = ["_seller_price"]  # 與 boundary diff 不符
    json.dump(t, open(os.path.join(tmp_path, "neg-mutdiff.json"), "w", encoding="utf-8"))
    r = _run_validator(v, str(tmp_path), "none")
    assert r.returncode != 0, "validator 未拒絕 mutation/diff mismatch"


def test_validator_rejects_source_mode_mismatch(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub（P6_TRACE_VALIDATOR 未設）")
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None,
                  result_source_override="shadow_legacy", out_dir=str(tmp_path))
    r = _run_validator(v, str(tmp_path), "none")
    assert r.returncode != 0, "validator 未拒絕 source/mode mismatch"


def test_validator_rejects_network_event(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub（P6_TRACE_VALIDATOR 未設）")
    t = run_trace(monkeypatch, "neg-net", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["event_log"].append({"order": 999, "event": "network_attempt", "dispatch_path": "none", "boundary": "requests"})
    t["network_call_count"] = 1
    json.dump(t, open(os.path.join(tmp_path, "neg-net.json"), "w", encoding="utf-8"))
    r = _run_validator(v, str(tmp_path), "none")
    assert r.returncode != 0, "validator 未拒絕 network violation"


def _bad_summary(tmp_path, mode, col, val):
    t = json.load(open(os.path.join(tmp_path, f"{mode}.json"), encoding="utf-8"))
    cells = {"s_inv": "3", "s_lookup": "4", "s_analysis": "5", "s_deal": "6", "s_upload": "7",
             "l_lookup": "8", "l_analysis": "9", "l_deal": "10", "l_upload": "11",
             "total_deal": "12", "bridge": "1", "source": "v2", "network": "0", "exit": "0"}
    cells[col] = val
    hdr = ("| mode | bridge | source | dispatch | s_inv | s_lookup | s_lookup_succ | s_analysis | s_deal | s_upload | "
           "l_lookup | l_lookup_succ | l_analysis | l_deal | l_upload | total_deal | s_added | s_removed | s_changed | "
           "l_added | l_removed | l_changed | network | exit |")
    row = ("| {mode} | {bridge} | {source} | structured | {s_inv} | {s_lookup} | 1 | {s_analysis} | {s_deal} | {s_upload} | "
           "{l_lookup} | 0 | {l_analysis} | {l_deal} | {l_upload} | {total_deal} | [] | [] | [] | [] | [] | [] | {network} | {exit} |").format(
        mode=mode, **cells)
    p = os.path.join(tmp_path, f"bad-summary-{col}.md")
    open(p, "w", encoding="utf-8").write(hdr + "\n" + row + "\n")
    return p


def test_validator_rejects_summary_source_mismatch(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub")
    run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    p = _bad_summary(tmp_path, "safe-eligible", "source", "shadow_legacy")
    r = _run_validator(v, str(tmp_path), p)
    assert r.returncode != 0, "validator 未拒絕 summary source mismatch"


def test_validator_rejects_summary_legacy_upload_mismatch(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub")
    run_trace(monkeypatch, "safe-no-eligible", "safe", [], _data(), out_dir=str(tmp_path))
    p = _bad_summary(tmp_path, "safe-no-eligible", "l_upload", "5")
    r = _run_validator(v, str(tmp_path), p)
    assert r.returncode != 0, "validator 未拒絕 summary legacy upload mismatch"


def test_validator_rejects_summary_mutation_mismatch(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub")
    run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    p = _bad_summary(tmp_path, "safe-eligible", "s_added", "[\"_x\"]")
    r = _run_validator(v, str(tmp_path), p)
    assert r.returncode != 0, "validator 未拒絕 summary mutation mismatch"


def test_validator_rejects_summary_network_mismatch(monkeypatch, tmp_path):
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub")
    run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    p = _bad_summary(tmp_path, "safe-eligible", "network", "3")
    r = _run_validator(v, str(tmp_path), p)
    assert r.returncode != 0, "validator 未拒絕 summary network mismatch"


# ================= P6-R1-E5-R3 新增 negative tests =================
def _rv(v, trace_dir, summary):
    import subprocess
    return subprocess.run(["python", v, "--trace-dir", trace_dir, "--summary", summary],
                          capture_output=True, text=True)


def _has_rule(out, rule):
    return rule in out.stdout


def _mk_summary(tmp_path, mode, col, val, base_trace):
    """從 base_trace 生成錯誤 summary（24 欄——只改 col）。"""
    cells = {"bridge": str(base_trace["production_bridge_called"]),
             "source": str(base_trace["observed_result_source"]),
             "dispatch": str(base_trace["observed_dispatch_path"]),
             "s_inv": str(base_trace["structured_consumer_invocation_count"]),
             "s_lookup": str(base_trace["structured_lookup_attempt_count"]),
             "s_lookup_succ": str(base_trace["structured_lookup_success_count"]),
             "s_analysis": str(base_trace["structured_analysis_attempt_count"]),
             "s_deal": str(base_trace["structured_deal_count"]),
             "s_upload": str(base_trace["structured_upload_count"]),
             "l_lookup": str(base_trace["legacy_lookup_attempt_count"]),
             "l_lookup_succ": str(base_trace["legacy_lookup_success_count"]),
             "l_analysis": str(base_trace["legacy_analysis_attempt_count"]),
             "l_deal": str(base_trace["legacy_deal_count"]),
             "l_upload": str(base_trace["legacy_upload_count"]),
             "total_deal": str(base_trace["total_deal_count"]),
             "s_added": json.dumps(base_trace.get("structured_added_keys", []), separators=(",", ":")),
             "s_removed": json.dumps(base_trace.get("structured_removed_keys", []), separators=(",", ":")),
             "s_changed": json.dumps(base_trace.get("structured_changed_keys", []), separators=(",", ":")),
             "l_added": json.dumps(base_trace.get("legacy_added_keys", []), separators=(",", ":")),
             "l_removed": json.dumps(base_trace.get("legacy_removed_keys", []), separators=(",", ":")),
             "l_changed": json.dumps(base_trace.get("legacy_changed_keys", []), separators=(",", ":")),
             "network": str(base_trace["network_call_count"]),
             "exit": str(base_trace["exit_code"])}
    cells[col] = val
    hdr = ("| mode | bridge | source | dispatch | s_inv | s_lookup | s_lookup_succ | s_analysis | s_deal | s_upload | "
           "l_lookup | l_lookup_succ | l_analysis | l_deal | l_upload | total_deal | "
           "s_added | s_removed | s_changed | l_added | l_removed | l_changed | network | exit |")
    row = ("| {mode} | {bridge} | {source} | {dispatch} | {s_inv} | {s_lookup} | {s_lookup_succ} | {s_analysis} | {s_deal} | {s_upload} | "
           "{l_lookup} | {l_lookup_succ} | {l_analysis} | {l_deal} | {l_upload} | {total_deal} | "
           "{s_added} | {s_removed} | {s_changed} | {l_added} | {l_removed} | {l_changed} | {network} | {exit} |").format(mode=mode, **cells)
    p = os.path.join(tmp_path, f"bad-summary-{col}.md")
    open(p, "w", encoding="utf-8").write(hdr + "\n" + row + "\n")
    return p


def _vpath():
    v = os.environ.get("P6_TRACE_VALIDATOR")
    if not v or not os.path.exists(v):
        pytest.skip("validator 在 Review Hub（P6_TRACE_VALIDATOR 未設）")
    return v


def test_validator_rejects_summary_dispatch_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    p = _mk_summary(tmp_path, "safe-eligible", "dispatch", "legacy", t)
    r = _rv(v, str(tmp_path), p)
    assert r.returncode != 0 and _has_rule(r, "SUMMARY_RAW_MISMATCH") and "dispatch" in r.stdout


def test_validator_rejects_summary_structured_lookup_success_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    p = _mk_summary(tmp_path, "safe-eligible", "s_lookup_succ", "9", t)
    r = _rv(v, str(tmp_path), p)
    assert r.returncode != 0 and _has_rule(r, "SUMMARY_RAW_MISMATCH") and "s_lookup_succ" in r.stdout


def test_validator_rejects_summary_legacy_lookup_success_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-no-eligible", "safe", [], _data(), out_dir=str(tmp_path))
    p = _mk_summary(tmp_path, "safe-no-eligible", "l_lookup_succ", "7", t)
    r = _rv(v, str(tmp_path), p)
    assert r.returncode != 0 and _has_rule(r, "SUMMARY_RAW_MISMATCH") and "l_lookup_succ" in r.stdout


def test_validator_rejects_summary_structured_mutation_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    p = _mk_summary(tmp_path, "safe-eligible", "s_added", '[\"_x\"]', t)
    r = _rv(v, str(tmp_path), p)
    assert r.returncode != 0 and _has_rule(r, "SUMMARY_RAW_MISMATCH") and "s_added" in r.stdout


def test_validator_rejects_summary_legacy_mutation_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-no-eligible", "safe", [], _data(), out_dir=str(tmp_path))
    p = _mk_summary(tmp_path, "safe-no-eligible", "l_changed", '[\"_seller_price\"]', t)
    r = _rv(v, str(tmp_path), p)
    assert r.returncode != 0 and _has_rule(r, "SUMMARY_RAW_MISMATCH") and "l_changed" in r.stdout


def test_validator_rejects_summary_exit_code_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    p = _mk_summary(tmp_path, "safe-eligible", "exit", "1", t)
    r = _rv(v, str(tmp_path), p)
    assert r.returncode != 0 and _has_rule(r, "SUMMARY_RAW_MISMATCH") and "exit" in r.stdout


def test_validator_rejects_structured_snapshot_hash_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["post_after_structured_hash"] = "deadbeef"
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "SNAPSHOT_HASH_MISMATCH")


def test_validator_rejects_legacy_snapshot_hash_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-no-eligible", "safe", [], _data(), out_dir=str(tmp_path))
    t["post_after_legacy_hash"] = "deadbeef"
    json.dump(t, open(os.path.join(tmp_path, "safe-no-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "SNAPSHOT_HASH_MISMATCH")


def test_validator_rejects_structured_diff_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["structured_added_keys"] = ["_fake"]
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "SNAPSHOT_DIFF_MISMATCH")


def test_validator_rejects_legacy_diff_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-no-eligible", "safe", [], _data(), out_dir=str(tmp_path))
    t["legacy_added_keys"] = []  # 原值 ["_seller_price"]（added——snapshot diff）
    json.dump(t, open(os.path.join(tmp_path, "safe-no-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "SNAPSHOT_DIFF_MISMATCH")


def test_validator_rejects_missing_structured_snapshot_content(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["snap_after_structured"] = None
    t["post_after_structured_hash"] = None
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "BOUNDARY_SNAPSHOT_MISSING")


def test_validator_rejects_missing_legacy_snapshot_content(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-no-eligible", "safe", [], _data(), out_dir=str(tmp_path))
    t["snap_after_legacy"] = None
    t["post_after_legacy_hash"] = None
    json.dump(t, open(os.path.join(tmp_path, "safe-no-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "BOUNDARY_SNAPSHOT_MISSING")


def test_validator_rejects_process_level_legacy_with_structured_events(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    # 人造：structured 事件 + legacy 事件同時存在（process-level legacy 不可歸因）
    t["event_log"].append({"order": 999, "event": "lookup_attempt", "dispatch_path": "legacy", "market": "x"})
    t["event_log"].append({"order": 1000, "event": "lookup_success", "dispatch_path": "legacy", "market": "x"})
    t["legacy_lookup_attempt_count"] = 1
    t["legacy_lookup_success_count"] = 1
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "LEGACY_BOUNDARY_PRECISION_INSUFFICIENT")


def test_validator_rejects_bridge_count_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["production_bridge_called"] = 5
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "BRIDGE_COUNT_MISMATCH")


def test_validator_rejects_processed_id_count_mismatch(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    t["processed_id_count"] = 9
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "PROCESSED_ID_COUNT_MISMATCH")


def test_validator_rejects_unpaired_structured_enter_exit(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    # 刪掉一個 exit event
    exits = [e for e in t["event_log"] if e["event"] == "structured_consumer_exit"]
    t["event_log"].remove(exits[0])
    t["structured_consumer_invocation_count"] = 1
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "STRUCTURED_ENTER_EXIT_UNPAIRED")


def test_validator_rejects_unpaired_bridge_call_return(monkeypatch, tmp_path):
    v = _vpath()
    t = run_trace(monkeypatch, "safe-eligible", "safe", [_cand()], None, out_dir=str(tmp_path))
    returns = [e for e in t["event_log"] if e["event"] == "bridge_return"]
    t["event_log"].remove(returns[0])
    json.dump(t, open(os.path.join(tmp_path, "safe-eligible.json"), "w", encoding="utf-8"))
    r = _rv(v, str(tmp_path), "none")
    assert r.returncode != 0 and _has_rule(r, "BRIDGE_CALL_RETURN_UNPAIRED")
