"""演化引擎 — 协调 Skill 的自动演化（修复、合并、拆分、废弃）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ...models.skill_model import Skill, SkillState
from ...utils.logger import get_logger
from .monitor import HealthStatus, SkillMonitor, SystemHealthReport
from .repair import RepairResult, SkillRepair

logger = get_logger(__name__)


class EvolutionAction(str, Enum):
    REPAIR = "repair"
    MERGE = "merge"
    SPLIT = "split"
    DEPRECATE = "deprecate"
    NONE = "none"


@dataclass
class EvolutionTask:
    """单个演化任务。"""
    task_id: str
    action: EvolutionAction
    skill_ids: List[str]
    reason: str
    priority: int = 0   # 越高越优先
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed: bool = False
    result: Optional[Dict[str, Any]] = None


@dataclass
class EvolutionReport:
    """演化周期报告。"""
    cycle_id: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    repaired: List[str] = field(default_factory=list)
    deprecated: List[str] = field(default_factory=list)
    merged: List[Tuple[List[str], str]] = field(default_factory=list)  # (source_ids, new_id)
    split: List[Tuple[str, List[str]]] = field(default_factory=list)   # (source_id, new_ids)
    errors: List[str] = field(default_factory=list)


class EvolutionEngine:
    """Skill 演化引擎。

    定期扫描 Skill 库，识别需要演化的 Skill，
    并协调修复、合并、拆分、废弃等操作。
    """

    def __init__(
        self,
        monitor: SkillMonitor,
        repair: SkillRepair,
        merger: Any,    # SkillMerger，避免循环导入
        wiki_manager: Any,  # SkillWikiManager
        graph_manager: Any,  # SkillGraphManager
        merge_similarity_threshold: float = 0.85,
        split_granularity_threshold: int = 4,  # 粒度 >= 此值时考虑拆分
    ) -> None:
        self._monitor = monitor
        self._repair = repair
        self._merger = merger
        self._wiki = wiki_manager
        self._graph = graph_manager
        self._merge_threshold = merge_similarity_threshold
        self._split_threshold = split_granularity_threshold

    async def run_evolution_cycle(self) -> EvolutionReport:
        """运行一次完整的演化周期。"""
        import uuid
        report = EvolutionReport(cycle_id=str(uuid.uuid4()))
        logger.info(f"演化周期开始: {report.cycle_id}")

        # 1. 获取所有 Released/Degraded Skill
        skills = await self._wiki.list(
            state=None,  # 获取所有状态
            limit=500,
        )
        active_skills = [
            s for s in skills
            if s.state in (SkillState.RELEASED, SkillState.DEGRADED)
        ]

        # 2. 健康评估
        system_report = self._monitor.evaluate_batch(active_skills)

        # 3. 生成演化任务
        tasks = self._generate_tasks(active_skills, system_report)
        report.tasks_total = len(tasks)
        logger.info(f"生成演化任务: {len(tasks)} 个")

        # 4. 执行演化任务（按优先级）
        tasks.sort(key=lambda t: t.priority, reverse=True)
        for task in tasks:
            try:
                await self._execute_task(task, report)
                report.tasks_completed += 1
            except Exception as e:
                report.tasks_failed += 1
                report.errors.append(f"任务 {task.task_id} 失败: {e}")
                logger.error(f"演化任务失败: {task.action.value} - {e}")

        report.completed_at = datetime.utcnow()
        logger.info(
            f"演化周期完成: 修复={len(report.repaired)}, "
            f"废弃={len(report.deprecated)}, "
            f"合并={len(report.merged)}, "
            f"拆分={len(report.split)}"
        )
        return report

    def _generate_tasks(
        self,
        skills: List[Skill],
        system_report: SystemHealthReport,
    ) -> List[EvolutionTask]:
        """根据健康报告生成演化任务。"""
        import uuid
        tasks: List[EvolutionTask] = []
        skill_map = {s.skill_id: s for s in skills}

        for hr in system_report.skill_reports:
            skill = skill_map.get(hr.skill_id)
            if not skill:
                continue

            if hr.status == HealthStatus.CRITICAL:
                # 危急 → 修复（高优先级）
                tasks.append(EvolutionTask(
                    task_id=str(uuid.uuid4()),
                    action=EvolutionAction.REPAIR,
                    skill_ids=[skill.skill_id],
                    reason=f"成功率危急: {hr.success_rate:.1%}",
                    priority=10,
                ))
            elif hr.status == HealthStatus.DEGRADED:
                # 退化 → 修复（中优先级）
                tasks.append(EvolutionTask(
                    task_id=str(uuid.uuid4()),
                    action=EvolutionAction.REPAIR,
                    skill_ids=[skill.skill_id],
                    reason=f"成功率退化: {hr.success_rate:.1%}",
                    priority=5,
                ))
            elif hr.status == HealthStatus.STALE:
                # 过期 → 废弃（低优先级）
                tasks.append(EvolutionTask(
                    task_id=str(uuid.uuid4()),
                    action=EvolutionAction.DEPRECATE,
                    skill_ids=[skill.skill_id],
                    reason="长期未使用",
                    priority=1,
                ))

            # 粒度过粗 → 拆分
            if skill.granularity_level >= self._split_threshold and skill.metrics.usage_count > 20:
                tasks.append(EvolutionTask(
                    task_id=str(uuid.uuid4()),
                    action=EvolutionAction.SPLIT,
                    skill_ids=[skill.skill_id],
                    reason=f"粒度过粗 (level={skill.granularity_level})",
                    priority=3,
                ))

        return tasks

    async def _execute_task(self, task: EvolutionTask, report: EvolutionReport) -> None:
        """执行单个演化任务。"""
        if task.action == EvolutionAction.REPAIR:
            await self._do_repair(task, report)
        elif task.action == EvolutionAction.DEPRECATE:
            await self._do_deprecate(task, report)
        elif task.action == EvolutionAction.MERGE:
            await self._do_merge(task, report)
        elif task.action == EvolutionAction.SPLIT:
            await self._do_split(task, report)
        task.completed = True

    async def _do_repair(self, task: EvolutionTask, report: EvolutionReport) -> None:
        skill = await self._wiki.get(task.skill_ids[0])
        if not skill:
            return
        health = self._monitor.evaluate_skill(skill)
        result = await self._repair.repair(skill, health)

        if result.should_deprecate:
            await self._wiki.deprecate(skill.skill_id, result.root_cause)
            report.deprecated.append(skill.skill_id)
        elif result.success and result.repaired_skill:
            await self._wiki.create(result.repaired_skill)
            await self._graph.sync_skill(result.repaired_skill)
            await self._graph.add_evolution(result.repaired_skill.skill_id, skill.skill_id)
            report.repaired.append(result.repaired_skill.skill_id)

    async def _do_deprecate(self, task: EvolutionTask, report: EvolutionReport) -> None:
        skill = await self._wiki.get(task.skill_ids[0])
        if not skill:
            return
        await self._wiki.deprecate(skill.skill_id, task.reason)
        report.deprecated.append(skill.skill_id)

    async def _do_merge(self, task: EvolutionTask, report: EvolutionReport) -> None:
        if len(task.skill_ids) < 2:
            return
        skill_a = await self._wiki.get(task.skill_ids[0])
        skill_b = await self._wiki.get(task.skill_ids[1])
        if not skill_a or not skill_b:
            return
        merge_result = await self._merger.merge(skill_a, skill_b)
        if merge_result.success and merge_result.merged_skill:
            await self._wiki.create(merge_result.merged_skill)
            await self._graph.sync_skill(merge_result.merged_skill)
            for edge in merge_result.edges_to_create:
                await self._graph.create_edge(edge)
            report.merged.append((task.skill_ids, merge_result.merged_skill.skill_id))

    async def _do_split(self, task: EvolutionTask, report: EvolutionReport) -> None:
        skill = await self._wiki.get(task.skill_ids[0])
        if not skill:
            return
        split_result = await self._merger.split(skill)
        if split_result.success and split_result.sub_skills:
            new_ids = []
            for sub in split_result.sub_skills:
                await self._wiki.create(sub)
                await self._graph.sync_skill(sub)
                new_ids.append(sub.skill_id)
            for edge in split_result.edges_to_create:
                await self._graph.create_edge(edge)
            report.split.append((skill.skill_id, new_ids))
