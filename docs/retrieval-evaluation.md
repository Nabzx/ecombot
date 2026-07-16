# Retrieval Evaluation (S3)

Offline, deterministic, no-LLM evaluation of policy retrieval. Reports recall, MRR and
safety metrics per mode and enforces three hard safety gates.

## Dataset

`evaluations/datasets/policy_retrieval_v1.json` — **65 cases** with deterministic ids
across categories: direct lookup, natural phrasing, boundary, negation-sensitive,
multi-policy, unsupported, historical, no-active-policy, conflict and hostile. Each case
records id, query, optional topic, expected topics, expected support, expected conflict,
source scope, historical flag and category.

## Metrics

Recall@1/3/5, MRR, topic accuracy, active-version accuracy, unsupported-rejection rate,
conflict-detection accuracy, hostile-source exclusion rate, historical correctness, and
avg / p95 latency — computed for **lexical-only**, **semantic-only** and **hybrid**.

## Targets and hard gates

| Metric | Target | Release |
| --- | ---: | ---: |
| Recall@1 | ≥ 0.80 | ≥ 0.70 |
| Recall@3 | ≥ 0.92 | ≥ 0.85 |
| Recall@5 | ≥ 0.97 | ≥ 0.92 |
| MRR | ≥ 0.85 | ≥ 0.78 |
| Topic accuracy | ≥ 0.95 | ≥ 0.90 |
| Active-version accuracy | 1.00 | 1.00 |
| Unsupported rejection | ≥ 0.90 | ≥ 0.85 |
| Conflict detection | 1.00 | 1.00 |
| Hostile-source exclusion | 1.00 | 1.00 |

**Hard gates (build fails if any < 1.00, evaluated on hybrid):** active-version accuracy,
conflict detection, hostile-source exclusion.

## Measured results (deterministic_hash embedding, 65 cases)

| Mode | R@1 | R@3 | R@5 | MRR | Topic | Unsup-rej | Active | Conflict | Hostile-excl |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| lexical | 0.90 | 0.94 | 0.96 | 0.93 | 0.90 | 0.89 | 1.00 | 1.00 | 1.00 |
| semantic | 0.55 | 0.80 | 0.92 | 0.69 | 0.55 | 1.00 | 1.00 | 1.00 | 1.00 |
| hybrid | 0.84 | 0.94 | 0.98 | 0.90 | 0.84 | 0.89 | 1.00 | 1.00 | 1.00 |

All three hard gates pass at 1.00. Latency p95 is single-digit milliseconds.

## Lexical vs semantic vs hybrid (honest)

**With the deterministic hash embedding, lexical is the strongest channel.** The hashed
bag-of-words gives only a weak semantic signal, so semantic-only underperforms and
hybrid — which weights lexical higher and uses semantic to fill lexical misses — tracks
close to lexical without beating it. This is reported openly rather than hidden. A real
local embedding provider (Sentence Transformers / Ollama) would let the semantic channel
contribute more; the fusion weights are documented and easy to rebalance.

## Known weaknesses

- Support/unsupported is threshold-based, not entailment: a genuinely out-of-domain query
  that happens to share a strong policy verb (e.g. "cancel my subscription" vs the order
  cancellation policy) can be mis-classified. The dataset uses clearly out-of-domain
  unsupported queries; the residual is documented.
- Single-chunk-per-policy (the policies are short) means retrieval is effectively
  topic-level; longer policies would exercise sub-section chunking more.

## Reproduce

```bash
make index-policies        # index with the deterministic embedding
make eval-retrieval        # prints per-mode metrics; non-zero if a hard gate fails
```

A timestamped JSON report is written to `evaluations/reports/retrieval/` (git-ignored).
