"""C5 regression: generic scene counts, gates, licensing, budget and recovery."""
import copy
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.shared.ai_config import MODEL
from src.scene_design.design import evaluate, validate_code
from src.scene_design.scene_assets import license_evidence
from src.shared.workflow import create_project, run_workflow, get_status
from src.shared.review import record_review
from tests.ai_support import FakeRouter, fake_fetch, valid_script


def code(seconds):
    return 'from manim import Scene, Dot, RIGHT\nclass GeneratedScene(Scene):\n    def construct(self):\n        dot = Dot()\n        self.add(dot)\n        self.play(dot.animate.shift(RIGHT), run_time='+repr(seconds)+')\n'


class SceneRouter(FakeRouter):
    def complete(self,payload):
        self.requests.append(copy.deepcopy(payload))
        properties=payload['response_format']['json_schema']['schema']['properties']
        context=json.loads(payload['messages'][1]['content'])
        if 'queries' in properties: result=dict(queries=[])
        elif 'objects' in properties:
            result=dict(scene_id=context['scene']['scene_id'],narration=context['scene']['narration'],purpose=context['script']['title'],
                duration=context['target_seconds'],objects=[dict(id='dot',kind='circle',label='Minh họa',x=.5,y=.5,width=.2,height=.2,color='#56DFC5',asset_id='',points=[])],
                timeline=[dict(object_id='dot',action='move',start=0,duration=context['target_seconds'],dx=.1,dy=0)],math=[],fallback_note='Dùng đồ họa Manim')
        else: result=dict(source=code(context['approved_design']['duration']),requires_design_change=False,reason='')
        return dict(id='scene-'+str(len(self.requests)),model=MODEL,usage=dict(prompt_tokens=100,completion_tokens=200,cost=.0001),
                    choices=[dict(finish_reason='stop',message=dict(content=json.dumps(result,ensure_ascii=False)))])


def fake_render(root,scene,version,source,name,config):
    root=Path(root); (root/'logs').mkdir(exist_ok=True); (root/'previews').mkdir(exist_ok=True)
    video=root/f'previews/{scene}_{version}.mp4'; video.write_bytes(b'fake video for orchestration test')
    log=root/f'logs/{scene}_{version}.log'; log.write_text('fake renderer')
    # Read the duration from the generated code, not from a global scene fixture.
    import ast
    tree=ast.parse((root/source).read_text())
    duration=next(k.value.value for n in ast.walk(tree) if isinstance(n,ast.Call) for k in n.keywords if k.arg=='run_time')
    result=dict(status='success',scene_id=scene,version=version,video_path=str(video),log_path=str(log),error=None)
    store.atomic_write(log.with_suffix('.json'),dict(result,video=dict(duration_seconds=duration)))
    return result


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'Layout previews require Pillow (video environment)')
class SceneFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project'; self.router=SceneRouter()
        self.addCleanup(patch.stopall)
        # This suite preserves the v1 compatibility path. The v2 primary path is
        # covered with real media in test_scene_v2 and test_scene_v2_workflow.
        from src.scene_design import scene_generation as sg
        patch('src.scene_design.scene_generation.DESIGN_PIPELINE_VERSION',1).start()
        patch('src.scene_design.scene_generation._design',side_effect=sg._legacy_design).start()
        patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=self.router).start()
        self.render=patch('src.scene_design.scene_generation.RENDERER',side_effect=fake_render).start()
        patch.dict(os.environ,{'OPENROUTER_MODEL':MODEL}).start()

    def approved(self,count=2,topic='Đạo hàm'):
        create_project(self.root,topic)
        router=FakeRouter()
        original=router.complete
        def content(payload):
            r=original(payload)
            if 'tools' not in payload:
                s=valid_script(); narration=s['scenes'][0]['narration']+' '+s['scenes'][1]['narration']
                words=narration.split(); s['title']=topic
                s['scenes']=[dict(scene_id=f'scene_{i+1}',narration=' '.join(words[i*len(words)//count:(i+1)*len(words)//count]),visual_intent='Minh họa',claim_refs=['claim_01']) for i in range(count)]
                r['choices'][0]['message']['content']=json.dumps(s,ensure_ascii=False)
            return r
        with patch('src.idea.generation.CLIENT_FACTORY',return_value=router),patch('src.idea.research.fetch_html',side_effect=fake_fetch):
            router.complete=content; run_workflow(self.root,ai=True)
        return record_review(self.root,'script','v001','approve',read_seconds=30)

    def test_two_topics_different_scene_counts_and_all_gates(self):
        for count,topic in ((2,'Đạo hàm'),(3,'Ngày và đêm')):
            with self.subTest(topic=topic):
                self.root=Path(self.temp.name)/str(count)
                self.render.reset_mock()
                self.approved(count,topic)
                state=run_workflow(self.root,ai=True)
                self.assertEqual(state['status'],'awaiting_review'); self.assertEqual(len(state['pending_reviews']),count)
                self.assertEqual(self.render.call_count,count)
                for item in state['pending_reviews']:
                    self.assertEqual(item['render_engine'],'manim')
                    self.assertTrue((self.root/item['manim_render']['path']).is_file())
                    self.assertEqual(state['design_previews'][item['scene_id']+':'+item['version']]['renderer'],'manim')
                    self.assertIn(topic,Path(item['review_path']).read_text())
                before=len(self.router.requests)
                self.assertEqual(run_workflow(self.root,ai=True),state)
                self.assertEqual(len(self.router.requests),before)
                for item in state['pending_reviews']: record_review(self.root,'scene_design',item['version'],'approve',scene_id=item['scene_id'])
                state=run_workflow(self.root,ai=True)
                self.assertEqual(state['stage'],'scene_clip'); self.assertEqual(len(state['pending_reviews']),count)
                for item in state['pending_reviews']: record_review(self.root,'scene_clip',item['version'],'approve',scene_id=item['scene_id'])
                state=run_workflow(self.root,ai=True)
                self.assertEqual(state['stage'],'final_video'); self.assertFalse((self.root/'final.mp4').exists())

    def test_design_and_clip_revision_preserve_old_bundle(self):
        self.approved(); state=run_workflow(self.root,ai=True,scene_id='scene_1'); first=state['pending_reviews'][0]
        checksum=store.digest(Path(first['path']))
        record_review(self.root,'scene_design','v001','request_changes',scene_id='scene_1',feedback='Chữ rõ hơn')
        state=run_workflow(self.root,ai=True)
        self.assertEqual(store.digest(Path(first['path'])),checksum)
        self.assertEqual(state['pending_reviews'][0]['version'],'v002')
        for item in state['pending_reviews']: record_review(self.root,'scene_design',item['version'],'approve',scene_id=item['scene_id'])
        state=run_workflow(self.root,ai=True); clip=state['pending_reviews'][0]
        record_review(self.root,'scene_clip',clip['version'],'request_changes',scene_id=clip['scene_id'],feedback='Sửa lỗi hiển thị theo thiết kế')
        state=run_workflow(self.root,ai=True,scene_id=clip['scene_id'])
        self.assertGreater(int(next(i for i in state['pending_reviews'] if i['scene_id']==clip['scene_id'])['version'][1:]),int(clip['version'][1:]))
        self.assertTrue(Path(clip['path']).exists())

    def test_bundle_tampering_blocks_review_and_unknown_scene(self):
        self.approved(); self.assertRaisesRegex(store.WorkflowError,'SCENE_NOT_FOUND',run_workflow,self.root,ai=True,scene_id='bad')
        state=run_workflow(self.root,ai=True); item=state['pending_reviews'][0]
        (self.root/item['manim_render']['code_path']).write_bytes(b'changed')
        self.assertRaisesRegex(store.WorkflowError,'ARTIFACT_CHANGED',record_review,self.root,'scene_design','v001','approve',scene_id=item['scene_id'])

    def test_shared_budget_and_missing_ledger_never_reset(self):
        self.approved(); run_workflow(self.root,ai=True,scene_id='scene_1')
        from src.scene_design import scene_generation as sg
        used=sg.summary(self.root)['budget_used_usd']; self.assertGreater(used,0)
        self.router.reserve=lambda *a: .5
        self.assertRaisesRegex(store.WorkflowError,'BUDGET_EXCEEDED',run_workflow,self.root,ai=True,scene_id='scene_2')
        self.assertEqual(sg.summary(self.root)['budget_used_usd'],used)
        (self.root/sg.BASE/'ledger.json').unlink()
        self.assertRaisesRegex(store.WorkflowError,'STATE_MISSING',run_workflow,self.root,ai=True,scene_id='scene_2')

    def test_interruption_before_registration_reuses_paid_calls(self):
        self.approved()
        with patch('src.shared.workflow._submit',side_effect=store.WorkflowError('STOP','simulated crash')):
            self.assertRaisesRegex(store.WorkflowError,'STOP',run_workflow,self.root,ai=True,scene_id='scene_1')
        calls=len(self.router.requests)
        state=run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(len(self.router.requests),calls); self.assertEqual(len(state['pending_reviews']),1)

    def test_unknown_api_result_requires_explicit_retry_and_preserves_budget(self):
        self.approved()
        with patch.object(self.router,'complete',side_effect=TimeoutError('lost connection')):
            self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN',run_workflow,self.root,ai=True,scene_id='scene_1')
        state=get_status(self.root)
        call_id=state['scene_generation']['jobs'][0]['uncertain_requests'][0]
        held=state['scene_generation']['budget_used_usd']
        with patch.object(self.router,'complete',side_effect=AssertionError('must not resend')):
            self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN',run_workflow,self.root,ai=True,scene_id='scene_1')
        state=run_workflow(self.root,ai=True,scene_id='scene_1',retry_uncertain=call_id)
        self.assertEqual(len(state['pending_reviews']),1)
        self.assertGreater(state['scene_generation']['budget_used_usd'],held)

    def test_successful_render_saved_before_job_update_is_reused(self):
        self.approved(); state=run_workflow(self.root,ai=True)
        for item in state['pending_reviews']:
            record_review(self.root,'scene_design',item['version'],'approve',scene_id=item['scene_id'])
        with patch('src.shared.workflow._submit',side_effect=store.WorkflowError('STOP','after render')):
            self.assertRaisesRegex(store.WorkflowError,'STOP',run_workflow,self.root,ai=True,scene_id='scene_1')
        count=self.render.call_count
        state=run_workflow(self.root,ai=True,scene_id='scene_1')
        self.assertEqual(self.render.call_count,count)
        self.assertEqual(len(state['pending_reviews']),1)

    def test_failed_preview_has_no_agent_repair_and_explicit_retry_reuses_design(self):
        self.approved()
        def failed(*args):
            result=dict(fake_render(*args),status='failed',error='RENDER_FAILED: always fails')
            store.atomic_write(Path(result['log_path']).with_suffix('.json'),result)
            return result
        self.render.side_effect=failed
        self.assertRaisesRegex(store.WorkflowError,'RENDER_FAILED',run_workflow,self.root,ai=True,scene_id='scene_1')
        self.assertEqual(self.render.call_count,1)
        self.assertFalse(get_status(self.root)['pending_reviews'])
        calls=len(self.router.requests)
        with patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('Saved JSON needs no AI')):
            self.assertRaisesRegex(store.WorkflowError,'RENDER_FAILED',run_workflow,self.root,ai=True,scene_id='scene_1')
        self.assertEqual(self.render.call_count,2)
        self.assertEqual(len(self.router.requests),calls)

    def test_failed_render_uses_new_version_and_keeps_log(self):
        self.approved()
        def fail_once(*args):
            result=fake_render(*args)
            if args[2]=='v001':
                result.update(status='failed',error='RENDER_FAILED: simulation')
                store.atomic_write(Path(result['log_path']).with_suffix('.json'),result)
            return result
        self.render.side_effect=fail_once
        self.assertRaisesRegex(store.WorkflowError,'RENDER_FAILED',run_workflow,self.root,ai=True,scene_id='scene_1')
        calls=len(self.router.requests)
        with patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('No AI on preview retry')):
            state=run_workflow(self.root,ai=True,scene_id='scene_1')
        design=state['pending_reviews'][0]
        self.assertEqual(design['version'],'v001')
        self.assertEqual(design['manim_render']['version'],'v002')
        self.assertTrue((self.root/'logs/scene_1_v001.log').exists())
        self.assertTrue((self.root/'logs/scene_1_v002.log').exists())
        self.assertEqual(len(self.router.requests),calls)

    def test_design_movie_is_reused_after_approval_without_network_or_rerender(self):
        self.approved()
        state=run_workflow(self.root,ai=True)
        expected={a['scene_id']:a['manim_render']['sha256'] for a in state['pending_reviews']}
        for item in state['pending_reviews']:
            record_review(self.root,'scene_design',item['version'],'approve',scene_id=item['scene_id'])
        calls=len(self.router.requests);renders=self.render.call_count
        with patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('No AI for promotion')):
            state=run_workflow(self.root,ai=True)
        self.assertEqual(len(self.router.requests),calls)
        self.assertEqual(self.render.call_count,renders)
        for item in state['pending_reviews']:
            self.assertTrue(item['reused_design_render'])
            self.assertEqual(item['sha256'],expected[item['scene_id']])


    def test_one_failed_scene_does_not_prevent_other_scenes_from_rendering(self):
        self.approved(2)
        def fail_first(*args):
            result=fake_render(*args)
            return dict(result,status='failed',error='RENDER_FAILED: scene one') if args[1]=='scene_1' else result
        self.render.side_effect=fail_first
        with self.assertRaisesRegex(store.WorkflowError,'SCENE_GENERATION_FAILED'):
            run_workflow(self.root,ai=True)
        state=get_status(self.root)
        self.assertEqual([a['scene_id'] for a in state['pending_reviews']],['scene_2'])
        self.assertEqual(self.render.call_count,2)
        self.assertEqual([j['status'] for j in state['scene_generation']['jobs']],['failed','awaiting_review'])

    def test_legacy_preview_runs_saved_code_without_ai_and_tampering_blocks_review(self):
        from src.scene_design.design_preview import generate
        self.approved()
        state=run_workflow(self.root,ai=True,scene_id='scene_1')
        design=state['pending_reviews'][0]
        with store.locked_project(self.root):
            project,reviews=wf._load(self.root)
            artifact=next(a for a in project['artifacts'] if a['stage']=='scene_design')
            artifact.pop('manim_render');artifact.pop('render_engine')
            project['design_previews']={'scene_1:v001':dict(path='legacy.mp4',renderer='pillow')}
            from src.shared.workflow import _save
            _save(self.root,project,reviews)
        calls=len(self.router.requests)
        with patch('src.scene_design.scene_generation.CLIENT_FACTORY',side_effect=AssertionError('Preview must not create an AI client')):
            result=generate(self.root,'scene_1','v001',design['sha256'])
        self.assertEqual(len(self.router.requests),calls)
        self.assertEqual(result['renderer'],'manim')
        self.assertEqual(result['version'],'v001')
        self.assertEqual(result['render_version'],'v002')
        calls=len(self.router.requests)
        self.assertEqual(generate(self.root,'scene_1','v001',design['sha256']),result)
        self.assertEqual(len(self.router.requests),calls)
        (self.root/result['code_path']).write_text('tampered')
        self.assertRaisesRegex(store.WorkflowError,'ARTIFACT_CHANGED',record_review,self.root,'scene_design','v001','approve',scene_id='scene_1')


    def test_server_recovers_interrupted_legacy_manim_job_without_rebilling(self):
        from src.scene_design import scene_generation as sg
        from src.scene_design.design_preview import generate
        from src.shared.review_server import ReviewServer
        import time
        self.approved()
        state=run_workflow(self.root,ai=True,scene_id='scene_1')
        design=state['pending_reviews'][0]
        with store.locked_project(self.root):
            project,reviews=wf._load(self.root)
            artifact=next(a for a in project['artifacts'] if a['stage']=='scene_design')
            artifact.pop('manim_render');artifact.pop('render_engine')
            project['design_previews']={}
            wf._save(self.root,project,reviews)
        expected=generate(self.root,'scene_1','v001',design['sha256'])
        with store.locked_project(self.root):
            project,reviews=wf._load(self.root);project['design_previews']={};wf._save(self.root,project,reviews)
        path=next(path for path in (self.root/sg.BASE).glob('manim_*/job.json'))
        job=store.read_json(path);job['status']='working';store.atomic_write(path,job)
        calls=len(self.router.requests);renders=self.render.call_count
        server=ReviewServer(self.root,0)
        try:
            server.recover()
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                with server.jobs_lock:
                    busy=any(t['state'] in ('working','queued') for t in server.jobs.values())
                if not busy:break
                time.sleep(.02)
            self.assertFalse(busy)
            self.assertFalse([t['error'] for t in server.jobs.values() if t.get('error')])
            self.assertEqual(get_status(self.root)['design_previews']['scene_1:v001'],expected)
            self.assertEqual(len(self.router.requests),calls)
            self.assertEqual(self.render.call_count,renders)
        finally:
            server.server_close()


class ScenePolicyTests(unittest.TestCase):
    def test_math_rejects_execution_and_computes_samples(self):
        self.assertEqual(evaluate('t**2+sin(pi/2)','t',3),10)
        for expr in ('__import__("os").system("true")','t.__class__','2**10000000'):
            with self.assertRaises(store.WorkflowError): evaluate(expr,'t',1)

    def test_image_requires_content_license_not_metadata_license(self):
        def row(license,url):
            return dict(imageinfo=[dict(mime='image/png',extmetadata={k:dict(value=v) for k,v in dict(LicenseShortName=license,LicenseUrl=url,Artist='Author',UsageTerms='CC0').items()})])
        self.assertEqual(license_evidence(row('CC0','https://creativecommons.org/publicdomain/zero/1.0/'))['license'],'CC0-1.0')
        for license in ('CC BY-SA 4.0','CC BY 4.0','Public domain',''):
            with self.assertRaises(store.WorkflowError): license_evidence(row(license,'https://creativecommons.org/publicdomain/zero/1.0/'))

    def test_code_policy(self):
        validate_code(code(1),[])
        for source in ('import os\n'+code(1),code(1)+'\nopen(".env")',code(1)+'\nImageMobject("other.png")'):
            with self.assertRaises(store.WorkflowError): validate_code(source,[])

    @unittest.skipUnless(importlib.util.find_spec('manim'),'Real renderer needs video environment')
    def test_generated_code_renders_real_animation(self):
        from src.scene_design.render import render_scene
        with TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'scene.py').write_text(code(1))
            r=render_scene(root,'generic','v001','scene.py','GeneratedScene',dict(quality='l',resolution=[320,180],fps=6,timeout_seconds=60))
            self.assertEqual(r['status'],'success',r)
            self.assertAlmostEqual(store.read_json(Path(r['log_path']).with_suffix('.json'))['video']['duration_seconds'],1,places=2)

    @unittest.skipUnless(importlib.util.find_spec('PIL'),'Requires Pillow')
    def test_asset_download_cache_and_evidence_are_bound_to_file(self):
        import io
        from PIL import Image
        from src.scene_design import scene_assets
        output=io.BytesIO(); Image.new('RGB',(10,10),'red').save(output,format='PNG')
        page=dict(pageid=1,imageinfo=[dict(mime='image/png',url='https://upload.wikimedia.org/test.png',descriptionurl='https://commons.wikimedia.org/wiki/File:Test.png',extmetadata={k:dict(value=v) for k,v in dict(LicenseShortName='CC0',LicenseUrl='https://creativecommons.org/publicdomain/zero/1.0/',Artist='Test author',UsageTerms='CC0').items()})])
        with TemporaryDirectory() as tmp:
            root=Path(tmp); job=root/'job'; job.mkdir()
            with patch('src.scene_design.scene_assets.search',return_value=[page]),patch('src.scene_design.scene_assets._get',return_value=output.getvalue()) as get:
                first=scene_assets.collect(root,job,['sample'])
                self.assertEqual(len(first['assets']),1); self.assertEqual(get.call_count,1)
            with patch('src.scene_design.scene_assets.search',side_effect=AssertionError('cached search')),patch('src.scene_design.scene_assets._get',side_effect=AssertionError('cached image')):
                self.assertEqual(scene_assets.collect(root,job,['sample']),first)
            asset=first['assets'][0]
            (root/asset['path']).write_bytes(b'changed')
            self.assertRaisesRegex(store.WorkflowError,'ASSET_CHANGED',scene_assets.collect,root,job,['sample'])
