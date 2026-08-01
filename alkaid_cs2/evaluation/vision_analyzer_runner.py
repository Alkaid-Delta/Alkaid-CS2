# -*- coding: utf-8 -*-
"""
vision_analyzer_runner.py — 真實 Vision Analyzer Evaluation Adapter（Phase 6.4C1）

包裝既有 Vision analyzer，產生 evaluation 可使用的標準化 payload。
- 不改 analyzer 正式邏輯（只透過注入的 analyzer 介面呼叫）
- 每張圖片獨立執行、單張失敗不中斷
- 標準化 payload 快取（離線 pytest 用）
- 禁止貨幣換算、不得提升 UNKNOWN currency、不得補 seller ask
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import concurrent.futures  # noqa: E402

from alkaid_cs2.evaluation.models import EvaluationCase

CACHE_SCHEMA_VERSION = "1.0"
ANALYZER_SCHEMA_VERSION = "1.0"


@dataclass
class AnalyzerRunConfig:
    model_name: str = "default"
    prompt_version: str = "v1"
    timeout_seconds: int = 20
    max_images: int = 5
    use_cache: bool = True
    write_cache: bool = True


@dataclass
class AnalyzerImageResult:
    image_index: int
    image_hash: str | None
    success: bool
    payload: dict | None = None
    error_code: str | None = None
    latency_ms: float = 0.0
    model_name: str = ""
    prompt_version: str = ""
    cached: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.image_index, bool) or not isinstance(self.image_index, int):
            raise TypeError("image_index 必須 int")
        if self.image_index < 0:
            raise ValueError("image_index 不可為負數")
        if self.success and (not isinstance(self.image_hash, str)
                             or not self.image_hash.strip()):
            raise ValueError("success=True 時 image_hash 必須非空")
        if not self.success:
            # 失敗時 hash 可為 None（image load 失敗無 bytes 可 hash）
            if self.image_hash is not None and not isinstance(self.image_hash, str):
                raise TypeError("image_hash 必須 str 或 None")
        if not isinstance(self.success, bool):
            raise TypeError("success 必須 bool")
        if self.success and self.payload is None:
            raise ValueError("success=True 時 payload 不得 None")
        if not self.success and not self.error_code:
            raise ValueError("失敗時 error_code 不得空白")
        if isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, (int, float)):
            raise TypeError("latency_ms 必須數字")
        if self.latency_ms < 0:
            raise ValueError("latency_ms 不可為負數")


def compute_image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()[:16]


_BINARY_KEYS = ("raw_bytes", "image_bytes", "bytes", "image_base64", "base64",
                "data_url")


def sanitize_payload(payload: Any) -> Any:
    """遞迴移除 binary/敏感欄位（Phase 6.4C1.1）。

    - bytes 值 → 移除（不得轉字串保存）
    - binary 欄位 key（raw_bytes 等）→ 移除
    - Authorization/cookie/token 欄位 → 移除
    """
    import copy
    if isinstance(payload, bytes):
        return None
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if not isinstance(k, str):
                out[k] = sanitize_payload(v)
                continue
            kl = k.lower()
            if kl in _BINARY_KEYS or kl in ("authorization", "cookie", "token",
                                            "api_key"):
                continue  # 移除
            cleaned = sanitize_payload(v)
            if cleaned is not None:
                out[k] = cleaned
        return out
    if isinstance(payload, list):
        return [sanitize_payload(v) for v in payload]
    return copy.deepcopy(payload)


def _analyzer_worker(queue, fn, args):
    """multiprocessing worker：執行 analyzer，結果回傳 queue（Phase 6.4C1.2）。"""
    try:
        result = fn(*args)
        queue.put(("ok", result))
    except Exception as exc:  # noqa: BLE001
        queue.put(("err", f"{type(exc).__name__}:{str(exc)[:200]}"))


def _run_with_timeout(fn, timeout_seconds: int, *args):
    """以 process isolation 執行 analyzer；timeout 可終止、無 zombie（6.4C1.2/6.4C1.3）。

    - timeout_seconds > 0 且 analyzer 不可 pickle → **不執行**，
      raise TypeError（由 caller 轉 analyzer_not_pickleable）——不得繞過 timeout
    - timeout_seconds == 0 → 允許同步執行（timeout disabled）
    - timeout → terminate process → raise TimeoutError
    """
    import multiprocessing as mp
    import pickle
    import queue as _queue
    if timeout_seconds == 0:
        return fn(*args)  # timeout disabled：允許同步（含 lambda）
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds 不可為負數")
    try:
        pickle.dumps(fn)
    except Exception:
        raise TypeError("analyzer not pickleable（timeout>0 時不得繞過 timeout）") from None
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_analyzer_worker, args=(q, fn, args), daemon=True)
    p.start()
    try:
        kind, payload = q.get(timeout=timeout_seconds)
        if kind == "ok":
            return payload
        raise RuntimeError(payload)
    except _queue.Empty:
        raise TimeoutError("analyzer timeout") from None
    finally:
        if p.is_alive():
            p.terminate()
            p.join(timeout=2)
        q.close()
        q.join_thread()


def normalize_vision_payload(payload: Any) -> dict:
    """標準化 analyzer 輸出（defensive copy；不清除 items/currency 語意）。

    不得提升 UNKNOWN currency、不得補 seller ask、不得換算。
    """
    import copy
    if not isinstance(payload, dict):
        return {"type": "other", "platform": "unknown", "items": [],
                "currency": None, "raw_normalized": True}
    p = copy.deepcopy(payload)
    p.setdefault("type", "other")
    p.setdefault("platform", "unknown")
    p.setdefault("items", [])
    p.setdefault("currency", None)
    p["raw_normalized"] = True
    return p


def _cache_key(image_hash: str, model_name: str, prompt_version: str) -> str:
    return hashlib.sha256(
        f"{image_hash}|{model_name}|{prompt_version}|{ANALYZER_SCHEMA_VERSION}"
        .encode("utf-8")).hexdigest()


def _safe_cache_path(cache_dir: Path, key: str) -> Path:
    if not key or "/" in key or "\\" in key or ".." in key:
        raise ValueError(f"cache key 含路徑字元（traversal 拒絕）: {key!r}")
    return Path(cache_dir) / f"{key}.json"


def cache_lookup(cache_dir: Path, image_hash: str, model_name: str,
                 prompt_version: str) -> dict | None:
    """讀 cache；model/prompt/schema 不一致視為 miss。"""
    p = _safe_cache_path(cache_dir, _cache_key(image_hash, model_name, prompt_version))
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if data.get("image_hash") != image_hash or \
            data.get("model_name") != model_name or \
            data.get("prompt_version") != prompt_version or \
            data.get("analyzer_schema_version") != ANALYZER_SCHEMA_VERSION:
        return None  # 不一致 → cache miss
    if not isinstance(data.get("result"), dict):
        return None
    return data["result"]


def cache_write(cache_dir: Path, image_hash: str, model_name: str,
                prompt_version: str, result: dict) -> Path:
    """寫 cache（atomic replace；Phase 6.4C1.3 自身防護）。

    - 自行 sanitize_payload（不依賴 caller 先 sanitize）
    - 驗證 sanitized 為 dict
    - 驗證可 JSON serialize
    """
    import json as _json
    sanitized = sanitize_payload(result)
    if sanitized is None or not isinstance(sanitized, dict):
        raise ValueError("cache result sanitize 後必須 dict")
    try:
        _json.dumps(sanitized)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cache result 無法 JSON serialize：{exc}") from None
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "image_hash": image_hash,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "analyzer_schema_version": ANALYZER_SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": sanitized,
    }
    p = _safe_cache_path(cache_dir, _cache_key(image_hash, model_name, prompt_version))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)  # atomic
    return p


def _sanitize_error(exc: Exception) -> str:
    msg = str(exc)
    msg = " ".join(msg.split())[:200]
    return msg


def run_analyzer_for_case(
    case: EvaluationCase,
    image_loader: Callable[[EvaluationCase, int], bytes | None],
    analyzer: Callable[[bytes, str], Any],
    config: AnalyzerRunConfig,
    cache_dir: Path | None = None,
    allow_external: bool = False,
) -> list[AnalyzerImageResult]:
    """逐圖執行 analyzer；單張失敗隔離；cache 命中跳過外部呼叫。

    - allow_external=False 且 cache miss → 回傳失敗結果（不呼叫 analyzer）
    - analyzer 回傳物件 defensive copy + normalize
    - 不保存圖片 bytes
    """
    results: list[AnalyzerImageResult] = []
    for img in case.images[:config.max_images]:
        idx = img.image_index
        raw = image_loader(case, idx)
        if raw is None:
            results.append(AnalyzerImageResult(
                image_index=idx, image_hash=None, success=False,
                error_code="image_load_failed", latency_ms=0.0,
                model_name=config.model_name, prompt_version=config.prompt_version))
            continue
        img_hash = compute_image_hash(raw)
        if config.use_cache and cache_dir is not None:
            cached = cache_lookup(cache_dir, img_hash, config.model_name,
                                  config.prompt_version)
            if cached is not None:
                results.append(AnalyzerImageResult(
                    image_index=idx, image_hash=img_hash, success=True,
                    payload=cached, latency_ms=0.0,
                    model_name=config.model_name, prompt_version=config.prompt_version,
                    cached=True))
                continue
        if not allow_external:
            results.append(AnalyzerImageResult(
                image_index=idx, image_hash=img_hash, success=False,
                error_code="cache_miss_offline", latency_ms=0.0,
                model_name=config.model_name, prompt_version=config.prompt_version))
            continue
        started = time.perf_counter()
        try:
            payload = _run_with_timeout(analyzer, config.timeout_seconds,
                                        raw, "")
            normalized = normalize_vision_payload(payload)
            normalized = sanitize_payload(normalized)  # 移除 binary/敏感欄位
            latency = (time.perf_counter() - started) * 1000.0
            if config.write_cache and config.use_cache and cache_dir is not None:
                cache_write(cache_dir, img_hash, config.model_name,
                            config.prompt_version, normalized)
            results.append(AnalyzerImageResult(
                image_index=idx, image_hash=img_hash, success=True,
                payload=normalized, latency_ms=round(latency, 1),
                model_name=config.model_name, prompt_version=config.prompt_version))
        except TypeError as exc:
            # 6.4C1.3：不可 pickle analyzer + timeout>0 → 不執行、不繞過
            latency = (time.perf_counter() - started) * 1000.0
            results.append(AnalyzerImageResult(
                image_index=idx, image_hash=img_hash, success=False,
                error_code="analyzer_not_pickleable",
                latency_ms=round(latency, 1),
                model_name=config.model_name, prompt_version=config.prompt_version))
        except TimeoutError:
            latency = (time.perf_counter() - started) * 1000.0
            results.append(AnalyzerImageResult(
                image_index=idx, image_hash=img_hash, success=False,
                error_code="analyzer_timeout",
                latency_ms=round(latency, 1),
                model_name=config.model_name, prompt_version=config.prompt_version))
        except Exception as exc:  # noqa: BLE001 — 單張失敗隔離
            latency = (time.perf_counter() - started) * 1000.0
            results.append(AnalyzerImageResult(
                image_index=idx, image_hash=img_hash, success=False,
                error_code=f"analyzer_error:{_sanitize_error(exc)}",
                latency_ms=round(latency, 1),
                model_name=config.model_name, prompt_version=config.prompt_version))
    return results


# ================================================================
# Fixture payload vs Analyzer payload 對比
# ================================================================
@dataclass
class PayloadComparison:
    image_kind_match: bool = False
    item_count_match: bool = False
    exact_name_matches: int = 0
    partial_name_matches: int = 0
    missing_items: int = 0
    extra_items: int = 0
    price_match: bool = False
    currency_match: bool = False
    role_match: bool = False
    confidence_delta: float = 0.0
    warning_codes: list[str] = field(default_factory=list)


def _norm_name(n: str) -> str:
    return (n or "").strip().lower()


def compare_fixture_and_analyzer_payload(
    fixture_payload: dict,
    analyzer_payload: dict,
) -> PayloadComparison:
    """比較人工 fixture Vision payload 與真實 analyzer payload。

    這是 Vision payload 層比較（非 production final output）。
    """
    c = PayloadComparison()
    f_type = str(fixture_payload.get("type") or "").lower()
    a_type = str(analyzer_payload.get("type") or "").lower()
    c.image_kind_match = f_type == a_type

    f_items = list(fixture_payload.get("items") or [])
    a_items = list(analyzer_payload.get("items") or [])
    c.item_count_match = len(f_items) == len(a_items)

    f_names = [_norm_name(it.get("name") or it.get("market_hash_name") or "")
               for it in f_items]
    a_names = [_norm_name(it.get("name") or it.get("market_hash_name") or "")
               for it in a_items]
    used = set()
    for fn in f_names:
        if fn and fn in a_names and a_names.index(fn) not in used:
            c.exact_name_matches += 1
            used.add(a_names.index(fn))
        elif fn:
            c.missing_items += 1
    for an in a_names:
        if not an or an in f_names:
            continue
        if any(an in fn or fn in an for fn in f_names):
            c.partial_name_matches += 1
        else:
            c.extra_items += 1

    f_price = None
    a_price = None
    for it in f_items:
        if it.get("price") is not None:
            f_price = str(it["price"])
            break
    for it in a_items:
        if it.get("price") is not None:
            a_price = str(it["price"])
            break
    c.price_match = (f_price is None and a_price is None) or \
        (f_price is not None and a_price is not None and f_price == a_price)
    f_cur = next((it.get("currency") for it in f_items
                  if it.get("currency") is not None), None)
    a_cur = next((it.get("currency") for it in a_items
                  if it.get("currency") is not None), None)
    c.currency_match = (f_cur is None and a_cur is None) or \
        (f_cur is not None and a_cur is not None and
         str(f_cur).upper() == str(a_cur).upper())
    f_role = fixture_payload.get("role")
    a_role = analyzer_payload.get("role")
    c.role_match = (f_role is None and a_role is None) or \
        (f_role is not None and a_role is not None and
         str(f_role).lower() == str(a_role).lower())
    f_conf = fixture_payload.get("confidence")
    a_conf = analyzer_payload.get("confidence")
    if isinstance(f_conf, (int, float)) and isinstance(a_conf, (int, float)):
        c.confidence_delta = round(abs(float(f_conf) - float(a_conf)), 4)
    return c
