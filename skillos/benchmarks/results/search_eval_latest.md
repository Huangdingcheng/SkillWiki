# SkillOS Search Evaluation Baseline

- Retrieval mode: `lexical_vs_hybrid`
- Query count: 20
- Catalog size: 22
- Lexical Top-1 hit rate: 20/20 (100.00%)
- Lexical Top-3 hit rate: 20/20 (100.00%)
- Hybrid Top-1 hit rate: 20/20 (100.00%)
- Hybrid Top-3 hit rate: 20/20 (100.00%)

| Query ID | Query | Expected | Lexical top | Hybrid top | Lexical rank | Hybrid rank |
| --- | --- | --- | --- | --- | --- | --- |
| search_eval_001 | fill form | fill_form | fill_form | fill_form | 1 | 1 |
| search_eval_002 | click element selector | click_element | click_element | click_element | 1 | 1 |
| search_eval_003 | type text input | type_text | type_text | type_text | 1 | 1 |
| search_eval_004 | locate element | locate_element | locate_element | locate_element | 1 | 1 |
| search_eval_005 | submit form | submit_form | submit_form | submit_form | 1 | 1 |
| search_eval_006 | extract selector text | extract_selector | extract_selector | extract_selector | 1 | 1 |
| search_eval_007 | parse openapi endpoint | parse_openapi_endpoint | parse_openapi_endpoint | parse_openapi_endpoint | 1 | 1 |
| search_eval_008 | build tool call | build_tool_call | build_tool_call | build_tool_call | 1 | 1 |
| search_eval_009 | validate response schema | validate_response_schema | validate_response_schema | validate_response_schema | 1 | 1 |
| search_eval_010 | extract procedural steps | extract_steps | extract_steps | extract_steps | 1 | 1 |
| search_eval_011 | extract function skill | extract_function_skill | extract_function_skill | extract_function_skill | 1 | 1 |
| search_eval_012 | normalize email helper | normalize_email | normalize_email | normalize_email | 1 | 1 |
| search_eval_013 | reflect failure repair | reflect_failure | reflect_failure | reflect_failure | 1 | 1 |
| search_eval_014 | detect schema change | detect_schema_change | detect_schema_change | detect_schema_change | 1 | 1 |
| search_eval_015 | trace provenance graph | trace_provenance | trace_provenance | trace_provenance | 1 | 1 |
| search_eval_016 | generate skill from trajectory | generate_skill_from_trajectory | generate_skill_from_trajectory | generate_skill_from_trajectory | 1 | 1 |
| search_eval_017 | verify json output | verify_json_output | verify_json_output | verify_json_output | 1 | 1 |
| search_eval_018 | summarize benchmark results | summarize_benchmark_results | summarize_benchmark_results | summarize_benchmark_results | 1 | 1 |
| search_eval_019 | propose maintenance change | propose_maintenance_change | propose_maintenance_change | propose_maintenance_change | 1 | 1 |
| search_eval_020 | compare skill snapshots | compare_skill_snapshots | compare_skill_snapshots | compare_skill_snapshots | 1 | 1 |
