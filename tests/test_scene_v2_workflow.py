"""Real workflow registration/review, real media and fake paid providers."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.scene_design import scene_generation as sg
from src.shared.review import record_review
from tests.scene_v2_support import DesignRouter,VisionRouter,story


def approved_project(root,count=1):
    wf.create_project(root,'Cát tuyến và đạo hàm')
    narration=story()['narration'];path=root/'input.md';path.write_text(narration,encoding='utf-8')
    scene_ids=[f'scene_{i+1}' for i in range(count)]
    wf.submit_artifact(root,'script','v001',path,scene_ids=scene_ids)
    payload=dict(script=dict(title='Cát tuyến và đạo hàm',scenes=[dict(scene_id=sid,narration=narration,visual_intent='h giảm, cát tuyến tiến gần tiếp tuyến') for sid in scene_ids]),brief={})
    (root/'input.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    with store.locked_project(root):
        p,r=wf._load(root);p['artifacts'][0]['bundle']={'script.json':dict(path='input.json',sha256=store.digest(root/'input.json'))};wf._save(root,p,r)
    record_review(root,'script','v001','approve')
    return root


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=approved_project(Path(self.tmp.name))
        self.router=DesignRouter();self.vision=VisionRouter()
        for target,value in [('src.scene_design.scene_generation.CLIENT_FACTORY',self.router),('src.scene_design.scene_vision.CLIENT_FACTORY',self.vision)]:
            patcher=patch(target,return_value=value);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch('src.scene_design.scene_generation._context',side_effect=self.context);patcher.start();self.addCleanup(patcher.stop)
    def context(self,root,p,r):
        a=wf._latest(p,'script',None);payload=store.read_json(root/'input.json')
        return a,payload,{'scene_1':3}
    def test_new_design_quality_gate_approved_video_reused_and_media(self):
        result=wf.run_workflow(self.root,ai=True);a=result['pending_reviews'][0]
        self.assertEqual(result['stage'],'scene_design');self.assertEqual(a['design_pipeline'],2)
        self.assertTrue(any('contact_sheet' in key for key in a['bundle']))
        self.assertFalse((self.root/'final.mp4').exists())
        calls=len(self.router.requests)+len(self.vision.requests)
        record_review(self.root,'scene_design',a['version'],'approve',scene_id='scene_1',expected_sha256=a['sha256'])
        result=wf.run_workflow(self.root,ai=True);clip=result['pending_reviews'][0]
        self.assertEqual(result['stage'],'scene_clip');self.assertEqual(clip['sha256'],a['manim_render']['sha256'])
        self.assertEqual(len(self.router.requests)+len(self.vision.requests),calls)
    def test_v2_code_feedback_reenters_design_gate_before_replacing_clip(self):
        from src.scene_design import scene_revision as rev
        result=wf.run_workflow(self.root,ai=True);a=result['pending_reviews'][0]
        record_review(self.root,'scene_design',a['version'],'approve',scene_id='scene_1')
        result=wf.run_workflow(self.root,ai=True);clip=result['pending_reviews'][0]
        cycle=rev.request_revision(self.root,'scene_1',clip['version'],clip['sha256'],'Sửa cách thể hiện cho rõ.')
        cycle=rev.resume_revision(self.root,cycle['id'])
        self.assertEqual(cycle['status'],'awaiting_design');candidate=cycle['designs']['scene_1']
        self.assertEqual(candidate['design_pipeline'],2)
        self.assertGreater(len(self.vision.requests),1)
        rev.approve_design(self.root,cycle['id'],'scene_1',candidate['sha256'])
        cycle=rev.resume_revision(self.root,cycle['id']);self.assertEqual(cycle['status'],'awaiting_clip')
        current=wf.get_status(self.root);latest=next(a for a in reversed(current['artifacts']) if a['stage']=='scene_clip')
        self.assertEqual(latest['sha256'],candidate['manim_render']['sha256'])
    def test_failed_visual_review_does_not_register_design(self):
        self.vision.reviews=[[dict(category='timing',beat_id='approach',object_ids=[],time=1,problem='Nhịp chưa đạt.',fix='Sửa timeline.')]]
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_GENERATION_FAILED'):wf.run_workflow(self.root,ai=True)
        result=wf.get_status(self.root);self.assertEqual(result['stage'],'scene_design');self.assertFalse(result['pending_reviews'])
        self.assertFalse(any(a['stage']=='scene_design' for a in result['artifacts']))


class V2AutoContinueHTTPTests(unittest.TestCase):
    def test_real_media_sequence_continues_after_exhausted_scene_and_keeps_reviews_pending(self):
        import threading,time,urllib.request
        from src.shared.review_server import ReviewServer
        with tempfile.TemporaryDirectory() as folder:
            root=approved_project(Path(folder),count=3)
            router=DesignRouter()
            issue=dict(category='timing',beat_id='approach',object_ids=['B'],time=1,
                       problem='Fixture first-scene failure.',fix='Keep the point timing criterion.')
            vision=VisionRouter([[issue],[issue],[issue],[]])
            def context(root,project,reviews):
                return wf._latest(project,'script',None),store.read_json(root/'input.json'),{f'scene_{i}':3 for i in (1,2,3)}
            with patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router), \
                 patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision), \
                 patch('src.scene_design.scene_generation._context',side_effect=context), \
                 patch('src.shared.ai_config.scene_trial_enabled',return_value=True), \
                 patch.dict(__import__('os').environ,{'SCENE_TRIAL_REPAIRS':'2'}):
                server=ReviewServer(root,0)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    data=json.dumps(dict(request_id='v2-http-auto-sequence',scene_id='scene_1',new_attempt=True)).encode()
                    def post():
                        request=urllib.request.Request(server.origin+'/api/continue',data=data,
                            headers={'Origin':server.origin,'X-Review-Token':server.token,'Content-Type':'application/json'})
                        with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)
                    self.assertTrue(post()['ok'])
                    deadline=time.monotonic()+120
                    while time.monotonic()<deadline:
                        with server.jobs_lock:task=dict(server.jobs.get('workflow',{}))
                        if task.get('state') in ('done','failed'):break
                        time.sleep(.05)
                    self.assertEqual(task.get('state'),'failed',task)
                    self.assertIn('SCENE_GENERATION_FAILED',task['error'])
                    jobs=sg._jobs(root)
                    self.assertEqual([j['input']['scene_id'] for j in jobs],['scene_1','scene_2','scene_3'])
                    self.assertEqual([j['status'] for j in jobs],['failed','awaiting_review','awaiting_review'])
                    self.assertEqual([j['max_repairs'] for j in jobs],[2,2,2])
                    self.assertEqual([j['repair_round'] for j in jobs],[2,0,0])
                    state=wf.get_status(root)
                    self.assertEqual([a['scene_id'] for a in state['pending_reviews']],['scene_2','scene_3'])
                    self.assertEqual(state['stage'],'scene_design')
                    for artifact in state['pending_reviews']:
                        self.assertEqual(artifact['design_pipeline'],2)
                        self.assertTrue((root/artifact['manim_render']['path']).is_file())
                        self.assertTrue(any('contact_sheet' in k for k in artifact['bundle']))
                    self.assertFalse(any(r['stage']=='scene_design' and r['status']=='approved' for r in state['review_history']))
                    calls=len(router.requests)+len(vision.requests)
                    self.assertTrue(post()['replayed'])
                    self.assertEqual(len(router.requests)+len(vision.requests),calls)
                finally:
                    server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
