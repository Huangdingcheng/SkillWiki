"""状态追踪器 — 追踪 Agent 执行过程中的世界状态变化。"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class StateSnapshot:
    """某一时刻的状态快照。"""
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    skill_id: Optional[str] = None
    skill_name: Optional[str] = None
    state: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    label: str = ""   # "before_skill_x" / "after_skill_x"


@dataclass
class RuntimeMemory:
    """Task-local execution memory for planning and repair evidence."""

    task_id: str
    goal: str = ""
    selected_skills: List[str] = field(default_factory=list)
    step_inputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    step_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    failure_events: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    verification_summary: Dict[str, Any] = field(default_factory=dict)
    reflection_summary: Dict[str, Any] = field(default_factory=dict)

    def remember_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append({
            "event_type": event_type,
            "payload": copy.deepcopy(payload),
            "timestamp": datetime.utcnow().isoformat(),
        })

    def remember_step_start(
        self,
        step_id: str,
        skill_id: str,
        skill_name: str,
        input_data: Dict[str, Any],
    ) -> None:
        if skill_id and skill_id not in self.selected_skills:
            self.selected_skills.append(skill_id)
        self.step_inputs[step_id] = {
            "skill_id": skill_id,
            "skill_name": skill_name,
            "input": copy.deepcopy(input_data),
        }
        self.remember_event("step_started", {"step_id": step_id, "skill_id": skill_id})

    def remember_step_success(
        self,
        step_id: str,
        skill_id: str,
        output_data: Dict[str, Any],
    ) -> None:
        self.step_outputs[step_id] = {
            "skill_id": skill_id,
            "output": copy.deepcopy(output_data),
        }
        self.remember_event("step_completed", {"step_id": step_id, "skill_id": skill_id})

    def remember_failure(
        self,
        step_id: str,
        skill_id: str,
        error: str,
        failure_type: str = "",
    ) -> None:
        event = {
            "step_id": step_id,
            "skill_id": skill_id,
            "error": error,
            "failure_type": failure_type,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.failure_events.append(event)
        self.remember_event("step_failed", event)

    def to_summary(self) -> Dict[str, Any]:
        summary = {
            "task_id": self.task_id,
            "goal": self.goal,
            "selected_skills": list(self.selected_skills),
            "step_count": len(self.step_inputs),
            "failure_count": len(self.failure_events),
            "failed_skill_ids": list({
                event["skill_id"]
                for event in self.failure_events
                if event.get("skill_id")
            }),
            "events": len(self.events),
        }
        if self.verification_summary:
            summary["verification"] = copy.deepcopy(self.verification_summary)
        if self.reflection_summary:
            summary["reflection"] = copy.deepcopy(self.reflection_summary)
        return summary


class StateTracker:
    """追踪 Agent 执行过程中的状态变化。

    维护一个状态栈，支持：
    - 快照（执行前/后）
    - 状态回滚（执行失败时）
    - 状态差异计算
    - 条件检查（前置/后置条件验证）
    """

    def __init__(self, task_id: str, initial_state: Optional[Dict[str, Any]] = None) -> None:
        self._task_id = task_id
        self._current: Dict[str, Any] = copy.deepcopy(initial_state or {})
        self._snapshots: List[StateSnapshot] = []
        self._checkpoint_stack: List[Dict[str, Any]] = []
        self._memory = RuntimeMemory(task_id=task_id)

        # 记录初始快照
        self._take_snapshot(label="initial")

    @property
    def current(self) -> Dict[str, Any]:
        return copy.deepcopy(self._current)

    @property
    def snapshots(self) -> List[StateSnapshot]:
        return list(self._snapshots)

    @property
    def memory(self) -> RuntimeMemory:
        return self._memory

    def update(self, changes: Dict[str, Any]) -> None:
        """更新当前状态（深度合并）。"""
        self._deep_merge(self._current, changes)

    def set(self, key: str, value: Any) -> None:
        """设置单个状态键。"""
        self._current[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._current.get(key, default)

    def snapshot_before(self, skill_id: str, skill_name: str) -> StateSnapshot:
        """在执行 Skill 前拍摄快照。"""
        return self._take_snapshot(
            skill_id=skill_id,
            skill_name=skill_name,
            label=f"before_{skill_name}",
        )

    def snapshot_after(self, skill_id: str, skill_name: str) -> StateSnapshot:
        """在执行 Skill 后拍摄快照。"""
        return self._take_snapshot(
            skill_id=skill_id,
            skill_name=skill_name,
            label=f"after_{skill_name}",
        )

    def push_checkpoint(self) -> None:
        """压入检查点（用于可回滚的操作）。"""
        self._checkpoint_stack.append(copy.deepcopy(self._current))

    def rollback(self) -> bool:
        """回滚到最近的检查点。"""
        if not self._checkpoint_stack:
            return False
        self._current = self._checkpoint_stack.pop()
        self._take_snapshot(label="rollback")
        return True

    def diff(self, snap_a: StateSnapshot, snap_b: StateSnapshot) -> Dict[str, Any]:
        """计算两个快照之间的状态差异。"""
        diff: Dict[str, Any] = {}
        all_keys = set(snap_a.state) | set(snap_b.state)
        for key in all_keys:
            old = snap_a.state.get(key)
            new = snap_b.state.get(key)
            if old != new:
                diff[key] = {"before": old, "after": new}
        return diff

    def check_conditions(self, conditions: List[str]) -> Dict[str, bool]:
        """检查条件列表（简单关键词匹配，生产环境可替换为 LLM 判断）。"""
        results: Dict[str, bool] = {}
        for cond in conditions:
            # 简单规则：检查状态中是否有对应的 True 值
            cond_lower = cond.lower()
            matched = False
            for key, val in self._current.items():
                if key.lower() in cond_lower and val:
                    matched = True
                    break
            results[cond] = matched
        return results

    def get_execution_trace(self) -> List[Dict[str, Any]]:
        """返回执行轨迹（所有快照的摘要）。"""
        return [
            {
                "label": s.label,
                "skill_name": s.skill_name,
                "timestamp": s.timestamp.isoformat(),
                "state_keys": list(s.state.keys()),
            }
            for s in self._snapshots
        ]

    def _take_snapshot(
        self,
        skill_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        label: str = "",
    ) -> StateSnapshot:
        snap = StateSnapshot(
            task_id=self._task_id,
            skill_id=skill_id,
            skill_name=skill_name,
            state=copy.deepcopy(self._current),
            label=label,
        )
        self._snapshots.append(snap)
        return snap

    def _deep_merge(self, base: Dict[str, Any], updates: Dict[str, Any]) -> None:
        for key, val in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(val, dict):
                self._deep_merge(base[key], val)
            else:
                base[key] = val
