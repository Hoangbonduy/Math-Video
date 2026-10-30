import copy,unittest
from src.scene_design.design import fit_candidate_layout,validate_design
from src.shared._workflow_store import WorkflowError

class CandidateLayoutTests(unittest.TestCase):
    def design(self):
        return dict(scene_id='scene_02',narration='Lời dẫn đã duyệt',purpose='Minh họa',duration=7,
                    objects=[dict(id='graph_curve',kind='plot',label='',x=.25,y=.6,width=.6,height=.4,color='#56DFC5',asset_id='',points=[[0,0],[1,1]])],
                    timeline=[],math=[],fallback_note='')
    def test_shift_draft_inside_without_resizing_or_changing_source(self):
        original=self.design();before=copy.deepcopy(original)
        fitted,changes=fit_candidate_layout(original)
        self.assertEqual(original,before)
        self.assertEqual(fitted['objects'][0]['x'],.3)
        self.assertEqual(fitted['objects'][0]['width'],.6)
        self.assertEqual(fitted['narration'],original['narration'])
        self.assertEqual(len(changes),1)
        validate_design(fitted,dict(scene_id='scene_02',narration=original['narration']),7,[])
        self.assertEqual(fit_candidate_layout(fitted),(fitted,[]))
    def test_oversized_object_is_still_rejected(self):
        design=self.design();design['objects'][0]['width']=1.2
        fitted,changes=fit_candidate_layout(design)
        self.assertEqual(changes,[])
        self.assertRaisesRegex(WorkflowError,'Invalid size',validate_design,fitted,dict(scene_id='scene_02',narration=design['narration']),7,[])
