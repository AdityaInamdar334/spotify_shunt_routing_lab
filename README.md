# Shunt Routing Lab

**A transparent, offline experiment about Spotify's Claude Code `shunt` plugin.**

Spotify reported substantial reductions in Claude-context tokens by routing some large-file and boilerplate tasks through AiKA worker modes. This project is **not Spotify's plugin** and does not reproduce its real Java-monorepo or provider benchmark. It models the routing and cost-accounting mechanism with generated Java-like fixtures, fixed policy rules, and a deliberately constrained deterministic worker.

Read the complete companion article in [`ARTICLE.md`](ARTICLE.md).

## Key distinction

**Parent-context token savings do not equal total provider-token savings.** The worker still consumes the underlying files. Under *hypothetical* token prices, cost can decrease even when the sum of all tokens increases. A shorter answer can also drop evidence necessary for correctness.

## Quick start

Requirements: Python 3.10 or later, standard library only.

```bash
cd spotify_shunt_routing_lab

python3 -m unittest discover -s tests -v
python3 -m shunt_lab.demo
python3 -m shunt_lab.benchmark --seeds 5 --output results/benchmark.json
```

No API key, CUDA device, model weights, Anthropic account, or Spotify Portal installation required.

## Measured outputs from the saved five-seed run

- Direct synthetic baseline: 0% parent-context saving, fact-label recall 100%.
- Main 350-line policy: **41.64%** less parent context, **1.18% more** total provider tokens, **33.69% less** modeled cost, **94.7%** required-fact recall.
- Risk-guard ablation plus advisory code-write delegation: **66.55%** less parent context, **53.87%** lower modeled cost, **89.5%** required-fact recall.
- One-fact-per-file worker: **41.77%** less parent context, **33.86%** lower modeled cost, **63.2%** required-fact recall.
- A 700-line threshold: no tasks delegated in these fixtures.

All these results come from *one synthetic, deterministic benchmark*. They are not results from Claude, Gemini Flash, Spotify AiKA, or real Java program execution. See [BENCHMARK_NOTES.md](BENCHMARK_NOTES.md) for exact methods and assumptions.

## What the modules do

- `shunt_lab/core.py`: Generate synthetic Java-like code, configure tasks, select routes, simulate worker summaries, and account for cost.
- `shunt_lab/hook_sim.py`: Approximate the common Read/Bash hook decisions without integrating into Claude Code. **Not a security policy parser.**
- `shunt_lab/demo.py`: Show which tasks are delegated and which labeled facts get dropped.
- `shunt_lab/benchmark.py`: Rebuild generated fixtures and run six pricing/quality ablations across five seeds.
- `tests/test_core.py`: Validate fixture labels, routing, accounting, quality degradation, hook heuristics, and deterministic reproduction.
- `results/benchmark.json`: Saved aggregation from the primary five-seed benchmark.
- `ARTICLE.md`: Full research tutorial.

## The policy under test

Direct tasks go to the hypothetical frontier parent. Bulk-read tasks are delegated if at least one supplied file exceeds the line threshold, unless the task has a criticality flag. Exact editing and concurrency reasoning stay with the parent. Code writing is advisory by default and can be enabled in an ablation.

The simulated worker is not a language model. It returns at most the first three explicitly labeled facts per file, or the first one in the shorter-summary ablation. This provides a known failure mode for testing what happens when a summary discards relevant evidence. The direct baseline is an oracle that can recover all fixture fact labels.

## How accounting works

Token estimates use `ceil(UTF-8 bytes / 4)`, an approximation rather than an actual model tokenizer. Prices are experimental parameters:

```text
Parent input:   $3.00 / 1M tokens
Parent output: $15.00 / 1M tokens
Worker input:   $0.30 / 1M tokens
Worker output:  $1.20 / 1M tokens
```

Latency is an assumption of 2 seconds for a direct task and 14 seconds for a delegated task, not a measured networking result. The benchmark does not model cache reads/writes, subscription entitlements, provider-specific tokenization, tool schemas, retries, or quality-driven fallback. It reports aggregate labeled-fact recall, not source-code reasoning accuracy.

## How to extend into a real evaluation

Use the exact same repository snapshot for a direct and delegated paired task. Collect usage fields for every model and cache, actual latency, transport retries, and total bill. Separately score task quality with independent correctness checks, including bugs that require cross-file reasoning. Do not infer a price comparison from raw token counts.

Do not use the simplistic hook simulator as a security control or plug it into an untrusted shell. Consult the upstream project and official Claude Code hook documentation for supported integration.

## Primary sources

- Spotify Engineering: https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90
- Upstream shunt plugin: https://github.com/spotify/portal-ai-plugins/tree/main/plugins/shunt
- Upstream README: https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/README.md
- Claude Code hooks reference: https://code.claude.com/docs/en/hooks

## Provenance

Independent educational implementation. No Spotify, Anthropic, or Google affiliation or endorsement. No source or code in this repository should be confused with Spotify's production benchmarks.
