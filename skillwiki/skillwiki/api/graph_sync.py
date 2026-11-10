"""Read-through graph projection of durable repository records.

Graph reads refresh the cache, so all mutation paths share the same projection
without making a successful repository write depend on a graph-cache write.
"""
from __future__ import annotations

from typing import Any


async def refresh_repository_graph(app: Any) -> list:
    listing = getattr(app.wiki, 'list_all_versions', app.wiki.list)
    skills = await listing(limit=10000)
    sync = getattr(app.graph, 'sync_repository_snapshot', None)
    if callable(sync):
        await sync(skills)
        from .graph_evidence import receipt_projection
        nodes, edges, diagnostics = receipt_projection(app, skills)
        await app.graph.replace_receipt_projection(nodes, edges, diagnostics)
    return skills
