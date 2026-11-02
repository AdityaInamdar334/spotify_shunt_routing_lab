"""Deterministic workload routing and accounting. NOT a language-model benchmark."""
from dataclasses import dataclass, asdict
from pathlib import Path
import re

FACT = re.compile(r'^\s*//\s*FACT:\s*([a-zA-Z0-9_./=-]+)', re.M)


def token_proxy(text: str) -> int:
    """ceil(UTF-8 bytes/4), a declared approximation, not tokenizer usage."""
    return (len(text.encode('utf-8')) + 3) // 4


def extract_facts(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(FACT.findall(text)))


@dataclass(frozen=True)
class Task:
    name: str
    files: tuple[str, ...]
    question: str
    expected: tuple[str, ...]
    category: str = 'bulk-read'  # bulk-read, code-write, edit, or reasoning
    critical: bool = False


@dataclass(frozen=True)
class Rates:
    parent_input_per_million: float = 3.00
    parent_output_per_million: float = 15.00
    worker_input_per_million: float = 0.30
    worker_output_per_million: float = 1.20
    # Synthetic price assumptions, not quoted provider prices.


@dataclass(frozen=True)
class Run:
    name: str
    route: str
    parent_input: int
    parent_output: int
    worker_input: int
    worker_output: int
    facts_expected: int
    facts_recalled: int
    latency_assumption_seconds: float
    estimated_usd: float

    @property
    def recall(self) -> float:
        return self.facts_recalled / self.facts_expected if self.facts_expected else 1.0

    def json(self) -> dict:
        return {**asdict(self), 'recall': self.recall}


def make_workspace(root: Path, seed: int) -> Path:
    """Build fixed-format synthetic Java-style files, with repeatable filler."""
    root.mkdir(parents=True, exist_ok=True)
    specs = {
        'AlphaService.java': (520 + (seed % 7) * 11, [
            'alpha.class=AlphaService', 'alpha.lookup=findUser', 'alpha.cache=writeThrough',
            'alpha.expiry=ttl300', 'alpha.auth=tenantScoped',
            'alpha.race=unsafeSharedMap', 'alpha.mutex=absent']),
        'BetaService.java': (640 + (seed % 7) * 8, [
            'beta.class=BetaService', 'beta.endpoint=/orders', 'beta.storage=postgres',
            'beta.retry=three', 'beta.idempotency=requestId', 'beta.race=unsynchronizedCounter']),
        'OrderTest.java': (410 + (seed % 7) * 4, [
            'test.class=OrderTest', 'test.style=JUnit5', 'test.assert=assertEquals',
            'test.fixture=inMemoryDb', 'test.edge=emptyOrder']),
        'Config.java': (80 + (seed % 7), [
            'config.profile=staging', 'config.timeout=300', 'config.retries=2']),
    }
    for name, (lines, facts) in specs.items():
        content = [f'// fixture={name} seed={seed}', f'public final class {name.replace(".java", "")} {{']
        for j, fact in enumerate(facts):
            content.append(f'    // FACT: {fact}')
            content.append(f'    void fn{j}() {{}}')
        while len(content) < lines - 1:
            content.append(f'    // filler line {len(content)} p{(len(content) * 17 + seed) % 101}')
        content.append('}')
        (root / name).write_text('\n'.join(content) + '\n', encoding='utf-8')
    return root


def default_tasks() -> tuple[Task, ...]:
    return (
        Task('single-summary', ('AlphaService.java',), 'Name the service and lookup method.',
             ('alpha.class=AlphaService', 'alpha.lookup=findUser')),
        Task('cache-semantics', ('AlphaService.java',), 'Explain cache and expiry.',
             ('alpha.cache=writeThrough', 'alpha.expiry=ttl300')),
        Task('cross-service', ('AlphaService.java', 'BetaService.java'), 'Describe public surfaces.',
             ('alpha.class=AlphaService', 'alpha.lookup=findUser', 'beta.class=BetaService', 'beta.endpoint=/orders')),
        Task('test-style', ('OrderTest.java',), 'Which test framework and assertions are used?',
             ('test.class=OrderTest', 'test.style=JUnit5', 'test.assert=assertEquals')),
        Task('small-config', ('Config.java',), 'What is the profile?', ('config.profile=staging',)),
        Task('security-review', ('AlphaService.java',), 'Check for tenant-scoping assumptions.',
             ('alpha.auth=tenantScoped',), critical=True),
        Task('concurrency-review', ('AlphaService.java', 'BetaService.java'), 'Find concurrency defects.',
             ('alpha.race=unsafeSharedMap', 'alpha.mutex=absent', 'beta.race=unsynchronizedCounter'),
             category='reasoning', critical=True),
        Task('exact-edit', ('AlphaService.java',), 'Edit a line with exact context.',
             ('alpha.cache=writeThrough',), category='edit', critical=True),
        Task('boilerplate', ('OrderTest.java', 'BetaService.java'), 'Generate tests following reference.',
             ('test.style=JUnit5', 'beta.endpoint=/orders'), category='code-write'),
    )


def load_task_files(root: Path, task: Task) -> tuple[tuple[str, str], ...]:
    # Demonstration reads only known fixture paths from a trusted root.
    return tuple((p, (root / p).read_text(encoding='utf-8')) for p in task.files)


def route(task: Task, files: tuple[tuple[str, str], ...], threshold: int = 350,
          guard: bool = True, code_writer: bool = False) -> str:
    if guard and (task.critical or task.category in {'reasoning', 'edit'}):
        return 'parent'
    if task.category == 'code-write':
        return 'worker' if code_writer else 'parent'
    if task.category != 'bulk-read':
        return 'parent'
    if any(text.count('\n') > threshold for _, text in files):
        return 'worker'
    return 'parent'


def summarize_worker(files: tuple[tuple[str, str], ...], cap_per_file: int = 3) -> tuple[str, ...]:
    """Deterministic *limited* keyword summarizer, not a real Flash response."""
    facts: list[str] = []
    for _, content in files:
        facts.extend(extract_facts(content)[:cap_per_file])
    return tuple(dict.fromkeys(facts))


def evaluate(task: Task, files: tuple[tuple[str, str], ...], mode: str,
             rates: Rates = Rates(), cap_per_file: int = 3,
             worker_latency_seconds: float = 12.0) -> Run:
    corpus = '\n'.join(content for _, content in files)
    qtok = token_proxy(task.question)
    file_tokens = token_proxy(corpus)
    # Parent answers are equal-length, so comparison isolates I/O routing.
    parent_out = 128
    if mode == 'worker':
        facts = summarize_worker(files, cap_per_file=cap_per_file)
        # Worker reads the same corpus and sends just its short summary to parent.
        summary = '\n'.join(facts)
        parent_in = 100 + qtok + token_proxy(summary)
        worker_in = file_tokens + qtok + 75
        worker_out = token_proxy(summary)
        latency = worker_latency_seconds + 2.0
    else:
        facts = tuple(dict.fromkeys(fact for _, content in files for fact in extract_facts(content)))
        parent_in = 100 + qtok + file_tokens
        worker_in = worker_out = 0
        latency = 2.0
    found = len(set(task.expected) & set(facts))
    cost = (parent_in * rates.parent_input_per_million +
            parent_out * rates.parent_output_per_million +
            worker_in * rates.worker_input_per_million +
            worker_out * rates.worker_output_per_million) / 1_000_000
    return Run(task.name, mode, parent_in, parent_out, worker_in,
               worker_out, len(task.expected), found, latency, cost)


def aggregate(results: list[Run]) -> dict:
    total_parent = sum(x.parent_input + x.parent_output for x in results)
    total_worker = sum(x.worker_input + x.worker_output for x in results)
    total_expected = sum(x.facts_expected for x in results)
    total_recalled = sum(x.facts_recalled for x in results)
    return {
        'tasks': len(results), 'delegations': sum(x.route == 'worker' for x in results),
        'parent_tokens': total_parent, 'worker_tokens': total_worker,
        'all_provider_tokens': total_parent + total_worker,
        'usd_assumption': round(sum(x.estimated_usd for x in results), 7),
        'micro_recall': round(total_recalled / total_expected, 6) if total_expected else 1.0,
        'mean_latency_assumption_seconds': round(sum(x.latency_assumption_seconds for x in results)/len(results), 3),
    }
