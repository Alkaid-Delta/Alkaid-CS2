# Phase 6.3A — Vision Evidence Adapter Foundation

> Vision / OCR 回傳 → V2 領域模型的安全轉換層。
> 本階段不接 crawler、不改 production、不查價、不換算。

## 1. 現有 Vision 呼叫鏈盤點

```
FB 圖片 → cdp_fb_crawler.fetch_posts()
  → va.analyze_image(bytes, custom_prompt=類型判斷)      vision_analyzer.py L32
    → {"type": "inventory"|"single"|"other", rows, cols}
  → inventory → vision_pipeline 切格子（手繪勾）
  → single → va.analyze_image(custom_prompt=提取JSON陣列)  L356
    → list[{name, wear, price, currency, stattrak}]
  → p['items'].append({name, wear, price, currency})       L379-384
  → if p['items']: break  ← 第一張圖成功就停               L389
  → 每件獨立成一筆 post（currency 標記）                   L401-416
  → process_posts → extract_skin_info / ×4.5              analyze_arbitrage L839
```

**盤點 10 問**：
1. 簽名：`analyze_image(image_bytes, retry=2, custom_prompt=None) -> dict | list | None`
2. 格式：list[dict]（`{chinese_name, wear, float, price, currency, stickers, stattrak, nametag}`）或 dict（`{market_hash_name, seller_price}`）或 None
3. 分類：prompt 內六類型（BUFF庫存/詳情/貼紙、Steam市集、BUFF市場列表、遊戲內檢視）；crawler 先問 type
4. **第一張 break：L389**
5. **拆 items：L307 + L379-384**
6. **currency：L376 `item.get('currency', 'RMB')`（預設 RMB）**
7. 失敗：analyzer 重試 retry+1 → None；crawler try/except → warning（L390-393）
8. **無 hash/URL 去重**
9. **無 confidence 欄位**
10. 平台靠 prompt 類型隱含；價格語意（賣價 vs 掛牌價）**無法區分**

## 2. 資料流（V2）

```
Vision payload（dict/list/JSON string/code fence/None）
→ normalize_vision_payload() → VisionRawResult（標準化中介）
→ vision_result_to_evidence() → ImageEvidence
  ├─ item_candidates: list[ItemCandidate]（evidence=VISION, verified=False）
  └─ price_candidates: list[PriceCandidate]（currency 原樣、不換算）
→ deduplicate_image_evidence()（metadata 去重）
```

## 3. 圖片類型與價格語意規則

| ImageKind | item 候選 | price 候選 | 備註 |
|-----------|----------|-----------|------|
| SINGLE_ITEM | ✅ | ✅ | source=IMAGE |
| MULTI_ITEM | ✅ 全部保留 | ✅ | 不取第一件 |
| INVENTORY_GRID | ❌ | ❌ | warning `inventory_grid_deferred` |
| MARKET_LISTING | ✅ | ✅ 但 **REFERENCE**（掛牌價≠賣價） | source=MARKET_SCREENSHOT |
| CHAT_SCREENSHOT | ✅（role 保守） | ✅（語意判斷） | source=CHAT |
| INSPECT_SCREENSHOT | ✅（商品/磨損） | ❌ 不產 seller price | |
| PAYMENT_PROOF | ❌ | ❌ | |
| TRADE_CONFIRMATION | 保守 | ❌ | |
| UNKNOWN | 保守 | 保守 | confidence ≤ 0.50 |

**價格型別**：售/賣/算/開價/帶走 → SELLER_ASK；同磨底/BUFF底 → BUFF_FLOOR；
算式（*、=）→ CALCULATED；打包/全收 → BUNDLE_TOTAL；其餘 → UNKNOWN。

## 4. Hallucination 防護

- confidence < 0.50 → `low_confidence` warning（候選保留，不得進 safe production）
- mhn 與 weapon/skin 結構衝突 → `name_component_conflict` + 信心降到 ≤0.50
- 未知 currency 字串 → Currency.UNKNOWN + `unknown_currency`
- price ≤ 0 → 不建立 PriceCandidate
- price < 50 或 > 5,000,000 → `suspicious_price_range`（不刪除）
- mhn = "UNKNOWN"/"NONE"/空白 → None
- 缺 weapon → skin-only + confidence ≤ 0.60
- **不標 verified=True**（名稱未經 csgoskins 驗證）

## 5. 圖片去重

- 鍵優先：image_hash > normalized URL > image_index
- normalize：去 fragment、query 排序、忽略 FB 簽章參數（`_nc_*`/`oh`/`oe`/`rt`…）
- **不**合併不同圖的 item、**不**依相似名稱合併、**不**下載圖片、**不**算 perceptual hash

## 6. 已知限制

- 磨損中文 → 英文為本地對照（未接 csgoskins 驗證）
- chinese_name 直接當 skin-only 名稱（未拆 weapon/skin 結構）
- 圖片語意（賣價/掛牌價）依賴 Vision 的 evidence 文字與 type 標記
- 尚未合併進 ParsedPost（Phase 6.3B）

---

## 7. Phase 6.3A.1 — Safety Hardening

### 去重交叉檢查
每筆 evidence **同時**檢查三鍵，任一成立即去重（保留第一筆）：
- image_hash 已出現
- normalized URL 已出現
- image_index 已出現

### 非交易圖片價格阻擋
| ImageKind | PriceCandidate | ItemCandidate |
|-----------|---------------|---------------|
| INVENTORY_GRID | ❌ | ❌（deferred） |
| INSPECT_SCREENSHOT | ❌（即使 payload 有 price/售語境） | ✅ |
| PAYMENT_PROOF | ❌ | ❌ |
| TRADE_CONFIRMATION | ❌ | ✅（保守） |

### confidence coercion
| 輸入 | 結果 |
|------|------|
| None（未提供） | SINGLE/MULTI 0.70、其他 0.50（無 warning） |
| 合法 finite 0~1 | 原值保留（**合法 0.0 不得提升**） |
| bool / 非數字 / NaN / Inf / 負數 / >1 | **0.0 + `invalid_confidence` warning**（不套預設） |
| UNKNOWN ImageKind | 合法信心仍上限 0.50 |

### MARKET_LISTING 價格語意優先
- evidence 含 同磨底/BUFF底/**最低價** → BUFF_FLOOR（BUFF 平台底價）
- 其他掛牌價 → **REFERENCE**（畫面含「售價」也不判 SELLER_ASK；無 seller_context 欄位支援）
- 檢查順序：MARKET_LISTING 分支 **先於** SELL 關鍵詞

### 領域驗證強化（VisionRawItem）
- raw_name/mhn/weapon/skin/wear：str 或 None
- stattrak：bool 或 None
- price_amount：finite Decimal 或 None（NaN → ValueError）
- bbox：拒 bool、非負 int、x2>=x1、y2>=y1
- evidence_text：str
- warnings defensive copy

### deepcopy
`ImageEvidence.raw_result` 與 `VisionRawResult.raw_payload` 改為 `copy.deepcopy`（巢狀結構不受外部修改影響）。
