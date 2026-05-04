"""SkillOS FastAPI application entry point."""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..utils.llm_client import LLMClient
from .deps import app_state
from .memory_store import MemoryGraphManager, MemoryWikiManager
from .routes import evolution, execution, graph, ingest, lifecycle, repository, skills, ws

logger = logging.getLogger(__name__)


def _default_skill_storage_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "storage" / "skill_repo" / "SkillStorage"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    llm_cfg = app.state.llm_cfg
    llm = LLMClient(llm_cfg)
    if app.state.repository_backend == "memory":
        wiki = MemoryWikiManager()
    else:
        from ..layers.skill_repository import SkillWikiManager

        wiki = SkillWikiManager(storage_dir=app.state.skill_storage_dir)
    graph_mgr = MemoryGraphManager()

    app_state.initialize(llm=llm, wiki=wiki, graph=graph_mgr)

    # Seed demo data, then mirror the Wiki state into the in-memory graph.
    if app.state.seed_demo:
        await _seed_demo_skills(wiki)
        await _sync_graph_from_wiki(wiki, graph_mgr)

    # Wire WebSocket broadcast events into the executor.
    from .routes.ws import broadcast
    if app_state.executor:
        async def ws_callback(event_type: str, data: Dict[str, Any]) -> None:
            await broadcast(event_type, data)
        app_state.executor.add_event_callback(ws_callback)

    yield


async def _sync_graph_from_wiki(wiki: Any, graph_mgr: MemoryGraphManager) -> None:
    """Best-effort startup sync from the Wiki store into the in-memory graph."""
    try:
        seeded_skills = await wiki.list(limit=10000)
        skill_ids = [skill.skill_id for skill in seeded_skills]
        for skill in seeded_skills:
            await graph_mgr.sync_skill(skill)

        if hasattr(graph_mgr, "sync_auto_edges"):
            for skill in seeded_skills:
                await graph_mgr.sync_auto_edges(skill, skill_ids)
    except Exception as exc:  # pragma: no cover - startup should survive graph issues
        logger.warning("Failed to sync seeded Skills into graph: %s", exc)


async def _seed_demo_skills(wiki: MemoryWikiManager) -> None:
    """Seed demo Skills and Meta-Skills with readable static text."""
    from ..models.skill_model import (
        Skill, SkillInterface, SkillImplementation,
        SkillState, SkillType, SkillProvenance,
    )

    def iface(
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        pre: Optional[list[str]] = None,
        post: Optional[list[str]] = None,
    ) -> SkillInterface:
        return SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    p["name"]: {
                        "type": p["type"],
                        "description": p.get("description", ""),
                    }
                    for p in inputs
                },
                "required": [p["name"] for p in inputs if p.get("required")],
            },
            output_schema={
                "type": "object",
                "properties": {
                    p["name"]: {
                        "type": p["type"],
                        "description": p.get("description", ""),
                    }
                    for p in outputs
                },
            },
            preconditions=pre or [],
            postconditions=post or [],
        )

    demos = [
        dict(
            name="click_element",
            description="Click a target element on a web page.",
            skill_type=SkillType.ATOMIC,
            tags=["web", "ui", "interaction"],
            interface=iface(
                [{"name": "selector", "type": "string", "description": "CSS selector", "required": True}],
                [{"name": "success", "type": "boolean", "description": "Whether the click was simulated successfully"}],
                pre=["Page is loaded"],
                post=["The target element has been clicked"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True  # Simulated click',
            ),
        ),
        dict(
            name="type_text",
            description="Type text into an input field.",
            skill_type=SkillType.ATOMIC,
            tags=["web", "ui", "input"],
            interface=iface(
                [
                    {"name": "selector", "type": "string", "description": "CSS selector", "required": True},
                    {"name": "text", "type": "string", "description": "Text to type", "required": True},
                ],
                [{"name": "success", "type": "boolean", "description": "Whether the text was entered"}],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True  # Simulated typing',
            ),
        ),
        dict(
            name="fill_form",
            description="Fill and submit a form by composing click and type skills.",
            skill_type=SkillType.FUNCTIONAL,
            tags=["web", "form", "functional"],
            interface=iface(
                [{"name": "form_data", "type": "object", "description": "Form field dictionary", "required": True}],
                [{"name": "submitted", "type": "boolean", "description": "Whether the form was submitted"}],
            ),
            implementation=SkillImplementation(
                language="python",
                sub_skill_ids=["click_element", "type_text"],
            ),
        ),
        dict(
            name="locate_element",
            description="Locate an element on a page and return a CSS selector.",
            skill_type=SkillType.ATOMIC,
            tags=["web", "ui", "query"],
            interface=iface(
                [{"name": "description", "type": "string", "description": "Element description", "required": True}],
                [{"name": "selector", "type": "string", "description": "Suggested CSS selector"}],
            ),
            implementation=SkillImplementation(
                language="python",
                prompt_template=(
                    "Find the element described by '{description}' on the page. "
                    "Return only one CSS selector string."
                ),
            ),
        ),
    ]

    meta_skills = [
        dict(
            name="generate_skill_from_task",
            description="Generate a reusable Skill draft from a task description.",
            skill_type=SkillType.STRATEGIC,
            meta_category="generation",
            tags=["meta", "generation", "strategic"],
            interface=iface(
                [
                    {"name": "task_description", "type": "string", "description": "Task description", "required": True},
                    {"name": "context", "type": "object", "description": "Optional context"},
                ],
                [
                    {"name": "skill_name", "type": "string"},
                    {"name": "skill_draft", "type": "object"},
                    {"name": "confidence", "type": "number"},
                ],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "You are the SkillOS Skill Builder. Extract a reusable Skill from this task:\n\n"
                    "{task_description}\n\n"
                    "Return JSON with name, description, input_schema, output_schema, and prompt_template."
                ),
            ),
        ),
        dict(
            name="generate_skill_from_trajectory",
            description="Extract a reusable Skill from an execution trajectory.",
            skill_type=SkillType.STRATEGIC,
            meta_category="generation",
            tags=["meta", "generation", "trajectory", "strategic"],
            interface=iface(
                [{"name": "trajectory", "type": "string", "description": "Execution trajectory text", "required": True}],
                [{"name": "skill_name", "type": "string"}, {"name": "skill_draft", "type": "object"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Analyze this execution trajectory and extract a reusable Skill pattern:\n\n"
                    "{trajectory}\n\n"
                    "Return a JSON Skill definition."
                ),
            ),
        ),
        dict(
            name="formalize_skill_schema",
            description="Convert an informal Skill description into JSON schemas.",
            skill_type=SkillType.STRATEGIC,
            meta_category="knowledge_management",
            tags=["meta", "schema", "formalization", "strategic"],
            interface=iface(
                [{"name": "informal_description", "type": "string", "description": "Informal Skill description", "required": True}],
                [{"name": "input_schema", "type": "object"}, {"name": "output_schema", "type": "object"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Convert this informal Skill description into standard JSON Schema:\n\n"
                    "{informal_description}\n\n"
                    "Return JSON with input_schema and output_schema."
                ),
            ),
        ),
        dict(
            name="generate_skill_tests",
            description="Generate test cases for a Skill.",
            skill_type=SkillType.STRATEGIC,
            meta_category="quality_assurance",
            tags=["meta", "testing", "quality", "strategic"],
            interface=iface(
                [
                    {"name": "skill_name", "type": "string", "required": True},
                    {"name": "skill_description", "type": "string", "required": True},
                    {"name": "input_schema", "type": "object"},
                ],
                [{"name": "test_cases", "type": "array"}, {"name": "test_count", "type": "integer"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Generate 3 to 5 test cases for this Skill.\n\n"
                    "Skill: {skill_name}\n"
                    "Description: {skill_description}\n"
                    "Input schema: {input_schema}\n\n"
                    "Return JSON covering normal, boundary, and failure cases."
                ),
            ),
        ),
        dict(
            name="audit_skill_safety",
            description="Audit a Skill implementation for safety risks.",
            skill_type=SkillType.STRATEGIC,
            meta_category="quality_assurance",
            tags=["meta", "safety", "audit", "strategic"],
            interface=iface(
                [
                    {"name": "skill_name", "type": "string", "required": True},
                    {"name": "implementation_code", "type": "string"},
                ],
                [
                    {"name": "is_safe", "type": "boolean"},
                    {"name": "risks", "type": "array"},
                    {"name": "audit_score", "type": "number"},
                ],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Audit this Skill for code injection, privilege escalation, resource abuse, and data leakage.\n\n"
                    "Skill: {skill_name}\n"
                    "Implementation code: {implementation_code}\n\n"
                    "Return JSON: {\"is_safe\": true, \"risks\": [], \"audit_score\": 0.0}."
                ),
            ),
        ),
        dict(
            name="verify_skill_postcondition",
            description="Verify whether execution output satisfies postconditions.",
            skill_type=SkillType.STRATEGIC,
            meta_category="quality_assurance",
            tags=["meta", "verification", "postcondition", "strategic"],
            interface=iface(
                [
                    {"name": "skill_name", "type": "string", "required": True},
                    {"name": "postconditions", "type": "array", "required": True},
                    {"name": "execution_output", "type": "object", "required": True},
                ],
                [{"name": "satisfied", "type": "boolean"}, {"name": "violations", "type": "array"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Verify whether Skill '{skill_name}' satisfies these postconditions.\n\n"
                    "Postconditions: {postconditions}\n"
                    "Execution output: {execution_output}\n\n"
                    "Return JSON: {\"satisfied\": true, \"violations\": []}."
                ),
            ),
        ),
        dict(
            name="repair_failed_skill",
            description="Analyze a failed Skill and propose a repair.",
            skill_type=SkillType.STRATEGIC,
            meta_category="maintenance",
            tags=["meta", "repair", "maintenance", "strategic"],
            interface=iface(
                [
                    {"name": "skill_name", "type": "string", "required": True},
                    {"name": "failure_info", "type": "string", "required": True},
                    {"name": "current_implementation", "type": "string"},
                ],
                [
                    {"name": "repaired_implementation", "type": "string"},
                    {"name": "repair_notes", "type": "string"},
                ],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Repair failed Skill '{skill_name}'.\n\n"
                    "Failure info: {failure_info}\n"
                    "Current implementation: {current_implementation}\n\n"
                    "Return JSON with repaired_implementation and repair_notes."
                ),
            ),
        ),
        dict(
            name="split_oversized_skill",
            description="Split an oversized Skill into smaller child Skills.",
            skill_type=SkillType.STRATEGIC,
            meta_category="maintenance",
            tags=["meta", "split", "decomposition", "strategic"],
            interface=iface(
                [
                    {"name": "skill_name", "type": "string", "required": True},
                    {"name": "skill_description", "type": "string", "required": True},
                    {"name": "split_reason", "type": "string"},
                ],
                [{"name": "sub_skills", "type": "array"}, {"name": "split_count", "type": "integer"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Split this oversized Skill into smaller Skills.\n\n"
                    "Skill: {skill_name}\n"
                    "Description: {skill_description}\n"
                    "Reason: {split_reason}\n\n"
                    "Return a JSON array where each item has name, description, and prompt_template."
                ),
            ),
        ),
        dict(
            name="merge_redundant_skills",
            description="Merge redundant Skills into one canonical Skill.",
            skill_type=SkillType.STRATEGIC,
            meta_category="maintenance",
            tags=["meta", "merge", "deduplication", "strategic"],
            interface=iface(
                [
                    {"name": "skill_names", "type": "array", "description": "Skill names to merge", "required": True},
                    {"name": "skill_descriptions", "type": "array"},
                ],
                [{"name": "merged_skill", "type": "object"}, {"name": "merge_notes", "type": "string"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Merge these redundant Skills into one canonical Skill.\n\n"
                    "Skill names: {skill_names}\n"
                    "Descriptions: {skill_descriptions}\n\n"
                    "Return a merged Skill JSON definition with name, description, and prompt_template."
                ),
            ),
        ),
        dict(
            name="deprecate_low_utility_skill",
            description="Decide whether a low-utility Skill should be deprecated.",
            skill_type=SkillType.STRATEGIC,
            meta_category="lifecycle",
            tags=["meta", "deprecation", "maintenance", "strategic"],
            interface=iface(
                [
                    {"name": "skill_name", "type": "string", "required": True},
                    {"name": "usage_count", "type": "integer", "required": True},
                    {"name": "success_rate", "type": "number", "required": True},
                    {"name": "last_used_days_ago", "type": "integer"},
                ],
                [{"name": "should_deprecate", "type": "boolean"}, {"name": "reason", "type": "string"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Evaluate whether Skill '{skill_name}' should be deprecated.\n\n"
                    "Usage count: {usage_count}\n"
                    "Success rate: {success_rate}\n"
                    "Last used days ago: {last_used_days_ago}\n\n"
                    "Return JSON: {\"should_deprecate\": false, \"reason\": \"...\"}."
                ),
            ),
        ),
        dict(
            name="update_skill_wiki_page",
            description="Generate updated Wiki documentation for a Skill.",
            skill_type=SkillType.STRATEGIC,
            meta_category="knowledge_management",
            tags=["meta", "wiki", "documentation", "strategic"],
            interface=iface(
                [
                    {"name": "skill_id", "type": "string", "required": True},
                    {"name": "update_reason", "type": "string", "required": True},
                    {"name": "new_description", "type": "string"},
                    {"name": "new_tags", "type": "array"},
                ],
                [{"name": "updated", "type": "boolean"}, {"name": "wiki_url", "type": "string"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Generate updated Wiki page content for Skill '{skill_id}'.\n\n"
                    "Update reason: {update_reason}\n"
                    "New description: {new_description}\n\n"
                    "Return Markdown documentation."
                ),
            ),
        ),
        dict(
            name="update_skill_graph_relation",
            description="Validate and update a Skill Graph relation.",
            skill_type=SkillType.STRATEGIC,
            meta_category="graph",
            tags=["meta", "graph", "relations", "strategic"],
            interface=iface(
                [
                    {"name": "source_skill", "type": "string", "required": True},
                    {"name": "target_skill", "type": "string", "required": True},
                    {"name": "relation_type", "type": "string", "description": "depends_on/composes/replaces", "required": True},
                    {"name": "weight", "type": "number"},
                ],
                [{"name": "edge_added", "type": "boolean"}, {"name": "graph_updated", "type": "boolean"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "Analyze whether relation '{relation_type}' between '{source_skill}' and '{target_skill}' is valid.\n\n"
                    "Return JSON: {\"valid\": true, \"reasoning\": \"...\"}."
                ),
            ),
        ),
    ]

    for data in demos + meta_skills:
        skill = Skill(
            **data,
            provenance=SkillProvenance(source_type="demo", created_by_agent="system"),
        )
        skill.transition_to(SkillState.VERIFIED)
        skill.transition_to(SkillState.RELEASED)
        for _ in range(20):
            skill.record_execution(success=True, latency_ms=120.0)
        for _ in range(2):
            skill.record_execution(success=False, latency_ms=500.0)
        try:
            await wiki.create(skill)
        except ValueError:
            pass


def create_app(
    api_key: str,
    model: str = "claude-sonnet-4-6",
    *,
    repository_backend: str = "git",
    skill_storage_dir: Optional[Path] = None,
    seed_demo: bool = True,
) -> FastAPI:
    from ..config.llm_config import LLMConfig

    llm_cfg = LLMConfig(api_key=api_key, model=model)

    app = FastAPI(
        title="SkillOS API",
        description="Skill-Centric Operating System for Self-Evolving Agents",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.llm_cfg = llm_cfg
    app.state.repository_backend = repository_backend
    app.state.skill_storage_dir = (skill_storage_dir or _default_skill_storage_dir()).resolve()
    app.state.seed_demo = seed_demo

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(lifecycle.router, prefix="/api/v1")
    app.include_router(graph.router, prefix="/api/v1")
    app.include_router(execution.router, prefix="/api/v1")
    app.include_router(evolution.router, prefix="/api/v1")
    app.include_router(ingest.router, prefix="/api/v1")
    app.include_router(repository.router, prefix="/api/v1")
    app.include_router(ws.router)

    @app.get("/")
    async def root() -> Dict[str, str]:
        return {"name": "SkillOS", "version": "1.0.0", "status": "running"}

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="SkillOS API Server")
    parser.add_argument("--api-key", required=True, help="Anthropic API Key")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--repository-backend", choices=["git", "memory"], default="git")
    parser.add_argument("--skill-storage-dir", default=None)
    parser.add_argument("--no-seed-demo", action="store_true")
    args = parser.parse_args()

    storage_dir = Path(args.skill_storage_dir).resolve() if args.skill_storage_dir else None
    app = create_app(
        api_key=args.api_key,
        model=args.model,
        repository_backend=args.repository_backend,
        skill_storage_dir=storage_dir,
        seed_demo=not args.no_seed_demo,
    )
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
