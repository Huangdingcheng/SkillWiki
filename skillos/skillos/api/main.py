"""SkillOS FastAPI application entry point."""

from __future__ import annotations

import argparse
import logging
import os
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
from .routes import evolution, execution, graph, host_info, ingest, lifecycle, repository, skills, ws

logger = logging.getLogger(__name__)

DEFAULT_YUNWU_API_KEY = "sk-9BwvNgcu3XHwbQ15aBcnryPWEgZgQq10PB27fJ3sVOZcSNcF"


def _default_skill_storage_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "storage" / "skill_repo" / "SkillStorage"


def _default_skill_repo_dir() -> Path:
    """
    main.py 路径通常是：

        outer-skillos/
            skillos/
                api/
                    main.py
            layers/
                storage/
                    skill_repo/
                        SkillStorage/

    所以 Path(__file__).resolve().parents[2] 指向 outer-skillos。
    """
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "layers" / "storage" / "skill_repo" / "SkillStorage"


def _default_embedding_cache_path() -> Path:
    return Path(__file__).resolve().parents[1] / "storage" / "embedding_cache" / "semantic_embeddings.json"


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

    app_state.initialize(llm=llm, wiki=wiki, graph=graph_mgr, embedding_cache_path=app.state.embedding_cache_path)

    # Seed demo data, then mirror the Wiki state into the in-memory graph.
    if app.state.seed_demo:
        await _seed_demo_skills(wiki)
        await _seed_anthropic_skills(wiki)
        await _sync_graph_from_wiki(wiki, graph_mgr)
        await _seed_static_knowledge_graph(wiki, graph_mgr)
        await _warmup_semantic_index(app_state.search, graph_mgr)

    # Wire WebSocket broadcast events into the executor.
    from .routes.ws import broadcast

    if app_state.executor:

        async def ws_callback(event_type: str, data: Dict[str, Any]) -> None:
            await broadcast(event_type, data)

        app_state.executor.add_event_callback(ws_callback)

    yield


async def _warmup_semantic_index(search: Any, graph_mgr: MemoryGraphManager) -> None:
    """Pre-compute persistent embeddings after deterministic demo data is ready."""
    if not search or not hasattr(search, "warmup"):
        return
    graph_nodes = []
    try:
        if hasattr(graph_mgr, "get_heterogeneous_graph"):
            graph = await graph_mgr.get_heterogeneous_graph(limit=500)
            graph_nodes = list(graph.nodes.values())
        await search.warmup(include_graph_nodes=graph_nodes)
        logger.info("Semantic embedding index warmup completed")
    except Exception as exc:  # pragma: no cover - startup should survive provider slowness
        logger.warning("Semantic embedding index warmup skipped: %s", exc)


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


async def _seed_anthropic_skills(wiki: Any) -> None:
    """Import vendored Anthropic Agent Skills as final immutable baselines."""
    from ..layers.input_knowledge.anthropic_skills import load_anthropic_skills

    vendor_dir = Path(__file__).resolve().parents[3] / "vendor" / "anthropic-skills"
    if not vendor_dir.exists():
        return
    result = load_anthropic_skills(vendor_dir, namespace="anthropic")
    imported = 0
    skipped = 0
    for skill in result.skills:
        try:
            existing = await wiki.get_by_name(skill.name, skill.version)
            if existing:
                skipped += 1
                continue
            await wiki.create(skill)
            imported += 1
        except Exception as exc:
            result.errors.append(f"{skill.name}: {exc}")
    if imported or result.errors:
        logger.info(
            "Anthropic Skill import completed: imported=%s skipped=%s errors=%s",
            imported,
            skipped + len(result.skipped),
            len(result.errors),
        )
        for error in result.errors[:5]:
            logger.warning("Anthropic Skill import error: %s", error)


async def _seed_static_knowledge_graph(wiki: Any, graph_mgr: MemoryGraphManager) -> None:
    """Seed the Figure 2 style heterogeneous graph from static desktop sources.

    This is intentionally deterministic and script-like: later iterations can
    replace each block with real Extractor/Normalizer/Summarizer/Indexer agents.
    """
    if not hasattr(graph_mgr, "upsert_node") or not hasattr(graph_mgr, "upsert_edge"):
        return

    from ..models.graph_model import (
        GraphNodeType,
        GraphRelationType,
        HeterogeneousGraphEdge,
        HeterogeneousGraphNode,
    )

    skills = await wiki.list(limit=10000)
    skills_by_name = {skill.name: skill for skill in skills}

    nodes = [
        HeterogeneousGraphNode(
            node_id="task:onboard_user_account",
            node_type=GraphNodeType.TASK,
            name="Onboard a user account",
            description="A user task that requires locating fields, typing credentials, and submitting a form.",
            labels=["task", "web", "onboarding"],
            source_type="user_task",
            metadata={"stage": "C. Knowledge / Experience Sources"},
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:login_form_walkthrough",
            node_type=GraphNodeType.TRAJECTORY,
            name="Login form walkthrough",
            description="A curated trajectory containing click, type, submit, and confirmation actions.",
            labels=["trajectory", "web", "form"],
            source_type="trajectory",
            metadata={"format": "static_desktop_seed"},
        ),
        HeterogeneousGraphNode(
            node_id="document:web_form_guidelines",
            node_type=GraphNodeType.DOCUMENT,
            name="Web form guidelines",
            description="Documentation that explains required form fields, validation, and postconditions.",
            labels=["document", "requirements"],
            source_type="document",
        ),
        HeterogeneousGraphNode(
            node_id="document:host_gui_action_policy",
            node_type=GraphNodeType.DOCUMENT,
            name="Host GUI action policy",
            description="Desktop automation policy: host actions must go through allowlisted tools such as opening apps, URLs, folders, or files.",
            labels=["document", "host", "safety", "allowlist"],
            source_type="document",
        ),
        HeterogeneousGraphNode(
            node_id="document:website_navigation_guidelines",
            node_type=GraphNodeType.DOCUMENT,
            name="Website navigation guidelines",
            description="Operational notes for website-use Skills that open Chrome, navigate to a URL, and preserve the target page state.",
            labels=["document", "website", "chrome", "navigation"],
            source_type="document",
        ),
        HeterogeneousGraphNode(
            node_id="document:browser_gui_observation_loop",
            node_type=GraphNodeType.DOCUMENT,
            name="Browser GUI observation loop",
            description="Guidelines for interactive browser workflows: start from the user goal, search or navigate, observe the visible page, choose the next page action, and stop only when the visible state satisfies the goal.",
            labels=["document", "browser", "gui", "observation", "agent-loop"],
            source_type="document",
            metadata={
                "loop": ["plan", "act", "observe", "revise", "validate"],
                "requires": ["DOM snapshot or screenshot observation", "bounded retry budget"],
            },
        ),
        HeterogeneousGraphNode(
            node_id="document:downloads_file_output_contract",
            node_type=GraphNodeType.DOCUMENT,
            name="Downloads file output contract",
            description="Contract for task Skills that save a deterministic answer artifact into the user's Downloads folder.",
            labels=["document", "downloads", "file", "artifact"],
            source_type="document",
        ),
        HeterogeneousGraphNode(
            node_id="document:desktop_document_workflow_contract",
            node_type=GraphNodeType.DOCUMENT,
            name="Desktop document workflow contract",
            description="Reusable workflow notes for reading a local text file, creating a WPS-openable document, inserting the text, and saving it to Desktop.",
            labels=["document", "desktop", "wps", "document", "workflow"],
            source_type="document",
        ),
        HeterogeneousGraphNode(
            node_id="api_doc:auth_service",
            node_type=GraphNodeType.API_DOC,
            name="Auth Service API",
            description="API documentation for login, session creation, and profile retrieval endpoints.",
            labels=["api", "auth"],
            source_type="api_doc",
            metadata={"endpoints": ["POST /login", "GET /profile"]},
        ),
        HeterogeneousGraphNode(
            node_id="api_doc:host_gui_launcher_api",
            node_type=GraphNodeType.API_DOC,
            name="Host GUI Launcher API",
            description="Tool API for host.open_application, host.open_chrome, host.open_file, host.open_or_create_file_in_vscode, host.open_downloads_folder, host.run_terminal_top, and host.run_terminal_command.",
            labels=["api", "host", "gui", "tool"],
            source_type="api_doc",
            metadata={"tool_calls": ["host.open_application", "host.open_chrome", "host.open_file", "host.open_or_create_file_in_vscode", "host.open_downloads_folder", "host.run_terminal_top", "host.run_terminal_command"]},
        ),
        HeterogeneousGraphNode(
            node_id="api_doc:chrome_url_launch_api",
            node_type=GraphNodeType.API_DOC,
            name="Chrome URL Launch API",
            description="Tool API for opening a URL or search target result in Google Chrome from an execution plan. Search targets can be first result, official result, domain/title hint, or an agent-selected match.",
            labels=["api", "chrome", "url", "tool", "target-result"],
            source_type="api_doc",
            metadata={"tool_calls": ["host.open_url_in_chrome", "host.open_search_first_result"], "default_chatgpt_url": "https://chatgpt.com/"},
        ),
        HeterogeneousGraphNode(
            node_id="api_doc:browser_gui_workflow_api",
            node_type=GraphNodeType.API_DOC,
            name="Browser GUI Workflow API",
            description="Tool API for a bounded browser observe-decide-act workflow. The current macOS runtime can launch Chrome, collect DOM/screenshot evidence, and attempt DOM-backed text/result clicks; OCR and cross-browser visual targeting remain future controller upgrades.",
            labels=["api", "browser", "gui", "observation", "tool"],
            source_type="api_doc",
            metadata={"tool_calls": ["host.browser_gui_workflow"], "supports": ["query", "goal", "max_rounds", "observations", "actions"]},
        ),
        HeterogeneousGraphNode(
            node_id="api_doc:downloads_file_writer_api",
            node_type=GraphNodeType.API_DOC,
            name="Downloads File Writer API",
            description="Tool API for writing deterministic text artifacts into ~/Downloads.",
            labels=["api", "downloads", "file", "tool"],
            source_type="api_doc",
            metadata={"tool_calls": ["host.write_downloads_text_file"]},
        ),
        HeterogeneousGraphNode(
            node_id="api_doc:wps_document_generator_api",
            node_type=GraphNodeType.API_DOC,
            name="WPS Document Generator API",
            description="Tool API for creating an RTF document from a source text file and opening it in WPS Office or the default document application.",
            labels=["api", "wps", "document", "file", "tool"],
            source_type="api_doc",
            metadata={"tool_calls": ["host.create_wps_document_from_text_file"]},
        ),
        HeterogeneousGraphNode(
            node_id="tool:browser_driver",
            node_type=GraphNodeType.TOOL,
            name="Browser Driver",
            description="A browser automation tool used by web interaction skills.",
            labels=["tool", "browser"],
            source_type="tool_doc",
        ),
        HeterogeneousGraphNode(
            node_id="tool:browser_gui_observer",
            node_type=GraphNodeType.TOOL,
            name="Browser GUI Observer",
            description="Runtime-side observer/controller for browser workflows. On macOS Chrome it captures DOM snapshots, visible text, candidate links/buttons/inputs, screenshot files, and action evidence for DOM-backed clicks.",
            labels=["tool", "browser", "gui", "observation", "runtime"],
            source_type="tool_doc",
            metadata={"observation_channels": ["screenshot", "dom_snapshot", "visible_text", "action_result"], "controller": "macos_chrome_dom_controller"},
        ),
        HeterogeneousGraphNode(
            node_id="task:open_chrome_browser",
            node_type=GraphNodeType.TASK,
            name="Open Chrome browser",
            description="A user task asking the agent to open Google Chrome on the host machine.",
            labels=["task", "host", "browser", "chrome"],
            source_type="user_task",
            metadata={"natural_language_command": "Please open the Chrome browser for me."},
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:open_chrome_browser_host_action",
            node_type=GraphNodeType.TRAJECTORY,
            name="Open Chrome host action trajectory",
            description="Trajectory: receive natural language command, retrieve open_chrome_browser, call host.open_chrome, record OS launch result.",
            labels=["trajectory", "host-action", "chrome"],
            source_type="trajectory",
            metadata={
                "steps": [
                    "User says: Please open the Chrome browser for me.",
                    "Execution agent retrieves the open_chrome_browser Skill.",
                    "Runtime invokes allowlisted host.open_chrome tool.",
                    "Host OS receives the Google Chrome launch request.",
                ]
            },
        ),
        HeterogeneousGraphNode(
            node_id="task:monitor_processes_with_top",
            node_type=GraphNodeType.TASK,
            name="Monitor processes with top",
            description="A user task asking the agent to open Terminal and run top to observe live process activity.",
            labels=["task", "host", "terminal", "process", "top", "monitor"],
            source_type="user_task",
            metadata={"natural_language_command": "Open Terminal and run top to monitor live processes for 10 seconds."},
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:terminal_top_monitor",
            node_type=GraphNodeType.TRAJECTORY,
            name="Terminal top monitor trajectory",
            description="Trajectory: infer the terminal process-monitoring intent, retrieve run_terminal_top_monitor, launch Terminal, run top, and keep the runtime active briefly.",
            labels=["trajectory", "host-action", "terminal", "top", "process"],
            source_type="trajectory",
            metadata={
                "steps": [
                    "User asks to inspect live process dynamics.",
                    "Execution agent retrieves process-monitoring graph context and the terminal top Skill.",
                    "Runtime invokes allowlisted host.run_terminal_top.",
                    "Terminal opens and top streams process data for several seconds.",
                ]
            },
        ),
        HeterogeneousGraphNode(
            node_id="tool:host_gui_launcher",
            node_type=GraphNodeType.TOOL,
            name="Host GUI Launcher",
            description="Allowlisted host-side GUI launcher tool for desktop actions such as opening Chrome, files, VS Code file workflows, and Terminal process monitors.",
            labels=["tool", "host", "gui", "allowlist"],
            source_type="tool_doc",
            metadata={"tool_calls": ["host.open_chrome", "host.open_application", "host.open_file", "host.open_or_create_file_in_vscode", "host.run_terminal_top"]},
        ),
        HeterogeneousGraphNode(
            node_id="script:macos_open_chrome",
            node_type=GraphNodeType.SCRIPT,
            name="macOS open Chrome command",
            description="Static script source for launching Chrome through the host OS application launcher.",
            labels=["script", "host", "macos", "chrome"],
            source_type="script",
            metadata={"command": "open -a 'Google Chrome'"},
        ),
        HeterogeneousGraphNode(
            node_id="script:macos_open_application",
            node_type=GraphNodeType.SCRIPT,
            name="macOS open application command",
            description="Static script source for opening an arbitrary allowlisted macOS application.",
            labels=["script", "host", "macos", "application"],
            source_type="script",
            metadata={"command": "open -a <Application Name>"},
        ),
        HeterogeneousGraphNode(
            node_id="script:macos_terminal_top_monitor",
            node_type=GraphNodeType.SCRIPT,
            name="macOS Terminal top monitor command",
            description="Static script source for opening Terminal and running top long enough to observe live process activity.",
            labels=["script", "host", "macos", "terminal", "top", "process"],
            source_type="script",
            metadata={"command": "osascript tell Terminal to do script 'top -o cpu -l <samples>'"},
        ),
        HeterogeneousGraphNode(
            node_id="script:macos_terminal_safe_command",
            node_type=GraphNodeType.SCRIPT,
            name="macOS Terminal safe command runner",
            description="Static script source for opening Terminal and running an agent-generated safe read-only command such as printenv, pwd, date, or ls.",
            labels=["script", "host", "macos", "terminal", "command", "safe"],
            source_type="script",
            metadata={"command_template": "osascript tell Terminal to do script '<safe_command>'", "allowed_examples": ["printenv", "pwd", "date", "ls"]},
        ),
        HeterogeneousGraphNode(
            node_id="script:macos_vscode_file_workflow",
            node_type=GraphNodeType.SCRIPT,
            name="macOS VS Code file workflow",
            description="Static script source for checking a Desktop file, creating it if missing, and opening it in VS Code via the code command when available.",
            labels=["script", "host", "macos", "vscode", "terminal", "file", "create"],
            source_type="script",
            metadata={"command_template": "test -f <path> || touch <path>; code <path>", "safe_host_tool": "host.open_or_create_file_in_vscode"},
        ),
        HeterogeneousGraphNode(
            node_id="script:macos_open_url_chrome",
            node_type=GraphNodeType.SCRIPT,
            name="macOS open URL in Chrome command",
            description="Static script source for launching Google Chrome with a target website URL.",
            labels=["script", "host", "macos", "chrome", "url"],
            source_type="script",
            metadata={"command": "open -a 'Google Chrome' <url>"},
        ),
        HeterogeneousGraphNode(
            node_id="script:google_first_result_url",
            node_type=GraphNodeType.SCRIPT,
            name="Google search target result URL command",
            description="Static script source for opening Google's target result for a query via btnI or target-hint query expansion. The default target is the first result, but agent context can add official/domain/title hints.",
            labels=["script", "search", "target-result", "first-result", "chrome", "url"],
            source_type="script",
            metadata={"url_template": "https://www.google.com/search?q=<query> <target_hint>&btnI=I", "default_target": "first result"},
        ),
        HeterogeneousGraphNode(
            node_id="script:browser_gui_observe_decide_act_loop",
            node_type=GraphNodeType.SCRIPT,
            name="Browser observe-decide-act loop",
            description="Pseudo-script source for interactive web tasks: open a search or target page, observe the current browser state, ask the agent for the next action, execute the selected browser action, then validate.",
            labels=["script", "browser", "gui", "agent-loop", "observation"],
            source_type="script",
            metadata={
                "pseudocode": [
                    "open Chrome with search query or target URL",
                    "repeat until success or max_rounds",
                    "capture screenshot/DOM/visible text observation",
                    "ask agent whether retrieved Skills help and what next action is needed",
                    "execute supported DOM click/navigate action and store evidence",
                ],
                "safe_host_tool": "host.browser_gui_workflow",
            },
        ),
        HeterogeneousGraphNode(
            node_id="script:python_write_downloads_file",
            node_type=GraphNodeType.SCRIPT,
            name="Python write Downloads text file",
            description="Static script source for writing a UTF-8 text artifact into the user's Downloads folder.",
            labels=["script", "python", "downloads", "file"],
            source_type="script",
            metadata={"path_template": "~/Downloads/{filename}.txt"},
        ),
        HeterogeneousGraphNode(
            node_id="script:python_create_wps_rtf_document",
            node_type=GraphNodeType.SCRIPT,
            name="Python create WPS RTF document",
            description="Static script source for reading a local .txt file, escaping it as RTF content, writing a Desktop document, and opening it with WPS Office.",
            labels=["script", "python", "wps", "rtf", "document", "desktop"],
            source_type="script",
            metadata={"path_template": "~/Desktop/<output>.rtf", "safe_host_tool": "host.create_wps_document_from_text_file"},
        ),
        HeterogeneousGraphNode(
            node_id="script:form_submit_helper",
            node_type=GraphNodeType.SCRIPT,
            name="Form submit helper",
            description="A static script source showing how to submit a form after validation.",
            labels=["script", "python"],
            source_type="script",
        ),
        HeterogeneousGraphNode(
            node_id="task:open_chatgpt_conversation",
            node_type=GraphNodeType.TASK,
            name="Open ChatGPT conversation",
            description="A user task asking the agent to open Chrome and navigate directly to the ChatGPT conversation UI.",
            labels=["task", "website", "chatgpt", "chrome"],
            source_type="user_task",
            metadata={"natural_language_command": "Open Chrome and go to the GPT conversation page."},
        ),
        HeterogeneousGraphNode(
            node_id="task:open_or_create_desktop_file_in_vscode",
            node_type=GraphNodeType.TASK,
            name="Open or create a Desktop file in VS Code",
            description="A user task asking the agent to use Terminal/code and VS Code to open a Desktop file, creating it when missing.",
            labels=["task", "host", "terminal", "vscode", "file", "desktop"],
            source_type="user_task",
            metadata={"natural_language_command": "Use Terminal to open code, then open ~/Desktop/111.txt in VS Code; create it if missing."},
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:vscode_open_or_create_desktop_file",
            node_type=GraphNodeType.TRAJECTORY,
            name="VS Code open-or-create Desktop file trajectory",
            description="Trajectory: infer the Desktop file path, check whether it exists, create it if needed, then launch VS Code for that file using the host workflow.",
            labels=["trajectory", "host-action", "terminal", "vscode", "file", "create"],
            source_type="trajectory",
            metadata={
                "steps": [
                    "User asks to open a Desktop file in VS Code through Terminal/code.",
                    "Execution agent retrieves the VS Code file workflow Skill.",
                    "Runtime resolves ~/Desktop/<filename> and checks existence.",
                    "Runtime creates the file if missing and opens it in VS Code.",
                ]
            },
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:open_chatgpt_url",
            node_type=GraphNodeType.TRAJECTORY,
            name="Open ChatGPT URL trajectory",
            description="Trajectory: open Chrome, navigate to https://chatgpt.com/, and leave the user on the conversation page.",
            labels=["trajectory", "website", "chatgpt"],
            source_type="trajectory",
        ),
        HeterogeneousGraphNode(
            node_id="task:interactive_browser_mail_workflow",
            node_type=GraphNodeType.TASK,
            name="Find and use a web mail service",
            description="A browser GUI task that requires searching for a service, choosing the right result, using cached credentials if available, and opening a target folder such as Sent.",
            labels=["task", "browser", "gui", "mail", "login", "interactive"],
            source_type="user_task",
            metadata={"natural_language_command": "Open the browser, find the Harbin Institute of Technology email service, log in with cached credentials, and open Sent mail."},
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:interactive_browser_mail_sent",
            node_type=GraphNodeType.TRAJECTORY,
            name="Interactive browser mail sent-folder trajectory",
            description="Trajectory: search for the target mail service, observe search results, choose a likely official result, observe login/home state, use cached credentials when available, then observe and open the Sent folder.",
            labels=["trajectory", "browser", "gui", "mail", "observation", "agent-loop"],
            source_type="trajectory",
            metadata={
                "steps": [
                    "Infer the service/search query from the user goal.",
                    "Open Chrome with the query or direct target if known.",
                    "Observe search results or current page state.",
                    "Select the official mail/login result when visible.",
                    "Use cached browser credentials if the page offers them; do not ask for secrets.",
                    "Observe mailbox navigation and click/open the Sent folder.",
                    "Validate that the visible page indicates Sent mail is open.",
                ]
            },
        ),
        HeterogeneousGraphNode(
            node_id="task:create_gpt_weather_answer_note",
            node_type=GraphNodeType.TASK,
            name="Create GPT weather answer note",
            description="A full task: open ChatGPT, ask a weather-style question, and save an answer artifact into Downloads.",
            labels=["task", "strategic", "chatgpt", "downloads"],
            source_type="user_task",
            metadata={"natural_language_command": "Open GPT, ask today's weather, and save the answer to Downloads."},
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:chatgpt_weather_answer_saved",
            node_type=GraphNodeType.TRAJECTORY,
            name="ChatGPT weather answer saved trajectory",
            description="Trajectory: open ChatGPT in Chrome, prepare a question, generate an answer artifact, and save it as gpt_weather_answer.txt.",
            labels=["trajectory", "strategic", "chatgpt", "downloads"],
            source_type="trajectory",
        ),
        HeterogeneousGraphNode(
            node_id="task:create_wps_document_from_desktop_text",
            node_type=GraphNodeType.TASK,
            name="Create WPS document from Desktop text",
            description="A full host task: open WPS, create a blank document, copy text from a Desktop .txt file, and save the document to Desktop.",
            labels=["task", "strategic", "wps", "document", "desktop", "file"],
            source_type="user_task",
            metadata={"natural_language_command": "Open WPS, create a blank document, copy ~/Desktop/111.txt into it, and save the document to Desktop."},
        ),
        HeterogeneousGraphNode(
            node_id="trajectory:wps_text_file_document_saved",
            node_type=GraphNodeType.TRAJECTORY,
            name="WPS text file document saved trajectory",
            description="Trajectory: resolve Desktop 111.txt, read its content, create an RTF/WPS-openable document, save it on Desktop, then open the result in WPS Office.",
            labels=["trajectory", "strategic", "wps", "document", "desktop", "file"],
            source_type="trajectory",
        ),
        HeterogeneousGraphNode(
            node_id="feedback:form_retry_needed",
            node_type=GraphNodeType.FEEDBACK,
            name="Form retry feedback",
            description="Runtime feedback indicating that missing fields should trigger a repair or retry strategy.",
            labels=["feedback", "runtime"],
            source_type="execution_trace",
        ),
        HeterogeneousGraphNode(
            node_id="agent:skill_builder",
            node_type=GraphNodeType.AGENT,
            name="Skill Builder Agent",
            description="Agent responsible for extracting reusable skills from tasks, trajectories, docs, and scripts.",
            labels=["agent", "builder"],
            source_type="system_agent",
        ),
        HeterogeneousGraphNode(
            node_id="agent:skill_librarian",
            node_type=GraphNodeType.AGENT,
            name="Skill Librarian Agent",
            description="Agent responsible for maintaining graph relationships, wiki pages, and versions.",
            labels=["agent", "librarian"],
            source_type="system_agent",
        ),
    ]

    for skill_name in (
        "type_text",
        "fill_form",
        "locate_element",
        "open_chrome_browser",
        "open_application",
        "run_terminal_command",
        "run_terminal_top_monitor",
        "open_local_file",
        "open_or_create_desktop_file_in_vscode",
        "complete_vscode_desktop_file_workflow",
        "open_url_in_chrome",
        "open_first_search_result",
        "capture_browser_page_observation",
        "choose_next_browser_action",
        "browser_gui_observe_and_act",
        "complete_interactive_browser_workflow",
        "open_chatgpt_conversation",
        "save_text_to_downloads",
        "complete_gpt_weather_note_task",
        "read_local_text_file",
        "create_blank_wps_document",
        "insert_text_into_document",
        "save_document_to_desktop",
        "create_wps_document_from_text_file",
        "complete_wps_text_file_document_workflow",
    ):
        skill = skills_by_name.get(skill_name)
        if not skill:
            continue
        nodes.append(HeterogeneousGraphNode(
            node_id=f"version:{skill.name}:{skill.version}",
            node_type=GraphNodeType.VERSION,
            name=f"{skill.display_name} v{skill.version}",
            description=f"Version node for {skill.name} v{skill.version}.",
            labels=["version", skill.skill_type.value],
            skill_id=skill.skill_id,
            version=skill.version,
            source_type="version_store",
            metadata={"state": skill.state.value},
        ))

    for node in nodes:
        await graph_mgr.upsert_node(node)

    def skill_id(name: str) -> Optional[str]:
        skill = skills_by_name.get(name)
        return skill.skill_id if skill else None

    edges: list[HeterogeneousGraphEdge | None] = [
        _hetero_seed_edge("task:onboard_user_account", "trajectory:login_form_walkthrough", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge("task:open_chrome_browser", "trajectory:open_chrome_browser_host_action", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("open_chrome_browser"), "trajectory:open_chrome_browser_host_action", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("open_chrome_browser"), "tool:host_gui_launcher", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("open_chrome_browser"), "script:macos_open_chrome", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("document:host_gui_action_policy", skill_id("open_chrome_browser"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:host_gui_launcher_api", skill_id("open_chrome_browser"), GraphRelationType.REQUIRES),
        _hetero_seed_edge("agent:skill_builder", skill_id("open_chrome_browser"), GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("open_application"), "tool:host_gui_launcher", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("open_application"), "script:macos_open_application", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("document:host_gui_action_policy", skill_id("open_application"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:host_gui_launcher_api", skill_id("open_application"), GraphRelationType.REQUIRES),
        _hetero_seed_edge(skill_id("run_terminal_command"), "tool:host_gui_launcher", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("run_terminal_command"), "script:macos_terminal_safe_command", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("document:host_gui_action_policy", skill_id("run_terminal_command"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:host_gui_launcher_api", skill_id("run_terminal_command"), GraphRelationType.REQUIRES),
        _hetero_seed_edge("task:monitor_processes_with_top", "trajectory:terminal_top_monitor", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("run_terminal_top_monitor"), "trajectory:terminal_top_monitor", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("run_terminal_top_monitor"), "tool:host_gui_launcher", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("run_terminal_top_monitor"), "script:macos_terminal_top_monitor", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("document:host_gui_action_policy", skill_id("run_terminal_top_monitor"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:host_gui_launcher_api", skill_id("run_terminal_top_monitor"), GraphRelationType.REQUIRES),
        _hetero_seed_edge(skill_id("open_local_file"), "tool:host_gui_launcher", GraphRelationType.USES),
        _hetero_seed_edge("document:host_gui_action_policy", skill_id("open_local_file"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:host_gui_launcher_api", skill_id("open_local_file"), GraphRelationType.REQUIRES),
        _hetero_seed_edge("task:open_or_create_desktop_file_in_vscode", "trajectory:vscode_open_or_create_desktop_file", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("open_or_create_desktop_file_in_vscode"), "trajectory:vscode_open_or_create_desktop_file", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("open_or_create_desktop_file_in_vscode"), "tool:host_gui_launcher", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("open_or_create_desktop_file_in_vscode"), "script:macos_vscode_file_workflow", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("document:host_gui_action_policy", skill_id("open_or_create_desktop_file_in_vscode"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:host_gui_launcher_api", skill_id("open_or_create_desktop_file_in_vscode"), GraphRelationType.REQUIRES),
        _hetero_seed_edge(skill_id("complete_vscode_desktop_file_workflow"), "trajectory:vscode_open_or_create_desktop_file", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("complete_vscode_desktop_file_workflow"), skill_id("open_or_create_desktop_file_in_vscode"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_vscode_desktop_file_workflow"), "script:macos_vscode_file_workflow", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("document:host_gui_action_policy", skill_id("complete_vscode_desktop_file_workflow"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:host_gui_launcher_api", skill_id("complete_vscode_desktop_file_workflow"), GraphRelationType.REQUIRES),
        _hetero_seed_edge(skill_id("save_text_to_downloads"), "script:python_write_downloads_file", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("save_text_to_downloads"), "api_doc:downloads_file_writer_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge("document:downloads_file_output_contract", skill_id("save_text_to_downloads"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("task:open_chatgpt_conversation", "trajectory:open_chatgpt_url", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("open_chatgpt_conversation"), "trajectory:open_chatgpt_url", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("open_chatgpt_conversation"), skill_id("open_url_in_chrome"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("open_chatgpt_conversation"), "script:macos_open_url_chrome", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("open_chatgpt_conversation"), "api_doc:chrome_url_launch_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge("document:website_navigation_guidelines", skill_id("open_chatgpt_conversation"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("open_url_in_chrome"), "script:macos_open_url_chrome", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("open_url_in_chrome"), "api_doc:chrome_url_launch_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge(skill_id("open_first_search_result"), "script:google_first_result_url", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("open_first_search_result"), "api_doc:chrome_url_launch_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge("document:website_navigation_guidelines", skill_id("open_first_search_result"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("task:interactive_browser_mail_workflow", "trajectory:interactive_browser_mail_sent", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("capture_browser_page_observation"), "tool:browser_gui_observer", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("capture_browser_page_observation"), "api_doc:browser_gui_workflow_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge("document:browser_gui_observation_loop", skill_id("capture_browser_page_observation"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("capture_browser_page_observation"), "trajectory:interactive_browser_mail_sent", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("choose_next_browser_action"), skill_id("capture_browser_page_observation"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("choose_next_browser_action"), "tool:browser_gui_observer", GraphRelationType.USES),
        _hetero_seed_edge("document:browser_gui_observation_loop", skill_id("choose_next_browser_action"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("choose_next_browser_action"), "trajectory:interactive_browser_mail_sent", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("browser_gui_observe_and_act"), skill_id("capture_browser_page_observation"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("browser_gui_observe_and_act"), skill_id("choose_next_browser_action"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("browser_gui_observe_and_act"), skill_id("open_first_search_result"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("browser_gui_observe_and_act"), "tool:browser_gui_observer", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("browser_gui_observe_and_act"), "script:browser_gui_observe_decide_act_loop", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("browser_gui_observe_and_act"), "api_doc:browser_gui_workflow_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge("document:browser_gui_observation_loop", skill_id("browser_gui_observe_and_act"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("browser_gui_observe_and_act"), "trajectory:interactive_browser_mail_sent", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("complete_interactive_browser_workflow"), skill_id("browser_gui_observe_and_act"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_interactive_browser_workflow"), skill_id("capture_browser_page_observation"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_interactive_browser_workflow"), skill_id("choose_next_browser_action"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_interactive_browser_workflow"), "tool:browser_gui_observer", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("complete_interactive_browser_workflow"), "script:browser_gui_observe_decide_act_loop", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("complete_interactive_browser_workflow"), "api_doc:browser_gui_workflow_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge("document:browser_gui_observation_loop", skill_id("complete_interactive_browser_workflow"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("complete_interactive_browser_workflow"), "trajectory:interactive_browser_mail_sent", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("task:create_gpt_weather_answer_note", "trajectory:chatgpt_weather_answer_saved", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("complete_gpt_weather_note_task"), "trajectory:chatgpt_weather_answer_saved", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("complete_gpt_weather_note_task"), skill_id("open_chatgpt_conversation"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_gpt_weather_note_task"), skill_id("save_text_to_downloads"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_gpt_weather_note_task"), "api_doc:downloads_file_writer_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge("document:downloads_file_output_contract", skill_id("complete_gpt_weather_note_task"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("task:create_wps_document_from_desktop_text", "trajectory:wps_text_file_document_saved", GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge(skill_id("complete_wps_text_file_document_workflow"), "trajectory:wps_text_file_document_saved", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("complete_wps_text_file_document_workflow"), skill_id("create_wps_document_from_text_file"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_wps_text_file_document_workflow"), skill_id("read_local_text_file"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("complete_wps_text_file_document_workflow"), skill_id("save_document_to_desktop"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("create_wps_document_from_text_file"), skill_id("read_local_text_file"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("create_wps_document_from_text_file"), skill_id("create_blank_wps_document"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("create_wps_document_from_text_file"), skill_id("insert_text_into_document"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("create_wps_document_from_text_file"), skill_id("save_document_to_desktop"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("create_wps_document_from_text_file"), "api_doc:wps_document_generator_api", GraphRelationType.REQUIRES),
        _hetero_seed_edge(skill_id("create_wps_document_from_text_file"), "script:python_create_wps_rtf_document", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("create_blank_wps_document"), "tool:host_gui_launcher", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("read_local_text_file"), "document:desktop_document_workflow_contract", GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("insert_text_into_document"), "document:desktop_document_workflow_contract", GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("save_document_to_desktop"), "document:desktop_document_workflow_contract", GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("document:desktop_document_workflow_contract", skill_id("complete_wps_text_file_document_workflow"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge(skill_id("fill_form"), "trajectory:login_form_walkthrough", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge("document:web_form_guidelines", skill_id("fill_form"), GraphRelationType.DOCUMENTS),
        _hetero_seed_edge("api_doc:auth_service", skill_id("locate_element"), GraphRelationType.REQUIRES),
        _hetero_seed_edge(skill_id("fill_form"), "script:form_submit_helper", GraphRelationType.DERIVED_FROM),
        _hetero_seed_edge(skill_id("fill_form"), skill_id("type_text"), GraphRelationType.COMPOSES_WITH),
        _hetero_seed_edge(skill_id("fill_form"), "tool:browser_driver", GraphRelationType.USES),
        _hetero_seed_edge(skill_id("type_text"), "tool:browser_driver", GraphRelationType.USES),
        _hetero_seed_edge("feedback:form_retry_needed", skill_id("fill_form"), GraphRelationType.FEEDS_BACK_TO),
        _hetero_seed_edge("agent:skill_builder", skill_id("fill_form"), GraphRelationType.PRODUCED_BY),
        _hetero_seed_edge("agent:skill_librarian", "document:web_form_guidelines", GraphRelationType.BELONGS_TO),
    ]

    for skill_name in (
        "type_text",
        "fill_form",
        "locate_element",
        "open_chrome_browser",
        "open_application",
        "run_terminal_command",
        "run_terminal_top_monitor",
        "open_local_file",
        "open_or_create_desktop_file_in_vscode",
        "complete_vscode_desktop_file_workflow",
        "open_url_in_chrome",
        "open_first_search_result",
        "capture_browser_page_observation",
        "choose_next_browser_action",
        "browser_gui_observe_and_act",
        "complete_interactive_browser_workflow",
        "open_chatgpt_conversation",
        "save_text_to_downloads",
        "complete_gpt_weather_note_task",
        "read_local_text_file",
        "create_blank_wps_document",
        "insert_text_into_document",
        "save_document_to_desktop",
        "create_wps_document_from_text_file",
        "complete_wps_text_file_document_workflow",
    ):
        skill = skills_by_name.get(skill_name)
        if skill:
            edges.append(_hetero_seed_edge(
                f"version:{skill.name}:{skill.version}",
                skill.skill_id,
                GraphRelationType.VERSION_OF,
            ))

    for edge in edges:
        if edge:
            await graph_mgr.upsert_edge(edge)


def _hetero_seed_edge(
    source_id: Optional[str],
    target_id: Optional[str],
    relation_type: Any,
    weight: float = 1.0,
) -> Any:
    from ..models.graph_model import HeterogeneousGraphEdge

    if not source_id or not target_id:
        return None
    return HeterogeneousGraphEdge(
        edge_id=f"seed:{relation_type.value}:{source_id}:{target_id}",
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        weight=weight,
        metadata={"auto_generated": True, "source": "static_desktop_seed"},
        created_by="static_desktop_seed",
    )


async def _seed_demo_skills(wiki: MemoryWikiManager) -> None:
    """Seed desktop Skills and Meta-Skills with readable static text."""
    from ..models.skill_model import (
        Skill,
        SkillImplementation,
        SkillInterface,
        SkillProvenance,
        SkillState,
        SkillTestCase,
        SkillType,
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

    def testcase(
        name: str,
        input_data: dict[str, Any],
        expected_output: Optional[dict[str, Any]] = None,
        tags: Optional[list[str]] = None,
    ) -> SkillTestCase:
        return SkillTestCase(
            name=name,
            description=f"Runtime validation case for {name}.",
            input_data=input_data,
            expected_output=expected_output or {},
            tags=tags or ["validation", "runtime"],
            is_regression=True,
        )

    demos = [
        dict(
            name="click_element",
            description="Locate and click a target UI element, returning a stable interaction record for downstream verification.",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            tags=["web", "ui", "interaction", "browser"],
            interface=iface(
                [{"name": "selector", "type": "string", "description": "CSS selector or semantic locator", "required": True}],
                [
                    {"name": "success", "type": "boolean", "description": "Whether the click was simulated successfully"},
                    {"name": "clicked_selector", "type": "string", "description": "The selector that was clicked"},
                ],
                pre=["Browser page is loaded", "Target element is visible and enabled"],
                post=["The target element has received a click event"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["clicked_selector"] = input_data.get("selector", "")',
                tool_calls=["Browser Driver"],
            ),
            test_cases=[
                testcase("click visible button", {"selector": "button[type=submit]"}, {"success": True}),
                testcase("click semantic login button", {"selector": "role=button[name=Login]"}, {"success": True}),
            ],
            tool_refs=["Browser Driver"],
            doc_refs=["document:web_form_guidelines"],
            trajectory_refs=["trajectory:login_form_walkthrough"],
        ),
        dict(
            name="type_text",
            description="Type text into an input field while preserving a normalized record of the field selector and value.",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            tags=["web", "ui", "input", "browser"],
            interface=iface(
                [
                    {"name": "selector", "type": "string", "description": "CSS selector", "required": True},
                    {"name": "text", "type": "string", "description": "Text to type", "required": True},
                ],
                [{"name": "success", "type": "boolean", "description": "Whether the text was entered"}],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["typed_selector"] = input_data.get("selector", "")\noutput["typed_length"] = len(str(input_data.get("text", "")))',
                tool_calls=["Browser Driver"],
            ),
            test_cases=[
                testcase("type username", {"selector": "#username", "text": "demo@example.com"}, {"success": True}),
                testcase("type password placeholder", {"selector": "#password", "text": "secret"}, {"success": True}),
            ],
            tool_refs=["Browser Driver"],
            doc_refs=["document:web_form_guidelines"],
            trajectory_refs=["trajectory:login_form_walkthrough"],
        ),
        dict(
            name="fill_form",
            description="Fill required form fields, validate required values, and submit the form as a reusable browser workflow.",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            granularity_level=2,
            tags=["web", "form", "functional", "validation", "browser"],
            interface=iface(
                [{"name": "form_data", "type": "object", "description": "Form field dictionary", "required": True}],
                [
                    {"name": "submitted", "type": "boolean", "description": "Whether the form was submitted"},
                    {"name": "missing_fields", "type": "array", "description": "Required fields that were missing"},
                ],
                pre=["Form schema is known", "Required fields are available in form_data"],
                post=["All required fields are populated before submit"],
            ),
            implementation=SkillImplementation(
                language="python",
                sub_skill_ids=["click_element", "type_text"],
                execution_order=["type_text", "click_element"],
            ),
            test_cases=[
                testcase("submit login form", {"form_data": {"username": "demo", "password": "secret"}}, {"submitted": True}),
                testcase("missing required field", {"form_data": {"username": "demo"}}, {"submitted": False}),
            ],
            tool_refs=["Browser Driver"],
            doc_refs=["document:web_form_guidelines"],
            trajectory_refs=["trajectory:login_form_walkthrough"],
            dependency_ids=["click_element", "type_text"],
            component_ids=["click_element", "type_text"],
        ),
        dict(
            name="locate_element",
            description="Resolve a natural-language UI element description into a stable selector candidate.",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            tags=["web", "ui", "query", "selector"],
            interface=iface(
                [{"name": "description", "type": "string", "description": "Element description", "required": True}],
                [
                    {"name": "selector", "type": "string", "description": "Suggested CSS selector"},
                    {"name": "confidence", "type": "number", "description": "Selector confidence"},
                ],
                pre=["Page DOM snapshot is available"],
                post=["A selector candidate is returned with confidence"],
            ),
            implementation=SkillImplementation(
                language="python",
                prompt_template=(
                    "Find the element described by '{description}' on the page. "
                    "Return only one CSS selector string."
                ),
                tool_calls=["Browser Driver"],
            ),
            test_cases=[
                testcase("locate login button", {"description": "the login button"}, {"selector": "button"}),
                testcase("locate username field", {"description": "username input field"}, {"selector": "input"}),
            ],
            tool_refs=["Browser Driver"],
            doc_refs=["api_doc:auth_service", "document:web_form_guidelines"],
        ),
        dict(
            name="validate_required_form_fields",
            description="Validate required fields before submitting a structured form and return field-level missing value diagnostics.",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            tags=["form", "validation", "document", "required-fields"],
            interface=iface(
                [
                    {"name": "required_fields", "type": "array", "description": "List of required field names", "required": True},
                    {"name": "form_data", "type": "object", "description": "Submitted form values", "required": True},
                ],
                [
                    {"name": "valid", "type": "boolean", "description": "Whether all required fields are present"},
                    {"name": "missing_fields", "type": "array", "description": "Missing required fields"},
                ],
                pre=["The form schema has been normalized"],
                post=["Missing field list is deterministic"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='required = input_data.get("required_fields", [])\nform = input_data.get("form_data", {})\nmissing = [field for field in required if not form.get(field)]\noutput["valid"] = len(missing) == 0\noutput["missing_fields"] = missing',
            ),
            test_cases=[
                testcase("all fields present", {"required_fields": ["username"], "form_data": {"username": "demo"}}, {"valid": True}),
                testcase("missing username", {"required_fields": ["username"], "form_data": {}}, {"valid": False}),
            ],
            doc_refs=["document:form_validation_spec", "document:web_form_guidelines"],
        ),
        dict(
            name="authenticate_and_fetch_profile",
            description="Authenticate a user through the Auth Service and fetch the profile with the returned session token.",
            skill_type=SkillType.FUNCTIONAL,
            domain="api",
            granularity_level=2,
            tags=["api", "auth", "profile", "api_doc", "agent-built"],
            interface=iface(
                [
                    {"name": "username", "type": "string", "description": "Login username", "required": True},
                    {"name": "password", "type": "string", "description": "Login password", "required": True},
                ],
                [
                    {"name": "authenticated", "type": "boolean", "description": "Authentication result"},
                    {"name": "profile", "type": "object", "description": "Fetched profile payload"},
                ],
                pre=["Auth Service API documentation is available"],
                post=["Profile request uses the token returned by login"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["authenticated"] = True\noutput["profile"] = {"username": input_data.get("username"), "source": "demo-auth-service"}',
                tool_calls=["HTTP Client"],
            ),
            test_cases=[
                testcase("valid credentials profile fetch", {"username": "demo", "password": "secret"}, {"authenticated": True}),
                testcase("invalid credentials rejection", {"username": "demo", "password": ""}, {"authenticated": False}),
            ],
            tool_refs=["HTTP Client"],
            doc_refs=["api_doc:auth_service_v1", "api_doc:auth_service"],
        ),
        dict(
            name="open_chrome_browser",
            description="Open Google Chrome on the host machine from a natural-language user command using an allowlisted host GUI launcher.",
            skill_type=SkillType.ATOMIC,
            domain="host",
            granularity_level=1,
            tags=["host", "browser", "chrome", "open", "launch", "gui", "application"],
            interface=iface(
                [
                    {
                        "name": "goal",
                        "type": "string",
                        "description": "Natural language request to open Chrome",
                    }
                ],
                [
                    {"name": "launched", "type": "boolean", "description": "Whether the host OS accepted the Chrome launch request"},
                    {"name": "application", "type": "string", "description": "Application requested from the host OS"},
                    {"name": "command", "type": "string", "description": "Allowlisted launcher command that was used"},
                ],
                pre=["User intent is to open Google Chrome", "Host GUI launcher is available"],
                post=["A Google Chrome launch request has been sent to the host OS"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["application"] = "Google Chrome"',
                tool_calls=["host.open_chrome"],
            ),
            tool_refs=["Host GUI Launcher"],
            trajectory_refs=["trajectory:open_chrome_browser_host_action"],
            doc_refs=["script:macos_open_chrome"],
            test_cases=[
                testcase(
                    "open chrome from natural language trajectory",
                    {"goal": "Please open the Chrome browser for me."},
                    {"launched": True, "application": "Google Chrome"},
                    tags=["validation", "trajectory"],
                )
            ],
        ),
        dict(
            name="open_application",
            description="Open a named host desktop application such as Chrome, Finder, or Terminal through the host GUI launcher.",
            skill_type=SkillType.ATOMIC,
            domain="host",
            granularity_level=1,
            tags=["host", "application", "open", "launch", "desktop", "gui"],
            interface=iface(
                [{"name": "application", "type": "string", "description": "Application name, for example Google Chrome", "required": False}],
                [
                    {"name": "launched", "type": "boolean"},
                    {"name": "application", "type": "string"},
                    {"name": "command", "type": "string"},
                ],
                pre=["Application name can be inferred or provided"],
                post=["The host OS receives an application launch request"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["application"] = input_data.get("application") or input_data.get("app_name")',
                tool_calls=["host.open_application"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:host_gui_action_policy", "api_doc:host_gui_launcher_api"],
            trajectory_refs=["trajectory:open_chrome_browser_host_action"],
        ),
        dict(
            name="run_terminal_command",
            description="Open Terminal and run a simple safe read-only command generated from the user's task, such as printenv, pwd, date, whoami, ls, or uname.",
            skill_type=SkillType.ATOMIC,
            domain="host",
            granularity_level=1,
            tags=["host", "terminal", "command", "shell", "environment", "printenv", "generic", "safe"],
            interface=iface(
                [
                    {"name": "goal", "type": "string", "description": "Natural language request for a simple terminal action"},
                    {"name": "command", "type": "string", "description": "Agent-generated safe command to run in Terminal", "required": False},
                ],
                [
                    {"name": "launched", "type": "boolean"},
                    {"name": "application", "type": "string"},
                    {"name": "command", "type": "string"},
                    {"name": "stdout_preview", "type": "string"},
                ],
                pre=["The requested operation is simple and can be represented as a safe read-only command", "Terminal is available on the host"],
                post=["Terminal is opened and the generated command is displayed/run"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["application"] = "Terminal"\noutput["command"] = input_data.get("command")',
                tool_calls=["host.run_terminal_command"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:host_gui_action_policy", "api_doc:host_gui_launcher_api", "script:macos_terminal_safe_command"],
            test_cases=[
                testcase(
                    "show environment variables in terminal",
                    {"goal": "打开终端显示环境变量", "command": "printenv"},
                    {"launched": True, "command": "printenv"},
                    tags=["validation", "terminal", "environment"],
                )
            ],
        ),
        dict(
            name="run_terminal_top_monitor",
            description="Open Terminal and run the top command for several seconds so the agent can demonstrate a live host process-monitoring action.",
            skill_type=SkillType.ATOMIC,
            domain="host",
            granularity_level=1,
            tags=["host", "terminal", "process", "top", "monitor", "realtime", "cpu", "runtime"],
            interface=iface(
                [
                    {"name": "goal", "type": "string", "description": "Natural language request to monitor live processes"},
                    {"name": "duration_seconds", "type": "integer", "description": "How long the runtime should keep the monitor visible", "required": False},
                ],
                [
                    {"name": "launched", "type": "boolean"},
                    {"name": "application", "type": "string"},
                    {"name": "command", "type": "string"},
                    {"name": "duration_seconds", "type": "integer"},
                ],
                pre=["User intent is to inspect live process or CPU activity", "Terminal is available on the host"],
                post=["Terminal is opened and top is running or has run for the requested observation window"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["application"] = "Terminal"\noutput["command"] = "top"',
                tool_calls=["host.run_terminal_top"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:host_gui_action_policy", "api_doc:host_gui_launcher_api", "script:macos_terminal_top_monitor"],
            trajectory_refs=["trajectory:terminal_top_monitor"],
            test_cases=[
                testcase(
                    "open terminal top process monitor",
                    {"goal": "Open Terminal and run top to monitor live processes for 10 seconds.", "duration_seconds": 10},
                    {"launched": True, "application": "Terminal"},
                    tags=["validation", "trajectory", "host"],
                )
            ],
        ),
        dict(
            name="open_url_in_chrome",
            description="Open Google Chrome and navigate directly to a requested URL such as OpenAI, ChatGPT, GitHub, or a provided website.",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            tags=["host", "browser", "chrome", "url", "website", "openai", "chatgpt"],
            interface=iface(
                [{"name": "url", "type": "string", "description": "Target URL; can be inferred from the goal", "required": False}],
                [
                    {"name": "launched", "type": "boolean"},
                    {"name": "url", "type": "string"},
                    {"name": "command", "type": "string"},
                ],
                pre=["Target URL is provided or inferable"],
                post=["Chrome is launched with the target URL"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["url"] = input_data.get("url")',
                tool_calls=["host.open_url_in_chrome"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:website_navigation_guidelines", "api_doc:chrome_url_launch_api"],
            trajectory_refs=["trajectory:open_chatgpt_url"],
        ),
        dict(
            name="open_first_search_result",
            description="Search the web for a user-provided query and open a target result in Chrome. The target can be the first result, an official result, a result containing a hint, or another agent-selected match.",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            granularity_level=2,
            tags=["host", "browser", "chrome", "search", "target-result", "first-result", "official", "website", "generic"],
            interface=iface(
                [
                    {"name": "query", "type": "string", "description": "Search query inferred from the user task", "required": True},
                    {"name": "target_hint", "type": "string", "description": "Optional target selector such as first, official, Login, or a domain/title hint", "required": False},
                    {"name": "result_rank", "type": "integer", "description": "Optional 1-based result rank; defaults to first when unspecified", "required": False},
                ],
                [
                    {"name": "launched", "type": "boolean"},
                    {"name": "query", "type": "string"},
                    {"name": "search_url", "type": "string"},
                ],
                pre=["The task asks to open a search result matching a target condition"],
                post=["Chrome is launched with a search-target result URL or browser controller workflow"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["query"] = input_data.get("query")',
                tool_calls=["host.open_search_first_result"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:website_navigation_guidelines", "api_doc:chrome_url_launch_api", "script:google_first_result_url"],
            test_cases=[
                testcase(
                    "open target result for a query",
                    {"query": "国科大夏令营", "target_hint": "official", "result_rank": 1},
                    {"launched": True},
                    tags=["validation", "search", "target-result"],
                )
            ],
        ),
        dict(
            name="capture_browser_page_observation",
            description="Capture the current browser page state for an agent loop, including visible text, candidate controls, screenshot or DOM evidence when available, and the last action result.",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            tags=["web", "browser", "gui", "observation", "screenshot", "dom", "atomic"],
            interface=iface(
                [
                    {"name": "goal", "type": "string", "description": "Original user goal guiding what to observe", "required": True},
                    {"name": "round", "type": "integer", "description": "Current observation round", "required": False},
                ],
                [
                    {"name": "visible_text", "type": "string", "description": "Visible page text or compact summary"},
                    {"name": "candidate_elements", "type": "array", "description": "Clickable/input candidates relevant to the goal"},
                    {"name": "evidence_type", "type": "string", "description": "screenshot/dom/stdout/simulated"},
                ],
                pre=["A browser page is open or launchable for the task"],
                post=["The agent receives enough page-state evidence to decide the next action"],
            ),
            implementation=SkillImplementation(
                language="host_tool",
                prompt_template=(
                    "Observe the current browser state for {goal}. Prefer DOM and visible text; "
                    "use screenshot evidence when DOM is unavailable. Return concise evidence and action candidates."
                ),
                tool_calls=["host.browser_gui_workflow"],
            ),
            tool_refs=["Browser GUI Observer"],
            doc_refs=["document:browser_gui_observation_loop", "api_doc:browser_gui_workflow_api"],
            trajectory_refs=["trajectory:interactive_browser_mail_sent"],
            test_cases=[
                testcase(
                    "observe browser mail search state",
                    {"goal": "Find a university mail service and open Sent mail.", "round": 1},
                    {"evidence_type": "simulated"},
                    tags=["validation", "browser", "observation"],
                )
            ],
        ),
        dict(
            name="choose_next_browser_action",
            description="Choose the next browser GUI action from an observation and the user goal, such as click a search result, type a query, use cached login, open a mailbox folder, or stop when the goal is satisfied.",
            skill_type=SkillType.ATOMIC,
            domain="web",
            granularity_level=1,
            tags=["web", "browser", "gui", "decision", "click", "type", "atomic"],
            interface=iface(
                [
                    {"name": "goal", "type": "string", "description": "Original user goal", "required": True},
                    {"name": "observation", "type": "object", "description": "Current page observation", "required": True},
                    {"name": "retrieved_skills", "type": "array", "description": "Candidate skills and graph context", "required": False},
                ],
                [
                    {"name": "action_type", "type": "string", "description": "click/type/navigate/search/wait/stop"},
                    {"name": "target", "type": "string", "description": "Selector, element description, URL, or query"},
                    {"name": "reason", "type": "string", "description": "Why this action advances the goal"},
                ],
                pre=["An observation has been captured"],
                post=["A single next action or stop decision is available for the browser controller"],
            ),
            implementation=SkillImplementation(
                language="natural_language",
                prompt_template=(
                    "Given user goal {goal}, current observation {observation}, and retrieved skill hints "
                    "{retrieved_skills}, choose exactly one next browser action. If the retrieved skill is only "
                    "partially relevant, adapt its idea instead of copying its fixed URL or fixed target."
                ),
                sub_skill_ids=["capture_browser_page_observation"],
            ),
            tool_refs=["Browser GUI Observer", "LLM Client"],
            doc_refs=["document:browser_gui_observation_loop"],
            trajectory_refs=["trajectory:interactive_browser_mail_sent"],
            component_ids=["capture_browser_page_observation"],
        ),
        dict(
            name="browser_gui_observe_and_act",
            description="Run one bounded browser observation-decision-action step for an interactive web task, using graph skills as hints while keeping the user goal as the source of truth.",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            granularity_level=2,
            tags=["web", "browser", "gui", "observe", "act", "functional", "agent-loop"],
            interface=iface(
                [
                    {"name": "goal", "type": "string", "description": "Original user task", "required": True},
                    {"name": "query", "type": "string", "description": "Search query or service name inferred by the agent", "required": False},
                    {"name": "max_rounds", "type": "integer", "description": "Maximum observe-act rounds", "required": False},
                ],
                [
                    {"name": "success", "type": "boolean"},
                    {"name": "observations", "type": "array"},
                    {"name": "actions", "type": "array"},
                    {"name": "requires_visual_controller", "type": "boolean"},
                ],
                pre=["The task requires visible browser interaction beyond direct URL navigation"],
                post=["The runtime records browser observations and selected actions with explicit evidence"],
            ),
            implementation=SkillImplementation(
                language="host_tool",
                prompt_template=(
                    "For {goal}, launch or reuse Chrome, search/navigate to {query}, then iterate: observe visible "
                    "state, choose the next action, execute if supported, and validate progress. Do not let a fixed "
                    "URL skill override the task when it is only a weak hint."
                ),
                tool_calls=["host.browser_gui_workflow"],
                sub_skill_ids=["capture_browser_page_observation", "choose_next_browser_action"],
                execution_order=["capture_browser_page_observation", "choose_next_browser_action"],
            ),
            tool_refs=["Browser GUI Observer", "Host GUI Launcher"],
            doc_refs=["document:browser_gui_observation_loop", "api_doc:browser_gui_workflow_api", "script:browser_gui_observe_decide_act_loop"],
            trajectory_refs=["trajectory:interactive_browser_mail_sent"],
            component_ids=["capture_browser_page_observation", "choose_next_browser_action", "open_first_search_result"],
            test_cases=[
                testcase(
                    "browser GUI search login and open sent",
                    {"goal": "打开浏览器，找到哈尔滨工业大学邮箱并登录，打开已发送", "query": "哈尔滨工业大学 邮箱 登录", "max_rounds": 4},
                    {"requires_visual_controller": True},
                    tags=["validation", "browser", "gui", "mail"],
                )
            ],
        ),
        dict(
            name="complete_interactive_browser_workflow",
            description="Complete a high-level interactive browser task by decomposing the goal into search/navigation, page observation, adaptive browser actions, and validation against the visible final state.",
            skill_type=SkillType.STRATEGIC,
            domain="web",
            granularity_level=3,
            tags=["strategic", "web", "browser", "gui", "login", "search", "workflow", "agent-loop"],
            interface=iface(
                [
                    {"name": "goal", "type": "string", "description": "High-level browser task", "required": True},
                    {"name": "query", "type": "string", "description": "Initial search query generated from the goal", "required": False},
                    {"name": "max_rounds", "type": "integer", "description": "Maximum observe-act rounds", "required": False},
                ],
                [
                    {"name": "success", "type": "boolean"},
                    {"name": "final_state", "type": "object"},
                    {"name": "observations", "type": "array"},
                    {"name": "actions", "type": "array"},
                ],
                pre=["The desired outcome depends on visible web-page interaction"],
                post=["The agent either reaches the requested visible page state or reports what visual controller capability is missing"],
            ),
            implementation=SkillImplementation(
                language="host_tool",
                prompt_template=(
                    "Use this strategic pattern for browser GUI tasks: decompose the user's goal, retrieve helpful "
                    "skills/documents, adapt only relevant skill logic, run browser_gui_observe_and_act for bounded "
                    "rounds, compare observations to the goal, and persist a generalized skill if the workflow is reusable."
                ),
                tool_calls=["host.browser_gui_workflow"],
                sub_skill_ids=["browser_gui_observe_and_act"],
            ),
            tool_refs=["Browser GUI Observer", "Host GUI Launcher", "LLM Client"],
            doc_refs=["document:browser_gui_observation_loop", "document:website_navigation_guidelines", "api_doc:browser_gui_workflow_api"],
            trajectory_refs=["trajectory:interactive_browser_mail_sent"],
            component_ids=["browser_gui_observe_and_act", "capture_browser_page_observation", "choose_next_browser_action", "open_first_search_result"],
            test_cases=[
                testcase(
                    "complete university mail sent workflow",
                    {"goal": "Open browser, find HIT email, log in using cached credentials, and open Sent mail.", "max_rounds": 5},
                    {"success": False},
                    tags=["validation", "strategic", "browser", "observation-loop"],
                )
            ],
        ),
        dict(
            name="open_local_file",
            description="Open a local file or folder on the host machine using the OS default application.",
            skill_type=SkillType.ATOMIC,
            domain="host",
            granularity_level=1,
            tags=["host", "file", "finder", "open", "folder", "desktop"],
            interface=iface(
                [{"name": "path", "type": "string", "description": "Absolute or home-relative file/folder path", "required": True}],
                [
                    {"name": "launched", "type": "boolean"},
                    {"name": "path", "type": "string"},
                ],
                pre=["The file or folder exists on the host machine"],
                post=["The OS receives an open-file request"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["path"] = input_data.get("path")',
                tool_calls=["host.open_file"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:host_gui_action_policy", "api_doc:host_gui_launcher_api"],
        ),
        dict(
            name="open_or_create_desktop_file_in_vscode",
            description="Check whether a requested Desktop file exists, create it if missing, and open it in Visual Studio Code using the Terminal/code workflow when available.",
            skill_type=SkillType.FUNCTIONAL,
            domain="host",
            granularity_level=2,
            tags=["host", "terminal", "code", "vscode", "visual-studio-code", "file", "desktop", "create", "open", "workflow"],
            interface=iface(
                [
                    {"name": "path", "type": "string", "description": "Absolute or home-relative file path; defaults to a Desktop file inferred from the task", "required": False},
                    {"name": "filename", "type": "string", "description": "Desktop filename when path is omitted", "required": False},
                    {"name": "goal", "type": "string", "description": "Natural language request", "required": False},
                ],
                [
                    {"name": "success", "type": "boolean"},
                    {"name": "launched", "type": "boolean"},
                    {"name": "path", "type": "string"},
                    {"name": "created", "type": "boolean"},
                    {"name": "existed_before", "type": "boolean"},
                    {"name": "command", "type": "string"},
                ],
                pre=["The user wants a local file opened in VS Code", "Desktop is writable when the file must be created"],
                post=["The requested file exists and a VS Code open request has been sent for that path"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["path"] = input_data.get("path")\noutput["application"] = "Visual Studio Code"',
                tool_calls=["host.open_or_create_file_in_vscode"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:host_gui_action_policy", "api_doc:host_gui_launcher_api", "script:macos_vscode_file_workflow"],
            trajectory_refs=["trajectory:vscode_open_or_create_desktop_file"],
            component_ids=["run_terminal_command", "open_local_file"],
            test_cases=[
                testcase(
                    "open or create Desktop text file in VS Code",
                    {"goal": "Use Terminal code to open Desktop 111.txt in VS Code; create it if missing.", "path": str(Path.home() / "Desktop" / "111.txt")},
                    {"success": True, "application": "Visual Studio Code"},
                    tags=["validation", "vscode", "desktop", "file"],
                )
            ],
        ),
        dict(
            name="complete_vscode_desktop_file_workflow",
            description="Complete the full desktop workflow for opening or creating a requested Desktop file in VS Code through a Terminal/code-style flow.",
            skill_type=SkillType.STRATEGIC,
            domain="desktop",
            granularity_level=3,
            tags=["strategic", "host", "terminal", "code", "vscode", "file", "desktop", "create", "workflow"],
            interface=iface(
                [
                    {"name": "path", "type": "string", "description": "Absolute Desktop file path to open or create", "required": False},
                    {"name": "filename", "type": "string", "description": "Desktop filename when path is omitted", "required": False},
                    {"name": "goal", "type": "string", "description": "Natural language task", "required": False},
                ],
                [
                    {"name": "success", "type": "boolean"},
                    {"name": "path", "type": "string"},
                    {"name": "created", "type": "boolean"},
                    {"name": "application", "type": "string"},
                ],
                pre=["The user objective is a VS Code local file workflow, not a web/search task"],
                post=["The requested Desktop file exists and is opened in VS Code"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["workflow"] = "vscode_desktop_file"\noutput["application"] = "Visual Studio Code"',
                tool_calls=["host.open_or_create_file_in_vscode"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:host_gui_action_policy", "api_doc:host_gui_launcher_api", "script:macos_vscode_file_workflow"],
            trajectory_refs=["trajectory:vscode_open_or_create_desktop_file"],
            component_ids=["open_or_create_desktop_file_in_vscode", "run_terminal_command", "open_local_file"],
            test_cases=[
                testcase(
                    "complete VS Code Desktop file workflow",
                    {"goal": "Open Desktop 111.txt in VS Code through Terminal code; create it if missing.", "path": str(Path.home() / "Desktop" / "111.txt")},
                    {"success": True, "application": "Visual Studio Code"},
                    tags=["validation", "strategic", "vscode", "file"],
                )
            ],
        ),
        dict(
            name="save_text_to_downloads",
            description="Create or overwrite a UTF-8 text file in the user's Downloads folder with a provided or inferred answer.",
            skill_type=SkillType.ATOMIC,
            domain="file",
            granularity_level=1,
            tags=["host", "file", "downloads", "save", "write", "answer"],
            interface=iface(
                [
                    {"name": "filename", "type": "string", "description": "Downloads filename", "required": False},
                    {"name": "content", "type": "string", "description": "Text content to save", "required": False},
                ],
                [
                    {"name": "path", "type": "string"},
                    {"name": "bytes_written", "type": "integer"},
                ],
                pre=["Downloads folder is writable"],
                post=["A text artifact exists in Downloads"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["filename"] = input_data.get("filename")',
                tool_calls=["host.write_downloads_text_file"],
            ),
            tool_refs=["Downloads File Writer"],
            doc_refs=["document:downloads_file_output_contract", "api_doc:downloads_file_writer_api"],
            trajectory_refs=["trajectory:chatgpt_weather_answer_saved"],
        ),
        dict(
            name="open_chatgpt_conversation",
            description="Open Chrome and navigate to the ChatGPT conversation interface for website-based GPT interaction.",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            granularity_level=2,
            tags=["website", "chrome", "chatgpt", "gpt", "openai", "conversation", "functional"],
            interface=iface(
                [{"name": "goal", "type": "string", "description": "Natural language navigation request"}],
                [
                    {"name": "launched", "type": "boolean"},
                    {"name": "url", "type": "string"},
                ],
                pre=["Host Chrome is available"],
                post=["ChatGPT conversation page is opened in Chrome"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["url"] = "https://chatgpt.com/"',
                tool_calls=["host.open_url_in_chrome"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:website_navigation_guidelines", "api_doc:chrome_url_launch_api"],
            trajectory_refs=["trajectory:open_chatgpt_url"],
            component_ids=["open_chrome_browser", "open_url_in_chrome"],
        ),
        dict(
            name="open_downloads_folder",
            description="Open the user's Downloads folder in Finder or the host file manager.",
            skill_type=SkillType.FUNCTIONAL,
            domain="host",
            granularity_level=2,
            tags=["host", "finder", "downloads", "folder", "open"],
            interface=iface(
                [{"name": "goal", "type": "string", "description": "Natural language request"}],
                [{"name": "path", "type": "string"}, {"name": "launched", "type": "boolean"}],
                pre=["Downloads folder exists"],
                post=["Downloads folder is opened"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True',
                tool_calls=["host.open_downloads_folder"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:host_gui_action_policy"],
        ),
        dict(
            name="complete_gpt_weather_note_task",
            description="Complete a desktop task flow: open ChatGPT in Chrome, prepare a weather-style question, and save the answer note into Downloads.",
            skill_type=SkillType.STRATEGIC,
            meta_category="generation",
            domain="desktop",
            granularity_level=3,
            tags=["strategic", "chatgpt", "gpt", "weather", "downloads", "answer", "workflow", "file"],
            interface=iface(
                [
                    {"name": "question", "type": "string", "description": "Question to ask or encode in the saved note", "required": False},
                    {"name": "filename", "type": "string", "description": "Downloads filename", "required": False},
                ],
                [
                    {"name": "opened_url", "type": "string"},
                    {"name": "saved_path", "type": "string"},
                    {"name": "answer_preview", "type": "string"},
                ],
                pre=["Chrome and Downloads folder are available"],
                post=["ChatGPT is opened and an answer artifact is saved to Downloads"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["workflow"] = "chatgpt_weather_note"',
                tool_calls=["host.complete_chatgpt_note_task"],
            ),
            tool_refs=["Host GUI Launcher", "Downloads File Writer"],
            doc_refs=["document:website_navigation_guidelines", "document:downloads_file_output_contract", "api_doc:downloads_file_writer_api"],
            trajectory_refs=["trajectory:chatgpt_weather_answer_saved"],
            component_ids=["open_chatgpt_conversation", "save_text_to_downloads"],
        ),
        dict(
            name="read_local_text_file",
            description="Resolve and read a local UTF-8 text file path so its content can be reused by a higher-level document or editing workflow.",
            skill_type=SkillType.ATOMIC,
            domain="file",
            granularity_level=1,
            tags=["host", "file", "text", "read", "desktop", "atomic"],
            interface=iface(
                [
                    {"name": "path", "type": "string", "description": "Absolute or home-relative .txt source path", "required": True},
                ],
                [
                    {"name": "path", "type": "string"},
                    {"name": "content", "type": "string"},
                    {"name": "bytes_read", "type": "integer"},
                ],
                pre=["The source text file exists and is readable"],
                post=["Text content is available for downstream document steps"],
            ),
            implementation=SkillImplementation(
                language="natural_language",
                prompt_template="Resolve {path}, verify it exists, and read its UTF-8 text for the next workflow step.",
            ),
            doc_refs=["document:desktop_document_workflow_contract"],
            trajectory_refs=["trajectory:wps_text_file_document_saved"],
        ),
        dict(
            name="create_blank_wps_document",
            description="Open WPS Office and prepare a blank document surface for text insertion.",
            skill_type=SkillType.ATOMIC,
            domain="document",
            granularity_level=1,
            tags=["host", "wps", "document", "blank", "open", "atomic"],
            interface=iface(
                [{"name": "application", "type": "string", "description": "Preferred document application", "required": False}],
                [{"name": "launched", "type": "boolean"}, {"name": "application", "type": "string"}],
                pre=["WPS Office or a default document app is available"],
                post=["A document application is opened or selected"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["launched"] = True\noutput["application"] = input_data.get("application") or "WPS Office"',
                tool_calls=["host.open_application"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:desktop_document_workflow_contract", "api_doc:host_gui_launcher_api"],
            trajectory_refs=["trajectory:wps_text_file_document_saved"],
        ),
        dict(
            name="insert_text_into_document",
            description="Insert prepared text content into the active document surface, normally after WPS or another editor is ready.",
            skill_type=SkillType.ATOMIC,
            domain="document",
            granularity_level=1,
            tags=["document", "text", "copy", "paste", "insert", "atomic"],
            interface=iface(
                [{"name": "content", "type": "string", "description": "Text content to insert", "required": True}],
                [{"name": "inserted", "type": "boolean"}, {"name": "content_preview", "type": "string"}],
                pre=["A destination document is active"],
                post=["The requested text exists in the document buffer"],
            ),
            implementation=SkillImplementation(
                language="natural_language",
                prompt_template="Insert the prepared text into the active document and preserve line breaks.",
            ),
            doc_refs=["document:desktop_document_workflow_contract"],
            trajectory_refs=["trajectory:wps_text_file_document_saved"],
        ),
        dict(
            name="save_document_to_desktop",
            description="Save the current document artifact to a parameterized Desktop output path.",
            skill_type=SkillType.ATOMIC,
            domain="document",
            granularity_level=1,
            tags=["host", "document", "desktop", "save", "output", "atomic"],
            interface=iface(
                [
                    {"name": "output_path", "type": "string", "description": "Desktop output document path", "required": False},
                    {"name": "document_format", "type": "string", "description": "rtf/doc/docx/txt", "required": False},
                ],
                [{"name": "output_path", "type": "string"}, {"name": "bytes_written", "type": "integer"}],
                pre=["Document content is available"],
                post=["A document artifact exists at the requested Desktop path"],
            ),
            implementation=SkillImplementation(
                language="natural_language",
                prompt_template="Save the current document to {output_path}, defaulting to an RTF document on Desktop.",
            ),
            doc_refs=["document:desktop_document_workflow_contract"],
            trajectory_refs=["trajectory:wps_text_file_document_saved"],
        ),
        dict(
            name="create_wps_document_from_text_file",
            description="Create a WPS-openable document from a source .txt file by reading the text, writing a Desktop RTF artifact, and opening it in WPS Office.",
            skill_type=SkillType.FUNCTIONAL,
            domain="document",
            granularity_level=2,
            tags=["host", "wps", "document", "text-file", "desktop", "functional", "workflow"],
            interface=iface(
                [
                    {"name": "source_path", "type": "string", "description": "Source .txt file to read", "required": False},
                    {"name": "output_path", "type": "string", "description": "Output document path, usually on Desktop", "required": False},
                    {"name": "goal", "type": "string", "description": "Original user task for path inference", "required": False},
                ],
                [
                    {"name": "success", "type": "boolean"},
                    {"name": "source_path", "type": "string"},
                    {"name": "output_path", "type": "string"},
                    {"name": "bytes_written", "type": "integer"},
                    {"name": "application", "type": "string"},
                ],
                pre=["The agent can resolve the source text file path"],
                post=["A WPS-openable document containing the source text exists at output_path"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["source_path"] = input_data.get("source_path") or input_data.get("path")\noutput["output_path"] = input_data.get("output_path")',
                tool_calls=["host.create_wps_document_from_text_file"],
                sub_skill_ids=["read_local_text_file", "create_blank_wps_document", "insert_text_into_document", "save_document_to_desktop"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:desktop_document_workflow_contract", "api_doc:wps_document_generator_api", "script:python_create_wps_rtf_document"],
            trajectory_refs=["trajectory:wps_text_file_document_saved"],
            component_ids=["read_local_text_file", "create_blank_wps_document", "insert_text_into_document", "save_document_to_desktop"],
            test_cases=[
                testcase(
                    "create WPS document from Desktop text file",
                    {"goal": "Open WPS, create a blank document, copy Desktop 111.txt into it, and save to Desktop.", "source_path": str(Path.home() / "Desktop" / "111.txt")},
                    {"success": True, "application": "WPS Office"},
                    tags=["validation", "wps", "document", "desktop"],
                )
            ],
        ),
        dict(
            name="complete_wps_text_file_document_workflow",
            description="Complete the full host document workflow: open WPS, create a blank document, copy the content of a resolved Desktop text file, and save the generated document to Desktop.",
            skill_type=SkillType.STRATEGIC,
            domain="desktop",
            granularity_level=3,
            tags=["strategic", "host", "wps", "document", "text-file", "desktop", "workflow"],
            interface=iface(
                [
                    {"name": "source_path", "type": "string", "description": "Source text file, e.g. ~/Desktop/111.txt", "required": False},
                    {"name": "output_path", "type": "string", "description": "Target document path on Desktop", "required": False},
                    {"name": "goal", "type": "string", "description": "Natural language document task", "required": False},
                ],
                [
                    {"name": "success", "type": "boolean"},
                    {"name": "source_path", "type": "string"},
                    {"name": "output_path", "type": "string"},
                    {"name": "application", "type": "string"},
                ],
                pre=["The task is a document creation/editing workflow, not a browser navigation task"],
                post=["The target Desktop document is created and opened for the user"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True\noutput["workflow"] = "wps_text_file_document"',
                tool_calls=["host.create_wps_document_from_text_file"],
                sub_skill_ids=["create_wps_document_from_text_file"],
            ),
            tool_refs=["Host GUI Launcher"],
            doc_refs=["document:desktop_document_workflow_contract", "api_doc:wps_document_generator_api", "script:python_create_wps_rtf_document"],
            trajectory_refs=["trajectory:wps_text_file_document_saved"],
            component_ids=["create_wps_document_from_text_file", "read_local_text_file", "create_blank_wps_document", "insert_text_into_document", "save_document_to_desktop"],
            test_cases=[
                testcase(
                    "complete WPS Desktop document workflow",
                    {"goal": "Open WPS, create a blank document, copy Desktop 111.txt into it, and save the document to Desktop."},
                    {"success": True, "application": "WPS Office"},
                    tags=["validation", "strategic", "wps", "document"],
                )
            ],
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

    def enrich_defaults(data: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(data)
        enriched.setdefault("domain", "skillos" if "meta" in enriched.get("tags", []) else "general")
        enriched.setdefault("granularity_level", 3 if enriched.get("skill_type") == SkillType.STRATEGIC else 1)
        enriched.setdefault("visibility", "kernel" if "meta" in enriched.get("tags", []) else "user")
        enriched.setdefault("tool_refs", ["LLM Client"] if enriched.get("skill_type") == SkillType.STRATEGIC else [])
        enriched.setdefault("doc_refs", ["docs/modules/04-self-management-agents.md"] if enriched.get("skill_type") == SkillType.STRATEGIC else [])
        enriched.setdefault("trajectory_refs", [])
        if not enriched.get("test_cases"):
            enriched["test_cases"] = [
                testcase(
                    f"{enriched['name']} runtime validation",
                    {"runtime_input": True},
                    {"ok": True},
                    tags=["validation", "runtime"],
                )
            ]
        return enriched

    for data in [enrich_defaults(item) for item in demos + meta_skills]:
        skill = Skill(
            **data,
            provenance=SkillProvenance(source_type="static_seed", created_by_agent="system"),
        )
        skill.transition_to(SkillState.VERIFIED)
        skill.transition_to(SkillState.RELEASED)

        for _ in range(20):
            skill.record_execution(success=True, latency_ms=120.0)

        for _ in range(2):
            skill.record_execution(success=False, latency_ms=500.0)

        existing = await wiki.get_by_name(skill.name, skill.version) if hasattr(wiki, "get_by_name") else None
        if existing:
            object.__setattr__(skill, "skill_id", existing.skill_id)
            object.__setattr__(skill, "created_at", existing.created_at)
            await wiki.update(
                existing.skill_id,
                name=skill.name,
                version=skill.version,
                display_name=skill.display_name,
                description=skill.description,
                tags=skill.tags,
                skill_type=skill.skill_type,
                meta_category=skill.meta_category,
                domain=skill.domain,
                granularity_level=skill.granularity_level,
                visibility=skill.visibility,
                state=skill.state,
                interface=skill.interface,
                implementation=skill.implementation,
                test_cases=skill.test_cases,
                test_trajectory_ids=skill.test_trajectory_ids,
                tool_refs=skill.tool_refs,
                trajectory_refs=skill.trajectory_refs,
                doc_refs=skill.doc_refs,
                metrics=skill.metrics,
                provenance=skill.provenance,
                dependency_ids=skill.dependency_ids,
                component_ids=skill.component_ids,
            )
        else:
            try:
                await wiki.create(skill)
            except ValueError:
                pass


def create_app(
    api_key: str,
    model: str = "gpt-5.4",
    *,
    api_url: str = "https://yunwu.ai",
    repository_backend: str = "git",
    skill_storage_dir: Optional[Path] = None,
    embedding_cache_path: Optional[Path] = None,
    seed_demo: bool = True,
) -> FastAPI:
    from ..config.llm_config import LLMConfig

    llm_cfg = LLMConfig(api_key=api_key, api_url=api_url, model=model)

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
    app.state.embedding_cache_path = (embedding_cache_path or _default_embedding_cache_path()).resolve()
    app.state.seed_demo = seed_demo

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(exc),
            },
        )

    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(lifecycle.router, prefix="/api/v1")
    app.include_router(graph.router, prefix="/api/v1")
    app.include_router(execution.router, prefix="/api/v1")
    app.include_router(evolution.router, prefix="/api/v1")
    app.include_router(ingest.router, prefix="/api/v1")
    app.include_router(host_info.router, prefix="/api/v1")
    app.include_router(repository.router, prefix="/api/v1")
    app.include_router(ws.router)

    @app.get("/")
    async def root() -> Dict[str, str]:
        return {
            "name": "SkillOS",
            "version": "1.0.0",
            "status": "running",
        }

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {
            "status": "ok",
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="SkillOS API Server")
    parser.add_argument(
        "--api-key",
        default=os.getenv("SKILLOS_API_KEY") or os.getenv("YUNWU_API_KEY") or DEFAULT_YUNWU_API_KEY,
        help="OpenAI-compatible API key. Defaults to SKILLOS_API_KEY/YUNWU_API_KEY, then the local demo default.",
    )
    parser.add_argument("--model", default=os.getenv("SKILLOS_MODEL", "gpt-5.4"))
    parser.add_argument("--api-url", default=os.getenv("SKILLOS_API_URL", "https://yunwu.ai/v1"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--repository-backend", choices=["git", "memory"], default="git")
    parser.add_argument("--skill-storage-dir", default=None)
    parser.add_argument("--embedding-cache-path", default=None)
    parser.add_argument("--no-seed-demo", action="store_true")
    args = parser.parse_args()

    storage_dir = Path(args.skill_storage_dir).resolve() if args.skill_storage_dir else None
    embedding_cache_path = Path(args.embedding_cache_path).resolve() if args.embedding_cache_path else None
    app = create_app(
        api_key=args.api_key,
        api_url=args.api_url,
        model=args.model,
        repository_backend=args.repository_backend,
        skill_storage_dir=storage_dir,
        embedding_cache_path=embedding_cache_path,
        seed_demo=not args.no_seed_demo,
    )
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
