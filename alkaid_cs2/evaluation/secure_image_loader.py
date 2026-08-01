# -*- coding: utf-8 -*-
"""
secure_image_loader.py — Secure image loader interface（Phase 6.4C2-B0）

- 只接受通過 validate_secure_store_reference() 的 reference
- 不支援 http/https、任意 local path、repository fixture path、data URL/base64
- 載入後驗證 SHA-256（mismatch 拒絕）
- bytes 只存在記憶體；不得寫 temp/repository/log/report/JSON
- 本階段只提供 FakeSecureImageLoader / InMemorySecureImageLoader（測試用）
"""
from __future__ import annotations

import hashlib
from typing import Protocol

from alkaid_cs2.evaluation.intake_validation import (
    validate_secure_store_reference,
)


class SecureImageLoader(Protocol):
    """外部 analyzer 的安全圖片載入 interface。"""

    def load(self, storage_reference: str, expected_sha256: str) -> bytes:
        """載入圖片 bytes（僅記憶體）；hash mismatch 拒絕。"""
        ...


class SecureImageLoadError(Exception):
    """secure 圖片載入失敗（錯誤訊息不含 reference/路徑原值）。"""


class InMemorySecureImageLoader:
    """記憶體圖片庫 loader（測試用；不得用於真實資料）。

    images: dict[str, bytes] — 以 secure-store://<opaque-id> 為 key。
    """

    def __init__(self, images: dict[str, bytes] | None = None) -> None:
        self._images: dict[str, bytes] = dict(images or {})

    def load(self, storage_reference: str, expected_sha256: str) -> bytes:
        if not isinstance(storage_reference, str) or \
                not validate_secure_store_reference(storage_reference):
            # Phase 6.4C2-B0：固定錯誤碼（不回顯 reference 原值）
            raise SecureImageLoadError("secure_reference_invalid")
        data = self._images.get(storage_reference)
        if data is None:
            raise SecureImageLoadError("secure_image_not_found")
        actual = hashlib.sha256(data).hexdigest()
        if not isinstance(expected_sha256, str) or actual != expected_sha256:
            raise SecureImageLoadError("secure_image_hash_mismatch")
        return data


class FakeSecureImageLoader(InMemorySecureImageLoader):
    """別名（本階段只有 in-memory 實作；不連真實雲端 storage）。"""
