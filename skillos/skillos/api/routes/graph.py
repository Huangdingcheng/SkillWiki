"""Skill graph routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import AppState, get_app_state
from ..schemas import (
    AddEdgeRequest,
    GraphData,
    GraphEdgeData,
    GraphNodeData,
    GraphViewData,
    GraphViewEdgeData,
    GraphViewNodeData,
    HeterogeneousGraphData,
    HeterogeneousGraphEdgeData,
    HeterogeneousGraphNodeData,
    OKResponse,
    SkillGraphProjectionData,
    SkillGraphProjectionEdgeData,
    SubgraphRequest,
)

router = APIRouter(prefix="/graph", tags=["graph"])


def _skill_to_node(skill) -> GraphNodeData:
    return GraphNodeData(
        id=skill.skill_id,
        name=skill.name,
        skill_type=skill.skill_type.value,
        state=skill.state.value,
        tags=skill.tags,
        version=skill.version,
        granularity_level=skill.granularity_level,
        success_rate=skill.metrics.success_rate,
        usage_count=skill.metrics.usage_count,
    )


def _edge_to_data(edge) -> GraphEdgeData:
    return GraphEdgeData(
        id=edge.edge_id,
        source=edge.source_id,
        target=edge.target_id,
        edge_type=edge.edge_type.value,
        weight=edge.weight,
    )


def _graph_node_to_data(node) -> GraphNodeData:
    return GraphNodeData(
        id=node.skill_id,
        name=node.name,
        skill_type=node.skill_type.value,
        state=node.state.value,
        tags=node.tags,
        version=node.version,
        granularity_level=node.granularity_level,
        success_rate=node.success_rate,
        usage_count=node.usage_count,
    )


def _projection_edge_to_data(edge) -> SkillGraphProjectionEdgeData:
    return SkillGraphProjectionEdgeData(
        id=edge.edge_id,
        source=edge.source_id,
        target=edge.target_id,
        edge_type=edge.edge_type.value,
        weight=edge.weight,
        confidence=edge.confidence,
        metadata=edge.metadata,
    )


def _hetero_node_to_data(node) -> HeterogeneousGraphNodeData:
    metadata = dict(node.metadata)
    for field_name in (
        "source_uri",
        "source_type",
        "skill_id",
        "skill_version",
        "skill_state",
        "execution_id",
        "status",
        "skill_ref",
        "validation_id",
        "outcome",
        "validator",
        "version_id",
        "version_label",
        "release_state",
    ):
        value = getattr(node, field_name, None)
        if value not in (None, ""):
            metadata.setdefault(field_name, value)

    return HeterogeneousGraphNodeData(
        id=node.node_id,
        kind=node.node_kind.value,
        name=node.name,
        description=node.description,
        metadata=metadata,
    )


def _hetero_edge_to_data(edge) -> HeterogeneousGraphEdgeData:
    metadata = dict(edge.metadata)
    if edge.description:
        metadata.setdefault("description", edge.description)
    if edge.created_by:
        metadata.setdefault("created_by", edge.created_by)

    return HeterogeneousGraphEdgeData(
        id=edge.edge_id,
        source=edge.source_id,
        target=edge.target_id,
        edge_type=edge.edge_type.value,
        weight=edge.weight,
        metadata=metadata,
    )


def _skill_node_to_view(node: GraphNodeData) -> GraphViewNodeData:
    return GraphViewNodeData(
        id=node.id,
        name=node.name,
        kind="skill",
        skill_type=node.skill_type,
        state=node.state,
        tags=node.tags,
        version=node.version,
        granularity_level=node.granularity_level,
        success_rate=node.success_rate,
        usage_count=node.usage_count,
    )


def _skill_edge_to_view(edge: GraphEdgeData) -> GraphViewEdgeData:
    return GraphViewEdgeData(
        id=edge.id,
        source=edge.source,
        target=edge.target,
        edge_type=edge.edge_type,
        weight=edge.weight,
    )


def _projection_edge_to_view(edge: SkillGraphProjectionEdgeData) -> GraphViewEdgeData:
    return GraphViewEdgeData(
        id=edge.id,
        source=edge.source,
        target=edge.target,
        edge_type=edge.edge_type,
        weight=edge.weight,
        confidence=edge.confidence,
        metadata=edge.metadata,
    )


def _hetero_node_to_view(node: HeterogeneousGraphNodeData) -> GraphViewNodeData:
    return GraphViewNodeData(
        id=node.id,
        name=node.name,
        kind=node.kind,
        description=node.description,
        metadata=node.metadata,
    )


def _hetero_edge_to_view(edge: HeterogeneousGraphEdgeData) -> GraphViewEdgeData:
    return GraphViewEdgeData(
        id=edge.id,
        source=edge.source,
        target=edge.target,
        edge_type=edge.edge_type,
        weight=edge.weight,
        metadata=edge.metadata,
    )


def _graph_view_node_matches_skill(node: GraphViewNodeData, skill_id: str) -> bool:
    return node.id == skill_id or str(node.metadata.get("skill_id", "")) == skill_id


def _collect_view_validation_evidence(
    nodes: List[GraphViewNodeData],
    edges: List[GraphViewEdgeData],
) -> Dict[str, List[Dict[str, Any]]]:
    nodes_by_id = {node.id: node for node in nodes}
    executions: Dict[str, Dict[str, Any]] = {}
    for edge in edges:
        if edge.edge_type != "executed_as":
            continue
        skill = nodes_by_id.get(edge.source)
        execution = nodes_by_id.get(edge.target)
        if not skill or not execution:
            continue
        if skill.kind != "skill" or execution.kind != "execution":
            continue
        executions[execution.id] = {
            "skill_id": str(skill.metadata.get("skill_id") or skill.id),
            "execution_node_id": execution.id,
            "execution_edge_id": edge.id,
            "execution_weight": edge.weight,
        }

    evidence_by_skill: Dict[str, List[Dict[str, Any]]] = {}
    for edge in edges:
        if edge.edge_type != "validated_by":
            continue
        execution = executions.get(edge.source)
        validation = nodes_by_id.get(edge.target)
        if not execution or not validation or validation.kind != "validation":
            continue
        skill_id = execution["skill_id"]
        evidence_by_skill.setdefault(skill_id, []).append({
            "evidence_source": "hetero_skill_execution_validation",
            "meta_path": "Skill->Execution->Validation",
            "execution_node_id": execution["execution_node_id"],
            "validation_node_id": validation.id,
            "outcome": validation.metadata.get("outcome", ""),
            "validator": validation.metadata.get("validator", ""),
            "confidence": min(float(execution["execution_weight"]), edge.weight),
            "source_edge_ids": [execution["execution_edge_id"], edge.id],
        })
    return evidence_by_skill


def _focus_graph_view(data: GraphViewData, skill_id: str, depth: int) -> GraphViewData:
    center_node_ids = {
        node.id
        for node in data.nodes
        if _graph_view_node_matches_skill(node, skill_id)
    }
    if not center_node_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Skill {skill_id} is not present in {data.view} graph view",
        )

    selected_node_ids: Set[str] = set(center_node_ids)
    for _ in range(depth):
        expanded = set(selected_node_ids)
        for edge in data.edges:
            if edge.source in selected_node_ids:
                expanded.add(edge.target)
            if edge.target in selected_node_ids:
                expanded.add(edge.source)
        if expanded == selected_node_ids:
            break
        selected_node_ids = expanded

    focused_nodes = [node for node in data.nodes if node.id in selected_node_ids]
    focused_edges = [
        edge
        for edge in data.edges
        if edge.source in selected_node_ids and edge.target in selected_node_ids
    ]
    focused_evidence = {
        evidence_skill_id: evidence
        for evidence_skill_id, evidence in data.validation_evidence.items()
        if evidence_skill_id == skill_id or evidence_skill_id in selected_node_ids
    }
    focused_stats = dict(data.stats)
    focused_stats.update({
        "focused_skill_id": skill_id,
        "focus_center_node_ids": sorted(center_node_ids),
        "focus_depth": depth,
        "node_count": len(focused_nodes),
        "edge_count": len(focused_edges),
    })
    focused_metadata = dict(data.metadata)
    focused_metadata["focus"] = {
        "skill_id": skill_id,
        "center_node_ids": sorted(center_node_ids),
        "depth": depth,
        "method_basis": (
            "typed graph neighborhood over the selected Skill; matches "
            "SKILLFOUNDRY provenance tracing and HIN meta-path views"
        ),
    }
    return data.model_copy(update={
        "nodes": focused_nodes,
        "edges": focused_edges,
        "stats": focused_stats,
        "metadata": focused_metadata,
        "validation_evidence": focused_evidence,
    })


@router.get("", response_model=GraphData)
async def get_full_graph(
    limit: int = Query(200, ge=1, le=500),
    app: AppState = Depends(get_app_state),
) -> GraphData:
    skills = await app.wiki.list(state=None, limit=limit)
    nodes = [_skill_to_node(skill) for skill in skills]
    edges: List[GraphEdgeData] = []
    try:
        subgraph = await app.graph.get_subgraph(
            skill_ids=[skill.skill_id for skill in skills],
            depth=1,
        )
        edges = [_edge_to_data(edge) for edge in subgraph.edges]
    except Exception:
        edges = []

    stats = await app.wiki.get_overview_stats()
    try:
        stats["graph_stats"] = await app.graph.get_stats()
    except Exception:
        pass
    return GraphData(nodes=nodes, edges=edges, stats=stats)


@router.get("/heterogeneous", response_model=HeterogeneousGraphData)
async def get_heterogeneous_graph(
    app: AppState = Depends(get_app_state),
) -> HeterogeneousGraphData:
    getter = getattr(app.graph, "get_hetero_graph", None)
    if callable(getter):
        hetero_graph = await getter()
    else:
        from ...models.graph_model import build_demo_hetero_graph

        hetero_graph = build_demo_hetero_graph()

    return HeterogeneousGraphData(
        nodes=[_hetero_node_to_data(node) for node in hetero_graph.nodes.values()],
        edges=[_hetero_edge_to_data(edge) for edge in hetero_graph.edges],
        stats=hetero_graph.get_stats(),
    )


@router.get("/projection/skill-only", response_model=SkillGraphProjectionData)
async def get_skill_only_projection(
    app: AppState = Depends(get_app_state),
) -> SkillGraphProjectionData:
    projector = getattr(app.graph, "project_hetero_to_skill_graph", None)
    if not callable(projector):
        raise HTTPException(status_code=501, detail="Skill-only projection is not available")
    projection = await projector()
    stats = dict(projection.metadata)
    stats.update({
        "node_count": len(projection.nodes),
        "edge_count": len(projection.edges),
    })
    return SkillGraphProjectionData(
        nodes=[_graph_node_to_data(node) for node in projection.nodes.values()],
        edges=[_projection_edge_to_data(edge) for edge in projection.edges],
        metadata=projection.metadata,
        validation_evidence=projection.metadata.get("validation_evidence", {}),
        stats=stats,
    )


@router.get("/view", response_model=GraphViewData)
async def get_graph_view(
    view: str = Query("skill_only", pattern=r"^(skill_only|provenance|version_impact)$"),
    limit: int = Query(300, ge=1, le=500),
    skill_id: Optional[str] = Query(None, description="Optional Skill ID to focus the view around."),
    depth: int = Query(2, ge=0, le=5, description="Neighborhood depth when skill_id is provided."),
    app: AppState = Depends(get_app_state),
) -> GraphViewData:
    if view == "skill_only":
        graph_data = await get_full_graph(limit=limit, app=app)
        stats = dict(graph_data.stats)
        stats.update({
            "view": view,
            "node_count": len(graph_data.nodes),
            "edge_count": len(graph_data.edges),
        })
        result = GraphViewData(
            view=view,
            source_endpoint="/api/v1/graph",
            nodes=[_skill_node_to_view(node) for node in graph_data.nodes],
            edges=[_skill_edge_to_view(edge) for edge in graph_data.edges],
            stats=stats,
            metadata={
                "paper_basis": ["HIN typed graph projection"],
                "description": "Legacy Skill-only graph view.",
            },
        )
        if skill_id:
            return _focus_graph_view(result, skill_id, depth)
        return result

    if view == "provenance":
        hetero_data = await get_heterogeneous_graph(app=app)
        stats = dict(hetero_data.stats)
        stats.update({"view": view})
        view_nodes = [_hetero_node_to_view(node) for node in hetero_data.nodes]
        view_edges = [_hetero_edge_to_view(edge) for edge in hetero_data.edges]
        validation_evidence = _collect_view_validation_evidence(view_nodes, view_edges)
        stats["validation_evidence_count"] = sum(
            len(items) for items in validation_evidence.values()
        )
        result = GraphViewData(
            view=view,
            source_endpoint="/api/v1/graph/heterogeneous",
            nodes=view_nodes,
            edges=view_edges,
            stats=stats,
            metadata={
                "paper_basis": ["SKILLFOUNDRY provenance", "HIN typed nodes and edges"],
                "description": "Heterogeneous Source/Skill/Execution/Validation/Version evidence graph.",
            },
            validation_evidence=validation_evidence,
        )
        if skill_id:
            return _focus_graph_view(result, skill_id, depth)
        return result

    projection_data = await get_skill_only_projection(app=app)
    stats = dict(projection_data.stats)
    stats.update({"view": view})
    result = GraphViewData(
        view=view,
        source_endpoint="/api/v1/graph/projection/skill-only",
        nodes=[_skill_node_to_view(node) for node in projection_data.nodes],
        edges=[_projection_edge_to_view(edge) for edge in projection_data.edges],
        stats=stats,
        metadata={
            **projection_data.metadata,
            "paper_basis": ["HIN meta-path projection", "GraphRAG relationship explanation"],
            "description": "Projected Skill graph with version, shared-source, and validation evidence annotations.",
        },
        validation_evidence=projection_data.validation_evidence,
    )
    if skill_id:
        return _focus_graph_view(result, skill_id, depth)
    return result


@router.post("/subgraph", response_model=GraphData)
async def get_subgraph(
    req: SubgraphRequest,
    app: AppState = Depends(get_app_state),
) -> GraphData:
    skill = await app.wiki.get(req.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_id} does not exist")

    try:
        subgraph = await app.graph.get_subgraph([req.skill_id], depth=req.depth)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    skill_ids = {edge.source_id for edge in subgraph.edges} | {
        edge.target_id for edge in subgraph.edges
    }
    skill_ids.add(req.skill_id)
    skill_map = await app.wiki.get_many(list(skill_ids))

    return GraphData(
        nodes=[_skill_to_node(skill) for skill in skill_map.values() if skill],
        edges=[_edge_to_data(edge) for edge in subgraph.edges],
        stats={
            "center_skill_id": req.skill_id,
            "depth": req.depth,
            "node_count": len(skill_map),
            "edge_count": len(subgraph.edges),
        },
    )


@router.post("/edges", response_model=OKResponse)
async def add_edge(
    req: AddEdgeRequest,
    app: AppState = Depends(get_app_state),
) -> OKResponse:
    from ...models.graph_model import SkillEdge
    from ...models.skill_model import EdgeType

    source = await app.wiki.get(req.source_id)
    target = await app.wiki.get(req.target_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or target Skill does not exist")
    try:
        edge_type = EdgeType(req.edge_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid edge type: {req.edge_type}") from exc

    edge = SkillEdge(
        source_id=req.source_id,
        target_id=req.target_id,
        edge_type=edge_type,
        weight=req.weight,
        metadata=req.metadata,
    )
    await app.graph.create_edge(edge)
    return OKResponse(message="Edge created")


@router.get("/{skill_id}/dependencies", response_model=List[Dict[str, Any]])
async def get_dependencies(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> List[Dict[str, Any]]:
    chain = await app.graph.get_dependency_chain(skill_id)
    skill_ids = chain if all(isinstance(item, str) for item in chain) else [
        item.skill_id for item in chain
    ]
    skill_map = await app.wiki.get_many(skill_ids)
    return [
        {"skill_id": skill.skill_id, "name": skill.name, "version": skill.version}
        for skill in skill_map.values()
        if skill
    ]


@router.get("/{skill_id}/execution-order", response_model=List[str])
async def get_execution_order(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> List[str]:
    return await app.graph.get_execution_order(skill_id)


@router.get("/stats/overview", response_model=Dict[str, Any])
async def get_graph_stats(
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    return await app.graph.get_stats()
