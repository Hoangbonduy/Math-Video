"""Local credential loading stays private and independent of the working directory."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from main import main
from src.shared.env import load_local_env
from src.shared.ai_config import MODEL, get_model
from src.shared._workflow_store import WorkflowError


class EnvTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_path = self.root / ".env"
        self.addCleanup(patch.stopall)
        patch("src.shared.env.ENV_PATH", self.env_path).start()
        patch.dict(os.environ, {}, clear=True).start()
        patch("src.shared.ai_config.provider_settings",return_value={}).start()

    def test_missing_and_empty_file_leave_key_unset(self):
        load_local_env()
        self.assertNotIn("OPENROUTER_API_KEY", os.environ)
        self.env_path.write_text('OPENROUTER_API_KEY=""\n')
        load_local_env()
        self.assertNotIn("OPENROUTER_API_KEY", os.environ)

    def test_quotes_comments_and_export_without_shell_execution(self):
        for value in ('test-key', '"test-key"', "'test-key'", '"test-key" # comment'):
            with self.subTest(value=value):
                os.environ.pop("OPENROUTER_API_KEY", None)
                self.env_path.write_text(f'# local config\nexport OPENROUTER_API_KEY={value}\n')
                load_local_env()
                self.assertEqual(os.environ["OPENROUTER_API_KEY"], "test-key")
        marker = self.root / "must-not-exist"
        os.environ.pop("OPENROUTER_API_KEY")
        value = f"$(touch {marker})"
        self.env_path.write_text(f'OPENROUTER_API_KEY="{value}"\nRUN_LIVE_AI_TESTS=1\n')
        load_local_env()
        self.assertEqual(os.environ["OPENROUTER_API_KEY"], value)
        self.assertFalse(marker.exists())
        self.assertNotIn("RUN_LIVE_AI_TESTS", os.environ)

    def test_existing_environment_wins_and_invalid_value_is_not_logged(self):
        self.env_path.write_text('OPENROUTER_API_KEY="private-test-key\n')
        with self.assertRaises(WorkflowError) as result:
            load_local_env()
        self.assertEqual(result.exception.code, "ENV_CONFIG")
        self.assertNotIn("private-test-key", str(result.exception))
        os.environ["OPENROUTER_API_KEY"] = "environment-key"
        load_local_env()
        self.assertEqual(os.environ["OPENROUTER_API_KEY"], "environment-key")

    def test_fpt_key_is_loaded_without_exposing_it(self):
        self.env_path.write_text('FPT_API_KEY="private-fpt-test"\n')
        load_local_env()
        self.assertEqual(os.environ['FPT_API_KEY'],'private-fpt-test')
        from src.shared.openrouter_client import redact
        self.assertEqual(redact('private-fpt-test'),'[REDACTED]')

    def test_model_loads_even_when_api_key_is_already_in_environment(self):
        os.environ["OPENROUTER_API_KEY"] = "environment-key"
        self.env_path.write_text('OPENROUTER_API_KEY="file-key"\nOPENROUTER_MODEL="anthropic/claude-haiku-5.5"\n')
        load_local_env()
        self.assertEqual(os.environ["OPENROUTER_API_KEY"], "environment-key")
        self.assertEqual(os.environ["OPENROUTER_MODEL"], MODEL)
        self.assertEqual(get_model(), MODEL)

    def test_model_is_read_after_loading_and_environment_model_wins(self):
        self.assertEqual(get_model(), MODEL)
        self.env_path.write_text('OPENROUTER_MODEL="test/configured-model"\nOPENROUTER_API_KEY="file-key"\n')
        load_local_env()
        self.assertEqual(get_model(), "test/configured-model")
        os.environ["OPENROUTER_MODEL"] = "test/environment-model"
        load_local_env()
        self.assertEqual(get_model(), "test/environment-model")

    def test_cli_loads_key_from_fixed_path_without_printing_or_persisting_it(self):
        self.env_path.write_text('OPENROUTER_API_KEY="private-test-key"\n')
        output = io.StringIO()
        project = self.root / "project"
        with redirect_stdout(output):
            code = main(["create", "--project-dir", str(project), "--topic", "Test"])
        self.assertEqual(code, 0)
        self.assertEqual(os.environ["OPENROUTER_API_KEY"], "private-test-key")
        self.assertEqual(json.loads(output.getvalue())["status"], "success")
        self.assertNotIn("private-test-key", output.getvalue())
        for name in ("project.json", "reviews.json"):
            self.assertNotIn("private-test-key", (project / name).read_text())

    def test_review_server_loads_env_without_cli_or_current_directory(self):
        from src.shared.review_server import ReviewServer
        self.env_path.write_text('OPENROUTER_API_KEY="private-web-test-key"\nOPENROUTER_MODEL="test/web-model"\nFPT_API_KEY="private-web-fpt"\n')
        with patch('os.getcwd',return_value='/tmp'):
            server = ReviewServer(self.root/'web-project',0)
        try:
            self.assertEqual(os.environ['OPENROUTER_API_KEY'],'private-web-test-key')
            self.assertEqual(os.environ['FPT_API_KEY'],'private-web-fpt')
            self.assertEqual(get_model(),'test/web-model')
            for name in ('project.json','reviews.json'):
                text=(server.root/name).read_text()
                self.assertNotIn('private-web-test-key',text)
                self.assertNotIn('private-web-fpt',text)
        finally: server.server_close()
