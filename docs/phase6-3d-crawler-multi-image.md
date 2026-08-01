# Phase 6.3D — Crawler Multi-Image Integration & End-to-End Validation

> 移除「第一張成功就 break」，所有圖片依序獨立分析，輸出 vision_inputs。
> 預設 parser mode 仍 off；crawler 舊 post["items"] 相容欄位保留。

## 1. 修改前資料流

```
FB post p['images'] → for img in images[:3]:
  → requests.get(timeout=15)
  → 分類 prompt（僅 inventory/single/other）→ inventory(跳過) / single
  → 提取 prompt → items
  → p['items'].append(每件)
  → if p['items']: break   ← 第一張成功就停（L389）
→ 每 item 扁平化為獨立 result（p{i}_item{j}）
```

## 2. 修改後資料流

```
FB post p['images']
→ analyze_post_images（URL 去重 → max_images → 循序）
  每張：下載(重試1次) → 分類 → 依類型提取 → CrawlerVisionImageResult
→ build_post_vision_fields
→ post["vision_inputs"]（含 image_index/image_url/payload）
→ post["vision_payloads"]（相容）
→ post["items"]（legacy 相容：第一個成功 payload）
→ post["currency"]（第一個成功單一幣別）
→ analyze_arbitrage.extract_vision_inputs_from_post()
→ parse_post_for_production() → Vision adapter → Evidence Merge → Legacy Adapter
```

## 3. 第一張 break 移除位置

`cdp_fb_crawler.py` 原 L389 `if p['items']: break` 已移除——改為
`analyze_post_images()` 循序處理全部（去重後 ≤ max_images）。

## 4. 輸出格式

```python
# 優先（V2 消費）
post["vision_inputs"] = [
    {"image_index": 0, "image_url": "...", "payload": {...}, "image_hash": None},
    ...
]
# 相容
post["vision_payloads"] = [payload0, payload1, ...]
```
避免雙欄位重複處理：`extract_vision_inputs_from_post()` 優先序為
vision_inputs > vision_payloads（只取其一）。

## 5. post["items"] 相容策略

- **第一個「具有效 items」的成功 payload**（inventory/payment 空 items 跳過繼續找）
- legacy items 與 currency **同一組 items 同源**（多幣別 → currency=None；找到後停止，不從後續圖補）
- 第一張失敗、第二張成功 → 用第二張
- V2 用完整 vision_inputs

## 6. image_index 策略

- **保留原始 images list 的 index**（去重與截斷後不重新編號：`[A,A,B]` → 0、2）
- 失敗圖片保留 index（中間失敗 → 成功圖 index 0、2，不重編號）
- 結果順序 = 處理順序

## 7. URL 去重策略

`normalize_crawler_image_url` 產出 **dedup_key**（去空白/fragment、query **保守排序且保留全部參數**——包括 FB CDN 簽章 oh/oe/_nc_*，不確定即保留）。
`deduplicate_post_image_refs` → `CrawlerImageRef(original_index, original_url, dedup_key)`：
- **original_url 用於實際下載**（絕不傳 normalized key）
- dedup_key 只供去重比較
- **不**下載判斷、**不**依商品名稱、**不**perceptual hash

## 8. 最大圖片數限制

`ALKAID_MAX_VISION_IMAGES_PER_POST`（預設 5、1-10、非法 fallback 5）
**DOM 擷取階段不限制張數**（保留完整清單）；去重後在 Vision 層套用
→ `vision_images_truncated:<dedup>:<max>` warning + metrics。

## 9. Timeout / Retry

- `ALKAID_VISION_IMAGE_TIMEOUT_SECONDS`（預設 20、5-60）
- 下載最多重試 **1 次**（timeout/暫時錯誤；404 不重試）→ error_code=download_timeout
- Vision analyzer 內部已有 retry=1（crawler **不疊加**）；timeout → vision_timeout
- `ALKAID_VISION_CONTINUE_ON_ERROR`（預設 true）：false 時首個失敗中止整篇

## 10. 單張失敗隔離

- 每張獨立 try（只捕捉 TimeoutError/ValueError/TypeError/JSONDecodeError/URLError/ConnectionError/OSError）
- 失敗 → CrawlerVisionImageResult(success=False, error_code) → 下一張繼續
- **空結果**（單一物品提取回傳 None/空 list）→ `empty_vision_result` 失敗
  （inventory 類型 items=[] 是有效結果，不視為失敗）
- 全部失敗 → vision_inputs=None → text-only pipeline 照常

## 10.1 Legacy items 支援 list payload

`build_post_vision_fields` 的第一個成功 payload 若本身是 list（Vision 直接回傳陣列），
直接當 legacy items（dict payload 則取 `payload["items"]`）。

## 11. Metrics（CrawlerVisionMetrics，16 欄）

`posts_with_images / images_discovered / images_after_dedup / images_truncated /
images_downloaded / image_download_failures / vision_attempts / vision_successes /
vision_failures / vision_timeouts / vision_retries / total_vision_duration_ms /
posts_with_any_vision_success / posts_with_all_vision_failed / multi_image_posts /
payloads_emitted` + `reset()` + `summary()`（不記錄敏感資料）。
`record_post` 驗證計數非負 int 拒 bool、successes ≤ 實際處理數。

## 12. Parser Mode 與 Crawler Vision 的關係

- crawler **始終**可跑 Vision（成本由 `ALKAID_MAX_VISION_IMAGES_PER_POST` 控制）
- parser mode（off/shadow/safe/v2_only）只決定 production 是否消費 vision_inputs
- off 模式：process_posts 忽略 vision_inputs，結果與舊版一致

## 13. Rollback

1. 還原 `cdp_fb_crawler.py` 圖片區段（git checkout 單檔）→ 回到第一張 break
2. 或設 `ALKAID_MAX_VISION_IMAGES_PER_POST=1`（等價單圖行為）
3. parser mode 維持 off 即 production 完全不受影響

## 14. 已知限制

- 中英名稱對齊需 mhn/component 一致（圖需提供 market_hash_name 或英文名）
- 圖片 price 需賣家語境（evidence 含售/算）才判 SELLER_ASK，否則 UNKNOWN
- 循序處理（無平行化）；成本防護僅靠 max_images
- crawler 外層既有 `except Exception`（ImportError/已知錯誤）保留

## 15. Phase 6.3E / Phase 7 建議

- 6.3E：Vision 名稱標準化（中英對齊 + 字典驗證）
- 7.x：平行化（串行保留順序前提下）、perceptual hash 去重、Vision 結果快取

---

## 16. Phase 6.3D.1 — Safety & Contract Hardening

### URL 原值與 dedup key 分離
- `CrawlerImageRef(original_index, original_url, dedup_key)`：**original_url 供下載**（保留 FB CDN 簽章 oh/oe/_nc_*），dedup_key 只供比較（保守排序、不移除任何參數）
- `normalize_crawler_image_url` 不再刪除任何 query 參數（不確定即保留）

### 原始 image_index 保留
- 去重與截斷後仍用原始 images list 的 index（[A,A,B] → 0、2，不重編號）

### 圖片類型映射
`market_listing/buff/steam_market → market`、`inspect_* → inspect`、`chat → chat`、
`payment/trade/other/unknown` 各自保留；payload `type` 原樣保存（crawler 不判價格語意）

### 一篇貼文一筆輸出
- `build_crawler_output_post()`：一 FB 貼文 → 一筆結果（不拆 p{i}_item{j}）
- 原始 id/author/text/link/images 不變

### 空 items 圖片類型規則
| 類型 | 空 items 行為 |
|------|--------------|
| inventory | ✅ 成功保存（rows/cols） |
| payment | ✅ 成功保存（不產 legacy items/currency） |
| trade / inspect | ✅ 保守保存 type + items=[]（Adapter 判定） |
| single / multi / market / chat | ❌ empty_vision_result 失敗 |

### DOM extended text
- `apply_dom_extended_text()`：只設 `p["dom_extended_text"]`（來源為 DOM 貼文延伸文字，
  **非 img alt attribute**），**不覆蓋 text、不跳過圖片**

### TYPE_PROMPT 完整類型
分類 prompt 明確支援 9 類型（inventory/single/multi/market/chat/inspect/payment/trade/other），
只做分類、不判斷 seller ask。

### URL dedup key 序列化（6.3D.3）
`normalize_crawler_image_url` 改用 `parse_qsl(keep_blank_values=True)` + 依 (key, value)
穩定排序 + `urlencode(doseq=True)`：
- **保留每組重複 query pair**（`a=1&a=2` 不併成 `a=1,2`）
- `a=1&a=2` 與 `a=1%2C2` 的 key 不同
- percent-encoded 值正規化一致；空白值保留；fragment 移除

### retry_count 合併（6.3D.3）
`final_retry_count = download_retry_count + vision_retry_count`：
- 第一次 timeout/connection/5xx 第二次成功 → retry_count=1（metrics vision_retries 同步 +1）
- 4xx / 空 body → 0（不重試）

### 下載最終失敗的 retry metrics（6.3D.3 最終修正）
`CrawlerVisionMetrics.record_download_retry(count)`：
- 下載**第二次仍失敗**（timeout/connection/5xx）→ `vision_retries += dl_retries`
- **不**呼叫 record_result()（Vision 未執行，不增加 vision_attempts/vision_failures）
- count 非負 int 拒 bool、負數 ValueError
- 4xx / 空 body 不增加 retry

### HTTP/timeout 錯誤策略（download_fb_image）
| 例外 | error_code | 重試 |
|------|-----------|------|
| requests.Timeout → TimeoutError | download_timeout | 1 次 |
| requests.ConnectionError → ConnectionError | download_connection_error | 1 次 |
| HTTP 4xx → HttpDownloadError | download_http_4xx | ❌ |
| HTTP 5xx → HttpDownloadError | download_http_5xx | 1 次 |
| 空 body → ValueError | empty_image_body | ❌ |

### continue_on_error
`analyze_post_images(..., continue_on_error)`：false 時第一張失敗**立即停止**（已成功保留）

### metrics 強化
`record_post` 驗證 discovered/after_dedup/truncated/successes 非負 int 拒 bool、
successes ≤ 實際處理數；summary 除零安全
