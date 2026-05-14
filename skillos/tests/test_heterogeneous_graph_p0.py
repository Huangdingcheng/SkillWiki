from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillos.api.deps import app_state
from skillos.api.main import _seed_demo_heterogeneous_graph
from skillos.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillos.api.routes import graph
from skillos.models.graph_model import (
    ExecutionGraphNode,
    GraphNodeKind,
    HeteroEdgeType,
    HeteroGraphEdge,
    HeteroSkillNode,
    HeteroNodeKind,
    SkillEdge,
    SourceGraphNode,
    ValidationGraphNode,
    VersionGraphNode,
)
from skillos.models.skill_model import EdgeType, Skill, SkillImplementation, SkillState, SkillType


def make_skill(name: str, skill_id: str | None = None) -> Skill:
    kwargs = {}
    if skill_id:
        kwargs["skill_id"] = skill_id
    return Skill(
        **kwargs,
        name=name,
        description=f"{name} handles browser automation",
        state=SkillState.RELEASED,
        skill_type=SkillType.ATOMIC,
        tags=["web"],
        domain="browser",
        implementation=SkillImplementation(
            language="python",
            prompt_template=f"Run {name}",
        ),
    )


async def seed_projection_fixture(graph_mgr: MemoryGraphManager) -> None:
    shared_source = await graph_mgr.add_hetero_node(
        SourceGraphNode(
            node_id="shared_source",
            name="Shared Source",
            source_uri="trajectory://shared-source",
        )
    )
    alpha = await graph_mgr.add_hetero_node(
        HeteroSkillNode(
            node_id="skill_alpha_node",
            name="skill_alpha",
            skill_id="skill-alpha",
            skill_version="2.0.0",
        )
    )
    beta = await graph_mgr.add_hetero_node(
        HeteroSkillNode(
            node_id="skill_beta_node",
            name="skill_beta",
            skill_id="skill-beta",
            skill_version="1.0.0",
        )
    )
    version = await graph_mgr.add_hetero_node(
        VersionGraphNode(
            node_id="version_alpha_2",
            name="Version Alpha",
            version_id="version-alpha-2",
            version_label="v2.0.0",
        )
    )
    execution = await graph_mgr.add_hetero_node(
        ExecutionGraphNode(
            node_id="execution_alpha",
            name="Execution Alpha",
            execution_id="execution-alpha",
            skill_ref=alpha.node_id,
        )
    )
    validation = await graph_mgr.add_hetero_node(
        ValidationGraphNode(
            node_id="validation_alpha",
            name="Validation Alpha",
            validation_id="validation-alpha",
            outcome="passed",
            validator="json_path_equals",
        )
    )

    for edge in [
        HeteroGraphEdge(
            edge_id="source-alpha",
            source_id=shared_source.node_id,
            target_id=alpha.node_id,
            edge_type=HeteroEdgeType.DERIVED_FROM,
            weight=0.8,
        ),
        HeteroGraphEdge(
            edge_id="source-beta",
            source_id=shared_source.node_id,
            target_id=beta.node_id,
            edge_type=HeteroEdgeType.DERIVED_FROM,
            weight=0.7,
        ),
        HeteroGraphEdge(
            edge_id="alpha-version",
            source_id=alpha.node_id,
            target_id=version.node_id,
            edge_type=HeteroEdgeType.VERSIONED_AS,
            weight=0.9,
        ),
        HeteroGraphEdge(
            edge_id="version-beta",
            source_id=version.node_id,
            target_id=beta.node_id,
            edge_type=HeteroEdgeType.COMPOSES_WITH,
            weight=0.85,
        ),
        HeteroGraphEdge(
            edge_id="alpha-execution",
            source_id=alpha.node_id,
            target_id=execution.node_id,
            edge_type=HeteroEdgeType.EXECUTED_AS,
            weight=0.75,
        ),
        HeteroGraphEdge(
            edge_id="execution-validation",
            source_id=execution.node_id,
            target_id=validation.node_id,
            edge_type=HeteroEdgeType.VALIDATED_BY,
            weight=0.65,
        ),
    ]:
        await graph_mgr.add_hetero_edge(edge)


@pytest.fixture
def wiki() -> MemoryWikiManager:
    return MemoryWikiManager()


@pytest.fixture
def graph_mgr() -> MemoryGraphManager:
    return MemoryGraphManager()


@pytest.fixture
def api_app(wiki: MemoryWikiManager, graph_mgr: MemoryGraphManager) -> FastAPI:
    app_state.wiki = wiki
    app_state.graph = graph_mgr
    app = FastAPI()
    app.include_router(graph.router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
async def test_memory_graph_manager_hetero_helpers_support_custom_graph(graph_mgr: MemoryGraphManager):
    assert GraphNodeKind is HeteroNodeKind

    source = await graph_mgr.add_hetero_node(
        SourceGraphNode(
            node_id="source-1",
            name="Source",
            description="Trajectory source",
            source_uri="trajectory://source-1",
        )
    )
    skill = await graph_mgr.add_hetero_node(
        HeteroSkillNode(
            node_id="skill-1",
            name="Skill",
            description="Derived skill",
            skill_id="skill-1",
            skill_version="1.0.0",
        )
    )
    execution = await graph_mgr.add_hetero_node(
        ExecutionGraphNode(
            node_id="execution-1",
            name="Execution",
            execution_id="execution-1",
            skill_ref=skill.node_id,
        )
    )
    validation = await graph_mgr.add_hetero_node(
        ValidationGraphNode(
            node_id="validation-1",
            name="Validation",
            validation_id="validation-1",
        )
    )
    version = await graph_mgr.add_hetero_node(
        VersionGraphNode(
            node_id="version-1",
            name="Version",
            version_id="version-1",
            version_label="v1.0.0",
        )
    )

    for edge in [
        HeteroGraphEdge(
            edge_id="edge-1",
            source_id=source.node_id,
            target_id=skill.node_id,
            edge_type=HeteroEdgeType.DERIVED_FROM,
        ),
        HeteroGraphEdge(
            edge_id="edge-2",
            source_id=skill.node_id,
            target_id=execution.node_id,
            edge_type=HeteroEdgeType.EXECUTED_AS,
        ),
        HeteroGraphEdge(
            edge_id="edge-3",
            source_id=execution.node_id,
            target_id=validation.node_id,
            edge_type=HeteroEdgeType.VALIDATED_BY,
        ),
        HeteroGraphEdge(
            edge_id="edge-4",
            source_id=validation.node_id,
            target_id=version.node_id,
            edge_type=HeteroEdgeType.VERSIONED_AS,
        ),
        HeteroGraphEdge(
            edge_id="edge-5",
            source_id=version.node_id,
            target_id=skill.node_id,
            edge_type=HeteroEdgeType.COMPOSES_WITH,
        ),
    ]:
        await graph_mgr.add_hetero_edge(edge)

    hetero_graph = await graph_mgr.get_hetero_graph()
    assert len(hetero_graph.nodes) == 5
    assert len(hetero_graph.edges) == 5
    assert set(hetero_graph.get_node_kinds()) == {"source", "skill", "execution", "validation", "version"}
    assert set(hetero_graph.get_edge_types()) == {
        "derived_from",
        "executed_as",
        "validated_by",
        "versioned_as",
        "composes_with",
    }


@pytest.mark.asyncio
async def test_memory_graph_manager_seeds_required_demo_chain(graph_mgr: MemoryGraphManager):
    await graph_mgr.seed_demo_hetero_chain(
        fill_form_skill_id="actual-fill-form-id",
        fill_form_skill_version="1.2.3",
    )

    hetero_graph = await graph_mgr.get_hetero_graph()
    assert set(hetero_graph.nodes) == {
        "source_demo_trajectory",
        "fill_form",
        "execution_demo",
        "validation_demo",
        "version_demo",
    }
    assert hetero_graph.nodes["fill_form"].skill_id == "actual-fill-form-id"
    assert hetero_graph.nodes["fill_form"].skill_version == "1.2.3"
    assert [
        (edge.source_id, edge.target_id, edge.edge_type.value)
        for edge in hetero_graph.edges
    ] == [
        ("source_demo_trajectory", "fill_form", "derived_from"),
        ("fill_form", "execution_demo", "executed_as"),
        ("execution_demo", "validation_demo", "validated_by"),
        ("validation_demo", "version_demo", "versioned_as"),
        ("version_demo", "fill_form", "composes_with"),
    ]


@pytest.mark.asyncio
async def test_startup_seed_writes_required_chain_into_hetero_memory_graph(
    wiki: MemoryWikiManager,
    graph_mgr: MemoryGraphManager,
):
    await wiki.create(make_skill("fill_form", skill_id="actual-fill-form-id"))

    await _seed_demo_heterogeneous_graph(wiki, graph_mgr)

    hetero_graph = await graph_mgr.get_hetero_graph()
    assert set(hetero_graph.nodes) == {
        "source_demo_trajectory",
        "fill_form",
        "execution_demo",
        "validation_demo",
        "version_demo",
    }
    assert hetero_graph.nodes["fill_form"].skill_id == "actual-fill-form-id"
    assert set(hetero_graph.get_edge_types()) == {
        "derived_from",
        "executed_as",
        "validated_by",
        "versioned_as",
        "composes_with",
    }
    legacy_subgraph = await graph_mgr.get_subgraph(["actual-fill-form-id"], depth=1)
    assert all(edge.metadata.get("hetero_demo") is not True for edge in legacy_subgraph.edges)


def test_old_graph_api_still_works(api_app: FastAPI, wiki: MemoryWikiManager, graph_mgr: MemoryGraphManager):
    first = make_skill("legacy_graph_click")
    second = make_skill("legacy_graph_locate")
    anyio.run(wiki.create, first)
    anyio.run(wiki.create, second)
    anyio.run(graph_mgr.sync_skill, first)
    anyio.run(graph_mgr.sync_skill, second)
    anyio.run(
        graph_mgr.create_edge,
        SkillEdge(
            edge_id="legacy-edge-1",
            source_id=first.skill_id,
            target_id=second.skill_id,
            edge_type=EdgeType.DEPENDS_ON,
        ),
    )

    client = TestClient(api_app)
    response = client.get("/api/v1/graph")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["nodes"]) == 2
    assert len(payload["edges"]) == 1
    assert payload["edges"][0]["edge_type"] == "depends_on"


def test_heterogeneous_graph_seed_does_not_pollute_old_graph_api(
    api_app: FastAPI,
    wiki: MemoryWikiManager,
    graph_mgr: MemoryGraphManager,
):
    fill_form = make_skill("fill_form", skill_id="actual-fill-form-id")
    anyio.run(wiki.create, fill_form)
    anyio.run(graph_mgr.sync_skill, fill_form)
    anyio.run(_seed_demo_heterogeneous_graph, wiki, graph_mgr)

    client = TestClient(api_app)
    response = client.get("/api/v1/graph")
    assert response.status_code == 200

    payload = response.json()
    assert [node["id"] for node in payload["nodes"]] == ["actual-fill-form-id"]
    assert payload["edges"] == []
    assert all("kind" not in node for node in payload["nodes"])


@pytest.mark.asyncio
async def test_skill_only_projection_returns_meta_path_edges_without_mutating_graph(
    graph_mgr: MemoryGraphManager,
):
    alpha = make_skill("skill_alpha", skill_id="skill-alpha")
    beta = make_skill("skill_beta", skill_id="skill-beta")
    await graph_mgr.sync_skill(alpha)
    await graph_mgr.sync_skill(beta)
    await graph_mgr.create_edge(
        SkillEdge(
            edge_id="manual-dependency",
            source_id="skill-alpha",
            target_id="skill-beta",
            edge_type=EdgeType.DEPENDS_ON,
            metadata={"source": "manual"},
        )
    )
    await graph_mgr.create_edge(
        SkillEdge(
            edge_id="auto:composes_with:skill-alpha:skill-beta",
            source_id="skill-alpha",
            target_id="skill-beta",
            edge_type=EdgeType.COMPOSES_WITH,
            metadata={"auto_generated": True, "source": "skill_repository"},
        )
    )
    await seed_projection_fixture(graph_mgr)

    before = await graph_mgr.get_subgraph(["skill-alpha", "skill-beta"], depth=1)
    before_snapshot = {
        edge.edge_id: edge.model_dump(mode="json")
        for edge in before.edges
    }
    projection = await graph_mgr.project_hetero_to_skill_graph()
    after = await graph_mgr.get_subgraph(["skill-alpha", "skill-beta"], depth=1)
    after_snapshot = {
        edge.edge_id: edge.model_dump(mode="json")
        for edge in after.edges
    }

    assert {edge.edge_id for edge in before.edges} == {
        "manual-dependency",
        "auto:composes_with:skill-alpha:skill-beta",
    }
    assert after_snapshot == before_snapshot
    assert set(projection.nodes) == {"skill-alpha", "skill-beta"}
    assert {
        (edge.source_id, edge.target_id, edge.edge_type.value)
        for edge in projection.edges
    } == {
        ("skill-alpha", "skill-beta", "evolved_from"),
        ("skill-alpha", "skill-beta", "similar_to"),
    }
    edges_by_projection = {
        edge.metadata["projection_source"]: edge
        for edge in projection.edges
    }
    assert edges_by_projection["hetero_skill_version_skill"].confidence == pytest.approx(0.85)
    assert edges_by_projection["hetero_skill_version_skill"].weight == pytest.approx(0.85)
    assert edges_by_projection["hetero_shared_source"].confidence == pytest.approx(0.7)
    assert edges_by_projection["hetero_shared_source"].weight == pytest.approx(0.7)
    for edge in projection.edges:
        assert edge.edge_id.startswith("projection:")
        assert edge.confidence > 0
        assert edge.metadata["projection_source"] in {
            "hetero_skill_version_skill",
            "hetero_shared_source",
        }
        assert edge.metadata["projection_generated"] is True
        assert edge.metadata["validation_evidence"]["source"][0]["validation_node_id"] == "validation_alpha"

    evidence = projection.metadata["validation_evidence"]["skill-alpha"][0]
    assert evidence["projection_source"] == "hetero_skill_execution_validation"
    assert evidence["outcome"] == "passed"
    assert evidence["validator"] == "json_path_equals"


@pytest.mark.asyncio
async def test_skill_only_projection_requires_explicit_meta_path_edge_types(
    graph_mgr: MemoryGraphManager,
):
    alpha = await graph_mgr.add_hetero_node(
        HeteroSkillNode(node_id="alpha", name="alpha", skill_id="alpha")
    )
    beta = await graph_mgr.add_hetero_node(
        HeteroSkillNode(node_id="beta", name="beta", skill_id="beta")
    )
    version = await graph_mgr.add_hetero_node(
        VersionGraphNode(node_id="version", name="version")
    )
    for edge in [
        HeteroGraphEdge(
            edge_id="wrong-first-hop",
            source_id=alpha.node_id,
            target_id=version.node_id,
            edge_type=HeteroEdgeType.COMPOSES_WITH,
        ),
        HeteroGraphEdge(
            edge_id="wrong-second-hop",
            source_id=version.node_id,
            target_id=beta.node_id,
            edge_type=HeteroEdgeType.VERSIONED_AS,
        ),
    ]:
        await graph_mgr.add_hetero_edge(edge)

    projection = await graph_mgr.project_hetero_to_skill_graph()
    assert projection.edges == []


def test_skill_only_projection_endpoint_returns_metadata_and_validation_evidence(
    api_app: FastAPI,
    graph_mgr: MemoryGraphManager,
):
    anyio.run(seed_projection_fixture, graph_mgr)

    client = TestClient(api_app)
    response = client.get("/api/v1/graph/projection/skill-only")
    assert response.status_code == 200

    payload = response.json()
    assert {node["id"] for node in payload["nodes"]} == {"skill-alpha", "skill-beta"}
    assert payload["stats"]["node_count"] == 2
    assert payload["stats"]["edge_count"] == 2
    assert "validation_evidence" in payload["stats"]
    assert payload["stats"]["validation_evidence"]["skill-alpha"][0]["validation_node_id"] == "validation_alpha"
    assert payload["metadata"]["projection_source"] == "heterogeneous_graph"
    assert payload["validation_evidence"]["skill-alpha"][0]["validation_node_id"] == "validation_alpha"
    assert {
        (edge["source"], edge["target"], edge["edge_type"])
        for edge in payload["edges"]
    } == {
        ("skill-alpha", "skill-beta", "evolved_from"),
        ("skill-alpha", "skill-beta", "similar_to"),
    }
    edges_by_projection = {
        edge["metadata"]["projection_source"]: edge
        for edge in payload["edges"]
    }
    assert edges_by_projection["hetero_skill_version_skill"]["confidence"] == pytest.approx(0.85)
    assert edges_by_projection["hetero_shared_source"]["confidence"] == pytest.approx(0.7)
    for edge in payload["edges"]:
        assert edge["confidence"] > 0
        assert edge["metadata"]["projection_source"].startswith("hetero_")
        assert edge["metadata"]["validation_evidence"]["source"][0]["validation_node_id"] == "validation_alpha"


def test_heterogeneous_graph_endpoint_returns_demo_structure(api_app: FastAPI):
    client = TestClient(api_app)
    response = client.get("/api/v1/graph/heterogeneous")
    assert response.status_code == 200

    payload = response.json()
    assert len(payload["nodes"]) == 5
    assert len(payload["edges"]) == 5
    assert payload["stats"]["node_count"] == 5
    assert payload["stats"]["edge_count"] == 5
    assert set(payload["stats"]["node_kind_distribution"]) == {
        "source",
        "skill",
        "execution",
        "validation",
        "version",
    }
    assert set(payload["stats"]["edge_type_distribution"]) == {
        "derived_from",
        "executed_as",
        "validated_by",
        "versioned_as",
        "composes_with",
    }
    assert {node["id"] for node in payload["nodes"]} == {
        "source_demo_trajectory",
        "fill_form",
        "execution_demo",
        "validation_demo",
        "version_demo",
    }
    assert {node["kind"] for node in payload["nodes"]} == {
        "source",
        "skill",
        "execution",
        "validation",
        "version",
    }
    nodes_by_id = {node["id"]: node for node in payload["nodes"]}
    assert nodes_by_id["source_demo_trajectory"]["metadata"]["source_type"] == "browser_trajectory"
    assert nodes_by_id["fill_form"]["metadata"]["skill_id"] == "fill_form"
    assert nodes_by_id["execution_demo"]["metadata"]["status"] == "completed"
    assert nodes_by_id["validation_demo"]["metadata"]["outcome"] == "passed"
    assert nodes_by_id["version_demo"]["metadata"]["version_label"] == "1.0.0"
    assert [
        (edge["source"], edge["target"], edge["edge_type"])
        for edge in payload["edges"]
    ] == [
        ("source_demo_trajectory", "fill_form", "derived_from"),
        ("fill_form", "execution_demo", "executed_as"),
        ("execution_demo", "validation_demo", "validated_by"),
        ("validation_demo", "version_demo", "versioned_as"),
        ("version_demo", "fill_form", "composes_with"),
    ]


def test_graph_view_endpoint_returns_skill_only_view(
    api_app: FastAPI,
    wiki: MemoryWikiManager,
    graph_mgr: MemoryGraphManager,
):
    first = make_skill("view_graph_click")
    second = make_skill("view_graph_submit")
    anyio.run(wiki.create, first)
    anyio.run(wiki.create, second)
    anyio.run(graph_mgr.sync_skill, first)
    anyio.run(graph_mgr.sync_skill, second)
    anyio.run(
        graph_mgr.create_edge,
        SkillEdge(
            edge_id="view-edge-1",
            source_id=first.skill_id,
            target_id=second.skill_id,
            edge_type=EdgeType.DEPENDS_ON,
        ),
    )

    client = TestClient(api_app)
    response = client.get("/api/v1/graph/view?view=skill_only")
    assert response.status_code == 200

    payload = response.json()
    assert payload["view"] == "skill_only"
    assert payload["source_endpoint"] == "/api/v1/graph"
    assert {node["kind"] for node in payload["nodes"]} == {"skill"}
    assert {edge["edge_type"] for edge in payload["edges"]} == {"depends_on"}
    assert payload["stats"]["view"] == "skill_only"


def test_graph_view_endpoint_returns_provenance_view(api_app: FastAPI):
    client = TestClient(api_app)
    response = client.get("/api/v1/graph/view?view=provenance")
    assert response.status_code == 200

    payload = response.json()
    assert payload["view"] == "provenance"
    assert payload["source_endpoint"] == "/api/v1/graph/heterogeneous"
    assert {node["kind"] for node in payload["nodes"]} == {
        "source",
        "skill",
        "execution",
        "validation",
        "version",
    }
    assert {edge["edge_type"] for edge in payload["edges"]} == {
        "derived_from",
        "executed_as",
        "validated_by",
        "versioned_as",
        "composes_with",
    }
    source = next(node for node in payload["nodes"] if node["kind"] == "source")
    assert source["metadata"]["source_type"] == "browser_trajectory"
    assert payload["validation_evidence"]["fill_form"][0]["validation_node_id"] == "validation_demo"


def test_graph_view_endpoint_focuses_provenance_view_on_same_skill(api_app: FastAPI):
    client = TestClient(api_app)
    response = client.get("/api/v1/graph/view?view=provenance&skill_id=fill_form")
    assert response.status_code == 200

    payload = response.json()
    assert payload["view"] == "provenance"
    assert payload["stats"]["focused_skill_id"] == "fill_form"
    assert payload["stats"]["focus_center_node_ids"] == ["fill_form"]
    assert payload["metadata"]["focus"]["method_basis"].startswith("typed graph neighborhood")
    assert {node["kind"] for node in payload["nodes"]} == {
        "source",
        "skill",
        "execution",
        "validation",
        "version",
    }
    assert {
        (edge["source"], edge["target"], edge["edge_type"])
        for edge in payload["edges"]
    } == {
        ("source_demo_trajectory", "fill_form", "derived_from"),
        ("fill_form", "execution_demo", "executed_as"),
        ("execution_demo", "validation_demo", "validated_by"),
        ("validation_demo", "version_demo", "versioned_as"),
        ("version_demo", "fill_form", "composes_with"),
    }
    evidence = payload["validation_evidence"]["fill_form"][0]
    assert evidence["meta_path"] == "Skill->Execution->Validation"
    assert evidence["validation_node_id"] == "validation_demo"
    assert evidence["outcome"] == "passed"


def test_graph_view_endpoint_returns_version_impact_projection(
    api_app: FastAPI,
    graph_mgr: MemoryGraphManager,
):
    anyio.run(seed_projection_fixture, graph_mgr)

    client = TestClient(api_app)
    response = client.get("/api/v1/graph/view?view=version_impact")
    assert response.status_code == 200

    payload = response.json()
    assert payload["view"] == "version_impact"
    assert payload["source_endpoint"] == "/api/v1/graph/projection/skill-only"
    assert {node["id"] for node in payload["nodes"]} == {"skill-alpha", "skill-beta"}
    assert {edge["metadata"]["projection_source"] for edge in payload["edges"]} == {
        "hetero_skill_version_skill",
        "hetero_shared_source",
    }
    assert payload["validation_evidence"]["skill-alpha"][0]["validation_node_id"] == "validation_alpha"


def test_graph_view_endpoint_focuses_version_impact_on_same_skill(
    api_app: FastAPI,
    graph_mgr: MemoryGraphManager,
):
    anyio.run(seed_projection_fixture, graph_mgr)

    client = TestClient(api_app)
    response = client.get("/api/v1/graph/view?view=version_impact&skill_id=skill-alpha&depth=1")
    assert response.status_code == 200

    payload = response.json()
    assert payload["view"] == "version_impact"
    assert payload["stats"]["focused_skill_id"] == "skill-alpha"
    assert payload["stats"]["focus_center_node_ids"] == ["skill-alpha"]
    assert {node["id"] for node in payload["nodes"]} == {"skill-alpha", "skill-beta"}
    assert {edge["metadata"]["projection_source"] for edge in payload["edges"]} == {
        "hetero_skill_version_skill",
        "hetero_shared_source",
    }
    assert payload["validation_evidence"]["skill-alpha"][0]["validation_node_id"] == "validation_alpha"


def test_graph_view_endpoint_rejects_unknown_view(api_app: FastAPI):
    client = TestClient(api_app)
    response = client.get("/api/v1/graph/view?view=unknown")
    assert response.status_code == 422
