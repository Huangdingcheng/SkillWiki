# Canonical Skill Schema v0.2

This document defines the canonical Skill asset shape for the SkillOS demo-paper sprint. It is a documentation contract only: the current runtime model remains `skillos/skillos/models/skill_model.py`.

## Purpose

Skill schema v0.2 treats a Skill as a reusable, evaluable, versioned asset rather than only a text description. The schema supports three layers:

- `atomic`: one irreducible action, such as click, type, or call one tool.
- `functional`: a reusable task-level capability composed from atomic actions.
- `strategic`: a higher-level orchestration, generation, maintenance, or governance skill.

The three layers align the current SkillOS model with the demo-paper direction inspired by SkillX, XSkill, SKILLFOUNDRY, and SkillsBench: skills should be layered, separated from raw experience, traceable to sources, and testable.

## P0 Required Fields

Every canonical Skill v0.2 asset must provide these fields for the P0 demo:

| Field | Requirement | Current model mapping |
| --- | --- | --- |
| `skill_id` | Stable globally unique identifier. | `Skill.skill_id` |
| `name` | Required snake_case canonical name. | `Skill.name`; validated by `validate_name()` |
| `version` | Semantic version string, for example `1.0.0`. | `Skill.version` |
| `display_name` | Human-readable name; may be derived from `name`. | `Skill.display_name`; auto-filled when empty |
| `description` | Short capability statement and intended use. | `Skill.description` |
| `skill_type` | One of `atomic`, `functional`, `strategic`. | `Skill.skill_type` / `SkillType` |
| `state` | Lifecycle state from raw experience through archive. | `Skill.state` / `SkillState` |
| `domain` | Primary operating domain, for example `web`, `api`, `repo`, `general`. | `Skill.domain` |
| `granularity_level` | Integer 1-5; use 1 for atomic, 2-3 for functional, 4-5 for strategic. | `Skill.granularity_level` |
| `tags` | Retrieval and grouping labels. | `Skill.tags`; normalized to lowercase |
| `interface.input_schema` | JSON Schema-like input contract. | `Skill.interface.input_schema` |
| `interface.output_schema` | JSON Schema-like output contract. | `Skill.interface.output_schema` |
| `implementation` | At least one executable representation: code, prompt template, or sub-skill list. | `Skill.implementation`; `SkillImplementation` requires `code`, `prompt_template`, or `sub_skill_ids` |
| `evaluation.verifier_specs` | Deterministic verifier definitions for repeatable validation. | `Skill.evaluation.verifier_specs` |
| `evaluation.test_case_refs` | External test fixture IDs or named repo test cases. | `Skill.evaluation.test_case_refs`; accepts the backlog alias `test_cases_refs` on input |
| `evaluation.benchmark_task_ids` | SkillsBench-style task IDs that exercise the Skill. | `Skill.evaluation.benchmark_task_ids` |
| `provenance.source_type` | Source category: `trajectory`, `doc`, `manual`, `merge`, `split`, or `adapt`. | `Skill.provenance.source_type` |
| `provenance.source_ids` | IDs of source trajectories, documents, or records. | `Skill.provenance.source_ids`; also mirrored by `trajectory_refs` / `doc_refs` when useful |
| `test_cases` or `test_trajectory_ids` | At least one validation artifact reference for candidate or later Skills. | `Skill.test_cases`, `Skill.test_trajectory_ids` |

For `strategic` Skills, `meta_category` is also required by the current model. Valid categories include `generation`, `maintenance`, `quality_assurance`, `knowledge_management`, `graph`, `lifecycle`, and `optimization`.

## P1 Optional Fields

P1 fields enrich retrieval, governance, evaluation, and graph explanation. They should be preserved when present but are not required for the first P0 schema documentation slice.

| Field | Use | Current model mapping |
| --- | --- | --- |
| `interface.preconditions` | Conditions that must hold before execution. | `Skill.interface.preconditions` |
| `interface.postconditions` | Conditions expected after successful execution. | `Skill.interface.postconditions` |
| `interface.side_effects` | Files, browser state, network calls, or external resources affected. | `Skill.interface.side_effects` |
| `implementation.tool_calls` | External tools or APIs used by the Skill. | `SkillImplementation.tool_calls`, `Skill.tool_refs` |
| `implementation.sub_skill_ids` | Ordered component Skills for functional or strategic composition. | `SkillImplementation.sub_skill_ids`, `Skill.component_ids` |
| `implementation.execution_order` | Explicit order or grouping for sub-skills. | `SkillImplementation.execution_order` |
| `provenance.parent_skill_ids` | Parent Skills used for evolution, merge, split, or adaptation. | `SkillProvenance.parent_skill_ids` |
| `provenance.created_by_agent` | Agent or subsystem that created the Skill. | `SkillProvenance.created_by_agent` |
| `provenance.creation_context` | Free-form source metadata, prompt notes, run IDs, or extraction evidence. | `SkillProvenance.creation_context` |
| `dependency_ids` | Skills that must be available before this Skill runs. | `Skill.dependency_ids`; graph edge `depends_on` |
| `component_ids` | Convenience projection of composed sub-skills. | `Skill.component_ids`; graph edge `composes_with` |
| `metrics` | Usage, latency, and success/failure counters. | `Skill.metrics` / `SkillMetrics` |
| `deprecation_reason` | Why a Skill should not be used. | `Skill.deprecation_reason` |
| `replacement_skill_id` | Preferred successor Skill. | `Skill.replacement_skill_id` |

Evaluation is now represented directly by `SkillEvaluation`. Keep `test_cases` for inline examples and `test_trajectory_ids` for replay references, but put repeatable verifier specs and benchmark membership in `Skill.evaluation` so runtime, benchmark, governance, and frontend code can read one stable contract.

## Demo Seed Examples

### Atomic: `click_element`

```json
{
  "name": "click_element",
  "version": "1.0.0",
  "display_name": "Click Element",
  "description": "Click one target element in a browser or UI automation context.",
  "skill_type": "atomic",
  "state": "S3",
  "domain": "web",
  "granularity_level": 1,
  "tags": ["web", "ui", "click", "atomic"],
  "interface": {
    "input_schema": {
      "type": "object",
      "required": ["selector"],
      "properties": {
        "selector": {"type": "string"},
        "timeout_ms": {"type": "integer", "default": 5000}
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "clicked": {"type": "boolean"},
        "target": {"type": "string"}
      }
    },
    "preconditions": ["A browser page is open."],
    "postconditions": ["The target element receives a click event."]
  },
  "implementation": {
    "language": "python",
    "tool_calls": ["browser.click"],
    "prompt_template": "Click the element identified by {selector}."
  },
  "test_cases": [
    {
      "name": "click_login_button",
      "description": "Clicks a visible login button.",
      "input_data": {"selector": "#login"},
      "expected_output": {"clicked": true}
    }
  ],
  "evaluation": {
    "verifier_specs": [
      {"type": "json_equals", "path": "output.clicked", "value": true}
    ],
    "test_case_refs": ["click_login_button"],
    "benchmark_task_ids": ["web_click_and_type"]
  },
  "provenance": {
    "source_type": "manual",
    "source_ids": ["demo_seed_atomic_click"]
  }
}
```

### Functional: `fill_form`

```json
{
  "name": "fill_form",
  "version": "1.0.0",
  "display_name": "Fill Form",
  "description": "Fill a web form using field selectors and values, then optionally submit it.",
  "skill_type": "functional",
  "state": "S3",
  "domain": "web",
  "granularity_level": 3,
  "tags": ["web", "form", "automation"],
  "interface": {
    "input_schema": {
      "type": "object",
      "required": ["fields"],
      "properties": {
        "fields": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["selector", "value"],
            "properties": {
              "selector": {"type": "string"},
              "value": {"type": "string"}
            }
          }
        },
        "submit_selector": {"type": "string"}
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "filled_count": {"type": "integer"},
        "submitted": {"type": "boolean"}
      }
    },
    "preconditions": ["A page containing the form is open."],
    "side_effects": ["Changes browser form state."]
  },
  "implementation": {
    "language": "workflow",
    "sub_skill_ids": ["type_text", "click_element"],
    "execution_order": ["type_text", "click_element"]
  },
  "component_ids": ["type_text", "click_element"],
  "test_trajectory_ids": ["trajectory_demo_login_form"],
  "evaluation": {
    "verifier_specs": [
      {"type": "json_equals", "path": "final_state.submitted", "value": true}
    ],
    "test_case_refs": ["trajectory_demo_login_form"],
    "benchmark_task_ids": ["web_fill_login_form"]
  },
  "provenance": {
    "source_type": "trajectory",
    "source_ids": ["trajectory_demo_login_form"],
    "creation_context": {"paper_source": "Trace2Skill/XSkill demo seed"}
  }
}
```

### Strategic: `generate_skill_from_trajectory`

```json
{
  "name": "generate_skill_from_trajectory",
  "version": "1.0.0",
  "display_name": "Generate Skill From Trajectory",
  "description": "Convert a raw execution trajectory into a candidate Skill with interface, implementation sketch, provenance, and validation references.",
  "skill_type": "strategic",
  "meta_category": "generation",
  "state": "S2",
  "domain": "general",
  "granularity_level": 5,
  "tags": ["skill-generation", "trajectory", "repository"],
  "interface": {
    "input_schema": {
      "type": "object",
      "required": ["trajectory_id"],
      "properties": {
        "trajectory_id": {"type": "string"},
        "target_layer": {"type": "string", "enum": ["atomic", "functional", "strategic"]}
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "candidate_skill_id": {"type": "string"},
        "confidence": {"type": "number"}
      }
    },
    "postconditions": ["A candidate Skill is created in state S1 or draft state S2."]
  },
  "implementation": {
    "language": "prompt",
    "prompt_template": "Extract a reusable Skill from trajectory {trajectory_id}; include interface, provenance, and tests."
  },
  "test_cases": [
    {
      "name": "extract_fill_form_candidate",
      "description": "Builds a fill_form candidate from a login form trajectory.",
      "input_data": {"trajectory_id": "trajectory_demo_login_form"},
      "expected_output": {"confidence": 0.7}
    }
  ],
  "evaluation": {
    "verifier_specs": [
      {"type": "json_exists", "path": "output.candidate_skill_id"}
    ],
    "test_case_refs": ["extract_fill_form_candidate"],
    "benchmark_task_ids": ["trajectory_to_skill_001"]
  },
  "provenance": {
    "source_type": "manual",
    "source_ids": ["demo_seed_strategic_generation"],
    "created_by_agent": "skill_librarian",
    "creation_context": {"paper_source": "Trace2Skill/SKILLFOUNDRY demo seed"}
  }
}
```

## Mapping to `models/skill_model.py`

The current Pydantic model already covers the v0.2 documentation contract:

- Identity and retrieval metadata live directly on `Skill`: `skill_id`, `name`, `version`, `display_name`, `description`, `tags`, `domain`.
- Layering uses `SkillType`; lifecycle uses `SkillState`.
- Strategic subtypes use `MetaSkillCategory`, and the model enforces that only strategic Skills can set `meta_category`.
- I/O contracts use `SkillInterface`.
- Executable content and composition use `SkillImplementation`.
- Validation artifacts use `SkillEvaluation`, `SkillTestCase`, `test_cases`, and `test_trajectory_ids`.
- Runtime evidence uses `SkillMetrics`.
- Source tracing uses `SkillProvenance`, plus `trajectory_refs` and `doc_refs`.
- Skill-only graph projection uses `dependency_ids`, `component_ids`, `implementation.sub_skill_ids`, and `provenance.parent_skill_ids`.
- Version and lifecycle behavior are represented by `version`, `transition_to()`, `bump_version()`, `released_at`, `deprecated_at`, `deprecation_reason`, and `replacement_skill_id`.

The additive code change for A-P0-1 is `SkillEvaluation`: older Skill JSON still loads because the field defaults to an empty contract, while new API callers can send verifier specs and benchmark task IDs explicitly.
