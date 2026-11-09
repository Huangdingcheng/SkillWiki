from pathlib import Path
import importlib.util

from skillwiki.layers.skill_management.gepa_adapter import candidate_package_admission


def test_candidate_package_is_admissible() -> None:
    package = Path(__file__).parents[2] / "artifacts/20261014-b-int-003/candidate-package"
    status, reason, manifest = candidate_package_admission(package)
    assert status == "blocked_candidate_license_scope_unresolved", reason
    assert manifest["entrypoint"] == "dialogue_parser:run"


def test_candidate_entrypoint_smoke() -> None:
    package = Path(__file__).parents[2] / "artifacts/20261014-b-int-003/candidate-package"
    spec = importlib.util.spec_from_file_location("b_int_003_dialogue_parser", package / "dialogue_parser.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = module.parse_dialogue("Guard: Halt!\nTraveler: Hello.")
    assert len(result["nodes"]) == 2
    assert result["edges"] == [{"from": "line-1", "to": "line-2"}]
