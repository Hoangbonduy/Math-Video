"""Fresh opening-form server with deterministic offline AI boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.shared.review_server import ReviewServer
from src.scene_design import scene_generation
from tests.ai_support import FakeRouter, fake_fetch
from tests.test_scenes import SceneRouter, fake_render

# This fixture's fake SceneRouter implements the legacy JSON contract. The v2
# renderer and visual gate have their own real-media browser fixture.
with (
    TemporaryDirectory() as directory,
    patch('src.idea.generation.CLIENT_FACTORY', return_value=FakeRouter()),
    patch('src.idea.research.fetch_html', side_effect=fake_fetch),
    patch('src.scene_design.scene_generation.CLIENT_FACTORY', return_value=SceneRouter()),
    patch('src.scene_design.scene_generation.RENDERER', side_effect=fake_render),
    patch('src.scene_design.scene_generation.DESIGN_PIPELINE_VERSION', 1),
    patch('src.scene_design.scene_generation._design', side_effect=scene_generation._legacy_design),
    patch('src.shared.ai_config.scene_trial_enabled', return_value=False),
    patch('src.idea.service.default_values', return_value=dict(topic='', duration='30–60 giây', format='landscape')),
):
    server = ReviewServer(Path(directory) / 'new-video', 0)
    print(server.origin, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
