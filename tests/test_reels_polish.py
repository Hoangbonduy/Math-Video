"""Local acceptance for Reels FPS, smooth display/core audit and text updates."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from manim import Text, Mobject, tempconfig
from src.scene_design import scene_render_v2 as renderer, scene_contract as contract
from src.scene_design.design import DEFAULT_CONFIG
from src.scene_design.render import _settings
from tests.scene_v2_support import design, obj, story


class ReelsPolishTests(unittest.TestCase):
    def test_defaults_and_explicit_resolution(self):
        self.assertEqual(DEFAULT_CONFIG['render']['resolution'], [360,640])
        self.assertEqual(DEFAULT_CONFIG['render']['fps'], 30)
        self.assertEqual(_settings({})[1]['resolution'], [360,640])
        self.assertEqual(_settings({'resolution':[720,1280]})[1]['fps'], 30)
        self.assertEqual(_settings({'resolution':[720,1280]})[1]['resolution'], [720,1280])

    def test_safe_area_checks_whole_highlighted_text_box(self):
        d=design(seconds=1)
        d.update(aspect_ratio=9/16,objects=[obj('caption','text',.5,.7225,.6,.05,'Safe')],groups=[],parameters=[],parameter_tracks=[],graphs=[],bindings=[],timeline=[])
        self.assertTrue(contract.motion_report(contract.compile_design(d),30)['passed'])
        d['timeline']=[dict(object_id='caption',action='highlight',start=0,duration=1,dx=0,dy=0,value=0,easing='linear')]
        result=contract.motion_report(contract.compile_design(d),30)
        self.assertTrue(any('social-video UI' in i['problem'] for i in result['issues']))
        d['aspect_ratio']=16/9
        self.assertTrue(contract.motion_report(contract.compile_design(d),30)['passed'])

    def test_text_motion_opacity_rotation_scale_never_rebuild_glyphs(self):
        d=design(seconds=1)
        d.update(aspect_ratio=9/16,objects=[obj('caption','text',.4,.3,.5,.06,'Delta t')],groups=[],parameters=[],parameter_tracks=[],graphs=[],bindings=[],timeline=[])
        for action,kw in [('move',dict(dx=.1,dy=.1)),('fade',{}),('rotate',dict(value=12)),('scale',dict(value=1.1))]:
            d['timeline'].append(dict(dict(object_id='caption',action=action,start=0,duration=1,dx=0,dy=0,value=0,easing='linear'),**kw))
        source,plan,frames=renderer.source_for(d,[],DEFAULT_CONFIG,story(seconds=1))
        namespace={};exec(source,namespace)
        def play(scene,*args,**kwargs):
            clock=scene.mobjects[0];controller=scene.mobjects[-1]
            for frame in range(1,31):
                clock.set_value(frame/30);controller.update(1/30)
        original=Mobject.become
        def become(mob,*args,**kwargs):
            if isinstance(mob,Text):raise AssertionError('Text rebuilt for a transform')
            return original(mob,*args,**kwargs)
        with tempfile.TemporaryDirectory() as folder, tempconfig(dict(media_dir=folder,dry_run=True,pixel_width=360,pixel_height=640,frame_width=4.5,frame_height=8)):
            with patch.dict(namespace,Text=unittest.mock.Mock(wraps=Text)):
                with patch.object(namespace['GeneratedScene'],'play',play),patch.object(Mobject,'become',become):
                    scene=namespace['GeneratedScene']();scene.construct()
                self.assertEqual(namespace['Text'].call_count,1)
            result=renderer._audit(plan,scene.design_audit,frames,30)
            self.assertTrue(result['passed'],result['issues'])

    def test_real_30fps_cubic_wave_glow_and_core_audit(self):
        d=design(seconds=2);d['aspect_ratio']=9/16
        d['graphs'][0].update(expression='x**3',samples=16,y_max=15.625)
        d['objects'][1].update(y=.4,height=.38)
        d['objects'][5].update(y=.25)
        d['bindings'][3].update(offset_x=.06,offset_y=.06)
        d['objects'].append(obj('emitted_wave','plot',.5,.68,.7,.06))
        d['objects'][-1]['points']=[[0,.5],[1,.5]]
        d['objects'].append(obj('car_body','rectangle',.5,.58,.2,.06))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'draft';work.mkdir()
            ref,checks,_=renderer.render(root,work,{},d,dict(assets=[]),copy.deepcopy(DEFAULT_CONFIG),story(seconds=2))
            self.assertTrue(checks['passed'],checks['issues'])
            self.assertEqual((ref['video']['width'],ref['video']['height'],ref['video']['fps']),(360,640,30))
            self.assertEqual(ref['video']['frame_count'],60)
            for row in checks['states']:
                self.assertEqual(row['objects']['emitted_wave']['path_point_count'],129)
                self.assertEqual(row['objects']['car_body']['stroke_width'],5)
            source,plan,frames=renderer.source_for(d,[],DEFAULT_CONFIG,story(seconds=2))
            self.assertEqual(plan['graphs']['f']['samples'],100)
            bad=copy.deepcopy(checks['states']);bad[0]['objects']['car_body']['center'][0]+=.001
            self.assertFalse(renderer._audit(plan,bad,frames,30)['passed'])

if __name__=='__main__':unittest.main()
