"""Skill 监控器 — 追踪运行时指标，检测性能退化。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ...models.maintenance_model import MaintenanceProposal
from ...models.skill_model import Skill, SkillState
from ...utils.logger import get_logger

logger = get_logger(__name__)


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"       # 成功率下降
    CRITICAL = "critical"       # 成功率极低
    STALE = "stale"             # 长期未使用
    UNKNOWN = "unknown"         # 数据不足


@dataclass
class SkillHealthReport:
    """Skill 健康报告。"""
    skill_id: str
    skill_name: str
    status: HealthStatus
    success_rate: float
    usage_count: int
    avg_latency_ms: float
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def needs_attention(self) -> bool:
        return self.status in (HealthStatus.DEGRADED, HealthStatus.CRITICAL)


@dataclass
class SystemHealthReport:
    """系统整体健康报告。"""
    total_skills: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    critical_count: int = 0
    stale_count: int = 0
    skill_reports: List[SkillHealthReport] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def health_ratio(self) -> float:
        if self.total_skills == 0:
            return 1.0
        return self.healthy_count / self.total_skills


class SkillMonitor:
    """Skill 运行时监控器。

    职责：
    - 评估 Skill 健康状态
    - 检测性能退化（成功率下降、延迟上升）
    - 识别需要修复/废弃的 Skill
    - 生成健康报告
    """

    # 健康阈值
    DEGRADED_SUCCESS_RATE = 0.7    # 低于此值 → DEGRADED
    CRITICAL_SUCCESS_RATE = 0.4    # 低于此值 → CRITICAL
    STALE_DAYS = 30                # 超过此天数未使用 → STALE
    MIN_EXECUTIONS_FOR_EVAL = 5    # 至少执行此次数才评估

    def evaluate_skill(self, skill: Skill) -> SkillHealthReport:
        """评估单个 Skill 的健康状态。"""
        issues: List[str] = []
        recommendations: List[str] = []
        status = HealthStatus.UNKNOWN

        metrics = skill.metrics
        total = metrics.total_executions

        if total < self.MIN_EXECUTIONS_FOR_EVAL:
            status = HealthStatus.UNKNOWN
            if total == 0:
                issues.append("从未被执行")
                recommendations.append("考虑添加测试用例验证功能")
        else:
            sr = metrics.success_rate
            if sr >= 0.9:
                status = HealthStatus.HEALTHY
            elif sr >= self.DEGRADED_SUCCESS_RATE:
                status = HealthStatus.DEGRADED
                issues.append(f"成功率偏低: {sr:.1%}")
                recommendations.append("检查失败原因，考虑修复或更新实现")
            else:
                status = HealthStatus.CRITICAL
                issues.append(f"成功率严重偏低: {sr:.1%}")
                recommendations.append("立即修复或废弃该 Skill")

        # 检查是否长期未使用
        if metrics.last_used_at:
            days_since_use = (datetime.utcnow() - metrics.last_used_at).days
            if days_since_use > self.STALE_DAYS and status == HealthStatus.HEALTHY:
                status = HealthStatus.STALE
                issues.append(f"{days_since_use} 天未使用")
                recommendations.append("考虑废弃或归档该 Skill")

        # 延迟检查
        if metrics.avg_latency_ms > 5000:
            issues.append(f"平均延迟过高: {metrics.avg_latency_ms:.0f}ms")
            recommendations.append("优化实现或增加超时处理")

        return SkillHealthReport(
            skill_id=skill.skill_id,
            skill_name=skill.name,
            status=status,
            success_rate=metrics.success_rate,
            usage_count=metrics.usage_count,
            avg_latency_ms=metrics.avg_latency_ms,
            issues=issues,
            recommendations=recommendations,
        )

    def evaluate_batch(self, skills: List[Skill]) -> SystemHealthReport:
        """批量评估，生成系统健康报告。"""
        report = SystemHealthReport(total_skills=len(skills))
        for skill in skills:
            hr = self.evaluate_skill(skill)
            report.skill_reports.append(hr)
            if hr.status == HealthStatus.HEALTHY:
                report.healthy_count += 1
            elif hr.status == HealthStatus.DEGRADED:
                report.degraded_count += 1
            elif hr.status == HealthStatus.CRITICAL:
                report.critical_count += 1
            elif hr.status == HealthStatus.STALE:
                report.stale_count += 1

        logger.info(
            f"系统健康报告: 总计={report.total_skills}, "
            f"健康={report.healthy_count}, 退化={report.degraded_count}, "
            f"危急={report.critical_count}, 过期={report.stale_count}"
        )
        return report

    def get_degraded_skills(self, skills: List[Skill]) -> List[Tuple[Skill, SkillHealthReport]]:
        """返回需要关注的 Skill 列表（DEGRADED + CRITICAL）。"""
        result = []
        for skill in skills:
            report = self.evaluate_skill(skill)
            if report.needs_attention:
                result.append((skill, report))
        result.sort(key=lambda x: x[1].success_rate)
        return result

    def should_trigger_repair(self, skill: Skill) -> bool:
        """判断是否应该触发自动修复。"""
        report = self.evaluate_skill(skill)
        return report.status in (HealthStatus.DEGRADED, HealthStatus.CRITICAL)

    def should_deprecate(self, skill: Skill) -> bool:
        """判断是否应该废弃 Skill。"""
        report = self.evaluate_skill(skill)
        return (
            report.status == HealthStatus.CRITICAL
            or (report.status == HealthStatus.STALE and skill.metrics.usage_count < 10)
        )

    def propose_maintenance(self, skill: Skill) -> Optional[MaintenanceProposal]:
        """Create a human-review proposal for unhealthy Skills."""
        report = self.evaluate_skill(skill)
        return MaintenanceProposal.from_health_report(report)
