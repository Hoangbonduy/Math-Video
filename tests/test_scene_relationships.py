"""Topic-independent scene relationships and bounded repair, without live AI."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.scene_design import scene_contract as c, scene_pipeline as pipeline
from src.scene_design.design import DEFAULT_CONFIG
from src.shared._workflow_store import WorkflowError
from tests.scene_v2_support import legacy_config, vehicle, obj, design, story, binding
from tests.test_fraction_overlaps import fraction_design


def collisions(d):
    return [i for i in c.motion_report(c.compile_design(d))['issues']
            if 'collides with separate object' in i['problem']]


class RelationshipTests(unittest.TestCase):
    def test_renaming_shapes_never_changes_collision_policy(self):
        for name in ['obstacle','road','panel','bg','screen','cake','car_body']:
            with self.subTest(name=name):
                d=fraction_design()
                d['objects'][0]['id']=name
                self.assertEqual(len(collisions(d)),2)
                self.assertTrue(any(name in i['object_ids'] for i in collisions(d)))

    def test_large_shapes_and_containment_need_explicit_intent(self):
        d=fraction_design()
        d['objects'][0].update(x=.5,y=.5,width=.9,height=.8)
        self.assertTrue(any('whole' in i['object_ids'] for i in collisions(d)))
        d['objects'][1]['overlap_with']=['whole','destination']
        self.assertFalse(collisions(d))

    def test_grouped_composite_is_valid_but_independent_parts_are_checked(self):
        d=vehicle()
        self.assertFalse(collisions(d))
        d['objects'].append(obj('block','rectangle',.5,.5,.08,.1))
        d['groups'][0]['members'].append('block')
        self.assertFalse(collisions(d))
        d['timeline'].append(dict(object_id='block',action='move',start=0,
                                 duration=3,dx=-.1,dy=0,value=0,easing='linear'))
        self.assertTrue(any('block' in i['object_ids'] for i in collisions(d)))

    def test_assembly_is_generic_and_does_not_exempt_other_parts(self):
        d=fraction_design()
        for o in d['objects']:
            o['id']={'whole':'housing','piece':'component','destination':'socket'}[o['id']]
        d['timeline'][0]['object_id']='component'
        d['objects'][1]['overlap_with']=['housing','socket']
        self.assertTrue(c.motion_report(c.compile_design(d))['passed'])
        d['objects'].append(obj('independent_part','rectangle',.5,.5,.08,.28))
        self.assertTrue(any('independent_part' in i['object_ids'] for i in collisions(d)))

    def test_rotation_scale_and_highlight_can_cause_collisions(self):
        for action,value in [('scale',2),('rotate',90),('highlight',0)]:
            with self.subTest(action=action):
                d=fraction_design()
                d['objects']=[obj('a','rectangle',.4,.5,.1,.2),
                              obj('b','rectangle',.485,.5,.1,.1)]
                d['timeline']=[dict(object_id='a',action=action,start=0,
                                    duration=3,dx=0,dy=0,value=value,easing='linear')]
                self.assertTrue(collisions(d))

    def test_declared_overlap_still_cannot_leave_the_frame(self):
        d=fraction_design()
        d['objects'][1]['overlap_with']=['whole','destination']
        d['timeline'][0]['dx']=1
        self.assertTrue(any('leaves the frame' in i['problem']
                            for i in c.motion_report(c.compile_design(d))['issues']))

    def test_hidden_shapes_do_not_collide(self):
        d=fraction_design()
        d['timeline'].append(dict(object_id='piece',action='appear',start=2.9,
                                 duration=.1,dx=0,dy=0,value=0,easing='linear'))
        found=collisions(d)
        self.assertTrue(found)
        self.assertTrue(all(i['time']>=2.9 for i in found))

    def test_contrast_on_rectangle_does_not_depend_on_name_or_size(self):
        for name in ['road','panel','arbitrary']:
            d=fraction_design()
            d['objects']=[obj(name,'rectangle',.5,.5,.8,.5),
                          obj('label','text',.5,.5,.2,.06,'text')]
            d['timeline']=[]
            self.assertTrue(any('background rectangle' in i['problem']
                                for i in c.motion_report(c.compile_design(d))['issues']))

    def test_graph_relations_still_pass_and_unreadable_label_is_reported(self):
        d=design()
        self.assertTrue(c.motion_report(c.compile_design(d))['passed'])
        d['bindings'][3].update(offset_x=0,offset_y=0)
        self.assertTrue(any('Text box overlaps dot' in i['problem']
                            for i in c.motion_report(c.compile_design(d))['issues']))


class RepairTests(unittest.TestCase):
    def response(self,d,entries):
        return dict(layout=[],colors=[],label_offsets=[],timeline=d['timeline'],
                    parameter_tracks=d['parameter_tracks'],intentional_overlaps=entries)

    def test_patch_rejects_blanket_and_text_exemptions(self):
        d=fraction_design()
        d['objects'].append(obj('label','text',.5,.1,.2,.06,'text'))
        issues=collisions(d)
        for entries in [
            [dict(id='whole',overlap_with=['destination'])],
            [dict(id='piece',overlap_with=['label'])],
            [dict(id='piece',overlap_with=['whole','whole'])]]:
            with self.subTest(entries=entries),patch.object(pipeline,'_ask',return_value=self.response(d,entries)):
                with self.assertRaises(WorkflowError):
                    pipeline._patch(None,dict(scene={},target_seconds=3),{},d,1,issues)

    def test_persistent_collision_can_rebuild_groups_and_paths(self):
        d=fraction_design();fixed=copy.deepcopy(d)
        fixed['objects'][1]['overlap_with']=['whole','destination']
        context=dict(scene=dict(scene_id=d['scene_id'],narration=d['narration']),
                     target_seconds=3,config=legacy_config())
        session=SimpleNamespace(job=dict(input=dict(max_repairs=2)),save=lambda:None)
        with tempfile.TemporaryDirectory() as folder,patch.object(pipeline,'_patch',return_value=d) as layout,patch.object(pipeline,'_design',return_value=fixed) as rebuild:
            result,checks,inner=pipeline._pre_render(session,context,story(),d,
                                                     dict(assets=[]),0,Path(folder))
        self.assertTrue(checks['passed'])
        self.assertEqual(inner,2)
        layout.assert_called_once()
        rebuild.assert_called_once()
        self.assertEqual(rebuild.call_args.args[4],'0_pre_2')
        self.assertEqual(result,fixed)

    def test_design_prompts_do_not_infer_topic_or_require_named_objects(self):
        prompts=[]
        for narration in ['radar','fraction addition','assembly','graph']:
            context=dict(scene=dict(narration=narration))
            with patch.object(pipeline,'_ask',return_value={}) as ask:
                pipeline._design(None,context,{},dict(assets=[]),0,None,[])
            prompts.append(ask.call_args.args[2])
        self.assertTrue(all(p==prompts[0] for p in prompts))
        for token in ['VEHICLE/RADAR','car_body','wheel_left','roadside','fraction pieces']:
            self.assertNotIn(token,prompts[0])

    def test_unsupported_repair_preserves_generic_geometry(self):
        issues=[dict(category='unsupported',problem='unsupported',fix='Use available geometry')]
        fixed=pipeline.repair_feedback(issues,0,2)
        self.assertEqual(fixed[0]['category'],'relation')
        self.assertNotIn('wave IDs',fixed[0]['fix'])
        self.assertEqual(issues[0]['category'],'unsupported')


if __name__=='__main__':
    unittest.main()
