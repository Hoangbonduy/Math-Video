"""Opening form -> script -> reviewed scene inputs, with offline providers."""
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.idea.service import apply_idea, save_draft, final_resolution, edit_script
from src.shared.ai_config import MODEL
from src.shared.review import record_review
from src.shared.review_server import ReviewServer
from tests.ai_support import FakeRouter, fake_fetch
from tests.workflow_support import WorkflowCase


IDEA = dict(topic='Vì sao có ngày và đêm?', goal='Hiểu sự tự quay của Trái Đất',
            audience='Học sinh lớp 6', duration='30–45 giây',
            expression='Hoạt họa + lời dẫn', style='Nền sáng, giải thích bằng quả địa cầu',
            format='portrait', reference='Chưa chọn tệp')


class IdeaCase(WorkflowCase):
    def setUp(self):
        super().setUp()
        patch.dict(os.environ, AI_BACKEND='openrouter', OPENROUTER_MODEL=MODEL).start()
        wf.create_project(self.project, 'Chủ đề cũ')
        self.router = FakeRouter()
        self.addCleanup(patch.stopall)
        patch('src.idea.generation.CLIENT_FACTORY', return_value=self.router).start()
        patch('src.idea.research.fetch_html', side_effect=fake_fetch).start()
        patch('src.shared.ai_config.scene_trial_enabled', return_value=False).start()
        # These fixtures use the legacy SceneRouter; v2 has its own real-render suite.
        from src.scene_design import scene_generation as sg
        patch('src.scene_design.scene_generation.DESIGN_PIPELINE_VERSION',1).start()
        patch('src.scene_design.scene_generation._design',side_effect=sg._legacy_design).start()


class IdeaTests(IdeaCase):
    def test_draft_is_persistent_and_does_not_start_generation(self):
        save_draft(self.project, IDEA)
        state = wf.get_status(self.project)
        self.assertEqual(state['idea_draft'], IDEA)
        self.assertEqual(state['topic'], 'Chủ đề cũ')
        self.assertFalse(state['artifacts'])
        self.assertFalse(self.router.requests)

    def test_all_opening_inputs_reach_writer_and_scene_designer(self):
        from tests.test_scenes import SceneRouter, fake_render
        apply_idea(self.project, IDEA)
        state = wf.run_workflow(self.project, ai=True)
        self.assertEqual((state['stage'],state['status']), ('script','awaiting_review'))
        prompt = json.loads(self.router.requests[-1]['messages'][1]['content'])
        self.assertEqual(prompt['topic'], IDEA['topic'])
        self.assertEqual(prompt['brief']['audience'], IDEA['audience'])
        self.assertEqual(prompt['brief']['max_seconds'], 45)
        for key in ('goal', 'expression', 'style'):
            self.assertIn(IDEA[key], prompt['brief']['requirements'])
        self.error('READ_DURATION_OUT_OF_RANGE', record_review, self.project, 'script', 'v001', 'approve', read_seconds=50)
        record_review(self.project, 'script', 'v001', 'approve', read_seconds=40)
        router = SceneRouter()
        with patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router), patch('src.scene_design.scene_generation.RENDERER',side_effect=fake_render) as render:
            state = wf.run_workflow(self.project, ai=True)
        self.assertEqual(state['stage'], 'scene_design')
        self.assertEqual(render.call_count,2)
        design_prompt = json.loads(router.requests[-1]['messages'][1]['content'])
        self.assertEqual(design_prompt['brief'], prompt['brief'])
        self.assertEqual(design_prompt['config']['render']['resolution'], [360,640])
        self.assertEqual(final_resolution(state), [1080,1920])

    def test_new_idea_discards_active_outputs_but_keeps_history_and_versions(self):
        apply_idea(self.project, IDEA)
        state = wf.run_workflow(self.project, ai=True)
        script = state['pending_reviews'][0]
        record_review(self.project, 'script', 'v001', 'approve', read_seconds=40)
        # Register an old scene with an ID the next script will not contain.
        path = self.project/'old-design.json'; path.write_text('{}')
        wf.submit_artifact(self.project,'scene_design','v001',path,scene_id='scene_01')
        before = Path(script['path']).read_bytes()
        changed = dict(IDEA, topic='Vì sao có mùa?', format='landscape')
        apply_idea(self.project, changed)
        state = wf.get_status(self.project)
        self.assertEqual(state['stage'], 'script')
        self.assertEqual(state['scene_ids'], [])
        self.assertFalse(state['pending_reviews'])
        self.assertTrue(all(a['invalidated'] for a in state['artifacts']))
        self.assertEqual(Path(script['path']).read_bytes(), before)
        state = wf.run_workflow(self.project, ai=True)
        self.assertEqual(state['pending_reviews'][0]['version'], 'v002')
        calls = len(self.router.requests)
        apply_idea(self.project, changed)
        wf.run_workflow(self.project, ai=True)
        self.assertEqual(len(self.router.requests), calls)
        self.assertEqual(len(wf.get_status(self.project)['artifacts']), 3)

    def test_invalid_input_preserves_existing_workflow(self):
        before = self.snapshots()
        for changed in (dict(IDEA, topic=''),dict(IDEA,duration='loạn'),dict(IDEA,duration='20–80 giây'),dict(IDEA,format='bad')):
            with self.assertRaises(store.WorkflowError): apply_idea(self.project, changed)
            self.assertEqual(self.snapshots(),before)

    def test_completed_video_is_archived_when_starting_a_new_idea(self):
        state=wf.run_workflow(self.project,mock=True)
        while state['stage']!='completed':
            for artifact in state['pending_reviews']:
                record_review(self.project,artifact['stage'],artifact['version'],'approve',scene_id=artifact['scene_id'])
            state=wf.run_workflow(self.project,mock=True)
        published=(self.project/'final.mp4').read_bytes()
        apply_idea(self.project,IDEA)
        self.assertFalse((self.project/'final.mp4').exists())
        archived=list((self.project/'finals').glob('published_before_idea_*.mp4'))
        self.assertEqual(len(archived),1)
        self.assertEqual(archived[0].read_bytes(),published)
        self.assertEqual(wf.run_workflow(self.project,ai=True)['pending_reviews'][0]['version'],'v002')

    def test_script_edit_updates_narration_and_visual_intent(self):
        apply_idea(self.project, IDEA)
        wf.run_workflow(self.project, ai=True)
        status = wf.get_status(self.project)
        artifact = next(a for a in status['artifacts'] if a['stage'] == 'script')
        script_before = store.read_json(self.project / artifact['bundle']['script.json']['path'])['script']
        sc1 = script_before['scenes'][0]

        new_narr = "Lời đọc mới cho cảnh một đã được người dùng chỉnh sửa trực tiếp."
        new_vis = "Hình ảnh minh họa mới hiển thị quả địa cầu xoay tròn rõ ràng."

        result = edit_script(self.project, artifact['version'], scene_id=sc1['scene_id'],
                             narration=new_narr, visual_intent=new_vis, expected_sha256=artifact['sha256'])
        self.assertTrue(result['ok'])
        self.assertNotEqual(result['sha256'], artifact['sha256'])

        project, _ = wf._load(self.project)
        updated_artifact = wf._latest(project, 'script', None)
        self.assertEqual(updated_artifact['sha256'], result['sha256'])

        payload = store.read_json(self.project / updated_artifact['bundle']['script.json']['path'])
        self.assertEqual(payload['script']['scenes'][0]['narration'], new_narr)
        self.assertEqual(payload['script']['scenes'][0]['visual_intent'], new_vis)

        md = (self.project / updated_artifact['path']).read_text(encoding='utf-8')
        self.assertIn(new_narr, md)
        self.assertIn(new_vis, md)

        wf._verify(self.project, updated_artifact)

        record_review(self.project, 'script', updated_artifact['version'], 'approve', read_seconds=35, expected_sha256=updated_artifact['sha256'])
        self.assertEqual(wf.get_status(self.project)['stage'], 'scene_design')
        self.error('WRONG_STAGE', edit_script, self.project, updated_artifact['version'], scene_id=sc1['scene_id'], narration="Lỗi")


class IdeaHttpTests(IdeaCase):
    def test_http_starts_from_form_and_script_approval_queues_designs(self):
        from tests.test_scenes import SceneRouter
        patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=SceneRouter()).start()
        server = ReviewServer(self.project,0)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        def get(route):
            with urllib.request.urlopen(server.origin+'/api/'+route,timeout=10) as response: return json.load(response)
        token = get('state')['token']
        def post(route, **data):
            payload=json.dumps(dict(request_id=str(time.time_ns()),**data)).encode()
            request=urllib.request.Request(server.origin+'/api/'+route,data=payload,
                headers={'Origin':server.origin,'X-Review-Token':token,'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=10) as response: return json.load(response)
        def idle():
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                value=get('state')
                if not any(t['state'] in ('queued','working') for t in value['tasks'].values()):
                    self.assertFalse([t['error'] for t in value['tasks'].values() if t.get('error')])
                    return value['project']
                time.sleep(.05)
            self.fail('Local generation did not complete')
        post('idea-draft',idea=IDEA)
        self.assertEqual(get('state')['project']['idea_draft'],IDEA)
        post('start',idea=IDEA)
        state=idle(); artifact=state['pending_reviews'][0]
        self.assertEqual(get('context')['brief']['audience'],IDEA['audience'])
        self.assertTrue(get('context')['sources'])
        # Edit script narration & visual_intent via API
        sc_id = get('context')['script']['scenes'][0]['scene_id']
        edit_res = post('script-edit', version=artifact['version'], sha256=artifact['sha256'],
                        scene_id=sc_id, narration="Lời đọc mới qua API", visual_intent="Hình ảnh mới qua API")
        self.assertTrue(edit_res.get('ok'))
        ctx = get('context')
        self.assertEqual(ctx['script']['scenes'][0]['narration'], "Lời đọc mới qua API")
        self.assertEqual(ctx['script']['scenes'][0]['visual_intent'], "Hình ảnh mới qua API")
        artifact = get('state')['project']['pending_reviews'][0]
        post('approve',stage='script',version=artifact['version'],sha256=artifact['sha256'],read_seconds=40)
        state=idle()
        self.assertEqual(state['stage'],'scene_design')
        self.assertEqual(len(state['pending_reviews']),2)
        self.assertTrue(all(a['stage']=='scene_design' for a in state['pending_reviews']))


class PortraitExportTests(WorkflowCase):
    def test_portrait_encoding_and_captions_follow_selected_format(self):
        from src.preview import export
        from src.shared.media_tools import run, binary, validate
        from tests.test_av import wave_bytes
        video=Path(self.temp.name)/'visual.mp4'
        run([binary('ffmpeg'),'-y','-v','error','-f','lavfi','-i','color=c=blue:s=360x640:r=30','-t','0.4','-c:v','libx264','-preset','ultrafast',str(video)])
        audio=video.with_suffix('.wav'); audio.write_bytes(wave_bytes(.4))
        captions=video.with_suffix('.srt')
        export.srt([dict(text='Xin chào',start=0,end=.4)],captions,resolution=(1080,1920))
        self.assertIn('PlayResX: 1080',captions.with_suffix('.ass').read_text())
        result=Path(self.temp.name)/'portrait.mp4'
        export.scene(video,audio,captions,result,.4)
        validate(result,audio=True,resolution=[1080,1920],fps=30)
        final=Path(self.temp.name)/'final.mp4'; export.assemble([result],final)
        validate(final,audio=True,resolution=[1080,1920],fps=30)
