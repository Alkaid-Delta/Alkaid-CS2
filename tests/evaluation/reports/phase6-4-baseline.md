# Vision Evaluation Report

- case 數：34
- safe expected true：22 / raw safe true：6 / raw safe false：6 / raw safe None：22
- multi-image：9 / multi-item：6
- git commit：9b9ff7a6e491e55cd5339f726c0e31bdd3e5927a
- readiness：**SHADOW_READY**

## legacy

### Safe gate confusion matrix
- TP=22 FP=6 FN=0 TN=6
- **safe false positive cases：6**
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
- item false positives：28
- seller price exact：0.00%
- seller price miss：100.00%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 2 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：28
- currency accuracy：0.00%
- wear accuracy：0.00%
- linking accuracy：0.00%
- image type accuracy：N/A
- raw conflict detection：N/A
- fallback to text_v2：0.00%
- fallback to skipped：0.00%
- avg latency：0.3ms / P50：0.3ms / P95：0.3ms
- avg image count：0.0 / avg retry：0.0
- blocked rate：17.65%

## text_v2

### Safe gate confusion matrix
- TP=21 FP=0 FN=1 TN=12
- **safe false positive cases：0**
### Metrics
- item exact match rate：52.63%
- item match recall (exact+partial)：55.26%
- item strict recall：52.63%
- item false positives：0
- seller price exact：58.33%
- seller price miss：41.67%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 2 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：0
- currency accuracy：100.00%
- wear accuracy：95.24%
- linking accuracy：100.00%
- image type accuracy：N/A
- raw conflict detection：N/A
- fallback to text_v2：0.00%
- fallback to skipped：38.24%
- avg latency：1.2ms / P50：1.2ms / P95：1.4ms
- avg image count：1.18 / avg retry：0.0
- blocked rate：38.24%

## vision_raw

### Safe gate confusion matrix
- TP=5 FP=1 FN=1 TN=5
- **safe false positive cases：1**
  - currency_conflict_008（[]）
### Metrics
- item exact match rate：89.47%
- item match recall (exact+partial)：89.47%
- item strict recall：89.47%
- item false positives：9
- seller price exact：91.67%
- seller price miss：8.33%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 2 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：8
- currency accuracy：100.00%
- wear accuracy：100.00%
- linking accuracy：97.06%
- image type accuracy：100.00%
- raw conflict detection：100.00%
- fallback to text_v2：0.00%
- fallback to skipped：0.00%
- avg latency：1.6ms / P50：1.6ms / P95：2.0ms
- avg image count：1.18 / avg retry：0.0
- blocked rate：41.18%

## vision_production

### Safe gate confusion matrix
- TP=21 FP=0 FN=1 TN=12
- **safe false positive cases：0**
### Metrics
- item exact match rate：52.63%
- item match recall (exact+partial)：55.26%
- item strict recall：52.63%
- item false positives：0
- seller price exact：58.33%
- seller price miss：41.67%
- seller price wrong amount：0.00%
- seller price wrong currency：0.00%
- **seller price false positive：0.00%（0 / 2 negative item opportunities）**（negative_item=0）
- extra unmatched seller asks：0
- seller asks on wrong item：0
- currency accuracy：100.00%
- wear accuracy：95.24%
- linking accuracy：100.00%
- image type accuracy：N/A
- raw conflict detection：N/A
- fallback to text_v2：29.41%
- fallback to skipped：38.24%
- avg latency：2.6ms / P50：2.8ms / P95：3.4ms
- avg image count：1.18 / avg retry：0.0
- blocked rate：38.24%

## Top warning codes
- corroborated_by_image：22
- corroborated_price_by_image：14
- vision_blocked：10
- vision_fallback_to_text：10
- vision_merged：10
- image_only_item：9
- vision_image_error：7
- v2_blocked：3
- image_order_linking：2

## Crash
- cases_executed=34 crash_count=0 crash_rate=0.0

## Known limitations
- all_cases_synthetic
- vision_payloads_are_fixture_outputs
- offline_legacy_is_not_deepseek_legacy
- latency_is_local_runtime_metadata
- image_type_accuracy_is_fixture_biased

## Readiness recommendation
**SHADOW_READY**
