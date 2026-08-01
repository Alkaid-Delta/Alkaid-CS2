# Vision Evaluation Report

- case 數：50
- safe expected true：32 / raw safe true：15 / raw safe false：13 / raw safe None：22
- multi-image：14 / multi-item：9
- git commit：5dcac837e6d9ae814ec1924ceafbe8b5b0159cf1
- readiness：**SHADOW_READY**

- readiness reasons：insufficient_eligible_cases, insufficient_real_case_count, insufficient_double_reviewed_real

## Dataset Quality
- total_loaded_cases：50
- evaluated_cases：48
- readiness_eligible_cases：48
- excluded_from_evaluation：2
- privacy errors：0 / warnings：0
- external analyzer cases：0 / cached analyzer cases：14

## Source distribution
- synthetic：34 / anonymized_real：0 / manual_fixture：10 / adversarial：6

## Review distribution
- double_review：8 / single_review：1 / disputed：1
- real data validation status：insufficient

## Fixture vs Analyzer
- cache lookup：59 / hit：18 / miss：41
- cached cases：14 / cached images：18
- external cases：0 / external images：0
- analyzer coverage：30.51%（59 eligible images）
- evaluated analyzer eligible images：59 / real analyzer eligible images：0
- real cache/analyzer coverage：0.00%（cached 0 / external 0）
> ⚠️ 注意：fixture-mirrored cache accuracy **不代表真實模型準確率**（cache 內容 = 人工 fixture payload 鏡像）
- images compared：18
- image kind accuracy：100.00% / item count：100.00% / item exact：100.00% / price exact：100.00% / currency：100.00%
- skipped（no fixture payload）：0 / skipped（no analyzer payload）：41
- disagreement cases：無

## legacy

### Safe gate confusion matrix
- TP=30 FP=8 FN=1 TN=9
- **safe false positive cases：8**
  - adv_image_order_swap_003（[]）
  - adv_same_item_multi_price_001（[]）
  - all_vision_failed_text_unsafe_028（[]）
  - multi_item_multi_price_ambiguous_017（[]）
  - single_rmb_not_safe_002（[]）
  - text_v2_ambiguous_not_safe_027（[]）
  - two_images_two_items_014（[]）
  - two_market_two_items_034（[]）
### Metrics
- item exact match rate：0.00%
- item match recall (exact+partial)：0.00%
- item strict recall：0.00%
- item false positives：38
- seller price exact：0.00%
- seller price miss：100.00%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 3 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：38
- currency accuracy：0.00%
- wear accuracy：0.00%
- linking accuracy：0.00%
- image type accuracy：N/A
- raw conflict detection：N/A
- fallback to text_v2：0.00%
- fallback to skipped：0.00%
- avg latency：0.3ms / P50：0.3ms / P95：0.3ms
- avg image count：0.0 / avg retry：0.0
- blocked rate：20.83%

## text_v2

### Safe gate confusion matrix
- TP=27 FP=0 FN=4 TN=17
- **safe false positive cases：0**
### Metrics
- item exact match rate：46.15%
- item match recall (exact+partial)：48.08%
- item strict recall：46.15%
- item false positives：2
- seller price exact：51.02%
- seller price miss：48.98%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 3 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：2
- currency accuracy：100.00%
- wear accuracy：95.65%
- linking accuracy：100.00%
- image type accuracy：N/A
- raw conflict detection：N/A
- fallback to text_v2：0.00%
- fallback to skipped：43.75%
- avg latency：1.5ms / P50：1.4ms / P95：2.1ms
- avg image count：1.23 / avg retry：0.0
- blocked rate：43.75%

## vision_raw

### Safe gate confusion matrix
- TP=10 FP=2 FN=4 TN=10
- **safe false positive cases：2**
  - adv_same_skin_two_wear_002（[]）
  - currency_conflict_008（[]）
### Metrics
- item exact match rate：82.69%
- item match recall (exact+partial)：82.69%
- item strict recall：82.69%
- item false positives：27
- seller price exact：81.63%
- seller price miss：18.37%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 3 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：12
- currency accuracy：100.00%
- wear accuracy：95.00%
- linking accuracy：95.24%
- image type accuracy：100.00%
- raw conflict detection：75.00%
- fallback to text_v2：0.00%
- fallback to skipped：0.00%
- avg latency：1.8ms / P50：1.8ms / P95：2.5ms
- avg image count：1.23 / avg retry：0.0
- blocked rate：45.83%

## vision_production

### Safe gate confusion matrix
- TP=27 FP=0 FN=4 TN=17
- **safe false positive cases：0**
### Metrics
- item exact match rate：46.15%
- item match recall (exact+partial)：48.08%
- item strict recall：46.15%
- item false positives：2
- seller price exact：51.02%
- seller price miss：48.98%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 3 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：2
- currency accuracy：100.00%
- wear accuracy：95.65%
- linking accuracy：100.00%
- image type accuracy：N/A
- raw conflict detection：N/A
- fallback to text_v2：33.33%
- fallback to skipped：43.75%
- avg latency：3.3ms / P50：3.4ms / P95：4.7ms
- avg image count：1.23 / avg retry：0.0
- blocked rate：43.75%

## Top warning codes
- corroborated_by_image：26
- image_only_item：22
- vision_blocked：18
- vision_fallback_to_text：16
- corroborated_price_by_image：15
- image_unknown_price：12
- vision_merged：10
- vision_image_error：8
- image_order_linking：3
- v2_blocked：3

## Crash
- cases_executed=50 crash_count=0 crash_rate=0.0

## Known limitations
- no_anonymized_real_cases
- all_cases_are_synthetic_or_manual
- external_analyzer_not_yet_executed
- analyzer_cache_is_fixture_mirrored
- image_hash_uses_url_placeholder
- price_comparison_first_price_only
- vision_payloads_are_fixture_outputs
- offline_legacy_is_not_deepseek_legacy
- latency_is_local_runtime_metadata
- image_type_accuracy_is_fixture_biased

## Readiness recommendation
**SHADOW_READY**
