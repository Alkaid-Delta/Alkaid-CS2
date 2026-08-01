# Phase 6.3C — Controlled Vision Production Integration

> 在 Production Bridge 中受控接入多圖片 Vision Evidence。
> 預設模式（off）不變；crawler 未修改（第一張 break 留待 6.3D）。

## 1. 資料流

```
post dict {id, author, url, content, images, items|vision_payloads|vision_inputs}
→ extract_vision_inputs_from_post()
→ parse_post_for_production(vision_inputs=...)
  ├─ off：legacy only（完全不跑 V2 / Vision）
  ├─ shadow：legacy 正式 + text V2 diff + Vision merge diff
  ├─ safe：Vision merge 安全？→ merged V2 : text V2 安全？→ V2 : legacy
  └─ v2_only：Vision 安全？→ V2 : text V2 安全？→ V2 : skipped
→ ProductionParseResult（data / source / vision_summary）
→ process_posts()（V2 不 ×4.5；legacy 保留 ×4.5）
```

## 2. 現有資料流盤點

- process_posts 可取得：`post["id"]`、`post["author"]`、`post["url"]`、`post["content"]`、`post["images"]`
- crawler 目前**沒有** vision_payloads / vision_inputs；只有舊 `post["items"]`（每圖獨立）
- crawler L386 `if p['items']:` 第一張成功即 break（**本階段不改**，6.3D 移除）
- vision_analyzer 只收 image bytes（`analyze_image(bytes, retry, custom_prompt)`）
- bridge 原本只收 image_urls → 本階段加 vision_inputs（預設 None，向後相容）

## 3. 各模式 Vision 行為

| 模式 | Vision | text V2 | legacy |
|------|--------|---------|--------|
| off | ❌ 完全不執行 | ❌ | ✅ 原行為 |
| shadow | 平行記錄 diff | 平行記錄 | ✅ 正式輸出 |
| safe | ✅ 安全則用 | fallback | 最後 fallback |
| v2_only | ✅ 安全則用 | fallback | ❌ 永不呼叫 |

**safe fallback 順序**：Vision merge 安全 → text-only V2 安全 → legacy
**v2_only fallback 順序**：Vision merge 安全 → text-only V2 安全 → skipped（blocked）

## 4. Vision merge 安全條件（vision_merge_safe_reasons）

`vision_not_used` / `vision_fallback:<reason>` / `no_merged_post` / `merge_status_not_ok` /
`escalation_reason` / `unresolved_item` / `no_legacy_result` / `legacy_blocked` /
`no_legacy_data` / `invalid_seller_price` / `not_single_safe_selling_item` /
`no_selected_item` / `no_selected_price` / `error_conflict` / `price_conflict` /
`currency_conflict` / `ambiguous_link` / `low_confidence` / `unknown_currency` /
`image_unknown_price` / `image_only_item`

空清單才代表安全。**不只依 parse_status 判斷。**

## 5. 圖片失敗策略

- 單張 payload=None / invalid JSON / malformed / adapter raise → `vision_image_error:<index>` + 繼續下一張
- 全部失敗 → `vision_used=False` + `fallback_reason=all_vision_images_failed` + 保留 text-only
- 只捕捉 ValueError / TypeError / JSONDecodeError（禁止 except Exception: pass）

## 6. extract_vision_inputs_from_post 規則

```
A. post["vision_inputs"]（VisionImageInput 或 dict）
B. post["vision_payloads"]（list → image_url 從 post["images"] 對應，無則 inline://post/<id>/image/<i>）
C. post["items"]（舊 crawler → 單一 payload {type: single|multi, platform: facebook, items}）
D. 無資料 → None；非法資料 warning + 有效項（不 crash）
```

## 7. Vision Metrics（新增 12 欄）

`vision_posts / vision_inputs / vision_evidence / vision_used / vision_fallback_to_text /
vision_fallback_to_legacy / vision_all_failed / vision_conflicts / vision_error_conflicts /
vision_items_added / vision_prices_added / vision_duplicate_images_removed`

record() 讀 vision_summary（缺欄位不 crash）；非 Vision post 不計。

## 8. ProductionParseResult 擴充

`vision_summary: dict | None`（input_count / evidence_count / merged_item_count /
merged_price_count / conflict_count / error_conflict_count / status / fallback_reason /
used / fallback_to_text / fallback_to_legacy / all_failed / items_added / prices_added /
duplicate_images_removed）——不含 payload / bytes / token。

## 9. 已知限制

- crawler 仍只輸出第一張圖 items（6.3D 移除 break）
- Vision 中英名稱對齊需 mhn/component 一致（名稱標準化留待後續）
- metrics 無持久化（重啟歸零）
- 非 thread-safe（本階段接受）

---

## 10. Phase 6.3C.1 — Hardening

### Exception narrowing
- text-only V2：`except Exception` → `except (TypeError, ValueError, json.JSONDecodeError)`
- 未知例外**向上拋出**（發現程式缺陷，不靜默轉 fallback）
- `v2_error = f"{type(exc).__name__}:{str(exc)[:200]}"`（截斷敏感內容）

### shadow vision_input_count
- `_extend_shadow_diff_vision(diff, vp, *, input_count)` 寫入真實 input 數（不再寫死 0）

### Defensive copy
- `VisionImageInput.__post_init__`：payload `copy.deepcopy`；image_url/hash strip 後保存
- `extract_vision_inputs_from_post`：已是 VisionImageInput 也**建立新物件**（不共享參照）

### post link 欄位
- `post_link = post.get("link") or post.get("url") or ""`（link 優先）

### warnings 去重保序
- `list(dict.fromkeys(warnings))`（不排序）套用於：
  VisionMergeProductionResult.warnings / ProductionParseResult.warnings /
  shadow warnings / vision fallback warnings

### VisionImageInput 驗證補強
- payload 拒絕 bool
- image_url / image_hash strip 後不可空
