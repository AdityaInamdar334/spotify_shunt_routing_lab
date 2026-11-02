# Spotify Cut Claude Code's Context Usage by 90%. The Interesting Part Was Not the Cheaper Model.

*Inside Spotify's open-source shunt plugin: enforced tool routing, ephemeral worker agents, the economics of delegated context, and a reproducible Python lab that measures when the optimization helps and when it quietly loses information.*

Imagine asking an AI coding assistant a straightforward question about a large Java service: "Which methods call the database?"

The assistant opens a 4,000-line file. It reads another service because the first calls into it. Then it opens a test class for context. The answer you needed consists of six lines, but the conversation now contains thousands of lines of source code that the frontier model had to ingest. The assistant may even encounter the same source again during another turn.

Nothing particularly intelligent happened during that file transfer. The interesting work was locating the relevant methods, connecting a few calls, and giving you a trustworthy explanation.

Now imagine keeping the source code out of the expensive assistant's context. A smaller model receives the raw files, answers a tightly scoped question, and returns a concise summary. The frontier model reasons over that summary. If it later needs an exact edit, it reads only the relevant section of the original file.

This is the architecture behind **shunt**, a Claude Code plugin published in Spotify's `portal-ai-plugins` repository. In September 2026, Dimitri Mazmanov reported approximately **90% average savings in Claude's context tokens** for large bulk-reading tasks using Portal by Spotify's AiKA modes [1]. The GitHub README provides individual results of **82%, 94%, and 94%** for three reported bulk-reading scenarios [2].

The numbers deserve attention. But they are not the most important part of the story.

The interesting innovation is that Spotify moved the routing policy out of a prose instruction and into a **tool-call enforcement layer**. Claude cannot simply ignore the preference to avoid reading a huge file because a hook can stop that specific tool invocation before it runs.

There is a second lesson, and it is less comfortable: saving 90% of one model's context tokens is not necessarily the same thing as saving 90% of total tokens, dollars, latency, or engineering time. A second model still has to process the original files. Its summaries can omit critical details. Network calls can take longer than simply reading a small file. And code produced by a worker model is not automatically correct merely because it arrived directly on disk.

This tutorial examines both sides, using the actual Spotify repository as the primary technical source and a fully reproducible, independent Python lab to test the accounting and information-loss tradeoffs.

**What we will build:** a small offline simulation of `shunt`'s routing decisions, synthetic Java-like project files, a deliberately capacity-limited worker summarizer, a two-model cost ledger, tests for routing rules, and a repeatable ablation suite. No API keys, Portal account, or Claude subscription are needed for the lab. The offline experiment does not call or evaluate any real language model. It isolates the mechanism so its assumptions remain inspectable.

---

## Contents

1. What Spotify actually measured
2. The expensive model is being used as a file transport
3. The execution graph behind shunt
4. Why a CLAUDE.md instruction was not enough
5. Layer one: the Read hook
6. Layer two: the Bash hook
7. Layer three: worker scripts and their transport
8. AiKA modes as declarative workers
9. Skills and the distinction between suggestions and enforcement
10. The economics of delegated context
11. Why token savings, cost savings, and latency diverge
12. Build the offline experiment
13. Generate reproducible project fixtures
14. Implement deterministic routing
15. Simulate a constrained worker
16. Account for all model tokens
17. Run the benchmark
18. Ablation one: file-size threshold
19. Ablation two: remove the risk guard
20. Ablation three: shorten summaries
21. Evaluate information loss, not just compression
22. Why this is different from prompt caching
23. The security boundary created by delegation
24. Deployment pitfalls revealed by the real source code
25. How to benchmark an actual coding-agent deployment
26. Installing Spotify's real plugin
27. When not to delegate
28. What the results establish, and what they do not
29. Extensions and research questions
30. References

---

## 1. What Spotify actually measured

Spotify's engineering report, published September 3, 2026, is titled *Portal by Spotify cut my Claude Code token usage by 90%* [1]. Its premise is that much of an agent's activity consists of transferring or rewriting information rather than solving difficult problems.

The repository adds more specific benchmarking information [2]:

- A single large-file scenario involving 4,014 lines fell from **33,684** Claude-context tokens without shunt to **5,737** with shunt. Spotify reports **82% savings**.
- A source-and-test-pair scenario involving 7,408 lines fell from **75,990** to **4,148** Claude-context tokens, reported as **94% savings**.
- A cross-service reading scenario involving 1,281 lines fell from **16,221** to **821** Claude-context tokens, again reported as **94% savings**.
- A code-writing scenario involving 3,667 lines is described separately: the baseline had 40,614 context tokens plus generation, while the delegated mode wrote 833 lines to disk. This is not presented as a directly comparable percentage.

The repository calls the mean bulk-reading saving 90%. Arithmetic on the three rounded percentages gives 90%, but that is not the same as a workload-weighted or cost-weighted average. The per-scenario corpus sizes differ substantially.

The source repository also includes a `benchmarks.json` specification for four benchmark task descriptions and a shell-based evaluation runner [7]. That fixture specification describes small checked-in test tasks, including a 602-line file. Do not confuse the specification's fixtures with the larger Java-monorepo measurements reported in the README. They serve related but distinct evaluation purposes.

**The critical measurement boundary:** these figures concern tokens entering or leaving Claude's context in the scenarios Spotify describes. They do not alone tell us the total number of tokens processed by the AiKA worker, its provider bill, its exact answer quality, or the end-to-end latency distribution.

That does not invalidate the optimization. It tells us what additional measurements an engineering team needs before adopting it.

## 2. The expensive model is being used as a file transport

Most agent loops mix several different kinds of work:

- **Retrieval:** locate files, functions, definitions, log lines, or symbols.
- **Compression:** condense a large input into a smaller useful representation.
- **Transformation:** generate tests, configuration stubs, repetitive interfaces, or documentation.
- **Reasoning:** trace a race condition, evaluate a design tradeoff, or formulate a safe migration.
- **Execution:** run tests, change code, validate output, and report evidence.

A common coding-agent design uses one frontier model for all five. But the model performing difficult reasoning does not necessarily need the raw material for every retrieval and transformation step.

If a 10,000-token file is needed only to identify three named functions, a 150-token answer may preserve nearly everything relevant to that question. If the question is instead whether two functions form a deadlock under a rare interleaving, the same compression may destroy the evidence needed to reason correctly.

The problem is not that small models are universally good at reading and universally bad at reasoning. The problem is that **the necessary information and verification burden vary by task**.

A routing policy must therefore consider at least four features: how much input would be transferred, how precisely the answer must preserve source details, what the consequence of an error would be, and how expensive an additional network hop is.

This is why a fixed line threshold can be an effective first approximation while still being incomplete.

## 3. The execution graph behind shunt

The system in Spotify's repository has four relevant execution components, even though the README describes its plugin as three layers [2].

```text
Developer's question
        |
        v
Claude Code chooses a tool call
        |
        v
PreToolUse hook evaluates that call
        |
        +---- Small or targeted read ----> original tool executes
        |
        +---- Large unrestricted read --> hook blocks
                                              |
                                              v
                                      /bulk-reader skill
                                              |
                                              v
                                      bulk-read script
                                              |
                                              v
                              Portal CLI: aika:invoke-chat
                                              |
                                              v
                                AiKA bulk-reader mode
                                              |
                                              v
                                   Concise worker answer
                                              |
                                              v
                                  Claude resumes its task
```

The distinction is architectural. The hook is not a classifier written in natural language. It is executable policy code. The script is not a free-form shell command assembled by Claude. It is a stable transport contract with named arguments. The AiKA mode is a separately configured worker. The skill is the human-readable usage guide that helps the assistant recover from a blocked call.

Shunt also includes a `code-writer` path: a worker receives a specification and an existing reference file, generates matching boilerplate, and can write the result directly to a specified output file. Importantly, the repository says **only bulk reading is enforced by hooks**. Code-writing delegation remains a skill-level suggestion [2].

## 4. Why a CLAUDE.md instruction was not enough

Spotify first tried keeping routing rules in `CLAUDE.md` [1]. That is a reasonable place for project instructions, but instructions compete with other context and cannot guarantee that a model will route every expensive read correctly.

Consider the difference between these two controls.

**Advisory rule:** "If a file is long, prefer the bulk-reading agent."

**Executable rule:** "If the pending Read tool call targets a file longer than 350 lines and does not specify a targeted range, deny the operation."

The second rule has an observable decision, an input schema, and test cases. You can evaluate it without asking a model whether it understood your instructions.

This is a general principle of agent infrastructure: use prompts to express goals and helpful conventions, but use programmatic controls for deterministic constraints such as file access, data boundaries, execution budgets, and approval requirements.

A hook is still only as good as its coverage. If it checks `Read` but not equivalent shell commands, the policy has a hole. If it checks simple shell commands but not every possible command composition, it remains a routing convenience rather than a complete security sandbox.

## 5. Layer one: the Read hook

The first real hook lives at `plugins/shunt/hooks/check-file-size` [3]. It accepts a tool-call JSON payload on standard input, extracts the requested path, and reads the configured threshold.

Its behavior can be reduced to the following logic:

```python
def should_block_read(file_lines, offset=None, limit=None, threshold=350):
    if offset is not None or limit is not None:
        return False
    if file_lines is None:
        return False
    return file_lines > threshold
```

The actual implementation is Bash and `jq`, and it contains additional checks for empty paths and missing files.

Why allow `offset` and `limit`?

Because reading lines 220 through 260 of a file to make a precise edit is different from dumping all 4,000 lines into a context window. The hook preserves that necessary narrow operation.

The policy is straightforward, but its measurement primitive is imperfect. Line count does not measure tokens. One minified 100-line file could be more expensive than a sparse 600-line file. So could a generated JSON file containing extremely long lines.

Line count is useful because it is fast and cheap to obtain, but a production router could improve on it by incorporating bytes, language-aware token estimates, and the likelihood that the tool result will actually be consumed by the parent model.

The environment variable `SHUNT_MIN_LINES` changes the threshold. The code defaults invalid numeric values back to 350. This is helpful operationally: a developer can tune one knob without rewriting the skill or worker mode.

## 6. Layer two: the Bash hook

A large file can reach Claude through shell output even if the dedicated Read tool is blocked.

The second hook, `check-bash-read`, watches Bash calls. It recognizes simple uses of `cat`, `head`, `tail`, `less`, and `more`, then applies a similar file-size threshold [4]. It permits some commands with pipelines or redirections because the original author treats those as targeted or non-context-producing operations.

Examples from the intended policy:

```bash
cat src/LargeService.java
# Potentially blocked when file exceeds the threshold.

cat src/LargeService.java | grep "findUser"
# Allowed as a targeted read in this heuristic.

git status
# Not the kind of bulk read shunt is trying to redirect.
```

This is intentionally a heuristic, not a Bash parser. A complex shell command can route content through `awk`, `sed`, `python`, command substitution, process substitution, or other mechanisms. The script does not claim to implement a security boundary against a determined adversary.

That matters when teams start treating routing controls as if they were access-control systems. **A cost-saving hook is not a data-loss-prevention policy.** For actual security boundaries, use filesystem permissions, network egress restrictions, model-provider data policies, isolated execution environments, and independently enforced access controls.

## 7. Layer three: worker scripts and their transport

The `bulk-read` script accepts a question and one or more paths [5]:

```bash
bulk-read \
  --question "Which methods call the database?" \
  --paths src/Service.java src/Handler.java
```

It validates that the files exist and are readable. It then streams their contents into a temporary message file using XML-like file wrappers:

```text
<file path="src/Service.java">
...original source bytes...
</file>

<file path="src/Handler.java">
...original source bytes...
</file>

Question: Which methods call the database?
```

The script forwards that message to a shared `shunt_invoke` function. The important operational choice is that the large corpus is sent to the **worker**, not through Claude's context window.

There is no persistent multi-turn worker conversation: each `aika:invoke-chat` invocation stands alone. A follow-up question sends the corpus to the worker again. That avoids importing the whole corpus into Claude, but it is not free globally. It spends worker-model input tokens again unless the underlying service uses an applicable caching mechanism, which this tutorial does not assume.

The `code-write` script follows the same architecture [6]:

```bash
code-write \
  --spec "Write unit tests for UserService" \
  --reference tests/OrderTest.java \
  --target tests/UserTest.java
```

The reference file is mandatory. This is a sensible design choice because boilerplate quality often depends on local conventions more than general language knowledge.

But writing directly to disk creates a different risk. The frontier model may not inspect every generated line, so a separate test or review step is necessary. The script also uses a simple line-based cleanup for Markdown fences. That works for common code-only outputs but is not a full syntax-aware parser.

## 8. AiKA modes as declarative workers

Spotify's AiKA modes define instructions, available tools, and optional processing rules without requiring a persistent custom agent server [1, 8]. The article uses two mode examples, `bulk-reader` and `code-writer`, with Gemini 2.5 Flash as the configured worker model.

The conceptual configuration for the reader is short:

```yaml
name: bulk-reader
description: Concise cross-file code analysis
instructions: |
  Read supplied source files.
  Answer only the specific question.
  Return concise, structured bullets.
  Do not invent file names or line numbers.
visibility: public
model: gemini-2.5-flash
resourceLimits:
  temperature: 0.2
```

The key is not the name Gemini. Spotify's modes decouple the work description from the worker model. You can change the model behind the mode without changing the Claude Code hook that triggers delegation.

That separation creates organizational advantages. A team can revise one worker configuration centrally, reuse it across repositories, and compare different worker backends on the same task set.

It also demands governance. A public mode is still an external processing destination from the perspective of the local repository. Before sending confidential source, check your organization's Portal deployment, enabled model providers, data-retention rules, and approval policies. A system that uses fewer frontier tokens but sends regulated code to an unapproved worker is not a successful optimization.

## 9. Skills are suggestions; hooks are enforcement

The two skills in the plugin are Markdown instruction files. They describe what kinds of tasks can be delegated and which command syntax to use [2, 9].

This creates a deliberate hierarchy:

1. The model may proactively recognize an appropriate delegation task through a skill.
2. If the model tries a disallowed large Read operation anyway, a hook blocks it.
3. The rejection names the skill that can complete the operation.
4. Claude receives the worker answer and continues.

It is tempting to say this guarantees the agent will always do the cheaper thing. It does not. It guarantees only that the specific tool calls covered by the installed hooks receive the routing decision. The plugin's own README makes the limitation explicit for `code-writer` [2].

Also, the shunt source emits a top-level `decision` object with `allow` or `block`. Current Claude Code documentation recommends the newer `hookSpecificOutput.permissionDecision` form for `PreToolUse` and treats the older top-level format as deprecated but compatible [10]. If you adopt or fork the implementation, run the real hook integration tests against your installed Claude Code version rather than assuming that a unit test of JSON shape proves enforcement in the host.

## 10. The economics of delegated context

We need to distinguish four quantities that are often collapsed into the word *tokens*.

**Parent input tokens.** The expensive model's received context, including source material or worker summaries.

**Parent output tokens.** Its generated answer, reasoning artifacts where billable, and other output charged under the applicable provider policy.

**Worker input tokens.** The full files, instructions, and question sent to AiKA's worker model.

**Worker output tokens.** The worker's summary or generated source code.

Without delegation, a simplified bill is:

```text
Direct cost =
    parent_input_tokens  * parent_input_price
  + parent_output_tokens * parent_output_price
```

With delegation, the bill becomes:

```text
Delegated cost =
    parent_summary_input_tokens * parent_input_price
  + parent_output_tokens        * parent_output_price
  + worker_corpus_input_tokens  * worker_input_price
  + worker_output_tokens        * worker_output_price
  + transport_and_other_costs
```

Prices in these expressions are per-token prices, not per-million-token prices.

In normal language: **delegation is economically useful when the cost avoided at the frontier model exceeds the cost of processing and verifying the same information somewhere else.**

The worker can read almost the same number of tokens that the frontier model would have read. Total processed tokens may stay level or increase. Savings come from moving work to a less expensive processor and reducing repeated expensive context.

The same economics appear in database query planning, storage hierarchies, and distributed computing. Moving computation closer to data can be efficient, but coordination and communication remain real costs.

## 11. Why three savings measures diverge

Suppose a question requires 20,000 tokens of source code but can be answered in 500 summary tokens.

If the parent sees the full source, it receives roughly 20,000 source tokens. If the parent sees the summary, it receives roughly 500. That is a very large parent-context reduction.

But the worker still sees the 20,000-token source. Ignoring repeated instructions and output tokens, total provider input could go from 20,000 to 20,500. **You reduced expensive input without reducing overall input.**

There is nothing contradictory about this result.

Latency can also rise if the worker request is sequential. In Spotify's report, delegations typically took 10 to 30 seconds, with the article describing a 30-second per-invocation cap [1]. The current public plugin code, however, sets `SHUNT_TIMEOUT_SECONDS` to 180 seconds by default and documents request-size ceilings [2, 11]. These refer to different versions or layers of the system; do not treat the values as interchangeable.

Finally, a 500-token summary can be wrong in a way the 20,000-token source is not. If the worker omits a lock-order constraint, the parent will reason correctly over incomplete evidence and still arrive at an unsafe conclusion.

That is why a serious benchmark must measure cost, latency, and **task-level correctness** separately.

---

# Build the companion lab

We will now implement a controlled study without any external model calls. The goal is to reproduce the accounting and routing mechanism, not to claim we have reproduced Spotify's production results.

The project is included as a separate ZIP. Every file and test referenced below is in that archive.

## 12. Set up the project

Use Python 3.10 or newer. No external dependencies are required.

```bash
cd spotify_shunt_routing_lab

python3 -m unittest discover -s tests -v

python3 -m shunt_lab.benchmark \
  --seeds 5 \
  --output results/benchmark.json
```

A successful run prints the baseline and five alternative routing configurations. It also saves a machine-readable JSON report.

The project contains a library for generating fixtures, a routing and cost evaluator, an approximate hook simulator, an experiment driver, and unit tests.

```text
spotify_shunt_routing_lab/
  shunt_lab/
    __init__.py
    core.py
    hook_sim.py
    benchmark.py
  tests/
    test_core.py
  results/
    benchmark.json
  requirements.txt
  README.md
  BENCHMARK_NOTES.md
  ARTICLE.md
```

## 13. Generate synthetic source files

We create a small Java-like repository with files of different lengths. The largest files include deliberately labeled facts about methods, caches, database calls, and concurrency hazards.

The labels make evaluation deterministic. They are **not** evidence that a model could discover these facts from real Java code. We use them because the point of this particular experiment is to study what happens when a compressor drops evidence.

An abbreviated fixture looks like this:

```java
public final class AlphaService {
    // FACT: alpha.class=AlphaService
    void fn0() {}

    // FACT: alpha.lookup=findUser
    void fn1() {}

    // FACT: alpha.cache=writeThrough
    void fn2() {}

    // FACT: alpha.race=unsafeSharedMap
    void fn5() {}
}
```

The generator pads the file with reproducible filler so that routing thresholds operate on files hundreds of lines long. A seed changes filler placement and line counts slightly. It does not change the underlying answer labels.

The full generator in `core.py` uses four fixtures. Typical sizes are roughly 520 lines, 640 lines, 410 lines, and 80 lines before seed-dependent adjustments.

The tasks include single-file summaries, cross-file questions, test-style extraction, small-file reads, security-sensitive review, concurrency reasoning, exact editing, and boilerplate generation.

## 14. Implement deterministic routing

Our policy captures the core idea of shunt, with one additional safety preference for critical tasks.

```python
def route(task, files, threshold=350,
          guard=True, code_writer=False):
    if guard and (
        task.critical or task.category in {"reasoning", "edit"}
    ):
        return "parent"

    if task.category == "code-write":
        return "worker" if code_writer else "parent"

    if task.category != "bulk-read":
        return "parent"

    if any(content.count("\n") > threshold
           for _, content in files):
        return "worker"

    return "parent"
```

This is a **simulation**, not Spotify's actual hook code. Spotify's hooks see tool calls and file metadata, not our abstract `critical` labels. In a real deployment, risk classification may itself require a trusted policy engine or human authorization. It should not be left to an unverified worker-model claim.

There is a design tradeoff here. Routing more tasks to cheap workers can save more expensive-model tokens. But if a task requires precise details or safety reasoning, compression is more likely to hurt than help.

The baseline always keeps work at the parent. The main policy delegates ordinary bulk reads when the file exceeds 350 lines, while keeping critical tasks and exact edits with the parent.

## 15. Build an intentionally limited worker

A real `bulk-reader` mode can use a model such as Gemini 2.5 Flash. Our offline substitute uses a fixed deterministic rule: return at most the first three labeled facts from each file.

```python
def summarize_worker(files, cap_per_file=3):
    facts = []
    for path, content in files:
        facts.extend(extract_facts(content)[:cap_per_file])
    return tuple(dict.fromkeys(facts))
```

This is not a convincing model of Gemini quality, and it is not meant to be. It isolates a failure mode that could affect any lossy summarizer: the worker can produce a concise, plausible summary that omits a fact needed by the downstream task.

We intentionally place some sensitive facts later in a file. For example, `alpha.race=unsafeSharedMap` appears after more general class, lookup, and cache facts. A worker limited to the first three facts will not include it.

A parent reasoning over that summary cannot recover the missing detail unless it reopens the source, queries the worker differently, or uses an independent verification tool.

This illustrates an information-theoretic constraint, not a model-specific deficiency. Once information is removed, downstream reasoning cannot reliably reconstruct it from nothing.

## 16. Record all four token streams

For repeatability, the lab estimates token counts as `ceil(UTF-8 byte length / 4)`. This is a transparent proxy, **not** the true tokenizer for Claude or Gemini.

```python
def token_proxy(text):
    return (len(text.encode("utf-8")) + 3) // 4
```

The direct configuration charges parent input for the full file contents. The delegated configuration charges worker input for the files and parent input for the summarized facts. Both configurations include fixed, disclosed parent-output assumptions.

For the cost calculation, the lab uses hypothetical rates of:

- $3 per million parent input tokens.
- $15 per million parent output tokens.
- $0.30 per million worker input tokens.
- $1.20 per million worker output tokens.

**These are selected experimental parameters, not verified current Claude or Gemini prices.** They let us ask how routing changes a bill under a given relative price ratio.

The latency model is also an assumption: the baseline receives a two-second fixed completion estimate, while a delegated task receives an additional twelve-second worker latency. The lab does not benchmark real network round trips.

For genuine cost accounting, use provider usage fields and billing exports, including separate cache writes, cache reads, uncached tokens, retries, tool orchestration, and any third-party platform charges. Anthropic's documentation explains its prompt-caching accounting and Claude Code usage reporting [12, 13].

## 17. Run the five-seed benchmark

Execute:

```bash
python3 -m shunt_lab.benchmark \
  --seeds 5 \
  --output results/benchmark.json
```

The script generates nine question types for each of five deterministic seeds, then evaluates six configurations. Each configuration therefore processes **45 synthetic task instances**.

The baseline uses the full source in the parent for every task. It is an idealized reference that extracts all synthetic fact labels. That makes it a controlled coverage oracle, not an accurate representation of a frontier model's real recall.

Against that baseline, the principal 350-line routing policy produces:

- **41.64% fewer parent-model tokens.**
- **1.18% more total tokens across parent and worker.**
- **33.69% lower modeled cost**, under the hypothetical price assumptions.
- **94.7% aggregate fact recall**, compared with 100% for the synthetic direct oracle.
- **20 delegated task instances** out of 45.

The last detail matters. The policy does not blindly send every request through a smaller model. It delegates the bulk reads while keeping tasks requiring exact edits or high-consequence reasoning local to the parent.

The result is not a repetition of Spotify's measured 90%. Different workloads and different assumptions produce different figures. The point is to give readers a benchmark they can inspect, modify, and challenge.

## 18. Ablation one: change the file-size threshold

The experiment tests thresholds of 150, 350, and 700 lines.

In this deliberately small fixture collection, thresholds of 150 and 350 make the same routing decisions. The normal bulk-read files all exceed both thresholds, while the small configuration file stays below both.

With a 700-line threshold, no fixture qualifies for bulk delegation. The policy behaves like the direct baseline and produces no savings.

This is a useful warning about threshold tuning. A threshold that performs beautifully on one repository may do nothing on another. The right distribution to inspect is not simply the average lines per source file. It is the distribution of **whole-file read operations that actually occur** during representative agent tasks.

A sensible production calibration process logs the number of file reads, their byte lengths, their estimated token lengths, whether they were targeted, and whether they were later needed for an edit. Optimize the threshold against those observed operations.

## 19. Ablation two: remove the risk guard

The `no-risk-guard` configuration deliberately allows a security-sensitive read and an advisory boilerplate write to be delegated, in addition to ordinary bulk reads. It still keeps explicitly classified reasoning and exact editing with the parent.

On our fixture set, that change raises parent-token savings from **41.64% to 66.55%**. The modeled dollar savings rise from **33.69% to 53.87%**.

But aggregate fact recall declines from approximately **94.7% to 89.5%**.

These are not independent observations about a deployed model. They are predictable consequences of the controlled summarizer's limited output: an additional delegated task needs a fact located after the worker's three-fact cutoff.

The ablation illustrates a general design question: how much quality risk is acceptable for a given reduction in context consumption?

A production router should not answer that question with a single universal percentage. Missing a noncritical class name is an inconvenience. Missing evidence of an authentication flaw is a different category of failure.

## 20. Ablation three: shorten worker summaries

There is an appealing optimization after you have built a worker summarizer: make its answers even shorter.

Our `shorter-worker-summary` configuration returns at most one fact per file instead of three. Parent context decreases slightly more, producing **41.77%** rather than **41.64%** parent-token savings. Modeled cost also improves only slightly, from **33.69%** to **33.86%** savings.

But fact recall falls to **63.2%**.

The numbers create a striking tradeoff. A tiny additional context saving sacrifices a large fraction of the information required for the task.

This is why summarization quality should not be measured merely by output length or apparent coherence. The correct metric depends on the task's required evidence.

For code navigation, measure whether the exact functions and call relationships are included. For test generation, measure compile success and mutation coverage. For debugging, measure whether the agent identifies the actual defect and produces a regression test. For security review, track severity-weighted recall and false negatives.

## 21. Evaluate information loss, not just compression

Our benchmark uses a simple recall measure: how many required fact labels survive into the answer-visible context?

That measure is intentionally limited. It does not test whether a language model can reason from the facts, whether the facts are accurate, or whether the code change is safe. It does, however, expose one important class of risk: a worker that discards necessary evidence.

A real evaluation should go further:

**Question-answering quality.** Curate questions with independently checked answers, score exact claims, and distinguish omissions from hallucinations.

**Cross-file correctness.** Include call graph questions that require following symbols across modules rather than simply spotting named functions.

**Rare-bug recall.** Include subtle concurrency, transaction-boundary, and permission bugs that are easy to miss when context is shortened. Spotify explicitly notes that its worker missed a thread-safety issue that Claude recognized when supplied appropriate context [1].

**Editing reliability.** Ask the agent to make a precise source change, then run tests and inspect the diff. The shunt design intentionally retains targeted original-source reads for edits.

**Code-generation quality.** For generated tests, measure compile rate, assertion strength, mutation score, and whether the output matches actual repository conventions. Writing hundreds of lines to disk is an output-volume observation, not a correctness measurement.

**Recovery cost.** Count cases where an incomplete worker summary caused Claude to read original files anyway. An inexpensive first pass that frequently triggers a second full read can become more expensive than direct execution.

**Tail latency.** Look at p50, p90, and p99 completion times, timeouts, and retry rates. Mean latency alone can hide painful workflow interruptions.

Do all of these with paired tasks and frozen source snapshots. Make the same task appear in both arms, randomize or counterbalance execution order, and evaluate outputs with a blinded independent rubric where possible.

---

## 22. Prompt caching is related, but not the same optimization

At first glance, shunt looks similar to prompt caching because both reduce repeated expensive context processing.

They work differently.

Prompt caching keeps a reusable prefix available to the same provider under a cache policy. The model may still conceptually receive the same prompt prefix, but cached input is priced or processed differently according to the provider's rules. Anthropic documents default five-minute caching behavior and explicit cache controls for some API configurations [12].

Shunt changes **which model receives the corpus**. Claude sees a shorter representation of the original source. The worker processes the full source elsewhere.

These mechanisms can coexist. A large stable corpus used for repeated questions could benefit from caching on the worker provider, assuming the worker interface supports it and the prefix stays identical. But a delegated request is not automatically a cache hit, and Spotify's `bulk-read` script does not itself establish a cross-provider shared cache.

A rigorous comparison therefore needs more than two configurations. Compare direct uncached reads, direct cached reads, delegated uncached reads, and delegated cached reads when the APIs actually support those options. Otherwise, a routing benchmark may attribute savings to delegation that mostly came from cache behavior, or vice versa.

## 23. Delegation creates a new security and trust boundary

The worker's context contains source code from your repository. That content may be confidential and may also contain untrusted instructions embedded in comments, test fixtures, Markdown, or generated files.

There are three distinct threats.

**Data exfiltration and policy mismatch.** The repository contents are forwarded to a worker provider. Validate model-provider approvals, retention, residency, logging, secrets handling, and allowed repositories before routing.

**Prompt injection through code comments.** A malicious file can contain prose that looks like instructions to the worker. XML-like wrappers distinguish file boundaries but do not automatically prevent prompt injection. A worker must treat source files as untrusted data and follow the authoritative task instruction rather than arbitrary instructions inside the files.

**Unverified writes.** The code-writing path can place model-generated code directly into the working tree. That is convenient, but the output should undergo syntax checks, unit tests, formatting, and human or stronger-model review before merging. The generated file should also be checked against the intended target path and scope of allowed changes.

Do not mistake shunt's Bash hook for an adversarial sandbox. It intentionally permits numerous forms of targeted reading and is not designed to police every way a command can access files.

## 24. Deployment pitfalls revealed by the source

Reading the actual scripts exposes details worth including in any operational design review.

### Command-line payload limits

The shared transport library builds a JSON payload and sends it through the `portal-cli` argument list [11]. On Linux, the code defaults to a payload ceiling of **120,000 bytes**. On other platforms, it defaults to **400,000 bytes**. The comments explain the operating-system argument-size constraints, including Linux's per-argument limit.

A file can exceed 350 lines and therefore trigger delegation, yet be too large for the worker invocation's command-line payload limit. That is not merely a performance problem: it is a potential workflow dead end unless the agent splits the corpus or performs a targeted read.

A more scalable transport could use standard input, a secured temporary artifact, or a provider-side file upload when the API supports it, rather than placing the entire file body in a command-line argument. Any alternative must preserve privacy and cleanup guarantees.

### Timeout configuration

The current script exposes `SHUNT_TIMEOUT_SECONDS` with a default value of **180 seconds** [11]. This should not be silently conflated with the September blog's description of a **30-second** cap [1]. The visible sources do not establish that these refer to the same software layer or deployment configuration.

### Name resolution

Modes can be addressed by name, and the Portal backend prefers the caller's own mode, then group modes, then public modes [2, 11]. That makes customization convenient. It also creates a reproducibility concern: two developers using the same logical mode name might silently resolve to different mode configurations.

For controlled benchmarks, pin the mode ID or record the resolved configuration, model identifier, prompt version, tool list, and other relevant settings.

### Output cleanup

The `code-write` script strips lines beginning with Markdown fences using a simple `sed` expression [6]. That assumes the fence lines are presentation wrappers, not meaningful intended file contents. For Markdown-writing tasks or generated code that legitimately includes triple-backtick text, a broad fence-removal operation can change the artifact.

### Hook decision schema

The current implementation emits a deprecated top-level PreToolUse decision form [3, 4]. Claude Code's official documentation recommends `hookSpecificOutput.permissionDecision` and still describes compatibility for the older representation [10]. This is a maintenance issue, not evidence that the plugin is broken. Test it with your installed host version and update a fork when necessary.

### Heuristic shell coverage

The Bash hook is intentionally simple. It uses regular expressions and string checks rather than a full shell parser [4]. Treat it as best-effort routing for common commands, not proof that no large read can reach the parent model.

These details are exactly why reviewing an open-source project is valuable. The engineering tradeoffs are often visible in a dozen lines of transport and policy code rather than in the headline benchmark.

## 25. How I would benchmark a real deployment

If I were evaluating shunt for a production engineering team, I would use the following protocol.

**First, collect a representative task set.** Include simple service questions, large cross-file navigation, code generation from conventions, debugging, security review, exact edits, and multi-step tasks. Sample across repositories and languages. Freeze the repository revision for each paired run.

**Second, define a baseline.** Run tasks using the same Claude Code model and tool permissions without shunt. Preserve full tool logs and token usage fields. Do not change the evaluation prompt or success criteria between variants.

**Third, run the routed variant.** Use the same initial state, model configuration, and task. Log every hook decision, worker invocation, resolved mode ID, tool result, timeout, retry, and fallback.

**Fourth, measure provider spend rather than relying on a text-length proxy.** Use actual usage fields for parent input, parent output, worker input, worker output, cache reads, and cache writes. Add Portal platform charges if any apply. Normalize costs to a single currency and state the pricing date.

**Fifth, score outcomes independently.** Use hidden regression tests and human review where needed. For knowledge questions, record fact-level precision and recall. For edits, validate the final repository diff and test results. For subtle bugs, include ground-truth issues that cannot be solved by shallow keyword extraction.

**Sixth, calculate savings only among successful tasks as well as across all attempts.** If a routed system is cheaper but fails more often, the bill per attempted task can look good while the **cost per correctly completed task** gets worse.

**Seventh, report uncertainty.** For latency and costs, use paired differences and confidence intervals or bootstrap estimates. For failures, publish counts and severity breakdowns. A single demo that saved 90% of context is not a sufficient release decision.

Finally, implement a fallback. If the worker fails, times out, returns an unsupported claim, or is asked for exact line numbers, the agent should be able to use controlled targeted reads or escalate to the frontier model. Keep a record of fallback frequency because it is part of the true economics.

## 26. Install Spotify's real shunt plugin

If you have access to a Portal instance with AiKA enabled, Spotify documents a plugin installation path [1, 2]:

```bash
claude plugin marketplace add spotify/portal-ai-plugins
claude plugin install portal@portal
claude plugin install shunt@portal
```

Start a new Claude Code session, run `/portal:setup`, and authenticate the Portal CLI against your instance.

Check whether your instance already has the `bulk-reader` and `code-writer` modes. The current README recommends verifying availability instead of assuming every Portal instance has identical public modes:

```bash
portal-cli actions aika:list-modes --json \
  --input '{"search": "bulk-reader"}'
```

For a controlled source-reading task, ask Claude a question spanning several large files. Inspect the actual tool history to determine whether the hook blocked the direct Read and the agent used the skill or script.

You can change the default line threshold with `.claude/settings.json`:

```json
{
  "env": {
    "SHUNT_MIN_LINES": "500"
  }
}
```

For the upstream hook and transport tests, the README documents:

```bash
bash evals/run.sh
```

Run that from the `plugins/shunt` directory in a checkout of Spotify's repository, not from the educational Python lab. The upstream project needs tools such as `bash` and `jq`. Its live token benchmark additionally requires authenticated Portal access:

```bash
bash evals/run.sh --benchmark
```

**Important:** Installing plugins and authenticating Portal are separate actions that you must perform in your own environment. The companion ZIP does not connect to Spotify's service and cannot validate a real deployed instance.

## 27. When not to delegate

The smartest optimization sometimes is to keep a task on the expensive model.

If you need to change a file, inspect the exact section you will edit. If you are chasing a thread-safety issue, avoid summaries that cannot preserve synchronization details. If you are investigating an authentication or authorization boundary, use independent checks and avoid relying on a compacted description of the code alone. If a file is short, skip the remote worker and its network latency. If source must not cross a provider boundary, do not delegate to a worker whose data-handling policy has not been approved.

The plugin embodies several of these choices already. It allows targeted reads, excludes debugging and architecture from the intended delegation scope, and uses an existing reference for boilerplate generation [1, 2].

For production systems, I would add a few explicit conditions: prohibit delegation for certain repositories or directories; require review before any worker-produced write is accepted; route exact-value extraction through deterministic code rather than summarization when feasible; and use structured outputs that include claims, file references, and uncertainty rather than free-form prose.

A cheap worker that provides verified evidence is useful. A cheap worker that simply sounds confident can impose a much larger downstream cost.

## 28. What the results establish, and what they do not

**Spotify establishes:** an actual plugin can redirect bulk source reads through AiKA modes, and the author reports large decreases in Claude-context token consumption in selected Java-monorepo scenarios. The source repository contains the hooks, transport scripts, evaluation scaffolding, and documented limits behind that design [1, 2].

**Our lab establishes:** in a deterministic fixture environment, moving large source inputs to a limited worker representation can reduce parent-model token accounting, alter modeled monetary cost, and lose task-relevant facts. The stated results are reproducible with the supplied Python code and synthetic assumptions.

**Neither establishes from these experiments alone:** a universal 90% reduction in total AI spending, a universal improvement in speed, parity of reasoning quality, zero data-governance risk, or the performance of any named current frontier or worker model on every repository.

Our lab's direct baseline is an idealized fact oracle. The worker is a truncated fact extractor, not Gemini. The prices are hypothetical. The latency values are assumptions. The seeds vary fixture length and filler rather than inducing independently sampled model behavior. These limits are important because they explain exactly what conclusions the experiment can support.

## 29. Research directions worth pursuing

A good next project would replace the deterministic worker with real interchangeable model adapters. Measure token usage from each provider's usage object and evaluate the same tasks using stable, blinded ground-truth labels. Record retries, cache consumption, and failure modes as carefully as successful completions.

Another extension is **learned routing under a quality constraint**. Instead of a fixed 350-line threshold, estimate each task's required reasoning depth, verification burden, and expected information loss. Route only when the predicted cost saving is positive and the probability of an unacceptable error remains below a chosen limit.

A third extension is **evidence-preserving summarization**. Rather than asking a worker to compress every file into prose, have it return typed findings with symbol names, source spans, and verifiable evidence. The parent can then inspect only the spans associated with the claims that matter. This is closer to an information-retrieval system with a language-model interface than a generic "make this shorter" prompt.

There is also a subtle open problem involving repeated context. If the same large file is frequently consulted, a smarter pipeline might cache worker analysis keyed to the file's content hash, invalidate it on edits, and avoid recomputing summaries unless the question changes. But it must preserve enough source provenance to detect when a cached answer no longer applies.

Finally, the economics should be evaluated in terms of **successful work delivered per unit of cost and latency**, not merely the number of tokens sent to the most expensive model.

### The larger lesson

Spotify's contribution is not evidence that frontier models are unnecessary. It is evidence that an agent should not use its most capable component for every byte of work.

The expensive model is most valuable when it must integrate incomplete evidence, reason about behavior, choose a safe edit, or resolve an ambiguity. That value is diluted when it is asked to serve as an expensive file reader for routine searches.

`shunt` changes that default. It intercepts a class of expensive operations, delegates them to a configurable worker, and lets Claude return to the kind of reasoning that justified using a frontier model in the first place.

But the stronger engineering principle is more demanding than "always delegate the easy stuff."

**Delegate only when the savings survive full cost accounting, the extra latency is acceptable, and the information returned is good enough for the actual decision.**

That is the difference between a 90% token-saving anecdote and a reliable model-routing system.

---

## 30. References and further reading

The Spotify sources below describe the original system. The companion lab and all synthetic benchmark numbers in this article are independent educational work and are not endorsed or validated by Spotify.

**[1]** Dimitri Mazmanov. *Portal by Spotify cut my Claude Code token usage by 90%*. Spotify Engineering, September 3, 2026. https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90

**[2]** Spotify. *shunt plugin README*, `spotify/portal-ai-plugins`, `main` branch, accessed November 2026. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/README.md

**[3]** Spotify. *check-file-size*, PreToolUse Read hook. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/hooks/check-file-size

**[4]** Spotify. *check-bash-read*, PreToolUse Bash hook. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/hooks/check-bash-read

**[5]** Spotify. *bulk-read* worker script. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/scripts/bulk-read

**[6]** Spotify. *code-write* worker script. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/scripts/code-write

**[7]** Spotify. *Shunt benchmark specifications and evaluation runner*. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/evals/benchmarks.json and https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/evals/run.sh

**[8]** Spotify for Backstage. *AiKA Modes documentation*. https://backstage.spotify.com/docs/portal/core-features-and-plugins/aika/modes

**[9]** Spotify. *bulk-reader skill*. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/skills/bulk-reader/SKILL.md

**[10]** Anthropic. *Claude Code Hooks Reference*. https://code.claude.com/docs/en/hooks

**[11]** Spotify. *Shunt's shared AiKA transport library*. https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/scripts/lib/aika.sh

**[12]** Anthropic. *Prompt Caching*. Claude Platform documentation. https://platform.claude.com/docs/en/build-with-claude/prompt-caching

**[13]** Anthropic. *Manage Costs Effectively*. Claude Code documentation. https://code.claude.com/docs/en/costs

**Companion project:** `spotify_shunt_routing_lab`, included in the downloadable ZIP. Run the saved benchmark and all unit tests before using its numbers in another publication. The synthetic fixture labels, hypothetical prices, and assumed latency are fully visible in the source code.
