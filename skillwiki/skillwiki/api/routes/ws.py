"""WebSocket 实时推送路由。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])

# 活跃连接集合
_connections: Set[WebSocket] = set()


async def broadcast(event: str, data: Dict[str, Any]) -> None:
    """向所有活跃 WebSocket 连接广播事件。"""
    if not _connections:
        return
    message = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
    dead = set()
    for ws in _connections:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _connections.add(websocket)
    try:
        await websocket.send_text(json.dumps({
            "event": "connected",
            "data": {"message": "SkillWiki WebSocket 已连接", "connections": len(_connections)},
        }))
        while True:
            # 保持连接，接收 ping/命令
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"event": "pong", "data": {}}))
            elif msg.get("type") == "subscribe":
                # 客户端订阅特定 skill_id 的事件
                await websocket.send_text(json.dumps({
                    "event": "subscribed",
                    "data": {"skill_id": msg.get("skill_id")},
                }))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)


@router.websocket("/ws/execution/{session_id}")
async def execution_websocket(websocket: WebSocket, session_id: str) -> None:
    """专用执行会话 WebSocket — 实时推送每个步骤的进度。"""
    await websocket.accept()
    _connections.add(websocket)

    async def on_event(event_type: str, data: Dict[str, Any]) -> None:
        try:
            await websocket.send_text(json.dumps(
                {"event": event_type, "session_id": session_id, "data": data},
                ensure_ascii=False,
                default=str,
            ))
        except Exception:
            pass

    # 将回调注入 app_state.executor（如果可用）
    from .deps import get_app_state  # type: ignore[import]
    # 注意：此处 deps 在路由模块外，通过直接导入获取
    try:
        from ..deps import app_state
        if app_state.executor:
            app_state.executor.add_event_callback(on_event)
    except Exception:
        pass

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw) if raw else {}
            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"event": "pong", "data": {}}))
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)
        try:
            from ..deps import app_state
            if app_state.executor:
                app_state.executor.remove_event_callback(on_event)
        except Exception:
            pass
