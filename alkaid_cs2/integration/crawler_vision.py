"""
crawler_vision.py — crawler 多圖片 Vision 處理（V2 Phase 6.3D）

把 crawler 的「每張圖片獨立分析」抽成可測試的純邏輯：
- URL 正規化/去重、最大圖片數限制
- 單張圖片：下載 → 分類 → 提取 → payload（保存原始結果，不扁平化）
- 多張圖片：循序處理、單張失敗隔離、保持順序
- Metrics 收集（不記錄敏感資料）

限制：不呼叫真實 API（依注入的 download/analyze func）、不換算貨幣、
不判斷 seller ask、不 flatten items。
"""
import copy
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse

# 已知可捕捉的圖片處理例外（禁止 except Exception: pass）
_KNOWN_ERRORS = (
    TimeoutError, ValueError, TypeError, json.JSONDecodeError,
    ConnectionError, OSError,
)

# FB CDN 暫時性追蹤參數（URL 去重時忽略）
_FB_TRACKING_QUERY = {
    "_nc_cat", "_nc_ht", "_nc_ohc", "_nc_oc", "_nc_rid", "_nc_sid",
    "ccb", "efg", "hydra", "oe", "oh", "otf", "rt", "tp",
}

# 分類 prompt（完整圖片類型；只分類，不判斷 seller ask）
_TYPE_PROMPT = (
    "判斷這張 CS2 交易截圖的圖片類型，只回傳 JSON:"
    '{"type":"inventory"或"single"或"multi"或"market"或"chat"或"inspect"'
    '或"payment"或"trade"或"other","rows":數字,"cols":數字}'
    "類型定義:"
    "inventory=多商品庫存格或清單, rows/cols=格子數(如3列7行)"
    "single=單一商品詳情或 Facebook 單商品販售圖"
    "multi=同一張圖包含多個獨立商品"
    "market=BUFF、Steam Market 或其他平台掛牌頁"
    "chat=Messenger、LINE、Discord 或交易聊天截圖"
    "inspect=遊戲內檢視、磨損、貼紙、浮點資訊頁"
    "payment=付款、匯款或交易收據"
    "trade=交易確認或交換畫面"
    "other=無法分類"
    "只做圖片類型分類，不判斷 seller ask"
)
_ITEM_PROMPT = (
    "CS2交易截圖.提取要賣的物品,輸出JSON陣列:"
    '[{"name":"完整中文名含★","wear":"磨損度","price":數字,"currency":"TWD/RMB"}]'
    "單一物品頁就是那件.無法辨識回傳[]"
)

# 空 items 仍視為成功 payload 的類型（保存給 Adapter 判定）
# payment=憑證不產商品；trade/inspect=保守保存 type + items=[]（Adapter 判定）
_EMPTY_ITEMS_OK_TYPES = ("payment", "trade", "inspect")


# ============================================================
# 環境設定
# ============================================================
def get_max_vision_images_per_post() -> int:
    """ALKAID_MAX_VISION_IMAGES_PER_POST：預設 5、範圍 1-10、非法 fallback 5。"""
    try:
        v = int(os.environ.get("ALKAID_MAX_VISION_IMAGES_PER_POST", "5"))
    except (TypeError, ValueError):
        print("  [FB] ⚠️ ALKAID_MAX_VISION_IMAGES_PER_POST 非法,用 5")
        return 5
    if not (1 <= v <= 10):
        print(f"  [FB] ⚠️ ALKAID_MAX_VISION_IMAGES_PER_POST={v} 超出 1-10,用 5")
        return 5
    return v


def get_vision_image_timeout_seconds() -> int:
    """ALKAID_VISION_IMAGE_TIMEOUT_SECONDS：預設 20、範圍 5-60、非法 fallback 20。"""
    try:
        v = int(os.environ.get("ALKAID_VISION_IMAGE_TIMEOUT_SECONDS", "20"))
    except (TypeError, ValueError):
        print("  [FB] ⚠️ ALKAID_VISION_IMAGE_TIMEOUT_SECONDS 非法,用 20")
        return 20
    if not (5 <= v <= 60):
        print(f"  [FB] ⚠️ ALKAID_VISION_IMAGE_TIMEOUT_SECONDS={v} 超出 5-60,用 20")
        return 20
    return v


def get_vision_continue_on_error() -> bool:
    """ALKAID_VISION_CONTINUE_ON_ERROR：預設 true。"""
    raw = os.environ.get("ALKAID_VISION_CONTINUE_ON_ERROR", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


# ============================================================
# 結果與 Metrics
# ============================================================
@dataclass
class CrawlerVisionImageResult:
    image_index: int
    image_url: str
    payload: object | None
    success: bool
    error_code: str | None = None
    duration_ms: float = 0.0
    retry_count: int = 0

    def __post_init__(self) -> None:
        import math

        if isinstance(self.image_index, bool) or not isinstance(self.image_index, int):
            raise TypeError(f"image_index 必須是非負 int，收到 {type(self.image_index).__name__}")
        if self.image_index < 0:
            raise ValueError(f"image_index 不可為負數，收到 {self.image_index}")
        if not isinstance(self.image_url, str) or not self.image_url.strip():
            raise ValueError("image_url 必須是非空 str")
        if not isinstance(self.success, bool):
            raise TypeError(f"success 必須是 bool，收到 {type(self.success).__name__}")
        if self.success and self.payload is None:
            raise ValueError("success=True 時 payload 不得為 None")
        if not self.success and (not self.error_code or not self.error_code.strip()):
            raise ValueError("success=False 時 error_code 不得空白")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, (int, float)):
            raise TypeError(f"duration_ms 必須是有限數字，收到 {type(self.duration_ms).__name__}")
        if not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError(f"duration_ms 必須是有限且 >= 0，收到 {self.duration_ms}")
        if isinstance(self.retry_count, bool) or not isinstance(self.retry_count, int):
            raise TypeError(f"retry_count 必須是非負 int，收到 {type(self.retry_count).__name__}")
        if self.retry_count < 0:
            raise ValueError(f"retry_count 不可為負數，收到 {self.retry_count}")
        # defensive copy（payload 不共享）
        self.image_url = self.image_url.strip()
        self.payload = copy.deepcopy(self.payload)


@dataclass
class CrawlerVisionMetrics:
    posts_with_images: int = 0
    images_discovered: int = 0
    images_after_dedup: int = 0
    images_truncated: int = 0
    images_downloaded: int = 0
    image_download_failures: int = 0
    vision_attempts: int = 0
    vision_successes: int = 0
    vision_failures: int = 0
    vision_timeouts: int = 0
    vision_retries: int = 0
    total_vision_duration_ms: float = 0.0
    posts_with_any_vision_success: int = 0
    posts_with_all_vision_failed: int = 0
    multi_image_posts: int = 0
    payloads_emitted: int = 0

    def record_result(self, r: CrawlerVisionImageResult) -> None:
        if not isinstance(r, CrawlerVisionImageResult):
            raise TypeError(f"必須是 CrawlerVisionImageResult，收到 {type(r).__name__}")
        self.vision_attempts += 1
        if r.success:
            self.vision_successes += 1
            self.payloads_emitted += 1
        else:
            self.vision_failures += 1
            if r.error_code == "vision_timeout":
                self.vision_timeouts += 1
        self.total_vision_duration_ms += r.duration_ms
        self.vision_retries += r.retry_count

    def record_download(self, ok: bool) -> None:
        if ok:
            self.images_downloaded += 1
        else:
            self.image_download_failures += 1

    def record_download_retry(self, count: int) -> None:
        """下載最終失敗時記錄重試次數（加入 vision_retries）。

        不得對下載失敗呼叫 record_result()（Vision 未執行，
        不能增加 vision_attempts/vision_failures）。
        """
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError(f"count 必須是非負 int，收到 {type(count).__name__}")
        if count < 0:
            raise ValueError(f"count 不可為負數，收到 {count}")
        self.vision_retries += count

    def record_post(self, *, discovered: int, after_dedup: int,
                    truncated: int, successes: int, multi_image: bool) -> None:
        for name, v in (("discovered", discovered), ("after_dedup", after_dedup),
                        ("truncated", truncated), ("successes", successes)):
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} 必須是非負 int，收到 {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{name} 不可為負數，收到 {v}")
        processed = after_dedup - truncated
        if successes > processed:
            raise ValueError(
                f"successes={successes} 不得大於實際處理數 {processed}")
        self.posts_with_images += 1
        self.images_discovered += discovered
        self.images_after_dedup += after_dedup
        self.images_truncated += truncated
        if successes > 0:
            self.posts_with_any_vision_success += 1
        else:
            self.posts_with_all_vision_failed += 1
        if multi_image:
            self.multi_image_posts += 1

    def reset(self) -> None:
        for name in self.__dataclass_fields__:
            default = self.__dataclass_fields__[name].default
            setattr(self, name, 0 if default == 0 else default)

    def summary(self) -> str:
        avg = (self.total_vision_duration_ms / self.vision_attempts
               if self.vision_attempts else 0.0)
        return (
            f"Vision crawler: posts={self.posts_with_images} "
            f"images={self.images_discovered} dedup={self.images_after_dedup} "
            f"truncated={self.images_truncated} downloaded={self.images_downloaded} "
            f"dl_fail={self.image_download_failures} "
            f"success={self.vision_successes} failed={self.vision_failures} "
            f"timeouts={self.vision_timeouts} retries={self.vision_retries} "
            f"payloads={self.payloads_emitted} avg_ms={avg:.0f} "
            f"multi={self.multi_image_posts}"
        )


# 全域 metrics 實例（crawler production 統計用；測試可 reset）
CRAWLER_VISION_METRICS = CrawlerVisionMetrics()


# ============================================================
# URL 正規化與去重
# ============================================================
def normalize_crawler_image_url(url: str) -> str:
    """去空白/fragment、query pair 保守正規化（**保留全部參數與重複值**）。

    - parse_qsl(keep_blank_values=True) 保留每組重複 pair
    - 依 (key, value) 穩定排序
    - urlencode(doseq=True) 序列化（**不把重複值合併成逗號**）
    - 移除 fragment
    用途：僅供去重比較（dedup_key），不得用於實際下載。
    """
    if not isinstance(url, str):
        raise TypeError(f"url 必須是 str，收到 {type(url).__name__}")
    parsed = urlparse(url.strip())
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    pairs.sort(key=lambda kv: (kv[0], kv[1]))
    query = urlencode(pairs, doseq=True)
    base = parsed._replace(fragment="", query=query).geturl()
    return base.rstrip("/")


@dataclass
class CrawlerImageRef:
    """去重參考：original_url 供下載，dedup_key 供去重比較。"""
    original_index: int
    original_url: str
    dedup_key: str

    def __post_init__(self) -> None:
        if isinstance(self.original_index, bool) or not isinstance(self.original_index, int):
            raise TypeError(f"original_index 必須是非負 int，收到 {type(self.original_index).__name__}")
        if self.original_index < 0:
            raise ValueError(f"original_index 不可為負數，收到 {self.original_index}")
        if not isinstance(self.original_url, str) or not self.original_url.strip():
            raise ValueError("original_url 必須是非空 str")
        if not isinstance(self.dedup_key, str) or not self.dedup_key.strip():
            raise ValueError("dedup_key 必須是非空 str")
        self.original_url = self.original_url.strip()
        self.dedup_key = self.dedup_key.strip()


def deduplicate_post_image_urls(image_urls: list[str]) -> list[str]:
    """向後相容：只回傳去重後的 original_url（保留第一次出現、穩定順序）。"""
    return [ref.original_url for ref in deduplicate_post_image_refs(image_urls)]


def deduplicate_post_image_refs(image_urls: list[str]) -> list[CrawlerImageRef]:
    """去重（保留第一個 original_url、原始 index）；空 URL 與非字串跳過。"""
    if not isinstance(image_urls, list):
        raise TypeError(f"image_urls 必須是 list，收到 {type(image_urls).__name__}")
    seen: set[str] = set()
    result: list[CrawlerImageRef] = []
    for i, url in enumerate(image_urls):
        if not isinstance(url, str) or not url.strip():
            continue
        key = normalize_crawler_image_url(url)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(CrawlerImageRef(
            original_index=i,
            original_url=url.strip(),
            dedup_key=key,
        ))
    return result


# ============================================================
# 圖片類型映射（crawler 保存 type 原樣，語意由 Vision Adapter 決定）
# ============================================================
_TYPE_MAPPING = {
    "inventory": "inventory",
    "inventory_grid": "inventory",
    "single": "single",
    "single_item": "single",
    "multi": "multi",
    "multi_item": "multi",
    "market": "market",
    "market_listing": "market",
    "buff": "market",
    "buff_listing": "market",
    "steam_market": "market",
    "steam": "market",
    "steam_listing": "market",
    "chat": "chat",
    "chat_screenshot": "chat",
    "inspect": "inspect",
    "inspect_screenshot": "inspect",
    "payment": "payment",
    "payment_proof": "payment",
    "trade": "trade",
    "trade_confirmation": "trade",
    "other": "other",
    "unknown": "unknown",
}


def _map_image_type(raw_type: object) -> str:
    """分類回傳值 → 標準化 type（不確定 → unknown）。"""
    t = str(raw_type or "").strip().lower()
    return _TYPE_MAPPING.get(t, "unknown")


class HttpDownloadError(Exception):
    """HTTP 下載錯誤（status 供永久/暫時判斷）。"""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"HTTP {status}")


def download_fb_image(url: str, timeout_seconds: int) -> bytes:
    """正式 requests 下載包裝：
    - requests.Timeout → 內建 TimeoutError
    - requests.ConnectionError → 內建 ConnectionError
    - HTTP >= 400 → HttpDownloadError(status)
    - 空 content → ValueError
    不記錄 cookie/header/token。
    """
    import requests

    try:
        resp = requests.get(url, timeout=timeout_seconds)
    except requests.Timeout:
        raise TimeoutError("download timeout") from None
    except requests.ConnectionError:
        raise ConnectionError("download connection error") from None
    except requests.RequestException as exc:
        raise ConnectionError(f"download error:{type(exc).__name__}") from None
    if resp.status_code >= 400:
        raise HttpDownloadError(resp.status_code)
    if not resp.content:
        raise ValueError("empty image body")
    return resp.content


# ============================================================
# 單張圖片分析
# ============================================================
def analyze_single_post_image(
    *,
    image_index: int,
    image_url: str,
    image_bytes: bytes,
    analyze_image_func: Callable,
) -> CrawlerVisionImageResult:
    """單張圖片：分類 → 依類型提取 → 保存原始 payload。"""
    started = time.monotonic()
    retries = 0

    try:
        # ── Step 1: 分類 ──
        type_result = analyze_image_func(
            image_bytes, custom_prompt=_TYPE_PROMPT, retry=1)
        if type_result and isinstance(type_result, dict):
            img_type = _map_image_type(type_result.get("type"))
        else:
            img_type = "unknown"

        # ── Step 2: 依類型分流（保存原始 payload，不刪除、不扁平化）──
        if img_type == "inventory":
            # inventory 是有效類型：保存 type/rows/cols（adapter 自行 deferred）
            payload = {
                "type": "inventory",
                "rows": int(type_result.get("rows") or 7) if type_result else 7,
                "cols": int(type_result.get("cols") or 3) if type_result else 3,
                "items": [],
            }
        else:
            # market/chat/single/multi/payment/trade/inspect：提取 items 但**不判斷語意**
            # （crawler 不得把 market 價格當 seller ask；由 Vision Adapter 決定）
            result = analyze_image_func(
                image_bytes, custom_prompt=_ITEM_PROMPT, retry=1)
            if result and isinstance(result, list):
                items = result
            elif result and isinstance(result, dict):
                items = [result]
            else:
                items = []
            if not items and img_type not in _EMPTY_ITEMS_OK_TYPES:
                # single/multi/market/chat 空 → 失敗（不產生空 payload）
                # payment/trade/inspect 空 → 保守保存 type + items=[]（Adapter 判定）
                duration = (time.monotonic() - started) * 1000.0
                return CrawlerVisionImageResult(
                    image_index=image_index, image_url=image_url,
                    payload=None, success=False, error_code="empty_vision_result",
                    duration_ms=round(duration, 1), retry_count=retries,
                )
            payload = {
                "type": img_type,
                "platform": "facebook",
                "items": items,
            }

        duration = (time.monotonic() - started) * 1000.0
        return CrawlerVisionImageResult(
            image_index=image_index, image_url=image_url,
            payload=payload, success=True,
            duration_ms=round(duration, 1), retry_count=retries,
        )
    except _KNOWN_ERRORS as exc:
        duration = (time.monotonic() - started) * 1000.0
        code = "vision_timeout" if isinstance(exc, TimeoutError) else \
            f"vision_error:{type(exc).__name__}"
        return CrawlerVisionImageResult(
            image_index=image_index, image_url=image_url,
            payload=None, success=False, error_code=code,
            duration_ms=round(duration, 1), retry_count=retries,
        )


# ============================================================
# 多張圖片處理
# ============================================================
def analyze_post_images(
    image_urls: list[str],
    *,
    download_image_func: Callable,
    analyze_image_func: Callable,
    max_images: int | None = None,
    timeout_seconds: int | None = None,
    continue_on_error: bool = True,
    metrics: CrawlerVisionMetrics | None = None,
) -> list[CrawlerVisionImageResult]:
    """URL 去重（original_url 下載 / dedup_key 比較）→ 限制數量 → 循序處理。

    - 保留原始 image_index（去重與截斷後仍用原始 index）
    - 單張失敗：continue_on_error=True 繼續下一張；False 立即停止（已成功保留）
    - 下載重試規則：timeout/暫時連線/5xx 最多 1 次；4xx 與空 body 不重試
    """
    if not isinstance(image_urls, list):
        raise TypeError(f"image_urls 必須是 list，收到 {type(image_urls).__name__}")

    max_images = max_images if max_images is not None else get_max_vision_images_per_post()
    timeout_seconds = (timeout_seconds if timeout_seconds is not None
                       else get_vision_image_timeout_seconds())

    refs = deduplicate_post_image_refs(image_urls)
    truncated = max(0, len(refs) - max_images)
    if truncated:
        print(f"  [FB] ⚠️ vision_images_truncated:{len(refs)}:{max_images}")
    refs_to_process = refs[:max_images]

    results: list[CrawlerVisionImageResult] = []
    for ref in refs_to_process:
        # ── 下載（用 original_url，絕不傳 normalized key；5xx/timeout 重試一次）──
        data: bytes | None = None
        dl_retries = 0
        for attempt in range(2):
            try:
                data = download_image_func(ref.original_url, timeout_seconds)
                break
            except HttpDownloadError as exc:
                if 400 <= exc.status < 500 or attempt == 1:
                    code = "download_http_4xx" if 400 <= exc.status < 500 else "download_http_5xx"
                    dl_retries = 1 if attempt == 1 else 0
                    if metrics:
                        metrics.record_download(False)
                        if dl_retries > 0:
                            # 下載最終失敗的重試計入 vision_retries（不增加 vision_attempts）
                            metrics.record_download_retry(dl_retries)
                    results.append(CrawlerVisionImageResult(
                        image_index=ref.original_index, image_url=ref.original_url,
                        payload=None, success=False, error_code=code,
                        duration_ms=0.0, retry_count=dl_retries))
                    break
                dl_retries = 1  # 5xx 且第一次 → 重試
            except TimeoutError:
                dl_retries = 1  # 第一次 timeout → 重試
                if attempt == 1:
                    if metrics:
                        metrics.record_download(False)
                        if dl_retries > 0:
                            metrics.record_download_retry(dl_retries)
                    results.append(CrawlerVisionImageResult(
                        image_index=ref.original_index, image_url=ref.original_url,
                        payload=None, success=False, error_code="download_timeout",
                        duration_ms=0.0, retry_count=dl_retries))
                    break
            except ConnectionError:
                dl_retries = 1  # 第一次連線錯誤 → 重試
                if attempt == 1:
                    if metrics:
                        metrics.record_download(False)
                        if dl_retries > 0:
                            metrics.record_download_retry(dl_retries)
                    results.append(CrawlerVisionImageResult(
                        image_index=ref.original_index, image_url=ref.original_url,
                        payload=None, success=False, error_code="download_connection_error",
                        duration_ms=0.0, retry_count=dl_retries))
                    break
            except ValueError:
                # 空 body（download_fb_image 契約）不重試
                if metrics:
                    metrics.record_download(False)
                results.append(CrawlerVisionImageResult(
                    image_index=ref.original_index, image_url=ref.original_url,
                    payload=None, success=False, error_code="empty_image_body",
                    duration_ms=0.0, retry_count=0))
                break
            except Exception:
                # 非預期下載錯誤（不靜默吞掉）
                raise
        if data is None:
            if results and not results[-1].success and not continue_on_error:
                break
            continue
        if metrics:
            metrics.record_download(True)

        # ── Vision 分析（analyzer 內部已有 retry，crawler 不疊加）──
        r = analyze_single_post_image(
            image_index=ref.original_index, image_url=ref.original_url,
            image_bytes=data, analyze_image_func=analyze_image_func,
        )
        # 合併下載重試次數：final = download_retries + vision_retries
        # （本階段 analyzer retry 不可取得時視為 0）
        if dl_retries:
            r.retry_count += dl_retries
        if metrics:
            metrics.record_result(r)
        results.append(r)

        # ── continue_on_error=False：第一張失敗立即停止（已成功保留）──
        if not r.success and not continue_on_error:
            break

    if metrics:
        metrics.record_post(
            discovered=len(image_urls), after_dedup=len(refs),
            truncated=truncated,
            successes=sum(1 for r in results if r.success),
            multi_image=len(refs_to_process) > 1,
        )
    return results


# ============================================================
# post Vision 欄位建置
# ============================================================
def apply_dom_extended_text(post: dict) -> bool:
    """設定 p["dom_extended_text"]（若有 DOM 延伸文字）；不覆蓋 p["text"]、不跳過圖片。

    注意：此欄位來自 DOM 貼文延伸文字（非 img alt attribute）。
    回傳是否設定。呼叫端必須照常處理圖片。
    """
    if not isinstance(post, dict):
        raise TypeError(f"post 必須是 dict，收到 {type(post).__name__}")
    ext = post.get("alt_text") or post.get("dom_extended_text")
    if ext:
        post["dom_extended_text"] = str(ext)
        return True
    return False


def build_crawler_output_post(post: dict) -> dict:
    """一篇 FB 原貼文 → 一筆輸出（不拆成多篇 synthetic posts）。

    保留原始 id/author/content(原文)/link/images；附加 vision/items/currency。
    """
    if not isinstance(post, dict):
        raise TypeError(f"post 必須是 dict，收到 {type(post).__name__}")
    r = {
        "id": post.get("id", ""),
        "author": post.get("author", ""),
        "content": post.get("text", ""),
        "link": post.get("url") or "https://www.facebook.com/groups/allinunderdog",
        "images": list(post.get("images") or []),
    }
    vis = post.get("vision_inputs")
    if vis:
        r["vision_inputs"] = copy.deepcopy(vis)
        if post.get("vision_payloads"):
            r["vision_payloads"] = copy.deepcopy(post["vision_payloads"])
    if post.get("items"):
        r["items"] = copy.deepcopy(post["items"])  # legacy 相容：第一個成功 payload
    if post.get("currency"):
        r["currency"] = post["currency"]
    if post.get("dom_extended_text"):
        r["dom_extended_text"] = post["dom_extended_text"]
    return r


def build_post_vision_fields(
    vision_results: list[CrawlerVisionImageResult],
) -> dict:
    """從結果建置 post 欄位（vision_inputs / vision_payloads / legacy_items / currency）。"""
    vision_inputs: list[dict] = []
    vision_payloads: list[object] = []
    legacy_items: list[dict] = []
    currency: str | None = None

    for r in vision_results:
        if not r.success:
            continue
        vision_inputs.append({
            "image_index": r.image_index,
            "image_url": r.image_url,
            "payload": copy.deepcopy(r.payload),
            "image_hash": None,
        })
        vision_payloads.append(copy.deepcopy(r.payload))

    # legacy items + currency 同源：第一個「具有效 items」的成功 payload
    # （inventory/payment 空 items 跳過，繼續找後一張；找到後停止，不從後續圖補 currency）
    for r in vision_results:
        if not r.success or r.payload is None:
            continue
        if isinstance(r.payload, dict):
            its = r.payload.get("items") or []
            if not isinstance(its, list) or not its:
                continue  # 空 items（inventory/payment 等）→ 跳過
            legacy_items = [
                {
                    "name": str(x.get("chinese_name") or x.get("name") or ""),
                    "wear": str(x.get("wear") or ""),
                    "price": x.get("price", 0),
                    "currency": x.get("currency", "RMB"),
                }
                for x in its if isinstance(x, dict)
            ]
            curs = {
                str(x.get("currency", "")).strip().upper()
                for x in its if isinstance(x, dict) and x.get("currency")
            }
            currency = curs.pop() if len(curs) == 1 else None
            break
        elif isinstance(r.payload, list) and r.payload:
            legacy_items = [
                {
                    "name": str(x.get("chinese_name") or x.get("name") or "")
                    if isinstance(x, dict) else str(x),
                    "wear": str(x.get("wear") or "") if isinstance(x, dict) else "",
                    "price": x.get("price", 0) if isinstance(x, dict) else 0,
                    "currency": x.get("currency", "RMB") if isinstance(x, dict) else "RMB",
                }
                for x in r.payload
            ]
            curs = {
                str(x.get("currency", "")).strip().upper()
                for x in r.payload if isinstance(x, dict) and x.get("currency")
            }
            currency = curs.pop() if len(curs) == 1 else None
            break

    return {
        "vision_inputs": vision_inputs or None,
        "vision_payloads": vision_payloads or None,
        "legacy_items": legacy_items,
        "currency": currency,
    }
