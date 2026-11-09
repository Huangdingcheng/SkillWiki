"""Tiny, frozen dialogue-to-graph task for real optimizer demonstrations.

The two diagnostic cases may drive reflection. The independently authored
holdout is excluded from optimizer input and can be run only through
``evaluate_holdout`` after candidate generation.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


DIAGNOSTIC_CASES = (
    {"id": "dialogue-d1", "split": "diagnostic", "input": "Guard: Halt!\nTraveler: Hello."},
    {"id": "dialogue-d2", "split": "diagnostic", "input": "Mira: Where is the station?\nOren: East."},
)
HELDOUT_CASES = (
    {"id": "dialogue-h1", "split": "heldout", "input": "Pilot: Ready?\nTower: Cleared."},
)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_graph(text: str) -> Mapping[str, Any]:
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    if match:
        value = match.group(1)
    graph = json.loads(value)
    if not isinstance(graph, Mapping):
        raise ValueError("graph output must be a JSON object")
    return graph


def expected_graph(dialogue: str) -> dict[str, list[dict[str, str]]]:
    nodes = []
    for index, line in enumerate(dialogue.splitlines(), 1):
        if ":" not in line:
            raise ValueError("each dialogue line must have Speaker: text form")
        speaker, text = line.split(":", 1)
        if not speaker.strip() or not text.strip():
            raise ValueError("speaker and utterance must be nonempty")
        nodes.append({"id": f"line-{index}", "speaker": speaker.strip(), "text": text.strip()})
    return {"nodes": nodes, "edges": [{"from": a["id"], "to": b["id"]} for a, b in zip(nodes, nodes[1:])]}


def verify_graph(dialogue: str, output: str | Mapping[str, Any]) -> tuple[bool, str]:
    try:
        graph = _parse_graph(output) if isinstance(output, str) else output
        if not isinstance(graph, Mapping):
            return False, "graph output must be a JSON object"
        expected = expected_graph(dialogue)
        # Exact structural and utterance checks; extra nodes/edges fail closed.
        if graph.get("nodes") != expected["nodes"]:
            return False, "nodes must preserve each line's order, speaker, text, and line-N id"
        if graph.get("edges") != expected["edges"]:
            return False, "edges must connect each consecutive line exactly once"
        return True, "valid directed dialogue graph"
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, f"invalid graph JSON: {exc}"


@dataclass
class DialogueTaskEvaluator:
    """Run a graph-producing model and cache score plus complete evidence."""

    model: Callable[..., Any]
    cache: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _errors: dict[tuple[str, str], Exception] = field(default_factory=dict, repr=False)
    calls: int = 0

    def __call__(self, skill_text: str, case: Mapping[str, Any]) -> dict[str, Any]:
        if case.get("split") not in {"diagnostic", "heldout"}:
            raise ValueError("case split must be diagnostic or heldout")
        case_fingerprint = _digest(json.dumps(dict(case), ensure_ascii=False, sort_keys=True))
        key = (_digest(skill_text), case_fingerprint)
        with self._lock:
            if key in self._errors:
                raise self._errors[key]
            cached = self.cache.get(key)
            if cached is not None:
                return dict(cached)
            # Hold the lock through invocation to prevent duplicate billable calls.
            self.calls += 1
            try:
                response = self.model(skill_text=skill_text, dialogue=str(case["input"]), case_id=str(case["id"]))
                if not isinstance(response, Mapping) or not isinstance(response.get("text"), str):
                    raise ValueError("graph model must return text/model/usage/receipt mapping")
            except Exception as exc:
                self._errors[key] = exc
                raise
            valid, feedback = verify_graph(str(case["input"]), response["text"])
            result = {"score": 1.0 if valid else 0.0, "output": response["text"],
                      "feedback": feedback, "valid": valid, "model": response.get("model"),
                      "usage": response.get("usage"), "receipt": response.get("receipt"),
                      "case_id": str(case["id"]), "split": case["split"], "skill_hash": key[0]}
            self.cache[key] = result
            return dict(result)

    def evaluate_holdout(self, skill_text: str) -> list[dict[str, Any]]:
        """Score hidden examples after optimization; never return them to GEPA."""
        return [self(skill_text, case) for case in HELDOUT_CASES]


__all__ = ["DIAGNOSTIC_CASES", "HELDOUT_CASES", "DialogueTaskEvaluator", "expected_graph", "verify_graph"]
