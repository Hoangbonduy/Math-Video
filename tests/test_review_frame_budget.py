"""Dense scene evidence stays bounded without discarding geometric audit frames."""
import copy
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.scene_design import scene_render_v2 as renderer
from src.scene_design.design import DEFAULT_CONFIG
from tests.scene_v2_support import design,story,obj


def dense_design():
    d=design(seconds=10.8)
    d.update(objects=[obj('wave','line',.5,.5,.7,.1)],groups=[],parameters=[],
             parameter_tracks=[],graphs=[],bindings=[])
    d['timeline']=[dict(object_id='wave',action='highlight',start=i*.4,duration=.24,
                        dx=0,dy=0,value=0,easing='linear') for i in range(26)]
    return d


class ReviewFrameTests(unittest.TestCase):
    def test_dense_milestones_keep_all_audit_frames_and_bound_visual_input(self):
        d=dense_design();sc=story(seconds=d['duration']);fps=12
        frames=renderer.frame_times(d,sc,fps)
        self.assertGreater(len(frames),64)
        chosen=renderer.review_frames(d,sc,fps,frames)
        self.assertEqual(len(chosen),64)
        self.assertEqual(chosen,sorted(set(chosen)))
        self.assertTrue(set(chosen)<=set(frames))
        self.assertEqual(chosen[0],0)
        self.assertEqual(chosen[-1],math.ceil(d['duration']*fps)-1)
        for t in d['timeline']:
            peak=math.floor((t['start']+t['duration']/2)*fps+1e-6)
            self.assertIn(peak,chosen)
        for b in sc['beats']:
            span=b['end']-b['start']
            for t in [b['start']+span*.15,(b['start']+b['end'])/2,b['end']-span*.05]:
                self.assertIn(math.floor(t*fps+1e-6),chosen)
        reverse=copy.deepcopy(d);reverse['timeline'].reverse()
        self.assertEqual(renderer.review_frames(reverse,sc,fps,frames),chosen)

    def test_sparse_scene_evidence_is_unchanged(self):
        d=design();sc=story();frames=renderer.frame_times(d,sc,12)
        self.assertEqual(renderer.review_frames(d,sc,12,frames),frames)

    def test_dense_real_video_audits_every_milestone_and_extracts_in_one_process(self):
        d=dense_design();sc=story(seconds=d['duration'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'draft';work.mkdir()
            with patch.object(renderer,'run',wraps=renderer.run) as processes:
                ref,checks,manifest=renderer.render(root,work,{},d,dict(assets=[]),DEFAULT_CONFIG,sc)
            self.assertTrue(checks['passed'],checks['issues'])
            self.assertGreater(checks['checked_keyframes'],64)
            self.assertEqual(len(checks['states']),checks['checked_keyframes'])
            self.assertEqual(len(manifest['frames']),64)
            self.assertEqual(manifest['sampling']['audit_frame_count'],checks['checked_keyframes'])
            self.assertEqual(processes.call_count,1)
            self.assertTrue((root/ref['path']).is_file())
            self.assertTrue(all((root/f['path']).is_file() for f in manifest['frames']))
