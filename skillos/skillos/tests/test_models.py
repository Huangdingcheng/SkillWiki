"""Phase 1 核心数据模型测试套件。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

import pytest

from skillos.models import (
    EdgeType,
    ExecutionStatus,
    ExperienceSourceType,
    ExperienceUnit,
    GraphStats,
    MetaSkillCategory,
    Skill,
    SkillEdge,
    SkillExecutionRecord,
    SkillGraphNode,
    SkillImplementation,
    SkillInterface,
    SkillMetrics,
    SkillProposal,
    SkillProposalStatus,
    SkillProvenance,
    SkillState,
    SkillSubgraph,
    SkillTestCase,
    SkillType,
    TrajectoryStep,
)


# ===========================================================================
# Skill Model Tests
# ===========================================================================

class TestSkillModel:

    def test_minimal_skill_creation(self):
        skill = Skill(name="click_element")
        assert skill.name == "click_element"
        assert skill.skill_type == SkillType.ATOMIC
        assert skill.state == SkillState.DRAFT
        assert skill.version == "1.0.0"
        assert skill.skill_id  # UUID 自动生成

    def test_display_name_auto_set(self):
        skill = Skill(name="fill_form")
        assert skill.display_name == "Fill Form"

    def test_display_name_custom(self):
        skill = Skill(name="fill_form", display_name="Fill Structured Form")
        assert skill.display_name == "Fill Structured Form"

    def test_name_validation_snake_case(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Skill(name="FillForm")  # 不是 snake_case
        with pytest.raises(ValidationError):
            Skill(name="fill-form")  # 连字符不允许
        with pytest.raises(ValidationError):
            Skill(name="123skill")  # 数字开头

    def test_name_valid_snake_case(self):
        skill = Skill(name="fill_form_v2")
        assert skill.name == "fill_form_v2"

    def test_tags_normalized(self):
        skill = Skill(name="test_skill", tags=["  Web  ", "FORM", "input"])
        assert skill.tags == ["web", "form", "input"]

    def test_meta_skill_requires_category(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Skill(name="strategic_skill", skill_type=SkillType.STRATEGIC)

    def test_meta_skill_with_category(self):
        skill = Skill(
            name="lifecycle_manager",
            skill_type=SkillType.STRATEGIC,
            meta_category=MetaSkillCategory.LIFECYCLE,
        )
        assert skill.meta_category == MetaSkillCategory.LIFECYCLE

    def test_non_meta_skill_cannot_have_category(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Skill(
                name="atomic_skill",
                skill_type=SkillType.ATOMIC,
                meta_category=MetaSkillCategory.LIFECYCLE,
            )

    def test_version_pattern(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Skill(name="test_skill", version="v1.0")
        with pytest.raises(ValidationError):
            Skill(name="test_skill", version="1.0")

    def test_granularity_level_range(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Skill(name="test_skill", granularity_level=0)
        with pytest.raises(ValidationError):
            Skill(name="test_skill", granularity_level=6)

    def test_state_transition_valid(self):
        skill = Skill(name="test_skill", state=SkillState.DRAFT)
        skill.transition_to(SkillState.VERIFIED)
        assert skill.state == SkillState.VERIFIED

    def test_state_transition_invalid(self):
        skill = Skill(name="test_skill", state=SkillState.DRAFT)
        with pytest.raises(ValueError, match="非法状态转换"):
            skill.transition_to(SkillState.RELEASED)  # 必须先 VERIFIED

    def test_state_transition_to_released_sets_timestamp(self):
        skill = Skill(name="test_skill", state=SkillState.VERIFIED)
        skill.transition_to(SkillState.RELEASED)
        assert skill.released_at is not None

    def test_state_transition_to_deprecated_sets_timestamp(self):
        skill = Skill(name="test_skill", state=SkillState.RELEASED)
        skill.transition_to(SkillState.DEPRECATED)
        assert skill.deprecated_at is not None

    def test_is_usable(self):
        skill = Skill(name="test_skill", state=SkillState.RELEASED)
        assert skill.is_usable()
        skill2 = Skill(name="test_skill2", state=SkillState.DEGRADED)
        assert skill2.is_usable()
        skill3 = Skill(name="test_skill3", state=SkillState.DRAFT)
        assert not skill3.is_usable()

    def test_bump_version_patch(self):
        skill = Skill(name="test_skill", version="1.2.3")
        skill.bump_version("patch")
        assert skill.version == "1.2.4"

    def test_bump_version_minor(self):
        skill = Skill(name="test_skill", version="1.2.3")
        skill.bump_version("minor")
        assert skill.version == "1.3.0"

    def test_bump_version_major(self):
        skill = Skill(name="test_skill", version="1.2.3")
        skill.bump_version("major")
        assert skill.version == "2.0.0"

    def test_record_execution_success(self):
        skill = Skill(name="test_skill")
        skill.record_execution(success=True, latency_ms=100.0)
        assert skill.metrics.usage_count == 1
        assert skill.metrics.success_count == 1
        assert skill.metrics.failure_count == 0
        assert skill.metrics.avg_latency_ms == 100.0

    def test_record_execution_failure(self):
        skill = Skill(name="test_skill")
        skill.record_execution(success=False, latency_ms=50.0)
        assert skill.metrics.failure_count == 1
        assert skill.metrics.success_count == 0

    def test_record_execution_avg_latency(self):
        skill = Skill(name="test_skill")
        skill.record_execution(success=True, latency_ms=100.0)
        skill.record_execution(success=True, latency_ms=200.0)
        assert skill.metrics.avg_latency_ms == pytest.approx(150.0)

    def test_success_rate_calculation(self):
        skill = Skill(name="test_skill")
        skill.record_execution(success=True, latency_ms=10.0)
        skill.record_execution(success=True, latency_ms=10.0)
        skill.record_execution(success=False, latency_ms=10.0)
        assert skill.metrics.success_rate == pytest.approx(2 / 3)

    def test_success_rate_zero_executions(self):
        skill = Skill(name="test_skill")
        assert skill.metrics.success_rate == 0.0

    def test_to_graph_node(self):
        skill = Skill(name="test_skill", state=SkillState.RELEASED)
        node = skill.to_graph_node()
        assert node["skill_id"] == skill.skill_id
        assert node["name"] == "test_skill"
        assert node["state"] == "S4"
        assert "success_rate" in node
        assert "usage_count" in node


# ===========================================================================
# SkillInterface Tests
# ===========================================================================

class TestSkillInterface:

    def test_default_interface(self):
        iface = SkillInterface()
        assert iface.input_schema == {}
        assert iface.preconditions == []

    def test_full_interface(self):
        iface = SkillInterface(
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            preconditions=["条件1"],
            postconditions=["结果1"],
            side_effects=["副作用1"],
        )
        assert len(iface.preconditions) == 1
        assert len(iface.side_effects) == 1


# ===========================================================================
# SkillImplementation Tests
# ===========================================================================

class TestSkillImplementation:

    def test_code_implementation(self):
        impl = SkillImplementation(code="print('hello')")
        assert impl.code == "print('hello')"

    def test_prompt_implementation(self):
        impl = SkillImplementation(prompt_template="Do {action}")
        assert impl.prompt_template == "Do {action}"

    def test_sub_skill_implementation(self):
        impl = SkillImplementation(sub_skill_ids=["id1", "id2"])
        assert len(impl.sub_skill_ids) == 2

    def test_empty_implementation_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SkillImplementation()  # 三者都为空


# ===========================================================================
# SkillMetrics Tests
# ===========================================================================

class TestSkillMetrics:

    def test_total_executions(self):
        m = SkillMetrics(success_count=7, failure_count=3)
        assert m.total_executions == 10

    def test_success_rate(self):
        m = SkillMetrics(success_count=9, failure_count=1)
        assert m.success_rate == pytest.approx(0.9)

    def test_success_rate_no_executions(self):
        m = SkillMetrics()
        assert m.success_rate == 0.0


# ===========================================================================
# Graph Model Tests
# ===========================================================================

class TestSkillEdge:

    def test_valid_edge(self):
        edge = SkillEdge(
            source_id="skill-a",
            target_id="skill-b",
            edge_type=EdgeType.DEPENDS_ON,
        )
        assert edge.source_id == "skill-a"
        assert edge.edge_type == EdgeType.DEPENDS_ON

    def test_self_loop_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SkillEdge(source_id="same", target_id="same", edge_type=EdgeType.DEPENDS_ON)

    def test_empty_id_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SkillEdge(source_id="", target_id="skill-b", edge_type=EdgeType.DEPENDS_ON)

    def test_weight_range(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SkillEdge(source_id="a", target_id="b", edge_type=EdgeType.SIMILAR_TO, weight=1.5)

    def test_to_neo4j_props(self):
        edge = SkillEdge(
            source_id="a", target_id="b",
            edge_type=EdgeType.COMPOSES_WITH,
            weight=0.8,
        )
        props = edge.to_neo4j_props()
        assert props["weight"] == 0.8
        assert "edge_id" in props


class TestSkillSubgraph:

    def _make_node(self, skill_id: str, name: str) -> SkillGraphNode:
        return SkillGraphNode(
            skill_id=skill_id,
            name=name,
            version="1.0.0",
            skill_type=SkillType.ATOMIC,
            state=SkillState.RELEASED,
        )

    def test_add_node(self):
        sg = SkillSubgraph()
        node = self._make_node("id1", "skill_a")
        sg.add_node(node)
        assert "id1" in sg.nodes

    def test_add_edge_valid(self):
        sg = SkillSubgraph()
        sg.add_node(self._make_node("id1", "skill_a"))
        sg.add_node(self._make_node("id2", "skill_b"))
        edge = SkillEdge(source_id="id1", target_id="id2", edge_type=EdgeType.DEPENDS_ON)
        sg.add_edge(edge)
        assert len(sg.edges) == 1

    def test_add_edge_missing_node_raises(self):
        sg = SkillSubgraph()
        sg.add_node(self._make_node("id1", "skill_a"))
        edge = SkillEdge(source_id="id1", target_id="id_missing", edge_type=EdgeType.DEPENDS_ON)
        with pytest.raises(ValueError):
            sg.add_edge(edge)

    def test_get_roots(self):
        sg = SkillSubgraph()
        sg.add_node(self._make_node("root", "root_skill"))
        sg.add_node(self._make_node("child", "child_skill"))
        edge = SkillEdge(source_id="child", target_id="root", edge_type=EdgeType.DEPENDS_ON)
        sg.add_edge(edge)
        roots = sg.get_roots()
        assert "child" in roots
        assert "root" not in roots

    def test_topological_sort(self):
        sg = SkillSubgraph()
        sg.add_node(self._make_node("a", "skill_a"))
        sg.add_node(self._make_node("b", "skill_b"))
        sg.add_node(self._make_node("c", "skill_c"))
        sg.add_edge(SkillEdge(source_id="b", target_id="a", edge_type=EdgeType.DEPENDS_ON))
        sg.add_edge(SkillEdge(source_id="c", target_id="b", edge_type=EdgeType.DEPENDS_ON))
        order = sg.topological_sort()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_topological_sort_cycle_raises(self):
        sg = SkillSubgraph()
        sg.add_node(self._make_node("a", "skill_a"))
        sg.add_node(self._make_node("b", "skill_b"))
        sg.add_edge(SkillEdge(source_id="a", target_id="b", edge_type=EdgeType.DEPENDS_ON))
        sg.add_edge(SkillEdge(source_id="b", target_id="a", edge_type=EdgeType.DEPENDS_ON))
        with pytest.raises(ValueError, match="环"):
            sg.topological_sort()

    def test_to_dict(self):
        sg = SkillSubgraph(name="test_subgraph")
        sg.add_node(self._make_node("id1", "skill_a"))
        d = sg.to_dict()
        assert d["name"] == "test_subgraph"
        assert d["node_count"] == 1
        assert d["edge_count"] == 0


# ===========================================================================
# Experience Model Tests
# ===========================================================================

class TestTrajectoryStep:

    def test_valid_step(self):
        step = TrajectoryStep(
            step_index=0,
            action_type="click",
            action_target="#submit-btn",
        )
        assert step.step_index == 0
        assert step.success is True

    def test_step_with_state(self):
        step = TrajectoryStep(
            step_index=1,
            action_type="type",
            action_value="hello",
            state_before={"form_filled": False},
            state_after={"form_filled": True},
        )
        assert step.state_after["form_filled"] is True


class TestExperienceUnit:

    def test_minimal_experience(self):
        exp = ExperienceUnit(source_type=ExperienceSourceType.BROWSER_TRAJECTORY)
        assert not exp.is_processed
        assert exp.step_count == 0

    def test_experience_with_steps(self):
        steps = [
            TrajectoryStep(step_index=i, action_type="click")
            for i in range(3)
        ]
        exp = ExperienceUnit(
            source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
            steps=steps,
        )
        assert exp.step_count == 3

    def test_mark_processed(self):
        exp = ExperienceUnit(source_type=ExperienceSourceType.DOCUMENTATION)
        exp.mark_processed(["skill-id-1", "skill-id-2"])
        assert exp.is_processed
        assert exp.processed_at is not None
        assert len(exp.extracted_skill_ids) == 2

    def test_tags_normalized(self):
        exp = ExperienceUnit(
            source_type=ExperienceSourceType.MANUAL_INPUT,
            tags=["  WEB  ", "FORM"],
        )
        assert exp.tags == ["web", "form"]


class TestSkillProposal:

    def test_pending_proposal(self):
        proposal = SkillProposal(
            source_experience_id="exp-123",
            proposed_name="fill_form",
            proposed_description="填写表单",
        )
        assert proposal.status == SkillProposalStatus.PENDING

    def test_accept_proposal(self):
        proposal = SkillProposal(
            source_experience_id="exp-123",
            proposed_name="fill_form",
            proposed_description="填写表单",
        )
        proposal.accept("skill-456")
        assert proposal.status == SkillProposalStatus.ACCEPTED
        assert proposal.generated_skill_id == "skill-456"

    def test_reject_proposal(self):
        proposal = SkillProposal(
            source_experience_id="exp-123",
            proposed_name="fill_form",
            proposed_description="填写表单",
        )
        proposal.reject("与已有 Skill 重复")
        assert proposal.status == SkillProposalStatus.REJECTED
        assert "重复" in proposal.rejection_reason

    def test_merge_proposal(self):
        proposal = SkillProposal(
            source_experience_id="exp-123",
            proposed_name="fill_form",
            proposed_description="填写表单",
        )
        proposal.merge_into("existing-skill-id")
        assert proposal.status == SkillProposalStatus.MERGED
        assert proposal.merged_into_skill_id == "existing-skill-id"


class TestSkillExecutionRecord:

    def test_start_execution(self):
        record = SkillExecutionRecord(skill_id="skill-1", skill_version="1.0.0")
        record.start()
        assert record.status == ExecutionStatus.RUNNING
        assert record.started_at is not None

    def test_complete_execution(self):
        record = SkillExecutionRecord(skill_id="skill-1", skill_version="1.0.0")
        record.start()
        record.complete({"result": "ok"}, {"state": "done"})
        assert record.status == ExecutionStatus.SUCCESS
        assert record.output_data == {"result": "ok"}
        assert record.latency_ms is not None
        assert record.latency_ms >= 0

    def test_fail_execution(self):
        record = SkillExecutionRecord(skill_id="skill-1", skill_version="1.0.0")
        record.start()
        record.fail("元素未找到", "ElementNotFoundError")
        assert record.status == ExecutionStatus.FAILED
        assert record.error_type == "ElementNotFoundError"
        assert record.latency_ms is not None


# ===========================================================================
# ORM Mapper Tests (no DB required)
# ===========================================================================

class TestORMMappers:

    def test_skill_to_orm_and_back(self):
        from skillos.storage.postgres_db import orm_to_skill, skill_to_orm

        original = Skill(
            name="test_skill",
            version="2.1.0",
            description="测试 Skill",
            skill_type=SkillType.FUNCTIONAL,
            domain="web",
            state=SkillState.RELEASED,
            tags=["web", "test"],
            interface=SkillInterface(
                input_schema={"type": "object"},
                preconditions=["条件1"],
            ),
            implementation=SkillImplementation(
                code="print('test')",
                tool_calls=["playwright"],
            ),
        )

        orm = skill_to_orm(original)
        assert orm.skill_id == original.skill_id
        assert orm.name == "test_skill"
        assert orm.skill_type == "functional"
        assert orm.state == "S4"
        assert json.loads(orm.tags) == ["web", "test"]

        restored = orm_to_skill(orm)
        assert restored.skill_id == original.skill_id
        assert restored.name == original.name
        assert restored.skill_type == SkillType.FUNCTIONAL
        assert restored.state == SkillState.RELEASED
        assert restored.tags == ["web", "test"]
        assert restored.interface.preconditions == ["条件1"]
        assert restored.implementation is not None
        assert restored.implementation.code == "print('test')"

    def test_skill_to_orm_no_implementation(self):
        from skillos.storage.postgres_db import skill_to_orm

        skill = Skill(name="no_impl_skill")
        orm = skill_to_orm(skill)
        assert orm.implementation_json is None

    def test_skill_to_orm_with_metrics(self):
        from skillos.storage.postgres_db import orm_to_skill, skill_to_orm

        skill = Skill(name="metrics_skill")
        skill.record_execution(success=True, latency_ms=150.0)
        skill.record_execution(success=False, latency_ms=200.0)

        orm = skill_to_orm(skill)
        assert orm.usage_count == 2
        assert orm.success_count == 1
        assert orm.failure_count == 1

        restored = orm_to_skill(orm)
        assert restored.metrics.usage_count == 2
        assert restored.metrics.success_rate == pytest.approx(0.5)
