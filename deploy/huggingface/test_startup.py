"""Run with python -m unittest discover -s deploy/huggingface -p 'test_*.py'."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skillwiki'))

def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded

startup = module('start')
package = module('prepare_space')

class StartupTests(unittest.TestCase):
    def test_missing_key_and_budget_fail(self):
        for env in ({}, {'LLM_API_KEY': 'offline-smoke-not-a-real-key'}):
            with self.subTest(env=bool(env)), self.assertRaises(ValueError):
                startup.runtime_environment(env)

    def test_invalid_budget_fails(self):
        for budget in ('nan', 'inf', '-1', '0'):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                startup.runtime_environment({'LLM_API_KEY': 'offline-smoke-not-a-real-key', 'SPACE_MODEL_BUDGET_CNY': budget})

    def test_relative_storage_fails(self):
        with self.assertRaises(ValueError):
            startup.runtime_environment({'LLM_API_KEY': 'offline-smoke-not-a-real-key', 'SPACE_MODEL_BUDGET_CNY': '1', 'SPACE_DATA_DIR': 'relative'})

    def test_defaults_and_persistent_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            env, root = startup.runtime_environment({'LLM_API_KEY': 'offline-smoke-not-a-real-key', 'SPACE_MODEL_BUDGET_CNY': '1', 'SPACE_DATA_DIR': temporary})
            self.assertEqual(env['LLM_TIMEOUT'], '120')
            self.assertEqual(env['LLM_MAX_TOKENS'], '16384')
            self.assertEqual(env['LLM_RETRY_COUNT'], '0')
            self.assertEqual(Path(env['SKILLOS_GOVERNANCE_REPO']), root / 'governance')
            self.assertEqual(Path(env['SKILLWIKI_CUMULATIVE_BUDGET_LEDGER_PATH']), root / 'budget/model-usage.jsonl')

    def test_unpriced_model_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(Exception):
            startup.runtime_environment({'LLM_API_KEY': 'offline-smoke-not-a-real-key', 'SPACE_MODEL_BUDGET_CNY': '1', 'SPACE_DATA_DIR': temporary, 'LLM_MODEL': 'unknown-provider-model'})

    def test_package_selection_excludes_private_runtime(self):
        for name in ('research/result.json', '.env', 'skillwiki/config.local.json', 'skillwiki-frontend/node_modules/package/index.js', 'skillwiki/skillwiki/storage/skill_repo/SkillStorage/example.json', 'skillwiki/skillwiki/config/.env', 'skillwiki/skillwiki/config/private.key'):
            with self.subTest(name=name):
                self.assertFalse(package.selected(name))
        self.assertTrue(package.selected('skillwiki/skillwiki/api/main.py'))
        self.assertTrue(package.selected('skillwiki-frontend/src/App.tsx'))

    def test_export_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            package.export(Path(temporary))

if __name__ == '__main__':
    unittest.main()
