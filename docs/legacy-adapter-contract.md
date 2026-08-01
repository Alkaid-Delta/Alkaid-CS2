# Legacy Adapter Contract（Phase 6.1）

> 新舊介面轉換契約：V2 ParsedPost → 舊版 `extract_skin_info()` 相容輸出。

## 1. 舊 extract_skin_info 的輸入／輸出

**簽名**：`extract_skin_info(post_text: str) -> dict | None`

| 路徑 | 輸出 |
|------|------|
| 字典命中（full_cn_to_en L383 / pattern_cn_to_en L429） | `{"market_hash_name": str, "seller_price": int, "confidence": "high"}` |
| DeepSeek（L522+） | `{"market_hash_name": str, "seller_price": int, "confidence": "high"/"medium"/"low"}` |
| 非 CS2 貼文（NONE） | `None` |
| 驗證失敗重試仍失敗（L562） | 回傳第一次未驗證資料（**已知缺陷**） |
| 無 API key / 例外 | `None` |

**seller_price 語意**：`>0` 有價格、`<=0`（-1）無價格（L841 `if sp <= 0: 跳過`）。

## 2. process_posts 實際使用的欄位（L823-875）

| 欄位 | 用途 | 必要性 |
|------|------|--------|
| `market_hash_name` | `lookup_buff_price(mh)` 查詢鍵 | **必要** |
| `seller_price` | `sp > 0` 判斷 + `_seller_price` 寫入 post + `round(sp*4.5)` RMB 轉換 | **必要**（int/float） |
| `confidence` | 僅 print 顯示（`print(f"...| {conf}")`） | 可選（str 或 float 皆可） |

## 3. 必要欄位

- `market_hash_name`: 非空 str（查價鍵）
- `seller_price`: 數字；None 會讓舊 `sp <= 0` 判斷 TypeError → **Phase 6.2 接入時 process_posts 必須先檢查 blocked / None**（adapter 本身輸出 None 表示無價格）

## 4. 可選欄位

- `confidence`（str/float 皆可；adapter 輸出 float 0.0-1.0）
- `source` / `item_role` / `selection_reason`（adapter 附加診斷欄位，舊呼叫端忽略）

## 5. Adapter 會 block 的情況（legacy_data=None, blocked=True）

| 情境 | reason |
|------|--------|
| ParseStatus.ERROR | UNRESOLVED（warning: parse_error） |
| ParseStatus.UNRESOLVED | UNRESOLVED |
| 無 items | NO_ITEM |
| 多件都有 SELLER_ASK | AMBIGUOUS（不取第一件） |
| 多件且無 selling role | AMBIGUOUS（不硬猜） |
| 多件只有一件有 ask（非 selling 全體） | AMBIGUOUS |
| 同一 item 多個不同金額 SELLER_ASK | AMBIGUOUS |
| 多件無 selling role 的 bundle 貼文 | AMBIGUOUS |
| item 有 validation_error | UNRESOLVED |
| item 名稱空白 | UNRESOLVED |
| seller ask 非 TWD | UNRESOLVED（warning: currency_conversion_required） |

## 6. 多商品為何不能安全降級

舊介面只有單一 `market_hash_name` + `seller_price`，無法表示「多商品各自價格」。
取第一件會造成：
- 名稱與價格錯配（紅線 7480 / 火神 14000 歷史缺陷）
- 誤報套利機會（拿 A 的價格套 B 的 BUFF 底價）
→ 多商品一律 blocked，由 V2 ParsedPost 多商品模型正確表示。

## 7. 非 TWD 價格為何不能在 adapter 換算

- 舊 DeepSeek prompt（L517）要求模型自己 ×4.5（重複換算缺陷）
- CurrencyService 是唯一換算處（Blueprint §8）
- adapter 若自行 ×4.5 會與 CurrencyService 雙重換算
→ adapter 只標記 `currency_conversion_required`，Phase 6.2/6.3 由 CurrencyService 統一處理。

## 7b. 貨幣與價格型別安全規則（Phase 6.1.1）

| 情境 | 處理 |
|------|------|
| SELLER_ASK + TWD | ✅ 可輸出 seller_price |
| SELLER_ASK + RMB/USD | blocked + `currency_conversion_required`（不換算） |
| SELLER_ASK + UNKNOWN 貨幣 | blocked + `currency_unknown`（**不得假設為 TWD**） |
| UNKNOWN / REFERENCE / BUFF_FLOOR / CALCULATED / BUNDLE_TOTAL 價格 | **絕不當 seller_price**；有 UNKNOWN 價格 → warning `unknown_price_not_used` |
| 唯一 selling 無 SELLER_ASK | blocked=False、輸出 market_hash_name、seller_price=None、warning `no_seller_price` |

## 7c. 只驗證被選商品（Phase 6.1.1）

1. 先依 role（SELLING）與 SELLER_ASK 決定可選商品
2. 只驗證**被選**商品的 validation_error / market_hash_name
3. 未選中 item 的 validation_error → warning `unselected_item[i]_validation_error`（**不阻擋**安全 selling item）
4. 無法確定選擇（多件 selling 無 ask 等）→ AMBIGUOUS blocked

## 8. Phase 6.2 正式接入策略

1. process_posts 中 `extract_skin_info()` 呼叫點（L833）改為：
   - 先跑 V2 `parse_post()` → `to_legacy_skin_info()`
   - `blocked=True` → 跳過該貼文（或記錄）
   - `legacy_data=None`（無 seller_price）→ 跳過
   - 成功 → 沿用現有 lookup_buff_price / analyze_arbitrage
2. 逐步替換：先接單商品貼文，多商品案例待 V2 analysis pipeline 完成
3. 保留 `extract_skin_info()` 作為 fallback（A/B 測試比對）
4. 非 TWD：經 CurrencyService 轉 TWD 後再輸出（不在 adapter 內）
