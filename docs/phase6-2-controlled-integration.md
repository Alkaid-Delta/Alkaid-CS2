# Phase 6.2 — Controlled Production Integration

> 在 process_posts() 中受控接入 V2 deterministic pipeline，保留 legacy fallback。
> 預設 `off`：production 行為與先前完全一致。

## 1. 正式呼叫鏈（盤點結果）

```
Facebook post {id, author, url, images, content, currency}
→ process_posts()                    analyze_arbitrage.py L823
→ extract_skin_info(post["content"]) L833（字典 → DeepSeek → csgoskins 驗證）
→ seller_price 判斷                  L839-844（RMB ×4.5 → sp<=0 跳過）
→ lookup_buff_price(mh)              L850（SQLite cs2_prices.db）
→ analyze_arbitrage(post, buff)      L859（套利計算 → 上雲/存歷史）
```

- post_text 來源：`post["content"]`（crawler：純文字貼文 = 原文；每圖獨立 = 單圖文字）
- post_id/author/url/images 皆可取得（crawler L138-143）
- seller_price ×4.5 時機：L839（`sp > 0 and currency == "RMB"`）
- None 風險點：L841 `sp <= 0`（None 會 TypeError）→ 已由 `is_valid_legacy_seller_price()` 防守
- feature flag 插入點：L831-845（Step 1）

## 2. Feature Flag

```bash
export ALKAID_V2_PARSER_MODE=off      # 預設：完全 legacy
export ALKAID_V2_PARSER_MODE=shadow   # legacy 正式 + V2 平行記錄差異
export ALKAID_V2_PARSER_MODE=safe     # 安全單商品走 V2，其餘 fallback legacy
export ALKAID_V2_PARSER_MODE=v2_only  # 只走 V2，blocked 直接跳過
```

非法值 → warning + fallback `off`（`get_v2_parser_mode()` L29）。

## 3. 各模式行為

| 模式 | 正式輸出 | V2 執行 | legacy 執行 | blocked/無價處理 |
|------|---------|---------|------------|-----------------|
| off | legacy | ❌ | ✅ | 原行為（跳過） |
| shadow | legacy | ✅（只記錄 diff） | ✅ | 不影響 legacy |
| safe | V2（安全案例） | ✅ | fallback | fallback legacy |
| v2_only | V2 | ✅ | ❌ | skipped + blocked |

safe 採用 V2 的條件（全數成立）：blocked=False、data 非 None、mhn 非空、
seller_price>0、item role=SELLING、選中 item/price、無 ambiguous/currency warning。

## 4. process_posts 修改點

- Step 1 依 `get_v2_parser_mode()` 分流
- off：原 L833-845 邏輯原封不動
- 其他：`parse_post_for_production()` + `_METRICS.record(result)`
- `result.blocked or data is None` → skip
- `source=="v2"` → 不再 ×4.5；`legacy/shadow_legacy` → 保留原 ×4.5
- `is_valid_legacy_seller_price(sp)` False → skip（不查價、不比較）

## 5. seller_price 防守

```python
is_valid_legacy_seller_price(value):
    - int/float（拒絕 bool）
    - math.isfinite（拒絕 NaN/±Inf）
    - value > 0
```

None / bool / 非數字 / <=0 / NaN / Inf → 直接 skip，永不進入 `sp <= 0` 比較或 `round(sp*4.5)`。

## 6. 防止 V2 重複換算

- V2 adapter 只輸出 TWD（Phase 6.1.1：RMB/USD/UNKNOWN → blocked）
- `source=="v2"` 分支明確 `pass`（不執行舊 ×4.5）
- 舊 ×4.5 只保留在 `legacy` / `shadow_legacy` 分支
- CurrencyService 本階段不接入 production

## 7. Metrics

`ProductionParseMetrics`：total / v2_used / legacy_used / shadow_runs / skipped /
v2_blocked / v2_fallback / name_mismatch / price_mismatch（`_METRICS.record()` 自動累計）。

## 8. 回退方式

```bash
unset ALKAID_V2_PARSER_MODE   # 或 export ALKAID_V2_PARSER_MODE=off
```
process_posts 立即回到原行為；V2 相關檔案皆為新增（integration/），刪除即完全移除。
