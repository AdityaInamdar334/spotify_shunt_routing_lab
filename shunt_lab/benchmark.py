"""Offline benchmark and ablations. Run: python -m shunt_lab.benchmark --seeds 5"""
import argparse
import json
import tempfile
from pathlib import Path
from .core import Rates, aggregate, default_tasks, evaluate, load_task_files, make_workspace, route


def run_benchmark(seeds=5, thresholds=(150, 350, 700), output=None):
    records = {}
    configs = [('direct', None, True, False),
               ('threshold-150', 150, True, False),
               ('threshold-350', 350, True, False),
               ('threshold-700', 700, True, False),
               ('no-risk-guard', 350, False, True),
               ('shorter-worker-summary', 350, True, False)]
    with tempfile.TemporaryDirectory(prefix='shunt-study-') as tmp:
        for name, threshold, guard, code_writer in configs:
            results = []
            for seed in range(seeds):
                root = make_workspace(Path(tmp) / f'seed-{seed}', seed)
                for task in default_tasks():
                    files = load_task_files(root, task)
                    decision = 'parent' if threshold is None else route(task, files, threshold, guard, code_writer)
                    cap = 1 if name == 'shorter-worker-summary' else 3
                    results.append(evaluate(task, files, decision, Rates(), cap_per_file=cap))
            records[name] = aggregate(results)
    direct = records['direct']
    for name, rec in records.items():
        rec['parent_token_saving_pct'] = round(100*(1-rec['parent_tokens']/direct['parent_tokens']), 2)
        rec['all_provider_token_saving_pct'] = round(100*(1-rec['all_provider_tokens']/direct['all_provider_tokens']), 2)
        rec['cost_saving_pct_assumed'] = round(100*(1-rec['usd_assumption']/direct['usd_assumption']), 2)
    data = {'kind': 'offline deterministic accounting, not a model evaluation',
            'token_metric': 'ceil(UTF8_bytes / 4)', 'seeds': seeds,
            'rates_per_million_usd': vars(Rates()),
            'assumed_delagated_latency_seconds': 14.0,
            'scenarios': records}
    if output:
        out = Path(output); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seeds', type=int, default=5)
    parser.add_argument('--output', default='results/benchmark.json')
    args = parser.parse_args()
    if args.seeds < 1: parser.error('seeds must be >=1')
    data = run_benchmark(args.seeds, output=args.output)
    print('Synthetic fixture benchmark. No Claude, Gemini, Spotify Portal, or live costs.')
    for name, row in data['scenarios'].items():
        print(f"{name:25s} parent saved={row['parent_token_saving_pct']:6.2f}%  "
              f"total tokens saved={row['all_provider_token_saving_pct']:7.2f}%  "
              f"estimated cost saved={row['cost_saving_pct_assumed']:6.2f}%  "
              f"quality recall={row['micro_recall']:.3f}  delegated={row['delegations']}")
    print(f'Wrote {args.output}')

if __name__ == '__main__': main()
