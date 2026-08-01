"""
image_deduplicator.py — 圖片 metadata 去重（V2 Phase 6.3A）

去重鍵優先：
1. image_hash 相同
2. normalize 後 URL 相同
3. 同 image_index 不得重複

本階段只做 metadata 去重，不計算 perceptual hash、不下載圖片。
"""
import re
from urllib.parse import parse_qs, urlparse

from alkaid_cs2.domain.image_evidence import ImageEvidence

# FB CDN 常見查詢參數（簽章/尺寸變體）——去重時忽略
_IGNORE_QUERY = {"_nc_cat", "_nc_ht", "_nc_ohc", "_nc_oc", "_nc_rid", "_nc_sid",
                 "ccb", "efg", "hydra", "oe", "oh", "otf", "rt", "tp"}


def normalize_image_url(url: str) -> str:
    """正規化圖片 URL：去 fragment、排序 query（忽略 FB 簽章參數）。"""
    if not isinstance(url, str):
        raise TypeError(f"url 必須是 str，收到 {type(url).__name__}")
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    keep = {k: v for k, v in query.items() if k.lower() not in _IGNORE_QUERY}
    sorted_q = "&".join(f"{k}={v[0]}" if len(v) == 1 else f"{k}={','.join(v)}"
                        for k, v in sorted(keep.items()))
    base = parsed._replace(fragment="", query=sorted_q).geturl()
    return base.rstrip("/")


def _dedup_key(ev: ImageEvidence) -> tuple[str, str]:
    """回傳 (image_hash, normalized_url)；hash 優先。"""
    if ev.image_hash:
        return (f"hash:{ev.image_hash}", "")
    return ("", normalize_image_url(ev.image_url))


def deduplicate_image_evidence(evidence: list[ImageEvidence]) -> list[ImageEvidence]:
    """
    metadata 去重：每筆同時檢查三鍵，任一成立即為重複（保留第一筆）：
    - image_hash 若非 None 且已出現
    - normalized URL 已出現
    - image_index 已出現
    不修改輸入；維持原順序。
    """
    if not isinstance(evidence, list):
        raise TypeError(f"evidence 必須是 list，收到 {type(evidence).__name__}")
    for ev in evidence:
        if not isinstance(ev, ImageEvidence):
            raise TypeError(f"evidence 每筆必須是 ImageEvidence，收到 {type(ev).__name__}")

    seen_hash: set[str] = set()
    seen_url: set[str] = set()
    seen_index: set[int] = set()
    result: list[ImageEvidence] = []

    for ev in evidence:
        hkey = f"hash:{ev.image_hash}" if ev.image_hash else None
        ukey = normalize_image_url(ev.image_url)

        # 任一重複鍵成立 → 跳過
        if hkey is not None and hkey in seen_hash:
            continue
        if ukey in seen_url:
            continue
        if ev.image_index in seen_index:
            continue

        if hkey is not None:
            seen_hash.add(hkey)
        seen_url.add(ukey)
        seen_index.add(ev.image_index)
        result.append(ev)

    return result
