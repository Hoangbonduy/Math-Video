"""Temporary browser project: offline AI provider, actual Manim rendering."""
from unittest.mock import patch
from tests import test_scenes as scene_fixtures
from tests.test_manim_agent import OfflineManimRouter
from src.shared import workflow as wf
from src.scene_design.render import render_scene
from src.shared.review_server import ReviewServer

flow=scene_fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
flow.setUp()
patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=OfflineManimRouter()).start()
patch('src.scene_design.scene_generation.RENDERER',side_effect=render_scene).start()
patch('src.shared.openrouter_client.OpenRouterClient._send',side_effect=AssertionError('No live AI in browser validation')).start()
flow.approved(2,topic='Đồ thị và tiếp tuyến')
wf.run_workflow(flow.root,ai=True)
patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('Design promotion cannot call AI')).start()
server=ReviewServer(flow.root,0)
print(server.origin,flush=True)
try:server.serve_forever()
finally:server.server_close();flow.doCleanups()
