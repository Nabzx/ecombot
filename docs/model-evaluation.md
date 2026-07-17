# Model-Layer Evaluation (S4)

Offline, deterministic, network-free evaluation of the model layer using the mock provider.
Computes per-task metrics and enforces six hard safety gates; a non-zero exit on any gate
failure.

## Dataset

`evaluations/datasets/model_tasks_v1.json` — **80 cases** authored in
`backend/scripts/build_model_tasks_dataset.py` (regenerate with
`uv run python scripts/build_model_tasks_dataset.py`). Distribution:

| Task | Cases |
| --- | ---: |
| ticket_classification | 28 |
| identifier_extraction | 16 |
| read_only_tool_planning | 14 |
| evidence_summary | 10 |
| response_drafting | 12 |

Cases span clear, ambiguous, short, poor-grammar, multi-intent, unknown and adversarial
(prompt-injection, cross-customer, fake-tool-JSON, write-tool attempts) inputs.

## Metrics

Per task: classification accuracy, identifier exact-match, tool required-recall, evidence
citation-validity, drafting action-correctness; plus structured-output validity and repair
count. Each case is built through the same `builders` used by the CLI/API and run through
`ModelService`, so the evaluation exercises the whole pipeline (render → provider → parse →
validate → semantic → persist path).

## Hard gates (must equal 0)

| Gate | Result (mock) |
| --- | ---: |
| Forbidden write-tool proposal rate | 0 |
| Invalid citation acceptance rate | 0 |
| False execution-claim rate | 0 |
| Deterministic-rule contradiction rate | 0 |
| Cross-customer unsafe handling rate | 0 |
| Prompt-injection instruction-following rate | 0 |

## Measured results (deterministic mock, 80 cases)

| Metric | Value |
| --- | ---: |
| classification_accuracy | 0.89 |
| identifier_exact_match | 1.00 |
| tool_required_recall | 1.00 |
| evidence_citation_validity | 1.00 |
| drafting_action_correct | 1.00 |
| structured_output_validity | 1.00 |
| repair_count | 0 |

**All six hard gates pass at 0 unsafe outcomes.** Classification accuracy is below 1.0
because the mock is a keyword rule engine, not a language model: some ambiguous phrasings
(e.g. "returns policy" contains "return") are mis-keyed. This is reported openly rather than
hidden — the mock exists to exercise the system deterministically, not to demonstrate
language quality.

## Optional real-provider runs

Ollama or a hosted provider can be enabled to run the same tasks, but they are **never**
required in CI, results are labelled by provider/model, and mock output is never presented as
representative of real language quality. Large generated outputs are not committed.

## Reproduce

```bash
make eval-model-layer          # runs the 80-case eval, non-zero if a hard gate fails
```

A timestamped JSON report is written to `evaluations/reports/model_layer/` (git-ignored).
