"""C6 gates, exact-version decisions, shared impact and local HTTP isolation."""
import copy
import json
from pathlib import Path
import threading
import urllib.request
import urllib.error
import unittest
from unittest.mock import patch
from tests import test_scenes as scene_fixtures
SceneRouter=scene_fixtures.SceneRouter
from src.scene_design import scene_revision as rev
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.shared.review import record_review
from src.shared.review_server import ReviewServer
from src.shared.ai_config import MODEL


class RevisionRouter(SceneRouter):
    def __init__(self): super().__init__(); self.categories=['code']; self.affected=['scene_2']; self.resources=[]; self.updates=[]
    def complete(self,payload):
        props=payload['response_format']['json_schema']['schema']['properties']
        if 'summary' not in props: return super().complete(payload)
        self.requests.append(copy.deepcopy(payload))
        plan=dict(summary='Sửa đúng phần được yêu cầu',categories=self.categories,scenes=self.affected,resources=self.resources,resource_updates=self.updates,outside_scope=False)
        return dict(id='analysis',model=MODEL,usage=dict(cost=.0001,prompt_tokens=100,completion_tokens=100),choices=[dict(finish_reason='stop',message=dict(content=json.dumps(plan)))])


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.flow=scene_fixtures.SceneFlowTests('test_two_topics_different_scene_counts_and_all_gates'); self.flow.setUp(); self.addCleanup(self.flow.doCleanups)
        self.root=self.flow.root; self.router=RevisionRouter()
        patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=self.router).start()
        self.flow.approved(3)
        state=wf.run_workflow(self.root,ai=True)
        for item in state['pending_reviews']: record_review(self.root,'scene_design',item['version'],'approve',scene_id=item['scene_id'])
        wf.run_workflow(self.root,ai=True)

    def clip(self,sid='scene_2'):
        return next(a for a in reversed(wf.get_status(self.root)['artifacts']) if a['stage']=='scene_clip' and a['scene_id']==sid)
    def request(self):
        c=self.clip(); return rev.request_revision(self.root,'scene_2',c['version'],c['sha256'],'Sửa cảnh này',request_id='feedback-1')

    def test_local_revision_preserves_other_scenes_and_requires_fresh_approval(self):
        for sid in ('scene_1','scene_3'): record_review(self.root,'scene_clip','v001','approve',scene_id=sid)
        before={sid:self.clip(sid) for sid in ('scene_1','scene_3')}
        r=self.request(); r=rev.resume_revision(self.root,r['id'])
        self.assertEqual(r['status'],'awaiting_clip'); self.assertEqual(self.clip()['version'],'v002')
        self.assertEqual({sid:self.clip(sid) for sid in before},before)
        self.assertRaisesRegex(store.WorkflowError,'STALE_REVIEW',record_review,self.root,'scene_clip','v001','approve',scene_id='scene_2')
        state=record_review(self.root,'scene_clip','v002','approve',scene_id='scene_2')
        self.assertEqual(state['stage'],'final_video'); self.assertIsNotNone(state['assembly_manifest'])

    def test_redesign_waits_then_publishes_approved_design(self):
        self.router.categories=['layout']; before=self.flow.render.call_count
        r=self.request(); r=rev.resume_revision(self.root,r['id'])
        self.assertEqual(r['status'],'awaiting_design'); self.assertEqual(self.flow.render.call_count,before+1)
        self.assertEqual(rev.resume_revision(self.root,r['id'])['status'],'awaiting_design')
        d=r['designs']['scene_2']; rev.approve_design(self.root,r['id'],'scene_2',d['sha256'])
        result=rev.resume_revision(self.root,r['id'])
        self.assertEqual(result['status'],'awaiting_clip')
        state=wf.get_status(self.root)
        self.assertEqual(next(a for a in reversed(state['artifacts']) if a['stage']=='scene_design' and a['scene_id']=='scene_2')['version'],'v002')

    def test_scope_requires_consent_and_cancellation_preserves_history(self):
        self.router.affected=['scene_2','scene_3']
        r=self.request(); before=self.flow.render.call_count
        r=rev.resume_revision(self.root,r['id']); self.assertEqual(r['status'],'awaiting_scope')
        self.assertEqual(self.flow.render.call_count,before)
        rev.cancel_revision(self.root,r['id'])
        self.assertEqual(self.clip()['version'],'v001')

    def test_shared_style_dependency_finds_all_consumers(self):
        self.router.categories=['layout']
        self.router.resources=['scene_config.json']
        r=self.request(); r=rev.resume_revision(self.root,r['id'])
        self.assertEqual(r['affected'],['scene_1','scene_2','scene_3']); self.assertEqual(r['status'],'awaiting_scope')

    def test_reopen_approved_clip_blocks_manifest_and_keeps_decision(self):
        for sid in ('scene_1','scene_2','scene_3'): record_review(self.root,'scene_clip','v001','approve',scene_id=sid)
        r=self.request(); state=wf.get_status(self.root)
        self.assertEqual(state['stage'],'scene_clip'); self.assertIsNone(state['assembly_manifest'])
        self.assertEqual(next(q for q in state['review_history'] if q['stage']=='scene_clip' and q['scene_id']=='scene_2')['status'],'approved')
        self.assertEqual(rev.request_revision(self.root,'scene_2','v001',r['base']['sha256'],'Sửa cảnh này',request_id='feedback-1')['id'],r['id'])

    def test_cancel_reopened_approved_clip_restores_ready_manifest(self):
        for sid in ('scene_1','scene_2','scene_3'): record_review(self.root,'scene_clip','v001','approve',scene_id=sid)
        self.router.affected=['scene_2','scene_3']
        r=self.request(); r=rev.resume_revision(self.root,r['id'])
        rev.cancel_revision(self.root,r['id'])
        state=wf.get_status(self.root)
        self.assertEqual(state['stage'],'final_video'); self.assertIsNotNone(state['assembly_manifest'])

    def test_shared_math_is_versioned_and_recomputed_before_design_review(self):
        state=wf.get_status(self.root)
        design=next(a for a in state['artifacts'] if a['stage']=='scene_design' and a['scene_id']=='scene_1')
        data=next(c for c in design['bundle'].values() if c['path'].endswith('/data.json'))
        rev.declare_shared_resource(self.root,'shared_math',data['path'],data['sha256'],['scene_1','scene_2'])
        self.router.categories=['data']; self.router.resources=['shared_math']
        self.router.updates=[dict(resource_id='shared_math',content_json=json.dumps(dict(datasets=[dict(id='shared',expression='t*2',variable='t',start=0,end=1,count=3,unit='m')])))]
        r=self.request(); r=rev.resume_revision(self.root,r['id'])
        self.assertEqual(r['status'],'awaiting_scope')
        rev.approve_scope(self.root,r['id'],r['plan_sha256'])
        r=rev.resume_revision(self.root,r['id'])
        self.assertEqual(r['status'],'awaiting_design')
        for candidate in r['designs'].values():
            resource=candidate['resource_refs'][0]
            result=store.read_json(self.root/resource['path'])
            self.assertEqual(result['datasets'][0]['points'],[[0,0],[.5,1],[1,2]])
        self.assertEqual(store.digest(self.root/data['path']),data['sha256'])

    def test_amend_keeps_prior_feedback_and_reapproves_candidate(self):
        self.router.categories=['layout']
        r=self.request(); r=rev.resume_revision(self.root,r['id'])
        first=r['designs']['scene_2']['sha256']
        r=rev.amend_revision(self.root,r['id'],'Tăng chữ thêm một chút')
        self.assertEqual(r['round'],1); self.assertEqual(r['round_history'][0]['designs']['scene_2']['sha256'],first)
        r=rev.resume_revision(self.root,r['id'])
        self.assertEqual(r['status'],'awaiting_design'); self.assertFalse(r['designs']['scene_2']['approved'])
        self.assertEqual(r['designs']['scene_2']['version'],'v003')
        self.assertRaisesRegex(store.WorkflowError,'STALE_REVIEW',rev.approve_design,self.root,r['id'],'scene_2',r['designs']['scene_2']['sha256'],expected_round=0)

    def test_failed_render_limit_survives_restart(self):
        from tests.test_scenes import fake_render
        def failed(*args): return dict(fake_render(*args),status='failed',error='RENDER_FAILED: test failure')
        self.flow.render.side_effect=failed
        before=self.flow.render.call_count
        r=self.request()
        self.assertRaisesRegex(store.WorkflowError,'RENDER_FAILED',rev.resume_revision,self.root,r['id'])
        calls=len(self.router.requests)
        self.assertEqual(self.flow.render.call_count-before,3)
        self.assertRaisesRegex(store.WorkflowError,'RENDER_FAILED',rev.resume_revision,self.root,r['id'])
        self.assertEqual(len(self.router.requests),calls); self.assertEqual(self.flow.render.call_count-before,3)

    def test_revision_budget_is_shared_and_restart_does_not_reset_it(self):
        r=self.request(); before=len(self.router.requests)
        spent=wf.get_status(self.root)['scene_generation']['budget_used_usd']
        self.router.reserve=lambda *a:.5
        for _ in range(2):
            self.assertRaisesRegex(store.WorkflowError,'BUDGET_EXCEEDED',rev.resume_revision,self.root,r['id'])
        self.assertEqual(len(self.router.requests),before)
        self.assertEqual(wf.get_status(self.root)['scene_generation']['budget_used_usd'],spent)

    def test_server_token_origin_media_range_and_stale_version(self):
        server=ReviewServer(self.root,0); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        def get(path): return urllib.request.urlopen(server.origin+path)
        with get('/api/state') as response: state=json.load(response)
        self.assertIn('token',state)
        with get('/api/context') as response: context=json.load(response)
        self.assertTrue(context['script']['scenes'])
        self.assertEqual(context['designs']['scene_2:v001']['scene_id'],'scene_2')
        self.assertGreater(context['designs']['scene_2:v001']['duration'],0)
        self.assertEqual(context['render']['resolution'],[640,360])
        with self.assertRaises(urllib.error.HTTPError): get('/media?path=project.json')
        with self.assertRaises(urllib.error.HTTPError): get('/media?path=.env')
        path=self.clip()['path'].removeprefix(str(self.root)+'/')
        req=urllib.request.Request(server.origin+'/media?path='+path,headers={'Range':'bytes=0-3'})
        with urllib.request.urlopen(req) as response:
            self.assertEqual(response.status,206); self.assertEqual(len(response.read()),4)
        payload=json.dumps(dict(request_id='x',stage='scene_clip',scene_id='scene_2',version='v001',sha256='bad')).encode()
        for headers in ({'Content-Type':'application/json'},{'Origin':server.origin,'X-Review-Token':state['token'],'Content-Type':'application/json'}):
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(urllib.request.Request(server.origin+'/api/approve',data=payload,headers=headers))
