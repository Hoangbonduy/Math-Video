"""Local UI fixture: delayed offline AI, real renderer, deliberate second-scene failure."""
import json,time
from unittest.mock import patch
from tests.test_scenes import SceneFlowTests
from tests.test_manim_agent import OfflineManimRouter
from src.scene_design.render import render_scene
from src.shared.review_server import ReviewServer

class Router(OfflineManimRouter):
    def complete(self,payload):
        time.sleep(2.5)
        return super().complete(payload)
    def reserve(self,payload,pricing):
        context=json.loads(payload['messages'][1]['content'])
        if context['scene']['scene_id']=='scene_2':return .6
        return super().reserve(payload,pricing)

flow=SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
flow.setUp();flow.approved()
router=Router()
patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router).start()
def renderer(*args):
    time.sleep(3)
    return render_scene(*args)
patch('src.scene_design.scene_generation.RENDERER',side_effect=renderer).start()
patch('src.shared.openrouter_client.OpenRouterClient._send',side_effect=AssertionError('No live AI')).start()
server=ReviewServer(flow.root,0)
print(server.origin,flush=True)
try:server.serve_forever()
finally:server.server_close();flow.doCleanups()
