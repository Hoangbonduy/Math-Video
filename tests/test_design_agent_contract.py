"""Replay actual invalid scene shapes with an offline provider; never calls a service."""
import copy,json,unittest
from unittest.mock import patch
from src.shared import _workflow_store as store
from src.scene_design import scene_generation as sg
from src.shared import workflow as wf
from src.shared.ai_config import MODEL
from src.scene_design.design import DEFAULT_CONFIG,design_schema,normalize_candidate_design,validate_design
from tests import test_scenes as fixtures


def invalid_design(scene,seconds):
    # Saved scene_01 failure: empty image reference/color and an empty text label.
    def obj(id,kind,**kwargs):
        return dict(id=id,kind=kind,label='',x=.5,y=.5,width=.2,height=.2,color='#F2F6FC',asset_id='',points=[],**kwargs)
    car=obj('car','image');car['color']=''
    return dict(scene_id=scene['scene_id'],narration=scene['narration'],purpose='Giải thích tốc độ',duration=seconds,
                objects=[car,obj('speed_text','text')],timeline=[dict(object_id='car',action='move',start=0,duration=seconds,dx=.1,dy=0)],
                math=[],fallback_note='Dùng hình vector vì không có ảnh.')


class ContractTests(unittest.TestCase):
    scene=dict(scene_id='scene_01',narration='Lời dẫn đã duyệt')
    def test_reports_all_semantic_errors_with_object_paths(self):
        design=invalid_design(self.scene,5);design['objects'][0]['color']='red'
        with self.assertRaises(store.WorkflowError) as result:validate_design(design,self.scene,5,[])
        error=str(result.exception)
        for expected in ('objects[0] (car).color','objects[0] (car).asset_id','objects[1] (speed_text).label','vector'):
            self.assertIn(expected,error)

    def test_empty_style_color_defaults_without_inventing_an_image_or_text(self):
        design=invalid_design(self.scene,5);before=copy.deepcopy(design)
        candidate,changes=normalize_candidate_design(design,DEFAULT_CONFIG)
        self.assertEqual(design,before)
        self.assertEqual(candidate['objects'][0]['color'],DEFAULT_CONFIG['foreground'])
        self.assertEqual(candidate['objects'][0]['asset_id'],'')
        self.assertEqual(candidate['objects'][1]['label'],'')
        self.assertTrue(changes)
        self.assertRaisesRegex(store.WorkflowError,'Unknown image',validate_design,candidate,self.scene,5,[])

    def test_unambiguous_hex_formatting_and_unknown_color_is_not_guessed(self):
        for raw,expected in ((' #abc ','#AABBCC'),('ff0011','#FF0011'),('#abcdef','#ABCDEF'),('bad-color','bad-color')):
            with self.subTest(raw=raw):
                design=invalid_design(self.scene,5);design['objects'][0]['color']=raw
                candidate,_=normalize_candidate_design(design,DEFAULT_CONFIG)
                self.assertEqual(candidate['objects'][0]['color'],expected)

    def test_schema_depends_on_available_assets_and_requires_six_digit_colors(self):
        for assets in ([],[dict(asset_id='licensed_car')]):
            schema=design_schema(assets);obj=schema['properties']['objects']['items']['properties']
            self.assertEqual('image' in obj['kind']['enum'],bool(assets))
            self.assertEqual(obj['asset_id']['enum'],['']+[a['asset_id'] for a in assets])
            self.assertEqual(obj['color']['pattern'],'^#[0-9a-fA-F]{6}$')
        self.assertEqual(design_schema([])['properties']['objects']['items']['properties']['asset_id']['enum'],[''])

    def test_structural_error_names_exact_path(self):
        design=invalid_design(self.scene,5);design['objects'][0]['color']=None
        self.assertRaisesRegex(store.WorkflowError,r'design.objects\[0\].color',validate_design,design,self.scene,5,[])

    def test_narration_duration_assets_and_visible_text_are_never_silently_repaired(self):
        design=invalid_design(self.scene,8);design['narration']='Changed'
        candidate,_=normalize_candidate_design(design,DEFAULT_CONFIG)
        with self.assertRaises(store.WorkflowError) as result:validate_design(candidate,self.scene,5,[])
        for expected in ('narration','duration','Unknown image','Empty text object'):self.assertIn(expected,str(result.exception))


class RepairRouter(fixtures.SceneRouter):
    def __init__(self):
        super().__init__();self.design_calls=0;self.always_invalid=False;self.truncate_first=False;self.wrong_model=False
        self.invalid_designs=2;self.empty_choices=False;self.missing_fallback=False
    def complete(self,payload):
        response=super().complete(payload)
        if 'objects' in payload['response_format']['json_schema']['schema']['properties']:
            self.design_calls+=1
            context=json.loads(payload['messages'][1]['content'])
            if self.always_invalid or self.design_calls<=self.invalid_designs:
                result=invalid_design(context['scene'],context['target_seconds'])
                response['choices'][0]['message']['content']=json.dumps(result,ensure_ascii=False)
            if self.truncate_first and self.design_calls==1:response['choices'][0]['finish_reason']='length'
            if self.missing_fallback and self.design_calls==1:
                result=json.loads(response['choices'][0]['message']['content']);result['fallback_note']=''
                response['choices'][0]['message']['content']=json.dumps(result,ensure_ascii=False)
            if self.empty_choices and self.design_calls==1:response['choices']=[]
            if self.wrong_model:response['model']='unexpected/model'
        return response


class AgentRepairTests(unittest.TestCase):
    def setUp(self):
        self.flow=fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates')
        self.flow.setUp();self.addCleanup(self.flow.doCleanups)
        self.flow.approved(1)
        self.root=self.flow.root;self.router=RepairRouter()
        patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=self.router).start()
        patch('src.shared.openrouter_client.OpenRouterClient._send',side_effect=AssertionError('No live AI in agent tests')).start()

    def test_resume_old_failed_repairs_reuses_cached_calls_and_repairs_all_errors(self):
        with patch('src.scene_design.scene_generation.DESIGN_REPAIR_LIMIT',1):
            self.assertRaisesRegex(store.WorkflowError,'Unknown image',wf.run_workflow,self.root,ai=True,scene_id='scene_1')
        job=sg._jobs(self.root)[0];directory=self.root/sg.BASE/job['job_id']
        old_calls={p.name:p.read_bytes() for p in (directory/'calls').glob('*.json')}
        calls=len(self.router.requests)
        state=wf.run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(self.router.requests),calls+1) # one new design repair; preview is local
        self.assertEqual(self.router.design_calls,3)
        for name,data in old_calls.items():self.assertEqual((directory/'calls'/name).read_bytes(),data)
        repair=next(p for p in self.router.requests if 'objects' in p['response_format']['json_schema']['schema']['properties'] and len(p['messages'])>4)
        self.assertIn('objects[0] (car).asset_id',repair['messages'][-1]['content'])
        self.assertIn('objects[1] (speed_text).label',repair['messages'][-1]['content'])
        self.assertTrue(store.read_json(directory/'validation_error_1.json')['repeated_candidate'])
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertEqual([c['phase'] for c in sg._jobs(self.root)[0]['calls']],['asset_plan','design','design_repair','design_repair_2'])
        self.assertAlmostEqual(state['scene_generation']['budget_used_usd'],.0004)
        self.assertEqual(self.flow.render.call_count,1)

    def test_repeated_invalid_candidates_stop_without_more_billing_on_resume(self):
        self.router.always_invalid=True
        self.assertRaisesRegex(store.WorkflowError,'Empty text object',wf.run_workflow,self.root,ai=True,scene_id='scene_1')
        before=len(self.router.requests);held=sg.summary(self.root)['budget_used_usd']
        self.assertEqual(before,4)
        self.assertRaisesRegex(store.WorkflowError,'Empty text object',wf.run_workflow,self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(self.router.requests),before)
        self.assertEqual(sg.summary(self.root)['budget_used_usd'],held)
        self.assertEqual(self.flow.render.call_count,0)
        self.assertFalse(wf.get_status(self.root)['pending_reviews'])

    def test_truncated_design_enters_repair_instead_of_rethrowing_text_parser_error(self):
        self.router.truncate_first=True
        state=wf.run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(state['pending_reviews']),1)
        job=sg._jobs(self.root)[0]
        self.assertIn('INVALID_MODEL_OUTPUT',store.read_json(self.root/sg.BASE/job['job_id']/'validation_error_0.json')['error'])

    def test_wrong_model_does_not_trigger_repair_or_render(self):
        self.router.wrong_model=True
        self.assertRaisesRegex(store.WorkflowError,'MODEL_MISMATCH',wf.run_workflow,self.root,ai=True,scene_id='scene_1')
        self.assertEqual(self.router.design_calls,1)
        self.assertEqual(self.flow.render.call_count,0)

    def test_empty_choices_enters_repair_without_crashing_error_handler(self):
        self.router.empty_choices=True
        state=wf.run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertEqual(self.router.design_calls,3)

    def test_missing_image_fallback_note_is_part_of_repairable_validation(self):
        original=self.router.complete
        self.router.invalid_designs=0;self.router.missing_fallback=True
        def complete(payload):
            response=original(payload)
            if 'queries' in payload['response_format']['json_schema']['schema']['properties']:
                response['choices'][0]['message']['content']=json.dumps(dict(queries=['car illustration']))
            return response
        with patch.object(self.router,'complete',side_effect=complete),patch('src.scene_design.scene_assets.collect',return_value=dict(assets=[])):
            state=wf.run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertEqual(self.router.design_calls,2)
        self.assertIn('fallback_note',self.router.requests[-1]['messages'][-1]['content'])
