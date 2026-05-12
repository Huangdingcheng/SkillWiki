"""Meta-Controller Agent — 调度自管理事件，协调各 Self-Management Agent。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from ...utils.logger import get_logger

logger = get_logger(__name__)


class TriggerEvent(str, Enum):
    SKILL_FAILURE = "skill_failure"
    NEW_DATA = "new_data"
    PERFORMANCE_DROP = "performance_drop"
    NEW_SKILL_PROPOSAL = "new_skill_proposal"
    AUDIT_FAILED = "audit_failed"
    MANUAL = "manual"


@dataclass
class ControlAction:
    event: TriggerEvent
    skill_id: Optional[str]
    action_type: str  # repair | audit | build | deprecate | split | update_library
    priority: int = 5  # 1=最高, 10=最低
    payload: Dict[str, Any] = field(default_factory=dict)
    scheduled: bool = False


class MetaControllerAgent:
    """调度自管理事件，协调 Builder/Auditor/Maintainer/Librarian。"""

    def __init__(
        self,
        builder: Optional[Any] = None,
        auditor: Optional[Any] = None,
        maintainer: Optional[Any] = None,
        librarian: Optional[Any] = None,
    ) -> None:
        self._builder = builder
        self._auditor = auditor
        self._maintainer = maintainer
        self._librarian = librarian
        self._action_queue: List[ControlAction] = []
        self._handlers: Dict[TriggerEvent, Callable] = {
            TriggerEvent.SKILL_FAILURE: self._handle_failure,
            TriggerEvent.NEW_DATA: self._handle_new_data,
            TriggerEvent.PERFORMANCE_DROP: self._handle_performance_drop,
            TriggerEvent.NEW_SKILL_PROPOSAL: self._handle_new_proposal,
            TriggerEvent.AUDIT_FAILED: self._handle_audit_failed,
        }

    def schedule(self, event: TriggerEvent, skill_id: Optional[str] = None, **payload: Any) -> ControlAction:
        """根据事件类型生成并排队控制动作。"""
        action = self._route_event(event, skill_id, payload)
        self._action_queue.append(action)
        self._action_queue.sort(key=lambda a: a.priority)
        logger.info(f"MetaController: 已调度 {action.action_type} for {skill_id} (event={event})")
        return action

    async def process_queue(self, wiki: Optional[Any] = None) -> List[Dict[str, Any]]:
        """处理队列中的所有待执行动作。"""
        results = []
        while self._action_queue:
            action = self._action_queue.pop(0)
            result = await self._execute_action(action, wiki)
            results.append(result)
        return results

    def _route_event(self, event: TriggerEvent, skill_id: Optional[str], payload: Dict) -> ControlAction:
        action_map = {
            TriggerEvent.SKILL_FAILURE: ("repair", 1),
            TriggerEvent.AUDIT_FAILED: ("audit", 2),
            TriggerEvent.PERFORMANCE_DROP: ("repair", 3),
            TriggerEvent.NEW_SKILL_PROPOSAL: ("build", 4),
            TriggerEvent.NEW_DATA: ("build", 5),
            TriggerEvent.MANUAL: ("audit", 5),
        }
        action_type, priority = action_map.get(event, ("audit", 5))
        return ControlAction(
            event=event,
            skill_id=skill_id,
            action_type=action_type,
            priority=priority,
            payload=payload,
        )

    async def _execute_action(self, action: ControlAction, wiki: Optional[Any]) -> Dict[str, Any]:
        try:
            handler = self._handlers.get(action.event)
            if handler:
                return await handler(action, wiki)
        except Exception as exc:
            logger.error(f"MetaController: 动作执行失败 {action.action_type}: {exc}")
        return {"action": action.action_type, "skill_id": action.skill_id, "success": False}

    async def _handle_failure(self, action: ControlAction, wiki: Optional[Any]) -> Dict[str, Any]:
        if not self._maintainer or not action.skill_id or not wiki:
            return {"action": "repair", "skill_id": action.skill_id, "success": False}
        skill = await wiki.get(action.skill_id)
        if not skill:
            return {"action": "repair", "skill_id": action.skill_id, "success": False}
        result = self._maintainer.repair(
            skill,
            failure_info=action.payload.get("failure_info", ""),
            audit_issues=action.payload.get("audit_issues"),
        )
        if result.success and result.updated_skill and self._librarian:
            await self._librarian.update(result.updated_skill, "自动修复")
        return {"action": "repair", "skill_id": action.skill_id, "success": result.success}

    async def _handle_new_data(self, action: ControlAction, wiki: Optional[Any]) -> Dict[str, Any]:
        if not self._builder:
            return {"action": "build", "success": False}
        raw = action.payload.get("raw_data", "")
        draft = self._builder.build_from_trajectory(raw) if raw else None
        if draft and self._librarian:
            await self._librarian.register_new(draft.skill)
        return {"action": "build", "success": bool(draft), "confidence": draft.confidence if draft else 0}

    async def _handle_performance_drop(self, action: ControlAction, wiki: Optional[Any]) -> Dict[str, Any]:
        return await self._handle_failure(action, wiki)

    async def _handle_new_proposal(self, action: ControlAction, wiki: Optional[Any]) -> Dict[str, Any]:
        if not self._builder:
            return {"action": "build", "success": False}
        task_desc = action.payload.get("task_description", "")
        draft = self._builder.build_from_task(task_desc)
        if draft and self._auditor:
            audit = self._auditor.audit(draft.skill)
            if audit.passed and self._librarian:
                await self._librarian.register_new(draft.skill)
                return {"action": "build", "success": True, "skill_name": draft.skill.name}
        return {"action": "build", "success": False}

    async def _handle_audit_failed(self, action: ControlAction, wiki: Optional[Any]) -> Dict[str, Any]:
        return await self._handle_failure(action, wiki)
