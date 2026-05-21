"""Skill graph routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import AppState, get_app_state
from ..schemas import AddEdgeRequest, GraphData, GraphEdgeData, GraphNodeData, OKResponse, SubgraphRequest

router = APIRouter(prefix="/graph", tags=["graph"])


def _skill_to_node(skill) -> GraphNodeData:
    return GraphNodeData(
        id=skill.skill_id,
        name=skill.display_name or skill.name,
        node_type="skill",
        description=skill.description,
        skill_type=skill.skill_type.value,
        state=skill.state.value,
        tags=skill.tags,
        labels=skill.tags,
        version=skill.version,
        domain=skill.domain,
        granularity_level=skill.granularity_level,
        success_rate=skill.metrics.success_rate,
        usage_count=skill.metrics.usage_count,
        source_type=skill.provenance.source_type if skill.provenance else None,
        metadata={"canonical_name": skill.name},
        visibility=skill.visibility.value,
    )


def _edge_to_data(edge) -> GraphEdgeData:
    return GraphEdgeData(
        id=edge.edge_id,
        source=edge.source_id,
        target=edge.target_id,
        edge_type=edge.edge_type.value,
        weight=edge.weight,
        confidence=edge.confidence,
        description=edge.description,
        metadata=edge.metadata,
    )


def _hetero_node_to_data(node) -> GraphNodeData:
    return GraphNodeData(
        id=node.node_id,
        name=node.name,
        node_type=node.node_type.value,
        description=node.description,
        skill_type=node.skill_type.value if node.skill_type else "",
        state=node.state.value if node.state else "",
        tags=node.labels,
        labels=node.labels,
        version=node.version or "",
        domain=node.domain,
        granularity_level=node.granularity_level,
        success_rate=node.success_rate,
        usage_count=node.usage_count,
        source_type=node.source_type,
        metadata=node.metadata,
        visibility=(node.metadata or {}).get("visibility"),
    )


def _hetero_edge_to_data(edge) -> GraphEdgeData:
    return GraphEdgeData(
        id=edge.edge_id,
        source=edge.source_id,
        target=edge.target_id,
        edge_type=edge.metadata.get("legacy_edge_type", edge.relation_type.value),
        weight=edge.weight,
        confidence=edge.confidence,
        description=edge.description,
        metadata=edge.metadata,
    )


@router.get("", response_model=GraphData)
async def get_full_graph(
    limit: int = Query(200, ge=1, le=500),
    app: AppState = Depends(get_app_state),
) -> GraphData:
    if hasattr(app.graph, "get_heterogeneous_graph"):
        subgraph = await app.graph.get_heterogeneous_graph(limit=limit)
        stats = await app.wiki.get_overview_stats()
        try:
            stats["graph_stats"] = await app.graph.get_stats()
        except Exception:
            pass
        return GraphData(
            nodes=[_hetero_node_to_data(node) for node in subgraph.nodes.values()],
            edges=[_hetero_edge_to_data(edge) for edge in subgraph.edges],
            stats=stats,
        )

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


@router.post("/subgraph", response_model=GraphData)
async def get_subgraph(
    req: SubgraphRequest,
    app: AppState = Depends(get_app_state),
) -> GraphData:
    skill = await app.wiki.get(req.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_id} does not exist")

    if hasattr(app.graph, "get_heterogeneous_graph"):
        try:
            subgraph = await app.graph.get_heterogeneous_graph([req.skill_id], depth=req.depth)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return GraphData(
            nodes=[_hetero_node_to_data(node) for node in subgraph.nodes.values()],
            edges=[_hetero_edge_to_data(edge) for edge in subgraph.edges],
            stats={
                "center_node_id": req.skill_id,
                "depth": req.depth,
                "node_count": len(subgraph.nodes),
                "edge_count": len(subgraph.edges),
            },
        )

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
