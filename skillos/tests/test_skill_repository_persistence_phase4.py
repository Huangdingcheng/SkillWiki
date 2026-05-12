from __future__ import annotations

import json

import pytest

from skillos.layers.skill_repository.graph_manager import SkillGraphManager
from skillos.models.graph_model import SkillEdge, SkillGraphNode, SkillSubgraph
from skillos.models.skill_model import (
    EdgeType,
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillType,
)
from skillos.storage.neo4j_db import SkillGraphRepository
from skillos.storage.postgres_db import orm_to_skill, skill_to_orm


def make_skill(
    name: str,
    *,
    implementation: SkillImplementation | None = None,
    provenance: SkillProvenance | None = None,
) -> Skill:
    return Skill(
        name=name,
        description=f"{name} persistence test",
        skill_type=SkillType.FUNCTIONAL,
        domain="browser",
        state=SkillState.RELEASED,
        tags=["web", "persistence"],
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        implementation=implementation or SkillImplementation(prompt_template=f"Run {name}"),
        provenance=provenance,
        dependency_ids=["dep-1"],
        component_ids=["component-1"],
    )


def test_skill_mapper_preserves_relationship_fields():
    skill = make_skill(
        "persist_parent",
        implementation=SkillImplementation(
            prompt_template="Run composed flow",
            sub_skill_ids=["child-1", "child-2"],
            tool_calls=["browser"],
        ),
        provenance=SkillProvenance(
            source_type="adapt",
            source_ids=["trajectory-1"],
            parent_skill_ids=["old-parent"],
            created_by_agent="builder",
        ),
    )
    skill.record_execution(success=True, latency_ms=100)

    restored = orm_to_skill(skill_to_orm(skill))

    assert restored.implementation is not None
    assert restored.implementation.sub_skill_ids == ["child-1", "child-2"]
    assert restored.implementation.tool_calls == ["browser"]
    assert restored.provenance is not None
    assert restored.provenance.parent_skill_ids == ["old-parent"]
    assert restored.provenance.source_ids == ["trajectory-1"]
    assert restored.dependency_ids == ["dep-1"]
    assert restored.component_ids == ["component-1"]
    assert restored.metrics.usage_count == 1


def test_graph_edge_neo4j_props_preserve_metadata():
    edge = SkillEdge(
        edge_id="auto:composes_with:parent:child",
        source_id="parent",
        target_id="child",
        edge_type=EdgeType.COMPOSES_WITH,
        metadata={"auto_generated": True, "source": "skill_repository"},
    )

    props = edge.to_neo4j_props()

    assert json.loads(props["metadata"]) == {
        "auto_generated": True,
        "source": "skill_repository",
    }


def test_neo4j_edge_parser_restores_metadata():
    repo = SkillGraphRepository.__new__(SkillGraphRepository)

    edge = repo._parse_edge_dict({
        "edge_id": "auto:composes_with:parent:child",
        "source_id": "parent",
        "target_id": "child",
        "edge_type": "COMPOSES_WITH",
        "weight": 1.0,
        "confidence": 1.0,
        "metadata": '{"auto_generated": true, "source": "skill_repository"}',
    })

    assert edge.metadata == {"auto_generated": True, "source": "skill_repository"}


@pytest.mark.asyncio
async def test_skill_graph_manager_sync_auto_edges_matches_memory_contract():
    fake_repo = FakeGraphRepository()
    manager = SkillGraphManager.__new__(SkillGraphManager)
    manager._graph = fake_repo

    parent = make_skill(
        "graph_parent",
        implementation=SkillImplementation(sub_skill_ids=["child-b", "child-b", "missing"]),
        provenance=SkillProvenance(source_type="adapt", parent_skill_ids=["old-parent"]),
    )
    parent_id = parent.skill_id
    manual = SkillEdge(
        edge_id="manual-edge",
        source_id=parent_id,
        target_id="child-a",
        edge_type=EdgeType.DEPENDS_ON,
        metadata={"auto_generated": False},
    )
    stale_auto = SkillEdge(
        edge_id=f"auto:{EdgeType.COMPOSES_WITH.value}:{parent_id}:child-a",
        source_id=parent_id,
        target_id="child-a",
        edge_type=EdgeType.COMPOSES_WITH,
        metadata={"auto_generated": True, "source": "skill_repository"},
    )
    fake_repo.edges.extend([manual, stale_auto])

    await manager.sync_auto_edges(parent, [parent_id, "child-a", "child-b", "old-parent"])

    edge_ids = {edge.edge_id for edge in fake_repo.edges}
    assert "manual-edge" in edge_ids
    assert stale_auto.edge_id not in edge_ids
    assert f"auto:{EdgeType.COMPOSES_WITH.value}:{parent_id}:child-b" in edge_ids
    assert f"auto:{EdgeType.EVOLVED_FROM.value}:{parent_id}:old-parent" in edge_ids
    assert all(edge.target_id != "missing" for edge in fake_repo.edges)


@pytest.mark.asyncio
async def test_skill_graph_manager_merges_multi_root_subgraphs():
    fake_repo = FakeGraphRepository()
    manager = SkillGraphManager.__new__(SkillGraphManager)
    manager._graph = fake_repo
    fake_repo.subgraphs = {
        "root-a": make_subgraph("root-a", "shared-edge"),
        "root-b": make_subgraph("root-b", "shared-edge"),
    }

    subgraph = await manager.get_subgraph(skill_ids=["root-a", "root-b"], depth=1)

    assert set(subgraph.nodes) == {"root-a", "root-b"}
    assert [edge.edge_id for edge in subgraph.edges] == ["shared-edge"]


def make_subgraph(skill_id: str, edge_id: str) -> SkillSubgraph:
    subgraph = SkillSubgraph()
    subgraph.nodes[skill_id] = SkillGraphNode(
        skill_id=skill_id,
        name=skill_id.replace("-", "_"),
        version="1.0.0",
        skill_type=SkillType.ATOMIC,
        state=SkillState.RELEASED,
    )
    subgraph.edges.append(SkillEdge(
        edge_id=edge_id,
        source_id="root-a",
        target_id="root-b",
        edge_type=EdgeType.COMPOSES_WITH,
    ))
    return subgraph


class FakeGraphRepository:
    def __init__(self) -> None:
        self.edges: list[SkillEdge] = []
        self.subgraphs: dict[str, SkillSubgraph] = {}

    async def create_edge(self, edge: SkillEdge) -> None:
        self.edges = [existing for existing in self.edges if existing.edge_id != edge.edge_id]
        self.edges.append(edge)

    async def delete_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if edge.edge_id != edge_id]

    async def get_edges(
        self,
        skill_id: str,
        direction: str = "both",
        edge_type: EdgeType | None = None,
    ) -> list[SkillEdge]:
        results = [
            edge for edge in self.edges
            if edge.source_id == skill_id or direction == "both" and edge.target_id == skill_id
        ]
        if direction == "out":
            results = [edge for edge in results if edge.source_id == skill_id]
        if direction == "in":
            results = [edge for edge in results if edge.target_id == skill_id]
        if edge_type:
            results = [edge for edge in results if edge.edge_type == edge_type]
        return results

    async def get_subgraph(self, skill_id: str, depth: int = 2) -> SkillSubgraph:
        return self.subgraphs.get(skill_id, SkillSubgraph())
