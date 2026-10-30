"""Regression checks for moved resources and the four browser feature scripts."""
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from src.shared.env import ENV_PATH
from src.shared.paths import PROJECT_ROOT
from src.shared.review_server import ReviewServer


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.paths.append(dict(attrs)['src'])


class SourceLayoutTests(unittest.TestCase):
    def test_settings_root_is_independent_of_feature_depth(self):
        self.assertEqual(PROJECT_ROOT, Path(__file__).resolve().parents[1])
        self.assertEqual(ENV_PATH, PROJECT_ROOT / '.env')
        self.assertTrue((PROJECT_ROOT / 'settings/ai_provider.json').is_file())

    def test_page_assets_load_in_order_without_exposing_python(self):
        with TemporaryDirectory() as directory:
            server = ReviewServer(Path(directory) / 'project', 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(server.origin, timeout=5) as response:
                    html = response.read().decode()
                parser = Scripts()
                parser.feed(html)
                self.assertEqual(parser.paths, [
                    '/idea/web.js', '/scene_design/web.js', '/preview/web.js',
                    '/script_history/web.js', '/app.js',
                ])
                for path in [*parser.paths, '/style.css']:
                    with self.subTest(path=path), urlopen(server.origin + path, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertGreater(len(response.read()), 100)
                for path in ('/shared/ai_config.py', '/idea/service.py',
                             '/idea/../shared/ai_config.py'):
                    with self.subTest(path=path), self.assertRaises(HTTPError) as failure:
                        urlopen(server.origin + path, timeout=5)
                    self.assertEqual(failure.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
