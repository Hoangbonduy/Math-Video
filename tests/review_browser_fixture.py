import json
from pathlib import Path
import shutil
from unittest.mock import patch
from tests.test_scenes import SceneFlowTests, fake_render
from tests.test_revisions import RevisionRouter
from src.shared import workflow as wf
from src.shared.review import record_review
from src.shared.review_server import ReviewServer
flow=SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates'); flow.setUp()
def media_renderer(*args):
 r=fake_render(*args)
 shutil.copyfile('tests/fixtures/workflow/scene_01.mp4',r['video_path'])
 return r
flow.render.side_effect=media_renderer
patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=RevisionRouter()).start()
flow.approved(3)
state=wf.run_workflow(flow.root,ai=True)
for a in state['pending_reviews']: record_review(flow.root,'scene_design',a['version'],'approve',scene_id=a['scene_id'])
wf.run_workflow(flow.root,ai=True)
server=ReviewServer(flow.root,0)
Path('/tmp/c6_browser_url').write_text(server.origin)
print(server.origin,flush=True)
try: server.serve_forever()
finally: server.server_close(); flow.doCleanups()
