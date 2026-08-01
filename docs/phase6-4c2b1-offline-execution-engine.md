# Phase 6.4C2-B1 — Offline Fake Execution Engine, Cache Reuse and Failure Containment

> **完全離線、無網路、無真實資料**：FakeSecureImageLoader + FakeExternalAnalyzerAdapter
> 在 evaluation-only 環境演練完整 execution pipeline。anonymized_real=0 維持。

## 1. 目標

validated execution plan → memory-only fake bytes → SHA-256 驗證 → fake adapter →
normalized result 驗證 → atomic cache write → atomic audit write →
per-image failure containment → deterministic rerun / cache reuse。

## 2. 執行前 Gate（preflight）

任一失敗 → 不呼叫 loader/adapter、不寫 cache、寫 blocked audit（v2）、exit blocked：

- `execution_plan_invalid`（plan.status != planned / schema 不符 / case key 非 64-hex）
- `execution_plan_dry_run_only`（plan.dry_run）
- `execution_plan_not_authorized`（plan.authorized=False）
- `execution_plan_count_mismatch`（case/image/case_keys/indexes 與 eligible 不一致）
- `execution_plan_hash_mismatch`（expected hashes 與 eligible image_hashes 不一致）
- `duplicate_execution_item`（重複 (opaque case key, image index) pair）
- `secure_image_loader_unavailable` / `analyzer_adapter_unavailable`
- `output_path_not_allowed`（cache/audit 不在 allowed local_data root）

## 3. Fake 元件

- **InMemorySecureImageLoader**：`load(reference, expected_sha256)`；只收
  secure-store:// reference；object 不存在 → `secure_image_not_found`；
  SHA-256 不符 → `secure_image_hash_mismatch`；invalid reference →
  `secure_reference_invalid`；**未知 loader exception → `secure_image_loader_failed`**
  （不得全部映射成 not_found）；回傳 immutable bytes、零寫盤、
  不 log bytes、不保存 reference/raw exception。
- **FakeExternalAnalyzerAdapter**：deterministic（digest 數值決定受控輸出，
  不把 input hash 字串/bytes 寫入 normalized_result）；無 network/env/disk。

## 4. Cache Identity 與 Hit/Miss 語意

**Cache key** = sha256(opaque_case_key + image_index + image_sha256 +
analyzer_name + analyzer_version + schema_version)。不得只用 case key。

| 情境 | 行為 |
|------|------|
| cache 不存在 | **miss** → loader → adapter → validate → atomic write |
| cache 合法且 identity 全符 | **hit** → 不呼叫 loader/adapter、不重寫 cache、succeeded+1 |
| cache 壞掉/identity 不符 | **invalid-cache failure**（cache_read_failed / cache_record_invalid / cache_identity_mismatch）→ failed+1，不得信任 |

**計數語意（Phase 6.4C2-B1.1 文件固定）**：
- `processed_image_count`：engine 處理的所有 image items
- `cache_hit_count`：合法 cache hit
- `cache_miss_count`：找不到合法 cache、準備進入 loader path 的 item
- `cache_invalid_count`：cache 存在但 JSON/schema/hash/identity/status 不合法
- `attempted_image_count`：**實際 adapter.analyze_image invocation 數**
- 關係：`processed == hits + misses + invalid`、`attempted <= misses`、
  `succeeded + failed == processed`、`cache_write <= succeeded`
- **loader 失敗**：miss+1、attempted 不增加、failed+1
- **adapter 被真正呼叫前才 attempted += 1**
- 不得把 attempted 定義成 cache misses

## 5. Failure Containment

每張圖失敗（loader/not found/hash mismatch/adapter/result schema/cache read/cache
write）→ 記錄固定錯誤碼、failed+1、不保存原始 exception/reference/case ID、
繼續下一張。**失敗 item 不建立 cache；成功 item 仍建立 cache。**

**Run status**：全部成功 → `completed`；部分成功 → `completed_with_failures`；
全部失敗 → `failed`；preflight 不通過 → `blocked`。

## 6. Audit Schema v2

`external-analyzer-audit-v2`（嚴格 allowlist）：v1 全部欄位 +
`processed_image_count` / `cache_hit_count` / `cache_miss_count` /
`cache_invalid_count` / `analyzer_name` / `analyzer_version`。

**v2 計數關係（B1.1）**：`processed == hits + misses + invalid`、
`attempted <= misses`、`succeeded + failed == processed`、
`cache_write <= succeeded`。

**Cache validation 錯誤正規化**：load 只回傳固定碼
`cache_read_failed`（讀取/JSON/UTF-8）／`cache_record_invalid`（schema/
result hash/normalized result/status）／`cache_identity_mismatch`（identity）——
不得暴露 schema detail。corrupted cache → invalid+1、failed+1、
不呼叫 loader/adapter、不重寫 cache、仍寫出合法 v2 audit。

**版本決定**：B1 execution 只寫 **v2**；v1 僅供 B0 dry-run（validate_audit_manifest
雙版本支援，不靜默改變 v1 語意）。

## 7. Adapter Identity Binding（B1.1）

execution 不得由 caller 任意宣稱 analyzer identity。preflight 強制：
`adapter.analyzer_name == analyzer_name`、`adapter.analyzer_version ==
analyzer_version`、`plan.adapter_name == analyzer_name`；不一致 →
`execution_adapter_identity_mismatch`（blocked、loader/adapter 呼叫 0、
cache write 0、blocked audit 合法寫出）。

## 8. Audit Write Failure Contract（B1.1）

- **preflight blocked audit 寫失敗**：summary status 仍 `blocked`，
  fixed_error_codes 含 `audit_write_failed`（不靜默吞掉）
- **execution 完成後 audit 寫失敗**：不影響已完成的 cache；
  summary status 降級 `completed` → `completed_with_failures`、
  含 `audit_write_failed`、不暴露 exception

## 9. Multi-Case Image Index 語意（B1.1）

plan.image_indexes 為 **per-case range**：case A [0]、case B [0] →
扁平 [0, 0] 合法；duplicate 判定只用 (opaque_case_key, image_index)——
不同 case key 的相同 image_index 不算 duplicate。

## 10. CLI 模式

B1 不公開 `--execute-fake`：只提供 **Python API**
（`execute_external_analyzer_plan`）給 integration tests。Production CLI 預設
仍為 `--dry-run`；repository 正式 manifest anonymized_real=0 → 仍 exit 2、
`no_eligible_real_cases` / `no_eligible_real_images`、不載入 bytes、
不呼叫 adapter、不寫 cache（No-Real-Data Safe Stop 維持）。

## 11. Network／SDK 零容忍

執行期間：socket（create_connection/socket/getaddrinfo）、urllib.urlopen、
http.client 全封鎖下正常執行（counter=0）；engine/adapter/loader/cache 原始碼
無 requests/httpx/aiohttp/openai/anthropic/deepseek/gemini import；
無 API_KEY/TOKEN/COOKIE/SECRET env 讀取。

## 12. Determinism

相同 manifest/fixture/fake bytes/run salt/analyzer version + 全新 cache 目錄 →
除 run_id/started_at/completed_at 外全部一致（counts/status/error codes）；
cache key 與 normalized result 跨 process 穩定。同目錄重跑 → cache hit（重用）。

## 13. 測試

- tests/unit/test_external_analyzer_execution.py（94：preflight/fake loader/
  cache hit-miss/containment/audit v2/determinism/timestamp/run_id 契約/
  cache write failure/audit relationship）
- tests/integration/test_fake_analyzer_execution.py（8：端到端/sentinel/
  repository 零存取/network-SDK-env 零容忍/no-real-data safe stop/determinism）
- B0 全部測試無回歸

## 14. Phase 6.4C2-B1.2 強化

1. **Audit timestamp integrity**：completed_at 在 audit 寫入時產生
   （monkeypatch _now_utc 依序驗證；completed 必 >= started）
2. **run_id 統一契約**：execution 不產生第二個 UUID——plan.run_id ==
   summary.run_id == audit.run_id == audit directory name；preflight 驗證
   run_id/created_at/cache_namespace 契約
3. **弱測試修正**：successful_item 檢查實際 cache 目錄（exactly one JSON +
   validate == [] + status success + 無 reference/bytes/case ID）；
   舊 cross-process 改名 same-process；subprocess proof 輸出真正
   sha256(canonical_result_bytes)（64 位小寫 hex）
4. **image SHA change same-cache miss**：同 key/index/name/version/cache
   目錄真跑兩次 engine，SHA 變 → miss（hit=0、miss=1、attempted=1、write=1）
5. **cache write failure containment**：monkeypatch write → 單張 failed、
   多張 completed_with_failures（第一張失敗第二張成功）、audit 仍合法
6. **audit relationship 精確測試**：4 測試各製造單一錯誤
   （processed 不一致 / attempted > misses / succeeded+failed != processed /
   cache_write > succeeded）

## 15. Phase 6.4C2-B1.3 強化

1. **created_at 真正 datetime 驗證**（_is_valid_utc_timestamp）：
   strptime 完整解析，拒 2026-99-99T99:99:99Z / 2026-02-31T25:61:61Z /
   非字串 / timezone offset / fractional seconds；閏日正確（2024-02-29 ✅
   2026-02-29 ❌）
2. **invalid plan.run_id 安全 trace 契約**（_safe_trace_run_id）：
   - 合法 → trace_run_id = plan.run_id（精確保留）
   - 非法 → 安全 `run-<12 hex>`；status=blocked、含 execution_plan_invalid、
     summary/audit/directory 全用安全 ID、**不回顯原始值**、
     **不誤報 audit_write_failed**（除非真正 filesystem write 失敗）、
     path traversal（../../etc）無法逃逸 audit root
3. **started_at 在 preflight 之前產生**（audit 時間涵蓋 preflight；
   事件順序以 monkeypatch 驗證）
4. **duplicate test-name AST audit**：module-level `def test_*` 同名即失敗
   （掃 3 測試檔；positive/allow controls：test_same 抓到、test_a/b 通過）；
   已刪除重複的 test_audit_v2_count_relationship_rejected（舊版）

## 16. Phase 6.4C2-B1.4 強化

1. **non-string run-ID 安全防護**：`_is_valid_run_id`（isinstance str +
   re.fullmatch）先判定——None/int/object 不做 slicing、不轉路徑、
   安全 blocked（run-<12 hex> trace ID、不回顯原值、不誤報
   audit_write_failed、不 raise TypeError）
2. **cache namespace 驗證**：run_id 無效時 expected_namespace=None、
   不 slicing；namespace 需 str + fullmatch `namespace-[0-9a-f]{12}` +
   與 run_id 派生值一致（不用 str() 掩蓋型別錯誤）
3. **fixed_error_codes order-preserving unique**：`_append_error_once` +
   `_dedupe_errors`（dict.fromkeys，非 set）；summary/audit 無重複、
   原始順序保留、全部在 KNOWN_ERROR_CODES
4. **共用 run-ID helper**：`_is_valid_run_id` 由 _safe_trace_run_id 與
   preflight 共用（無 circular import；analyzer_audit 保持相同 regex 契約）

## 17. Baseline 不變

anonymized_real=0 → readiness 維持 SHADOW_READY；fake execution 不算 real
analyzer run；reasons 維持三碼；無 external_analyzer_ready/thresholds_met/
SAFE_PILOT_CANDIDATE。