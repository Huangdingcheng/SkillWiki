"""FastAPI 应用依赖注入 — 共享组件单例。"""

from __future__ import annotations

from typing import Any, Optional

from ..layers.feedback_evolution import EvolutionEngine, SkillMonitor, SkillRepair
from ..layers.input_knowledge import ExperiencePipeline
from ..layers.skill_governance import SkillMerger, SkillReviewer, VersionController
from ..layers.skill_management import (
    MetaControllerAgent,
    SkillAuditorAgent,
    SkillBuilderAgent,
    SkillLibrarianAgent,
    SkillMaintainerAgent,
)
from ..layers.skill_runtime import (
    CompositionAgent,
    ReflectionAgent,
    SkillExecutor,
    SkillPlanner,
    SkillRetriever,
    StateTracker,
    VerifierAgent,
)
from ..utils.llm_client import LLMClient


class AppState:
    """全局应用状态，持有所有共享组件。"""

    def __init__(self) -> None:
        self.llm: Optional[LLMClient] = None
        self.wiki: Optional[Any] = None
        self.graph: Optional[Any] = None
        self.search: Optional[Any] = None
        self.monitor: Optional[SkillMonitor] = None
        self.repair: Optional[SkillRepair] = None
        self.merger: Optional[SkillMerger] = None
        self.reviewer: Optional[SkillReviewer] = None
        self.version_ctrl: Optional[VersionController] = None
        self.retriever: Optional[SkillRetriever] = None
        self.planner: Optional[SkillPlanner] = None
        self.executor: Optional[SkillExecutor] = None
        self.composer: Optional[CompositionAgent] = None
        self.verifier: Optional[VerifierAgent] = None
        self.reflector: Optional[ReflectionAgent] = None
        self.builder: Optional[SkillBuilderAgent] = None
        self.auditor: Optional[SkillAuditorAgent] = None
        self.maintainer: Optional[SkillMaintainerAgent] = None
        self.librarian: Optional[SkillLibrarianAgent] = None
        self.meta_controller: Optional[MetaControllerAgent] = None
        self.pipeline: Optional[ExperiencePipeline] = None
        self.evolution: Optional[EvolutionEngine] = None
        self.state_tracker: StateTracker = StateTracker(task_id="session")
        self.pg_conn: Optional[Any] = None           # PostgresConnection
        self.execution_history_repo: Optional[Any] = None  # ExecutionHistoryRepository
         
    def initialize(self, llm: LLMClient, wiki: Any, graph: Any) -> None:
        from .memory_store import MemorySearchEngine
        self.llm = llm
        self.wiki = wiki
        self.graph = graph
        self.search = MemorySearchEngine(wiki)
        self.monitor = SkillMonitor()
        self.repair = SkillRepair(llm)
        self.merger = SkillMerger(llm)
        self.reviewer = SkillReviewer(llm)
        self.version_ctrl = VersionController()
        self.retriever = SkillRetriever(llm_client=llm, search_engine=self.search)
        self.planner = SkillPlanner(llm_client=llm)
        self.executor = SkillExecutor(skill_registry=wiki, llm_client=llm)
        self.composer = CompositionAgent(llm_client=llm)
        self.verifier = VerifierAgent(llm_client=llm)
        self.reflector = ReflectionAgent(llm_client=llm)
        self.builder = SkillBuilderAgent(llm_client=llm)
        self.auditor = SkillAuditorAgent(llm_client=llm)
        self.maintainer = SkillMaintainerAgent(llm_client=llm)
        self.librarian = SkillLibrarianAgent(
            wiki_manager=wiki,
            graph_manager=graph,
            version_controller=self.version_ctrl,
        )
        self.meta_controller = MetaControllerAgent(
            builder=self.builder,
            auditor=self.auditor,
            maintainer=self.maintainer,
            librarian=self.librarian,
        )
        self.pipeline = ExperiencePipeline(llm_client=llm)
        self.evolution = EvolutionEngine(
            monitor=self.monitor,
            repair=self.repair,
            merger=self.merger,
            wiki_manager=wiki,
            graph_manager=graph,
        )


app_state = AppState()


def get_app_state() -> AppState:
    return app_state
