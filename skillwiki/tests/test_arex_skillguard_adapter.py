from skillwiki.layers.skill_governance.arex_skillguard_adapter import (
    AREX_PINNED_COMMIT,
    AREX_REPOSITORY,
    SKILLGUARD_SOURCE,
    assess_arex_refresh,
    assess_skillguard_contract_drift,
)
import pytest


@pytest.mark.parametrize("state", ("unchanged", "updated", "stale", "removed", "unknown"))
def test_arex_refresh_preserves_state_and_never_promotes(state):
    result = assess_arex_refresh(
        source_commit="0123456789abcdef0123456789abcdef01234567",
        observed_state=state,
        evidence=("source-diff.json",),
    )
    payload = result.to_dict()
    assert payload["observed_state"] == state
    assert payload["source_identity"] == {
        "repository": AREX_REPOSITORY,
        "pinned_commit": AREX_PINNED_COMMIT,
        "license": "Apache-2.0",
    }
    assert result.review_required is True
    assert result.promotion_allowed is False
    assert result.auto_promote is False


def test_arex_rejects_invalid_state_and_identity_tampering():
    with pytest.raises(ValueError, match="unsupported AREX refresh state"):
        assess_arex_refresh(source_commit="abc", observed_state="published")


@pytest.mark.parametrize(
    ("change_kind", "environment_status", "expected"),
    (
        ("comment_only", "known", "pass"),
        ("comment_only", "unknown", "defer"),
        ("required_dependency_drift", "known", "block"),
        ("required_api_drift", "known", "block"),
        ("unknown_environment", "known", "defer"),
        ("required_api_drift", "unknown", "defer"),
    ),
)
def test_skillguard_contract_gate_is_conservative(change_kind, environment_status, expected):
    result = assess_skillguard_contract_drift(
        change_kind=change_kind,
        environment_status=environment_status,
        evidence=("contract-observation.json",),
    )
    assert result.status == expected
    assert result.source == SKILLGUARD_SOURCE
    assert result.executable_package is None
    assert result.executable_verified is False
    assert result.promotion_allowed is False
    assert result.to_dict()["conceptual_only"] is True


def test_skillguard_unknown_inputs_fail_closed_without_upstream_calls():
    result = assess_skillguard_contract_drift(
        change_kind="new_unclassified_change",
        environment_status="known",
    )
    assert result.status == "defer"
    assert "unrecognised" in result.reason
    with pytest.raises(ValueError, match="environment status"):
        assess_skillguard_contract_drift(
            change_kind="required_dependency_drift",
            environment_status="maybe",
        )
