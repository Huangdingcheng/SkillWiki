"""Skill 图谱路由。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import AppState, get_app_state
from ..schemas import AddEdgeRequest, GraphData, GraphEdgeData, GraphNodeData, OKResponse, SubgraphRequest

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


@router.get("", response_model=GraphData)
async def get_full_graph(
    limit: int = Query(200, ge=1, le=500),
    app: AppState = Depends(get_app_state),
) -> GraphData:
    """获取完整图谱数据（用于前端 G6 渲染）。"""
    skills = await app.wiki.list(state=None, limit=limit)
    nodes = [_skill_to_node(s) for s in skills]

    # 获取所有边
    edges: List[GraphEdgeData] = []
    try:
        subgraph = await app.graph.get_subgraph(
            skill_ids=[s.skill_id for s in skills],
            depth=1,
        )
        for edge in subgraph.edges:
            edges.append(GraphEdgeData(
                id=edge.edge_id,
                source=edge.source_id,
                target=edge.target_id,
                edge_type=edge.edge_type.value,
                weight=edge.weight,
            ))
    except Exception:
        pass  # 图数据库不可用时返回仅节点

    stats = await app.wiki.get_overview_stats()
    return GraphData(nodes=nodes, edges=edges, stats=stats)


@router.post("/subgraph", response_model=GraphData)
async def get_subgraph(
    req: SubgraphRequest,
    app: AppState = Depends(get_app_state),
) -> GraphData:
    skill = await app.wiki.get(req.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_id} 不存在")

    try:
        subgraph = await app.graph.get_subgraph([req.skill_id], depth=req.depth)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    skill_ids = {e.source_id for e in subgraph.edges} | {e.target_id for e in subgraph.edges}
    skill_ids.add(req.skill_id)
    skill_map = await app.wiki.get_many(list(skill_ids))

    nodes = [_skill_to_node(s) for s in skill_map.values() if s]
    edges = [
        GraphEdgeData(
            id=e.edge_id,
            source=e.source_id,
            target=e.target_id,
            edge_type=e.edge_type.value,
            weight=e.weight,
        )
        for e in subgraph.edges
    ]
    return GraphData(nodes=nodes, edges=edges)


@router.post("/edges", response_model=OKResponse)
async def add_edge(
    req: AddEdgeRequest,
    app: AppState = Depends(get_app_state),
) -> OKResponse:
    from ...models.graph_model import EdgeType, SkillEdge
    try:
        edge_type = EdgeType(req.edge_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效边类型: {req.edge_type}")

    edge = SkillEdge(
        source_id=req.source_id,
        target_id=req.target_id,
        edge_type=edge_type,
        weight=req.weight,
        metadata=req.metadata,
    )
    await app.graph.create_edge(edge)
    return OKResponse(message="边已创建")


@router.get("/{skill_id}/dependencies", response_model=List[Dict[str, Any]])
async def get_dependencies(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> List[Dict[str, Any]]:
    try:
        chain = await app.graph.get_dependency_chain(skill_id)
        return [{"skill_id": s.skill_id, "name": s.name, "version": s.version} for s in chain]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{skill_id}/execution-order", response_model=List[str])
async def get_execution_order(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> List[str]:
    try:
        return await app.graph.get_execution_order(skill_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/overview", response_model=Dict[str, Any])
async def get_graph_stats(
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    try:
        return await app.graph.get_stats()
    except Exception:
        return {}
