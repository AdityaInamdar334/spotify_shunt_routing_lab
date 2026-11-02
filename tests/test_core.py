import json
import tempfile
import unittest
from pathlib import Path
from shunt_lab.core import *
from shunt_lab.benchmark import run_benchmark
from shunt_lab.hook_sim import inspect_read, inspect_bash

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = make_workspace(Path(self.temp.name), 0)
    def tearDown(self): self.temp.cleanup()
    def files(self, name):
        task = next(t for t in default_tasks() if t.name == name)
        return task, load_task_files(self.root, task)
    def test_token_proxy(self):
        self.assertEqual(token_proxy('12345'), 2)
        self.assertEqual(token_proxy(''), 0)
    def test_all_fixtures_generated(self):
        self.assertEqual(len(list(self.root.glob('*.java'))), 4)
    def test_all_expected_facts_are_present(self):
        for t in default_tasks():
            allfacts = set(f for _,txt in load_task_files(self.root, t) for f in extract_facts(txt))
            self.assertTrue(set(t.expected).issubset(allfacts))
    def test_routing_long_file(self):
        t, f = self.files('single-summary')
        self.assertEqual(route(t, f), 'worker')
    def test_small_file_stays_parent(self):
        t, f = self.files('small-config')
        self.assertEqual(route(t, f), 'parent')
    def test_guard_critical(self):
        t, f = self.files('concurrency-review')
        self.assertEqual(route(t, f), 'parent')
    def test_no_guard_risks_reasoning(self):
        t, f = self.files('concurrency-review')
        self.assertEqual(route(t, f, guard=False), 'parent')  # reasoning still explicitly nondelegable
    def test_coded_writer_is_advisory(self):
        t, f = self.files('boilerplate')
        self.assertEqual(route(t, f), 'parent')
        self.assertEqual(route(t, f, code_writer=True), 'worker')
    def test_worker_has_less_parent_context(self):
        t, f = self.files('single-summary')
        self.assertLess(evaluate(t,f,'worker').parent_input, evaluate(t,f,'parent').parent_input)
    def test_worker_consumes_provider_tokens(self):
        t, f = self.files('single-summary')
        self.assertGreater(evaluate(t,f,'worker').worker_input, 0)
    def test_direct_perfect_facts(self):
        for t in default_tasks():
            self.assertEqual(evaluate(t, load_task_files(self.root,t), 'parent').recall, 1.0)
    def test_working_memory_absent_is_not_verified(self):
        t, f = self.files('concurrency-review')
        self.assertLess(evaluate(t,f,'worker').recall,1.0)
    def test_summary_cap_ablation(self):
        t, f = self.files('test-style')
        self.assertLess(evaluate(t,f,'worker',cap_per_file=1).recall, evaluate(t,f,'worker',cap_per_file=3).recall)
    def test_read_hook_block(self):
        self.assertEqual(inspect_read({'tool_input':{}}, 'a\n'*351),'block')
    def test_read_hook_targeted(self):
        self.assertEqual(inspect_read({'tool_input':{'limit':5}}, 'a\n'*351),'allow')
    def test_read_hook_missing(self):
        self.assertEqual(inspect_read({'tool_input':{}}, None),'allow')
    def test_bash_hook_block(self):
        self.assertEqual(inspect_bash('cat large.java', lambda p:'x\n'*500), 'block')
    def test_bash_hook_piped(self):
        self.assertEqual(inspect_bash('cat large.java | grep X', lambda p:'x\n'*500), 'allow')
    def test_repeatability(self):
        a=run_benchmark(2)
        b=run_benchmark(2)
        self.assertEqual(a,b)
    def test_baseline_no_worker(self):
        b=run_benchmark(2)
        self.assertEqual(b['scenarios']['direct']['worker_tokens'],0)
    def test_guard_ablation_quality(self):
        b=run_benchmark(2)
        self.assertGreaterEqual(b['scenarios']['threshold-350']['micro_recall'], b['scenarios']['no-risk-guard']['micro_recall'])

if __name__ == '__main__': unittest.main()
