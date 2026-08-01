# -*- coding: utf-8 -*-
"""
external_analyzer_adapter.py — Evaluation-only analyzer adapter（Phase 6.4C2-B0）

- 定義受控 interface：ExternalAnalyzerAdapter.analyze_image(bytes, *, case_key, image_index) -> dict
- 本階段只有 FakeExternalAnalyzerAdapter / FailingExternalAnalyzerAdapter
- 不得 import production analyzer client / OpenAI / Anthropic / DeepSeek SDK /
  requests / urllib / socket；不得讀 API key；不得連任何 endpoint
"""
from __future__ import annotations

import hashlib
import json
from typing import Protocol

from alkaid_cs2.evaluation.analyzer_cache import (
    AnalyzerCacheWriteError,
    validate_normalized_result,
    write_analyzer_cache_record,
)


class ExternalAnalyzerAdapter(Protocol):
    """外部 analyzer 的受控 interface（evaluation-only）。"""

    analyzer_name: str
    analyzer_version: str

    def analyze_image(
        self,
        image_bytes: bytes,
        *,
        case_key: str,
        image_index: int,
    ) -> dict:
        """回傳 normalized result dict（需通過 validate_normalized_result）。"""
        ...


class FakeExternalAnalyzerAdapter:
    """deterministic fake（離線、無 network、可重現）。"""

    analyzer_name = "fake-analyzer"
    analyzer_version = "0.1.0"

    def analyze_image(
        self,
        image_bytes: bytes,
        *,
        case_key: str,
        image_index: int,
    ) -> dict:
        digest = hashlib.sha256(image_bytes).hexdigest()
        return {
            "kind": "image",
            "item_count": 1,
            "items": [{
                "name": f"fake-item-{digest[:8]}-{image_index}",
                "wear": "Field-Tested",
                "currency": "TWD",
                "price": "1000",
                "image_index": image_index,
            }],
            "warnings": [],
        }


class FailingExternalAnalyzerAdapter:
    """固定失敗 fake（測試 error containment）。"""

    analyzer_name = "failing-analyzer"
    analyzer_version = "0.1.0"

    def analyze_image(
        self,
        image_bytes: bytes,
        *,
        case_key: str,
        image_index: int,
    ) -> dict:
        raise RuntimeError("analyzer crashed (simulated)")

    @staticmethod
    def result_cache_write(
        record: dict,
        cache_dir: str,
    ) -> None:
        # 與 runner 的錯誤處理一致：cache 寫入失敗 → AnalyzerCacheWriteError
        raise AnalyzerCacheWriteError("cache_write_failed")


def validate_normalized_result_for_tests(result: dict) -> list[str]:
    """測試輔助：直接驗證 normalized result。"""
    return validate_normalized_result(result)


def _json_dumps_canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
