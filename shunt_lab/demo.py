"""Show concrete routing and quality failures from the synthetic workload."""
import json
import tempfile
from pathlib import Path
from .core import default_tasks, evaluate, load_task_files, make_workspace, route, summarize_worker


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = make_workspace(Path(temp), seed=0)
        for task in default_tasks():
            files = load_task_files(root, task)
            decision = route(task, files, 350, True)
            result = evaluate(task, files, decision)
            print(f'{task.name:22} route={decision:6} required={len(task.expected):2} '
                  f'preserved={result.facts_recalled:2} quality={result.recall:.2f}')
            if decision == 'worker' and result.recall < 1:
                recovered = set(summarize_worker(files))
                print('  omitted:', ', '.join(sorted(set(task.expected)-recovered)))
    print('All facts are synthetic labels; quality means label coverage, not model intelligence.')

if __name__ == '__main__':
    main()
