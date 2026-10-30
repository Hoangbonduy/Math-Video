"""Browser fixture uses real Manim render and fake external providers only."""
import tempfile,copy,json
from pathlib import Path
from unittest.mock import patch
from src.shared import workflow as wf
from src.scene_design import scene_generation as sg
from src.shared.review_server import ReviewServer
from tests.test_scene_v2_workflow import approved_project
from tests.scene_v2_support import DesignRouter,VisionRouter
with tempfile.TemporaryDirectory() as folder:
    root=approved_project(Path(folder));router=DesignRouter();vision=VisionRouter()
    def context(root,p,r):return wf._latest(p,'script',None),json.loads((root/'input.json').read_text()),{'scene_1':3}
    with patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router),patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision),patch('src.scene_design.scene_generation._context',side_effect=context):
        wf.run_workflow(root,ai=True)
        server=ReviewServer(root,0);print(server.origin,flush=True)
        try:server.serve_forever()
        finally:server.server_close()
