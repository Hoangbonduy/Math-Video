"""Regression tests for intentional fraction-piece assembly overlaps."""
import copy
import unittest
from unittest.mock import patch

from src.scene_design import scene_contract as c, scene_pipeline as pipeline
from src.shared._workflow_store import WorkflowError
from tests.scene_v2_support import vehicle, obj


def fraction_design():
    d=vehicle()
    d.update(groups=[], objects=[
        obj('whole','rectangle',.3,.5,.2,.3),
        obj('piece','rectangle',.25,.5,.09,.28),
        obj('destination','rectangle',.7,.5,.2,.3)],
        timeline=[dict(object_id='piece',action='move',start=0,duration=3,
                       dx=.4,dy=0,value=0,easing='linear')])
    return d


class FractionOverlapTests(unittest.TestCase):
    def test_explicit_source_and_destination_overlaps_only(self):
        d=fraction_design()
        issues=c.motion_report(c.compile_design(d))['issues']
        collisions=[i for i in issues if 'collides with separate object' in i['problem']]
        self.assertEqual(len(collisions),2)
        self.assertTrue(all('roadside' not in i['fix'] for i in collisions))
        d['objects'][1]['overlap_with']=['whole','destination']
        self.assertTrue(c.motion_report(c.compile_design(d))['passed'])
        d['objects'].append(obj('obstacle','rectangle',.5,.5,.08,.28))
        issues=c.motion_report(c.compile_design(d))['issues']
        self.assertTrue(any(set(i['object_ids'])=={'piece','obstacle'} for i in issues))

    def test_collision_patch_preserves_geometry_and_validates_ids(self):
        d=fraction_design()
        issues=c.motion_report(c.compile_design(d))['issues']
        response=dict(layout=[],colors=[],label_offsets=[],
                      timeline=d['timeline'],parameter_tracks=[],
                      intentional_overlaps=[dict(id='piece',overlap_with=['whole','destination'])])
        context=dict(scene={},target_seconds=3)
        with patch.object(pipeline,'_ask',return_value=response) as ask:
            fixed=pipeline._patch(None,context,{},d,1,issues)
        self.assertIn('intentional_overlaps',ask.call_args.args[-1]['required'])
        self.assertEqual(fixed['timeline'],d['timeline'])
        self.assertEqual(d['objects'][1]['overlap_with'],[])
        self.assertTrue(c.motion_report(c.compile_design(fixed))['passed'])
        response['intentional_overlaps'][0]['overlap_with']=['missing']
        with patch.object(pipeline,'_ask',return_value=response):
            with self.assertRaises(WorkflowError):
                pipeline._patch(None,context,{},d,1,issues)


if __name__=='__main__':
    unittest.main()
