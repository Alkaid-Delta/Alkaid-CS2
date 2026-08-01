# Phase 6.4C2-B0 — Secure External Analyzer Execution Harness and No-Data Safety Gate

> **目前沒有 anonymized_real（0 cases）→ 所有 analyzer execution 都安全停止。**
> 本階段只建立安全執行框架，未實際呼叫任何外部 Analyzer。

## 1. 目的

建立 deterministic、可測試、**預設關閉**的 external analyzer runner，在真實資料
進入之前先把安全界線與錯誤語意定死。

## 2. 預設狀態

```
external_analyzer_enabled = False
```

進入 analyzer execution path 必須**同時**具備：

1. CLI flag：`--allow-external-analyzer`
2. Environment：`EVALUATION_ALLOW_EXTERNAL_ANALYZER=1`

任一缺少 → 不載入圖片 bytes、不建立 network client、不呼叫 adapter、不寫 cache，
以固定錯誤碼安全停止。

## 3. 授權 Gate

`can_run_external_analyzer(...)` 六條件全數成立才允許：

- cli_allowed
- env_allowed
- anonymized_real_case_count > 0
- eligible_real_image_count > 0
- secure loader available
- adapter available

目前 baseline（0 cases / 0 images）→ 一定回傳 False，含 `no_eligible_real_cases`。
**不得因 framework 完成而提升 readiness。**

## 4. 固定錯誤碼

```
external_analyzer_flag_missing / external_analyzer_env_missing /
external_analyzer_not_authorized / no_eligible_real_cases /
no_eligible_real_images / secure_image_loader_unavailable /
analyzer_adapter_unavailable / secure_reference_invalid /
secure_image_not_found / secure_image_hash_mismatch /
analyzer_execution_failed / analyzer_result_invalid /
cache_write_failed / audit_write_failed
```

不回顯：storage reference 原值、本機路徑、case ID、圖片 bytes、base64、token、
API key、endpoint URL、request payload、analyzer 原始 exception、private metadata。

## 5. SecureImageLoader

`SecureImageLoader.load(storage_reference, expected_sha256) -> bytes`

- 只接受通過 `validate_secure_store_reference()` 的 reference
- 不支援 http/https、任意 local path、repository fixture path、data URL/base64
- 載入後驗證 SHA-256（mismatch → `secure_image_hash_mismatch`）
- bytes 只存在記憶體：**不寫 temp / repository / log / report / JSON**
- 本階段只有 `InMemorySecureImageLoader` / `FakeSecureImageLoader`（測試用）
- 無真實雲端 connector

## 6. ExternalAnalyzerAdapter

`ExternalAnalyzerAdapter.analyze_image(image_bytes, *, case_key, image_index) -> dict`

- 本階段只有 `FakeExternalAnalyzerAdapter`（deterministic）與
  `FailingExternalAnalyzerAdapter`（固定失敗）
- 不得 import production analyzer client / OpenAI / Anthropic / DeepSeek SDK /
  requests / urllib / socket；不得讀 API key；不得連任何 endpoint

## 7. Execution Plan

`ExternalAnalyzerExecutionPlan`：schema_version / run_id / created_at / case_count /
image_count / case_keys / image_indexes / expected_hashes / adapter_name /
cache_namespace / dry_run / authorized / status

- **case_keys = compute_opaque_case_key(case_id, run_salt)**（不可逆 opaque key）；
  不保存原 case ID 或 run salt
- run_salt 每次 run 產生、不寫 Git、不顯示 CLI、不用 production secret
- 不保存原 case ID / storage reference / 圖片路徑 / bytes / 原始文字

## 8. Dry-Run（scripts/run_real_analyzer_evaluation.py）

`--dry-run`（本階段唯一模式）：

- 驗證 manifest、驗證 authorization gate、計算 eligible cases/images、
  建立 execution plan
- 不載入 bytes、不建 adapter、不呼叫 network、不寫 cache、不寫 repository
- audit plan 寫到 Git 外 `local_data/evaluation_analyzer_runs/<run_id>/audit.json`
- **anonymized_real=0 → 安全停止**：`[real-analyzer] no_eligible_real_cases`（exit 2）
- exit code：0 = 合法 dry-run 且有案例；2 = gate/no-data failure；
  1 = 非預期 workflow failure
- **不得因「沒有資料」假裝 dry-run 成功**

## 9. Analyzer Cache（Git 外）

`local_data/evaluation_analyzer_cache/`（.gitignore 已加 `local_data/`）

record 只允許：

```
schema_version / opaque_case_key / image_index / image_sha256 /
analyzer_name / analyzer_version / analyzed_at / normalized_result /
result_sha256 / status
```

不得保存：image bytes / base64 / storage reference / case ID / raw response 全文 /
headers / API token / endpoint / private metadata。`normalized_result` 必須通過
schema 驗證（kind / item_count / items[].name 等）。

## 10. Audit Manifest（Git 外）

`local_data/evaluation_analyzer_runs/<run_id>/audit.json`：

```
schema_version / run_id / started_at / completed_at / dry_run /
authorization_flag_present / authorization_env_present /
eligible_case_count / eligible_image_count / attempted_image_count /
succeeded_image_count / failed_image_count / cache_write_count /
result / fixed_error_codes / image_hash_hashes
```

- 不含 storage reference / case ID / path / bytes / 原始 exception
- image hash 只存**再次雜湊**（`hash_image_hashes`）
- 本階段 blocked run：`result="blocked"`；**fixed_error_codes 是 gate reasons
  集合，可能同時包含**：external_analyzer_flag_missing /
  external_analyzer_env_missing / no_eligible_real_cases /
  no_eligible_real_images / secure_image_loader_unavailable /
  analyzer_adapter_unavailable / external_analyzer_not_authorized
  （不保證只有一項；實際組合由 gate 決定）

## 11. 禁止範圍

禁止修改 production parser / crawler / bridge / analyzer adapter / API client /
env / feature flags / deployment；禁止引入 requests / urllib / socket / httpx /
aiohttp / OpenAI / Anthropic / DeepSeek SDK。本階段只使用 Protocol、fake adapter、
in-memory loader。

## 12. Phase 6.4C2-B0.1 強化

1. **eligible cases 由 validated manifest 產生**（`load_eligible_cases_from_manifest`）：
   manifest 缺失/壞 JSON/schema 失敗 → 固定碼；只收 anonymized_real +
   privacy passed + double_review
2. **空 manifest 合法但觸發 no_eligible_real_cases**（不假裝 dry-run 成功）
3. **disputed / single review 不可進 readiness execution**
4. **dry-run 不呼叫 loader factory / load / adapter factory / analyze**
   （counter/spy 測試驗證，即使注入合法 eligible case 也為 0）
5. **normalized_result 使用 strict allowlist**（top-level 4 欄 + item 6 欄；
   未知欄位/型別/enum/遞迴 privacy 全驗證）
6. **cache/audit 使用 atomic single-file write**
   （canonical bytes → 唯一 temp → write+flush+fsync → os.replace；失敗清 temp）
7. **output 僅限 local_data root**（`resolve_local_data_subdir`；
   測試 root 只能透過 `main(argv, *, local_data_root_override=...)`
   **程式內注入**——CLI 已移除 `--local-data-root`，傳入會被拒絕）
8. **blocked audit 的 fixed_error_codes 是 gate reasons 集合，不保證只有一項**
    （可能包含 no_eligible_real_cases / no_eligible_real_images /
    external_analyzer_not_authorized / flag / env missing 的任意組合）
9. **測試全部使用隔離 tmp local_data**（不掃描 repository local_data；
   精確讀取本次 run 的 audit）
10. **本階段未載入真實 bytes、未呼叫外部 Analyzer**

### Hash 豁免範圍（intake_validation.py 配套）

- **只有受控 SHA-256 欄位中的合法 64 位小寫 hex，才豁免 generic
  base64_like heuristic**（SHA256_EXEMPT_FIELDS + 值/元素格式驗證）；
  自由文字與任意欄位中的 64 hex 仍照常掃描
- 適用於受控 hash 欄位值：image_sha256 / result_sha256 / image_hash_hashes /
  expected_hashes / reviewer_inputs_hash / final_ground_truth_hash /
  fixture_sha256 / original_image_hashes / redacted_image_hashes
- 任意欄位（notes/metadata/payload）的 base64-like 字串**照常掃描**
- auth_keyword 豁免仍只限 STORAGE_REFERENCE_FIELDS + 合法值

## 13. Phase 6.4C2-B0.2 強化

1. **production local_data root 固定為 PROJECT_ROOT/local_data**
   （不可由 argv / environment / manifest 改寫）
2. **測試 root 只能透過 `main(argv, *, local_data_root_override=...)`
   程式內注入**（不得使用任何公開 CLI 參數改寫 root）
3. **CLI 已移除 `--local-data-root`**（傳入即 argparse 拒絕 exit 2）
4. **fixture raw bytes SHA-256 與 manifest fixture_sha256 綁定**
   （讀 raw bytes 計算實際 hash 比對，不得用固定值）
5. **fixture case_id / source / privacy / image hashes 全部交叉檢查**
   （case_id 一致、source=anonymized_real、privacy scan 0 error、
   image hashes 全 64 位小寫 hex、hash 數 == ref 數 == image_reference_count）
6. **任一 integrity failure 回傳空 eligible cases**（不產生部分案例、
   不建 execution plan、不載入圖片 bytes、不回顯 case ID/path/hash/reference）
7. **SHA-256 base64 豁免只限受控欄位**（SHA256_EXEMPT_FIELDS；
   自由文字即使內容是純 64 位小寫 hex 也不豁免）
8. **warnings 為受控 list[str]**（非空、≤200 字元、≤50 個、照常 privacy scan）
9. **integration tests 不讀 repository local_data 或 historical audit**
   （全部使用隔離 tmp root；repository local_data 執行前後 snapshot 一致）
10. **sentinel 使用非空 eligible case 驗證去識別化**
    （case_id=SECRET_CASE_12345 不存在於 audit/plan/paths/cache，只存 opaque key）
11. **本階段未執行外部 Analyzer、未載入真實圖片 bytes**

## 14. Phase 6.4C2-B0.3 強化

1. **刪除 vacuous sentinel 測試**（空 manifest + 已移除 CLI 參數 + for loop 空跑）
2. **sentinel 測試使用非空、schema-valid eligible fixture**
   （case_id=SECRET_CASE_12345；fixture raw hash 綁定；capsys 實際捕捉
   stdout/stderr；sentinel 不存在於 out/err/audit/run-dir/plan）
3. **fixture missing 與 case-ID mismatch 是兩個獨立測試**
   （missing → manifest_case_fixture_missing；
   檔名 real_001.json + 內容 case_id=other_case →
   manifest_fixture_case_id_mismatch）
4. **repository local_data runtime 不讀不寫，而非只做 source substring 檢查**：
   - test_isolated_run_never_reads_repository_historical_audit：
     monkeypatch 攔截 repository runs 路徑的 open/stat → 執行隔離 dry-run →
     **呼叫次數為 0**、tmp audit 正常建立
   - test_repository_local_data_untouched_by_tests：執行完整隔離流程，
     snapshot（bytes 層級）前後完全一致 + 6 項流程斷言
5. **env flag 真實套用並恢復**（_run_cli_isolated 以 try/finally 設定/恢復
   os.environ；audit 記錄 authorization_env_present=true；執行後 env 恢復）
6. **實際 cache path 驗證**：tmp_path/local_data/cache（錯誤的 tmp_path/cache
   測試已移除）

## 15. Phase 6.4C2-B0.4 強化（已驗證結果）

1. **sentinel stdout/stderr 以 capsys 實際捕捉**：
   `captured.out.strip() == "[real-analyzer] dry_run_valid"`、`captured.err == ""`、
   SECRET_CASE_ID 與 secure-store://img-1 不在 out/err
2. **完整 filesystem isolation spy**（test_isolated_run_never_accesses_
   repository_local_data）：攔截 builtins.open / io.open / os.stat /
   Path.open / read_text / read_bytes / write_text / write_bytes / iterdir /
   glob / rglob；PathLike 經 os.fspath() 處理；counter：read/write/stat/list
   全部為 0；任何 repository local_data 存取即 AssertionError
3. **runtime proof 與 bytes snapshot 互補**：spy 證明執行期間零存取、
   snapshot 證明內容未變（兩者都保留）
4. **env 前後恢復已驗證**：preexisting value 恢復、absent 保持 absent
   （保存舊值 + finally 恢復，不用無條件 pop）
5. **弱式 assertion 已移除**：blocked 輸出必須全部是已知固定錯誤碼集合成員，
   stderr 必須為空

## 16. Phase 6.4C2-B0.5 強化（已驗證結果）

1. **env 責任分工（Phase 6.4C2-B0.8 最終）**：
   - **被測 production-like flow**：使用 `_temporary_env` 保存並恢復 env
   - **pytest 測試前置隔離**：使用 `monkeypatch.setenv` /
     `monkeypatch.delenv`（pytest 自動恢復測試前 outer state）
   - **AST audit**：禁止測試函式直接修改 os.environ
   - parameterized runtime flows（present/absent × 3 流程）驗證
     outer env 完整保留/保持 absent
2. **filesystem spy 補齊低階 OS APIs**：os.open / os.listdir / os.scandir /
   os.mkdir / os.makedirs / os.remove / os.unlink / os.rename / os.replace /
   Path.mkdir / Path.unlink / Path.rename / Path.replace
   （9 counters：open/read/write/stat/list/mkdir/remove/rename/replace；
   PathLike 經 os.fspath()；不用 resolve 避免 stat 遞迴；wrapper 保存 real）
   **rename/replace 同時驗證 source 與 destination**：任一端位於
   repository local_data 都會拒絕（destination-only 陽性控制驗證）
3. **spy 陽性控制**：os_open/listdir/scandir/replace/unlink 對 repository
   local_data 虛擬目標操作 → AssertionError + counter==1；
   tmp_path 操作完全放行
4. **完整 runtime isolation**：隔離 dry-run 後 9 counters 全 0、
   exit==2、tmp audit 精確 1 個、blocked、no_eligible_real_cases、
   tmp cache 空、repository snapshot 一致

## 17. Phase 6.4C2-B0.6 強化（已驗證結果）

1. **rename/replace 雙路徑 guard**（_guard_two_paths + Path 專用 wrapper）：
   source 或 destination 任一端位於 repository local_data 都拒絕
2. **destination-only 陽性控制**（6 tests）：os.rename / os.replace /
   Path.rename / Path.replace 的 external-source → repo-destination、
   repo-source → external-destination 全攔截（AssertionError + counter==1）；
   tmp→tmp rename/replace 正常允許
3. **env 專測恢復 outer process 狀態**：parameterized runtime flows
   （repository_snapshot / sentinel_cli / isolated_helper × present/absent）
   驗證 outer env 完整保留或保持 absent
4. **source-string audit 移除**（exempt_fns 豁免會漏真實錯誤），
   改以 runtime parameterized 驗證

## 18. Phase 6.4C2-B0.7 強化（已驗證結果）

1. **測試前置狀態一律使用 pytest monkeypatch**：
   - present 情境：`monkeypatch.setenv(ENV_FLAG, "value")`
   - absent 情境：`monkeypatch.delenv(ENV_FLAG, raising=False)`
   - pytest 在測試結束後自動恢復**真正測試開始前的 outer process 狀態**
2. **被測 flow 使用 `_temporary_env` helper**（production-like 責任），
   測試 fixture isolation 使用 pytest monkeypatch——兩者清楚區分：
   1. 被測 flow：`_temporary_env`（保存舊值 + finally 恢復）
   2. 測試 isolation：pytest monkeypatch（自動恢復 outer state）
3. **AST audit**（test_no_test_function_directly_mutates_env）：
   `ast` 掃描測試函式，實際禁止：
   - subscript assign/delete：`os.environ[...] = ...` / `del os.environ[...]`
   - method calls：`pop` / `update` / `clear` / `setdefault` /
     `__setitem__` / `__delitem__`
   - `os.putenv` / `os.unsetenv`
   只允許 `_temporary_env` helper 本體直接操作 + monkeypatch.setenv/delenv
   （不使用函式名稱 exempt allowlist）；
   **audit 有 11 個 synthetic positive/allow controls**（不是只掃描
   目前檔案得到空結果）
4. **subprocess isolation proof**（flaky 收斂後保留）：
   - env 從 parent copy + 明確保留 PARENT_SENTINEL_VALUE
   - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、完整 node IDs、獨立 temp
     cache dir、timeout 受控、failure 只回顯 stdout/stderr 尾端
   - 連續執行 3 次 returncode 0、parent sentinel 不變
   - **只證明 process isolation，不作為同-process restoration 的
     主要證據**；同-process 主要證據是 monkeypatch present/absent
     tests + parameterized runtime flows

## 19. 測試

- tests/unit/test_external_analyzer_gate.py：**37**
- tests/unit/test_secure_image_loader.py：**7**
- tests/unit/test_analyzer_cache.py：**58**
- tests/integration/test_real_analyzer_runner.py：**68**
- **B0 合計：170**（B0.8 最終 pytest 輸出為準）

## 20. Baseline 維持

anonymized_real=0 → readiness 維持 SHADOW_READY、reasons 不變
（insufficient_eligible_cases / no_real_cases_ingested / real_analyzer_not_run）。
不得新增 external_analyzer_ready / real_dataset_validated / thresholds_met /
SAFE_PILOT_CANDIDATE。
