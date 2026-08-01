# Phase 6.3B — Text and Multi-Image Evidence Merge

> 把 deterministic text ParsedPost 與多張 ImageEvidence 合併為單一 ParsedPost。
> 不接 crawler、不呼叫 Vision、不下載圖片、不換算貨幣。

## 1. 資料流

```
ParsedPost (text) ─┐
                   ├─→ deduplicate_image_evidence（重複圖片只處理一次）
ImageEvidence[]  ──┘      ↓
                  merge_text_and_image_evidence()
                  ├─ item 合併（text 優先 + 圖片新增 + 衝突檢測）
                  ├─ text linker（link_prices_to_items）
                  ├─ 圖片預關聯（1:1 / 1:N；N:N → AMBIGUOUS_LINK）
                  ├─ price 合併（合併/衝突/共存）
                  ├─ 索引重建（雙向一致）
                  └─ 新 ParsedPost（metadata + escalation_reason + status）
```

## 2. Item 合併規則

| 情境 | 處理 |
|------|------|
| mhn 完全相同 | 合併（text 為主、confidence max、`corroborated_by_image:N`） |
| mhn 不同但 weapon+skin+stattrak 相容 | 視為同一商品候選（wear 差異走衝突流程） |
| 文字缺 wear、圖片有 wear | 補全（不降 confidence） |
| wear 不同 | 兩筆保留 + WEAR_CONFLICT（warning） |
| 圖片新增商品 | 保留 + `image_only_item:<idx>` |
| role 不同（皆非 UNKNOWN） | ROLE_CONFLICT（warning），不覆蓋 text role |
| 等價但 mhn 不同 | ITEM_NAME_CONFLICT（warning） |
| 僅 skin 名稱相同 | **不算等價**（不得只用 skin 判定） |

## 3. Price 合併規則

| 情境 | 處理 |
|------|------|
| 同 item 同金額同 currency 同 type | 合併 + `corroborated_price_by_image:N` |
| 同 item 兩個 SELLER_ASK 不同金額 | 兩筆保留 + PRICE_CONFLICT（**error**） |
| 同金額不同 currency | 兩筆保留 + CURRENCY_CONFLICT（**error**） |
| BUFF_FLOOR/REFERENCE vs SELLER_ASK | **共存不衝突**（不換算） |
| UNKNOWN price | 保留 + `image_unknown_price:N`（不覆蓋明確 SELLER_ASK） |

## 4. 圖片預關聯策略

```
同圖 1 item + N prices  → 全部綁該 item
同圖 N item + N prices  → 按順序綁（warning image_order_linking）
同圖 N item + M prices  → AMBIGUOUS_LINK（error）、price 不綁
text candidates         → 原 linker（有文字位置）
```

## 5. Conflict 規則表

| ConflictType | severity | 觸發 |
|--------------|----------|------|
| WEAR_CONFLICT | warning | text/image wear 不同 |
| ROLE_CONFLICT | warning | text/image role 不同（皆非 UNKNOWN） |
| ITEM_NAME_CONFLICT | warning | 等價但 mhn 不同 |
| PRICE_CONFLICT | **error** | 同商品兩個 SELLER_ASK 不同金額 |
| CURRENCY_CONFLICT | **error** | 同金額不同幣別 |
| AMBIGUOUS_LINK | **error** | 多商品多價格無法確定對應 |

## 6. parse_status 更新策略

| 情境 | status |
|------|--------|
| 原 ERROR | ERROR（保留） |
| 有任何 conflict（含 warning） | PARTIAL |
| 無圖片合併 | 原 status（text linker 結果） |
| 有 image-only item | PARTIAL（需 Validation Gate） |
| 無 conflict 且全部商品有價格 | OK |

**escalation_reason**：有 error conflict → `vision_merge_conflict`。

## 7. 已知限制

- 圖片名稱（chinese_name）與文字名稱（翻譯後英文）需靠 mhn 或 component 對齊；
  中文 vs 英文 skin 無法直接比對（Vision 名稱標準化留待後續）
- image 預關聯的「按順序」依賴 Vision 回傳順序穩定
- role 衝突不自動改寫（intent 重新推導留待 pipeline 層）
