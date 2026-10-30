"""Fixed preview compiler: data safety, supported objects and overlapping motion."""
import ast
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.scene_design.scene_preview import source_for
from src.scene_design.design import DEFAULT_CONFIG
from src.scene_design.render import render_scene
from src.shared import _workflow_store as store


def sample_design():
    def obj(id,kind,x,y,width,height,label='',color='#56DFC5',points=None,asset_id=''):
        return dict(id=id,kind=kind,x=x,y=y,width=width,height=height,label=label,color=color,
                    points=points or [],asset_id=asset_id)
    def beat(id,action,start,duration,dx=0,dy=0):
        return dict(object_id=id,action=action,start=start,duration=duration,dx=dx,dy=dy)
    return dict(scene_id='mixed',narration='Lời dẫn đã duyệt',purpose='Kiểm tra các đối tượng',duration=2,
        objects=[obj('title','text',.5,.1,.8,.12,'Đồ thị và chuyển động'),
                 obj('moving','circle',.2,.65,.12,.12,color='#FF2222'),
                 obj('box','rectangle',.5,.85,.25,.1,'Box'),obj('axis','line',.5,.5,.7,0),
                 obj('curve','plot',.5,.3,.6,.2,points=[[0,0],[.5,1],[1,.2]]),
                 obj('photo','image',.85,.75,.15,.15,asset_id='blue')],
        timeline=[beat('moving','move',0,2,dx=.4),beat('moving','move',.5,1.5,dy=-.2),
                  beat('moving','highlight',.6,1),beat('title','appear',0,.3),beat('photo','fade',1,1)],
        math=[],fallback_note='')


class ScenePreviewTests(unittest.TestCase):
    def test_literals_are_reproducible_and_special_text_cannot_become_python(self):
        design=sample_design();design['objects']=design['objects'][:1];design['timeline']=[]
        design['objects'][0]['label']="Dòng 1\n'\"; __import__('os').system('bad')"
        original=copy.deepcopy(design)
        source=source_for(design,[],DEFAULT_CONFIG)
        self.assertEqual(source,source_for(design,[],DEFAULT_CONFIG))
        self.assertEqual(design,original)
        tree=ast.parse(source)
        value=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and n.targets[0].id=='DESIGN')
        self.assertEqual(value,design)

    def test_all_objects_render_and_overlapping_moves_share_one_clock(self):
        import av
        import numpy as np
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'assets').mkdir()
            Image.new('RGBA',(32,32),(30,60,250,180)).save(root/'assets/blue.png')
            assets=[dict(asset_id='blue',path='assets/blue.png')]
            style=copy.deepcopy(DEFAULT_CONFIG)
            style['render']=dict(quality='l',resolution=[320,180],fps=12,timeout_seconds=60)
            (root/'scene.py').write_text(source_for(sample_design(),assets,style))
            result=render_scene(root,'mixed','v001','scene.py','GeneratedScene',style['render'])
            self.assertEqual(result['status'],'success',result)
            info=store.read_json(Path(result['log_path']).with_suffix('.json'))['video']
            self.assertAlmostEqual(info['duration_seconds'],2,delta=1/12)
            with av.open(result['video_path']) as movie:
                frames=[frame.to_ndarray(format='rgb24') for i,frame in enumerate(movie.decode(video=0)) if i in (0,20)]
            self.assertEqual(len(frames),2)
            self.assertGreater(int(frames[0][135,272,2]),120)
            self.assertLess(int(frames[0][135,272,2]),230) # preserve original image alpha
            centers=[]
            for frame in frames:
                red=(frame[:,:,0].astype(float)>100)&(frame[:,:,0]>frame[:,:,1]*1.5)&(frame[:,:,0]>frame[:,:,2]*1.5)
                y,x=np.where(red);self.assertGreater(len(x),10);centers.append((x.mean(),y.mean()))
            self.assertGreater(centers[1][0]-centers[0][0],80)
            self.assertLess(centers[1][1]-centers[0][1],-15)

    def test_failure_after_animation_starts_reports_error_instead_of_timeout(self):
        source="""from manim import *
class GeneratedScene(Scene):
    def construct(self):
        dot=Dot()
        def fail(mob,dt):
            if self.time>.1:
                raise ValueError('preview animation failed')
        dot.add_updater(fail)
        self.add(dot)
        self.play(dot.animate.shift(RIGHT),run_time=1)
"""
        with TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'scene.py').write_text(source)
            result=render_scene(root,'error','v001','scene.py','GeneratedScene',
                                dict(quality='l',resolution=[320,180],fps=6,timeout_seconds=10))
            self.assertEqual(result['status'],'failed')
            self.assertIn('preview animation failed',result['error'])
            self.assertNotIn('TIMEOUT',result['error'])
