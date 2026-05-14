"""Phase 5-7 治理层、运行时层、演化层测试套件。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from skillos.layers.feedback_evolution import (
    EvolutionAction,
    HealthStatus,
    SkillHealthReport,
    SkillMonitor,
    SkillRepair,
    SystemHealthReport,
)
from skillos.layers.skill_governance import (
    ChangeType,
    MergeResult,
    ReviewResult,
    ReviewStatus,
    SkillMerger,
    SkillReviewer,
    SplitResult,
    VersionController,
)
from skillos.layers.skill_runtime import (
    ExecutionPlan,
    PlanStep,
    SkillExecutor,
    SkillPlanner,
    StateTracker,
    StepStatus,
)
from skillos.models import (
    EdgeType,
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillMetrics,
    SkillState,
    SkillType,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def mock_llm():
    llm = MagicMock()
    response = MagicMock()
    response.content = "{}"
    llm.chat.return_value = response
    return llm


@pytest.fixture
def released_skill():
    return Skill(
        name="fill_form",
        description="填写页面上的结构化表单，支持多种字段类型",
        skill_type=SkillType.FUNCTIONAL,
        domain="web",
        state=SkillState.RELEASED,
        tags=["web", "form"],
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"fields": {"type": "object"}}, "required": ["fields"]},
            output_schema={"type": "object", "properties": {"filled_count": {"type": "integer"}}},
            preconditions=["页面上存在可编辑的表单字段"],
            postconditions=["所有字段已填写"],
        ),
        implementation=SkillImplementation(
            sub_skill_ids=["id1", "id2"],
            prompt_template="填写表单字段 {fields}",
        ),
    )


@pytest.fixture
def degraded_skill():
    skill = Skill(
        name="broken_skill",
        description="一个成功率很低的 Skill",
        state=SkillState.DEGRADED,
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
        ),
        implementation=SkillImplementation(prompt_template="执行操作"),
    )
    # 模拟大量失败
    for _ in range(3):
        skill.record_execution(success=True, latency_ms=100)
    for _ in range(7):
        skill.record_execution(success=False, latency_ms=200)
    return skill


# ===========================================================================
# VersionController Tests
# ===========================================================================

class TestVersionController:

    def test_record_change(self, released_skill):
        vc = VersionController()
        record = vc.record_change(
            released_skill,
            ChangeType.DESCRIPTION_UPDATED,
            "更新了描述",
        )
        assert record.skill_id == released_skill.skill_id
        assert record.change_type == ChangeType.DESCRIPTION_UPDATED

    def test_get_history(self, released_skill):
        vc = VersionController()
        vc.record_change(released_skill, ChangeType.CREATED, "创建")
        vc.record_change(released_skill, ChangeType.DESCRIPTION_UPDATED, "更新描述")
        history = vc.get_history(released_skill.skill_id)
        assert len(history) == 2

    def test_get_history_empty(self):
        vc = VersionController()
        assert vc.get_history("nonexistent") == []

    def test_compute_diff_description_change(self, released_skill):
        vc = VersionController()
        old = released_skill.model_copy(deep=True)
        new = released_skill.model_copy(deep=True)
        new.description = "新的描述"
        diff = vc.compute_diff(old, new)
        assert "description" in diff
        assert diff["description"]["new"] == "新的描述"

    def test_compute_diff_no_change(self, released_skill):
        vc = VersionController()
        diff = vc.compute_diff(released_skill, released_skill.model_copy(deep=True))
        assert diff == {}

    def test_compute_diff_interface_change(self, released_skill):
        vc = VersionController()
        old = released_skill.model_copy(deep=True)
        new = released_skill.model_copy(deep=True)
        new.interface.preconditions = ["新条件"]
        diff = vc.compute_diff(old, new)
        assert "interface" in diff

    def test_suggest_version_bump_interface(self):
        vc = VersionController()
        assert vc.suggest_version_bump({"interface": {}}) == "major"

    def test_suggest_version_bump_description(self):
        vc = VersionController()
        assert vc.suggest_version_bump({"description": {}}) == "patch"

    def test_suggest_version_bump_skill_type(self):
        vc = VersionController()
        assert vc.suggest_version_bump({"skill_type": {}}) == "minor"

    def test_determine_change_type(self):
        vc = VersionController()
        assert vc.determine_change_type({"interface": {}}) == ChangeType.INTERFACE_CHANGED
        assert vc.determine_change_type({"description": {}}) == ChangeType.DESCRIPTION_UPDATED
        assert vc.determine_change_type({"tags": {}}) == ChangeType.TAGS_UPDATED
        assert vc.determine_change_type({"state": {}}) == ChangeType.STATE_TRANSITIONED

    def test_create_new_version(self, released_skill):
        vc = VersionController()
        old = released_skill.model_copy(deep=True)
        new = released_skill.model_copy(deep=True)
        new.description = "更新的描述"

        updated, record = vc.create_new_version(old, new)
        assert updated.version != old.version
        assert updated.state == SkillState.DRAFT
        assert record.change_type == ChangeType.DESCRIPTION_UPDATED

    def test_is_breaking_change(self, released_skill):
        vc = VersionController()
        record = vc.record_change(
            released_skill, ChangeType.INTERFACE_CHANGED, "接口变更"
        )
        assert record.is_breaking()

    def test_is_not_breaking_change(self, released_skill):
        vc = VersionController()
        record = vc.record_change(
            released_skill, ChangeType.DESCRIPTION_UPDATED, "描述更新"
        )
        assert not record.is_breaking()


# ===========================================================================
# SkillReviewer Tests
# ===========================================================================

class TestSkillReviewer:

    @pytest.mark.asyncio
    async def test_review_auto_approved(self, mock_llm, released_skill):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "overall_score": 9.0,
            "status": "approved",
            "comments": [],
            "summary": "质量优秀",
            "auto_fix_suggestions": {},
        })
        reviewer = SkillReviewer(mock_llm, auto_approve_threshold=8.0)
        result = await reviewer.review(released_skill)
        assert result.is_approved
        assert result.status == ReviewStatus.AUTO_APPROVED

    @pytest.mark.asyncio
    async def test_review_rejected_low_score(self, mock_llm, released_skill):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "overall_score": 3.0,
            "status": "rejected",
            "comments": [
                {"field": "implementation", "severity": "error", "message": "实现不完整"},
                {"field": "interface", "severity": "error", "message": "接口缺失"},
                {"field": "description", "severity": "error", "message": "描述不清"},
            ],
            "summary": "质量不达标",
        })
        reviewer = SkillReviewer(mock_llm, auto_reject_threshold=4.0)
        result = await reviewer.review(released_skill)
        assert not result.is_approved
        assert result.status == ReviewStatus.REJECTED

    @pytest.mark.asyncio
    async def test_review_needs_revision(self, mock_llm, released_skill):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "overall_score": 6.5,
            "status": "needs_revision",
            "comments": [
                {"field": "test_cases", "severity": "warning", "message": "测试用例不足"},
            ],
            "summary": "需要补充测试",
        })
        reviewer = SkillReviewer(mock_llm)
        result = await reviewer.review(released_skill)
        assert result.status == ReviewStatus.NEEDS_REVISION
        warning_comments = [c for c in result.comments if c.severity == "warning"]
        assert len(warning_comments) == 1

    @pytest.mark.asyncio
    async def test_review_llm_failure_fallback(self, mock_llm, released_skill):
        mock_llm.chat.return_value.content = "这不是 JSON"
        reviewer = SkillReviewer(mock_llm)
        result = await reviewer.review(released_skill)
        assert result.status == ReviewStatus.NEEDS_REVISION

    @pytest.mark.asyncio
    async def test_review_and_release(self, mock_llm):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "overall_score": 9.5,
            "status": "approved",
            "comments": [],
            "summary": "优秀",
        })
        skill = Skill(
            name="test_skill",
            description="测试 Skill",
            state=SkillState.VERIFIED,
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object", "properties": {}},
            ),
            implementation=SkillImplementation(prompt_template="执行"),
        )
        reviewer = SkillReviewer(mock_llm, auto_approve_threshold=8.0)
        updated_skill, result = await reviewer.review_and_release(skill)
        assert result.is_approved
        assert updated_skill.state == SkillState.RELEASED


# ===========================================================================
# SkillMerger Tests
# ===========================================================================

class TestSkillMerger:

    @pytest.fixture
    def skill_a(self):
        return Skill(
            name="fill_login_form",
            description="填写登录表单",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            state=SkillState.RELEASED,
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {"username": {"type": "string"}, "password": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"logged_in": {"type": "boolean"}}},
                preconditions=["登录页面已打开"],
            ),
            implementation=SkillImplementation(prompt_template="填写登录表单"),
        )

    @pytest.fixture
    def skill_b(self):
        return Skill(
            name="fill_register_form",
            description="填写注册表单",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            state=SkillState.RELEASED,
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {"email": {"type": "string"}, "password": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"registered": {"type": "boolean"}}},
                preconditions=["注册页面已打开"],
            ),
            implementation=SkillImplementation(prompt_template="填写注册表单"),
        )

    @pytest.mark.asyncio
    async def test_merge_success(self, mock_llm, skill_a, skill_b):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "merged_name": "fill_auth_form",
            "merged_description": "填写认证相关表单（登录/注册）",
            "merged_type": "functional",
            "merged_domain": "web",
            "merged_granularity_level": 2,
            "merged_tags": ["web", "form", "auth"],
            "merged_interface": {
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {}},
                "preconditions": ["认证页面已打开"],
                "postconditions": ["认证操作已完成"],
                "side_effects": [],
            },
            "merged_implementation": {
                "language": "python",
                "prompt_template": "填写认证表单 {fields}",
            },
            "merge_rationale": "两个 Skill 功能高度相似，合并为通用版本",
        })
        merger = SkillMerger(mock_llm)
        result = await merger.merge(skill_a, skill_b)
        assert result.success
        assert result.merged_skill is not None
        assert result.merged_skill.name == "fill_auth_form"
        assert result.merged_skill.state == SkillState.DRAFT
        replaces_edges = [
            edge for edge in result.edges_to_create
            if edge.edge_type == EdgeType.REPLACES
        ]
        similar_edges = [
            edge for edge in result.edges_to_create
            if edge.edge_type == EdgeType.SIMILAR_TO
        ]
        assert len(replaces_edges) == 2
        assert len(similar_edges) == 1

    @pytest.mark.asyncio
    async def test_merge_llm_failure(self, mock_llm, skill_a, skill_b):
        mock_llm.chat.return_value.content = "无效响应"
        merger = SkillMerger(mock_llm)
        result = await merger.merge(skill_a, skill_b)
        assert not result.success
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_split_success(self, mock_llm, released_skill):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "sub_skills": [
                {
                    "name": "locate_form_field",
                    "description": "定位表单字段",
                    "skill_type": "atomic",
                    "granularity_level": 1,
                    "domain": "web",
                    "tags": ["web"],
                    "interface": {
                        "input_schema": {"type": "object", "properties": {}},
                        "output_schema": {"type": "object", "properties": {}},
                        "preconditions": [],
                        "postconditions": [],
                    },
                    "implementation": {"prompt_template": "定位字段"},
                },
                {
                    "name": "input_field_value",
                    "description": "输入字段值",
                    "skill_type": "atomic",
                    "granularity_level": 1,
                    "domain": "web",
                    "tags": ["web"],
                    "interface": {
                        "input_schema": {"type": "object", "properties": {}},
                        "output_schema": {"type": "object", "properties": {}},
                        "preconditions": [],
                        "postconditions": [],
                    },
                    "implementation": {"prompt_template": "输入值"},
                },
            ],
            "split_rationale": "fill_form 粒度过粗，拆分为定位和输入两个原子操作",
            "composition_order": ["locate_form_field", "input_field_value"],
        })
        merger = SkillMerger(mock_llm)
        result = await merger.split(released_skill)
        assert result.success
        assert len(result.sub_skills) == 2
        assert all(s.state == SkillState.DRAFT for s in result.sub_skills)
        evolved_edges = [
            edge for edge in result.edges_to_create
            if edge.edge_type == EdgeType.EVOLVED_FROM
        ]
        composition_edges = [
            edge for edge in result.edges_to_create
            if edge.edge_type == EdgeType.COMPOSES_WITH
        ]
        assert len(evolved_edges) == 2
        assert len(composition_edges) == 2


# ===========================================================================
# StateTracker Tests
# ===========================================================================

class TestStateTracker:

    def test_initial_state(self):
        tracker = StateTracker("task-1", {"page_loaded": True})
        assert tracker.current["page_loaded"] is True

    def test_update_state(self):
        tracker = StateTracker("task-1")
        tracker.update({"form_filled": True, "count": 3})
        assert tracker.current["form_filled"] is True
        assert tracker.current["count"] == 3

    def test_deep_merge(self):
        tracker = StateTracker("task-1", {"nested": {"a": 1, "b": 2}})
        tracker.update({"nested": {"b": 99, "c": 3}})
        state = tracker.current
        assert state["nested"]["a"] == 1   # 保留
        assert state["nested"]["b"] == 99  # 更新
        assert state["nested"]["c"] == 3   # 新增

    def test_snapshot_before_after(self):
        tracker = StateTracker("task-1")
        tracker.snapshot_before("skill-1", "click_element")
        tracker.update({"clicked": True})
        tracker.snapshot_after("skill-1", "click_element")
        assert len(tracker.snapshots) == 3  # initial + before + after

    def test_rollback(self):
        tracker = StateTracker("task-1", {"value": 1})
        tracker.push_checkpoint()
        tracker.update({"value": 99})
        assert tracker.current["value"] == 99
        ok = tracker.rollback()
        assert ok
        assert tracker.current["value"] == 1

    def test_rollback_empty_stack(self):
        tracker = StateTracker("task-1")
        assert not tracker.rollback()

    def test_diff(self):
        tracker = StateTracker("task-1")
        snap1 = tracker._take_snapshot(label="s1")
        tracker.update({"new_key": "new_value"})
        snap2 = tracker._take_snapshot(label="s2")
        diff = tracker.diff(snap1, snap2)
        assert "new_key" in diff
        assert diff["new_key"]["before"] is None
        assert diff["new_key"]["after"] == "new_value"

    def test_execution_trace(self):
        tracker = StateTracker("task-1")
        tracker.snapshot_before("s1", "skill_a")
        tracker.snapshot_after("s1", "skill_a")
        trace = tracker.get_execution_trace()
        assert len(trace) == 3
        assert trace[1]["skill_name"] == "skill_a"


# ===========================================================================
# ExecutionPlan Tests
# ===========================================================================

class TestExecutionPlan:

    def _make_plan(self) -> ExecutionPlan:
        plan = ExecutionPlan(task_id="t1", task_description="测试任务")
        s1 = PlanStep(step_index=0, skill_id="s1", skill_name="skill_a")
        s2 = PlanStep(step_index=1, skill_id="s2", skill_name="skill_b", depends_on=[s1.step_id])
        s3 = PlanStep(step_index=2, skill_id="s3", skill_name="skill_c", depends_on=[s1.step_id])
        plan.steps = [s1, s2, s3]
        return plan, s1, s2, s3

    def test_get_ready_steps_initial(self):
        plan, s1, s2, s3 = self._make_plan()
        ready = plan.get_ready_steps()
        assert len(ready) == 1
        assert ready[0].step_id == s1.step_id

    def test_get_ready_steps_after_s1(self):
        plan, s1, s2, s3 = self._make_plan()
        s1.status = StepStatus.SUCCESS
        ready = plan.get_ready_steps()
        assert len(ready) == 2  # s2 和 s3 都可以并行执行

    def test_is_complete(self):
        plan, s1, s2, s3 = self._make_plan()
        assert not plan.is_complete
        for s in [s1, s2, s3]:
            s.status = StepStatus.SUCCESS
        assert plan.is_complete

    def test_has_failures(self):
        plan, s1, s2, s3 = self._make_plan()
        assert not plan.has_failures
        s2.status = StepStatus.FAILED
        assert plan.has_failures

    def test_to_summary(self):
        plan, s1, s2, s3 = self._make_plan()
        s1.status = StepStatus.SUCCESS
        summary = plan.to_summary()
        assert summary["total"] == 3
        assert summary["completed"] == 1


# ===========================================================================
# SkillExecutor Tests
# ===========================================================================

class TestSkillExecutor:

    @pytest.mark.asyncio
    async def test_execute_single_success(self, released_skill):
        executor = SkillExecutor()
        record = await executor.execute_single(
            released_skill,
            {"fields": {"username": "test"}},
        )
        assert record.status.value == "success"
        assert record.latency_ms is not None

    @pytest.mark.asyncio
    async def test_execute_plan_simple(self, released_skill):
        plan = ExecutionPlan(task_id="t1", task_description="测试")
        step = PlanStep(
            step_index=0,
            skill_id=released_skill.skill_id,
            skill_name=released_skill.name,
        )
        plan.steps = [step]
        skill_map = {released_skill.skill_id: released_skill}

        executor = SkillExecutor()
        final_state = await executor.execute_plan(plan, skill_map)
        assert step.status == StepStatus.SUCCESS
        assert plan.is_complete

    @pytest.mark.asyncio
    async def test_execute_plan_missing_skill(self):
        plan = ExecutionPlan(task_id="t1", task_description="测试")
        step = PlanStep(step_index=0, skill_id="nonexistent", skill_name="missing")
        plan.steps = [step]

        executor = SkillExecutor()
        await executor.execute_plan(plan, {})
        assert step.status == StepStatus.FAILED

    def test_event_callback(self, released_skill):
        events = []
        executor = SkillExecutor()
        executor.add_event_callback(lambda t, d: events.append((t, d)))
        executor._emit("test_event", {"key": "value"})
        assert len(events) == 1
        assert events[0][0] == "test_event"


# ===========================================================================
# SkillMonitor Tests
# ===========================================================================

class TestSkillMonitor:

    def test_healthy_skill(self, released_skill):
        for _ in range(10):
            released_skill.record_execution(success=True, latency_ms=100)
        monitor = SkillMonitor()
        report = monitor.evaluate_skill(released_skill)
        assert report.status == HealthStatus.HEALTHY
        assert report.success_rate == 1.0

    def test_degraded_skill(self, degraded_skill):
        monitor = SkillMonitor()
        report = monitor.evaluate_skill(degraded_skill)
        assert report.status in (HealthStatus.DEGRADED, HealthStatus.CRITICAL)
        assert report.needs_attention

    def test_unknown_status_no_executions(self):
        skill = Skill(
            name="new_skill",
            description="新 Skill",
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object", "properties": {}},
            ),
            implementation=SkillImplementation(prompt_template="执行"),
        )
        monitor = SkillMonitor()
        report = monitor.evaluate_skill(skill)
        assert report.status == HealthStatus.UNKNOWN

    def test_stale_skill(self, released_skill):
        for _ in range(10):
            released_skill.record_execution(success=True, latency_ms=100)
        # 模拟长期未使用
        released_skill.metrics.last_used_at = datetime.utcnow() - timedelta(days=40)
        monitor = SkillMonitor()
        report = monitor.evaluate_skill(released_skill)
        assert report.status == HealthStatus.STALE

    def test_evaluate_batch(self, released_skill, degraded_skill):
        for _ in range(10):
            released_skill.record_execution(success=True, latency_ms=100)
        monitor = SkillMonitor()
        system_report = monitor.evaluate_batch([released_skill, degraded_skill])
        assert system_report.total_skills == 2
        assert system_report.healthy_count >= 1

    def test_should_trigger_repair(self, degraded_skill):
        monitor = SkillMonitor()
        assert monitor.should_trigger_repair(degraded_skill)

    def test_should_not_trigger_repair_healthy(self, released_skill):
        for _ in range(10):
            released_skill.record_execution(success=True, latency_ms=100)
        monitor = SkillMonitor()
        assert not monitor.should_trigger_repair(released_skill)


# ===========================================================================
# SkillRepair Tests
# ===========================================================================

class TestSkillRepair:

    @pytest.mark.asyncio
    async def test_repair_success(self, mock_llm, degraded_skill):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "root_cause": "Prompt 模板不够精确",
            "fix_type": "prompt_fix",
            "fixed_implementation": {
                "language": "python",
                "prompt_template": "改进后的 prompt: {input}",
            },
            "updated_preconditions": ["条件1"],
            "updated_postconditions": [],
            "confidence": 0.85,
            "repair_notes": "优化了 prompt 模板",
        })
        monitor = SkillMonitor()
        health = monitor.evaluate_skill(degraded_skill)
        repair = SkillRepair(mock_llm)
        result = await repair.repair(degraded_skill, health)
        assert result.success
        assert result.repaired_skill is not None
        assert result.repaired_skill.state == SkillState.DRAFT
        assert result.repaired_skill.version != degraded_skill.version

    @pytest.mark.asyncio
    async def test_repair_recommends_deprecate(self, mock_llm, degraded_skill):
        import json
        mock_llm.chat.return_value.content = json.dumps({
            "root_cause": "功能已被其他 Skill 替代",
            "fix_type": "deprecate",
            "confidence": 0.9,
            "repair_notes": "建议废弃",
        })
        monitor = SkillMonitor()
        health = monitor.evaluate_skill(degraded_skill)
        repair = SkillRepair(mock_llm)
        result = await repair.repair(degraded_skill, health)
        assert result.success
        assert result.should_deprecate

    @pytest.mark.asyncio
    async def test_repair_llm_failure(self, mock_llm, degraded_skill):
        mock_llm.chat.return_value.content = "无效响应"
        monitor = SkillMonitor()
        health = monitor.evaluate_skill(degraded_skill)
        repair = SkillRepair(mock_llm)
        result = await repair.repair(degraded_skill, health)
        assert not result.success
        assert result.error != ""
