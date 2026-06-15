import importlib.util
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "config.py"

spec = importlib.util.spec_from_file_location("config", MODULE_PATH)
config = importlib.util.module_from_spec(spec)
sys.modules["config"] = config
spec.loader.exec_module(config)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_base_url_prefers_review_assistant_env(self):
        os.environ["DEEPSEEK_BASE_URL"] = "https://deepseek.example/"
        os.environ["REVIEW_ASSISTANT_BASE_URL"] = "https://review.example/"
        self.assertEqual(config.get_base_url(), "https://review.example")

    def test_model_and_workers_env(self):
        os.environ["REVIEW_ASSISTANT_MODEL"] = "custom-model"
        os.environ["REVIEW_ASSISTANT_WORKERS"] = "7"
        self.assertEqual(config.get_model("fallback"), "custom-model")
        self.assertEqual(config.get_workers(3), 7)

    def test_step7_model_has_specific_override(self):
        os.environ.pop("REVIEW_ASSISTANT_STEP7_MODEL", None)
        os.environ["REVIEW_ASSISTANT_MODEL"] = "main-model"
        self.assertEqual(config.get_step7_model(), "main-model")
        os.environ["REVIEW_ASSISTANT_STEP7_MODEL"] = "table-model"
        self.assertEqual(config.get_step7_model(), "table-model")

    def test_proxy_defaults_preserve_current_bypass_behavior(self):
        self.assertTrue(config.should_strip_proxy())
        os.environ["REVIEW_ASSISTANT_USE_PROXY"] = "true"
        self.assertFalse(config.should_strip_proxy())

    def test_zotero_web_config(self):
        os.environ["ZOTERO_API_KEY"] = "zkey"
        os.environ["ZOTERO_LIBRARY_TYPE"] = "group"
        os.environ["ZOTERO_LIBRARY_ID"] = "42"
        os.environ["ZOTERO_WEB_IMPORT"] = "true"
        os.environ["ZOTERO_SYNC_TIMEOUT"] = "30"
        self.assertEqual(config.get_zotero_api_key(), "zkey")
        self.assertEqual(config.get_zotero_library_type(), "group")
        self.assertEqual(config.get_zotero_library_id(), "42")
        self.assertTrue(config.get_zotero_web_import())
        self.assertEqual(config.get_zotero_sync_timeout(), 30)


if __name__ == "__main__":
    unittest.main()
