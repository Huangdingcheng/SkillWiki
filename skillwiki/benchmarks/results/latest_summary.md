# SkillOS Demo Benchmark Summary

| task_id | no_skill | raw_prompt | with_skill | winner | failure reason |
| --- | --- | --- | --- | --- | --- |
| api_build_tool_call | failed | failed | success | with_skill | no_skill: Path not found: output.tool_call.name; Path not found: output.tool_call.arguments.name; raw_prompt: Path not found: output.tool_call.name; Path not found: output.tool_call.arguments.name |
| api_parse_openapi_endpoint | failed | failed | success | with_skill | no_skill: Path not found: output.method; Path not found: output.path; raw_prompt: Path not found: output.method; Path not found: output.path |
| api_validate_response_schema | failed | failed | success | with_skill | no_skill: Path not found: output.valid; Path not found: output.missing_fields; raw_prompt: Path not found: output.valid; Path not found: output.missing_fields |
| doc_extract_steps | failed | failed | success | with_skill | no_skill: Path not found: output.steps; raw_prompt: Path not found: output.steps |
| script_extract_function_skill | failed | failed | success | with_skill | no_skill: Path not found: output.skill_name; Path not found: output.inputs; raw_prompt: Path not found: output.skill_name; Path not found: output.inputs |
| skill_detect_breaking_schema_change | failed | failed | success | with_skill | no_skill: Path not found: output.breaking_change; Path not found: output.changed_fields; raw_prompt: Path not found: output.breaking_change; Path not found: output.changed_fields |
| skill_graph_trace_provenance | failed | failed | success | with_skill | no_skill: Path not found: output.path; Path not found: output.path; raw_prompt: Path not found: output.path; Path not found: output.path |
| skill_repair_failed_postcondition | failed | failed | success | with_skill | no_skill: Path not found: output.proposal.recommended_action; Path not found: output.proposal.patch_hint; raw_prompt: Path not found: output.proposal.recommended_action; Path not found: output.proposal.patch_hint |
| web_click_and_type | failed | failed | success | with_skill | no_skill: Expected output.success == True, got False.; Path not found: output.actions; raw_prompt: Expected output.success == True, got False.; Path not found: output.actions |
| web_extract_selector | failed | failed | success | with_skill | no_skill: Path not found: output.selector; raw_prompt: Path not found: output.selector |
| web_fill_login_form | failed | failed | success | with_skill | no_skill: Expected output.success == True, got False.; Path not found: output.final_state.submitted; Path not found: output.page; raw_prompt: Expected output.success == True, got False.; Path not found: output.final_state.submitted; Path not found: output.page |
| web_submit_form | failed | failed | success | with_skill | no_skill: Path not found: output.status; Expected output.success == True, got False.; raw_prompt: Path not found: output.status; Expected output.success == True, got False. |

## Mode Totals

- `no_skill`: 0/12 (0.00%)
- `raw_prompt`: 0/12 (0.00%)
- `with_skill`: 12/12 (100.00%)
