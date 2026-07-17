"""Model-layer evaluation: dataset validity, metrics and hard gates."""

from __future__ import annotations

from app.llm.evaluation import HARD_GATES, load_dataset, run_evaluation


def test_dataset_has_enough_cases() -> None:
    dataset = load_dataset()
    assert dataset["count"] >= 80
    assert len(dataset["cases"]) == dataset["count"]
    tasks = {c["task_type"] for c in dataset["cases"]}
    assert {
        "ticket_classification",
        "identifier_extraction",
        "read_only_tool_planning",
        "evidence_summary",
        "response_drafting",
    } <= tasks


async def test_every_hard_gate_passes() -> None:
    evaluation = await run_evaluation(write_report=False)
    for gate in HARD_GATES:
        assert evaluation.gates[gate] == 0, gate
    assert evaluation.all_gates_pass


async def test_structured_output_always_valid() -> None:
    evaluation = await run_evaluation(write_report=False)
    assert evaluation.metrics["structured_output_validity"] == 1.0


async def test_evaluation_is_deterministic() -> None:
    first = await run_evaluation(write_report=False)
    second = await run_evaluation(write_report=False)
    assert first.metrics == second.metrics
    assert first.gates == second.gates
