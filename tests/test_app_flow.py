import copy,json,tempfile,threading,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch
from src.shared import workflow as wf
from src.shared import _workflow_store as store
from src.scene_design import scene_generation as sg
from src.scene_design import scene_contract as c
from src.scene_design import scene_state as state
from src.scene_design import scene_drafts
from src.preview import continuous_preview
from src.shared.review_server import ReviewServer
from tests.test_scene_v2_workflow import approved_project
from tests.scene_v2_support import design,story,binding,DesignRouter,VisionRouter

class AppFlowTests(unittest.TestCase):
    def test_highlight_and_reference_alias_preserve_graph_geometry(self):
        d=design();d['objects'].append(dict(d['objects'][4],id='tangent'))
        d['bindings'].append(binding('derivative','tangent_line','tangent',source_a='pointA',graph='curve',extent='full'))
        d['bindings'][0]['graph']='curve'
        d['timeline']=[dict(object_id='A',action='highlight',start=0,duration=3,dx=0,dy=0,value=0,easing='smooth')]
        plan=c.compile_design(d);normal=copy.deepcopy(d);normal['timeline']=[];baseline=c.compile_design(normal)
        for frame in range(37):
            actual=state.state_at(plan,frame/12);expected=state.state_at(baseline,frame/12)
            self.assertEqual(actual['objects']['A']['center'],expected['objects']['A']['center'])
            self.assertAlmostEqual(actual['values']['derivative'],2,places=6)
        self.assertTrue(c.motion_report(plan)['passed'])
        d['timeline'][0]['action']='move';d['timeline'][0]['dx']=.1
        with self.assertRaisesRegex(store.WorkflowError,'Bound geometry'):c.compile_design(d)

    def test_static_visual_groups_allow_world_space_relations_but_moving_groups_must_match(self):
        d=design();d['groups']=[dict(id='first',members=['A']),dict(id='second',members=['B','secant','nameB'])]
        self.assertTrue(c.motion_report(c.compile_design(d))['passed'])
        d['timeline']=[dict(object_id='first',action='move',start=0,duration=3,dx=.1,dy=0,value=0,easing='linear')]
        with self.assertRaisesRegex(store.WorkflowError,'moving group hierarchy'):c.compile_design(d)

    def test_saved_design_render_never_calls_codex_or_fpt_and_preserves_reviews(self):
        from src.scene_design import scene_replay
        with tempfile.TemporaryDirectory() as folder:
            root=approved_project(Path(folder));router=DesignRouter();vision=VisionRouter([[dict(category='layout',beat_id='approach',object_ids=['B'],time=1,problem='Nhãn chưa rõ.',fix='Sửa nhãn.')]])
            with patch('src.scene_design.scene_generation._context',side_effect=self.context),patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router),patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision):
                with self.assertRaises(store.WorkflowError):wf.run_workflow(root,ai=True,scene_trial=True)
            before=[(root/n).read_bytes() for n in ['project.json','reviews.json']]
            with patch('src.scene_design.scene_generation._context',side_effect=self.context),patch('src.shared.codex_client.CodexClient.complete',side_effect=AssertionError('No Codex')),patch('src.preview.audio.synthesize',side_effect=AssertionError('No FPT')):
                scene_replay.generate(root,'scene_1','saved-replay')
                # A cached replay must return after releasing the project file
                # lock; get_status acquires that lock itself.
                from contextlib import contextmanager
                original_lock=store.locked_project;original_status=wf.get_status;held=[0]
                @contextmanager
                def tracked_lock(*args,**kwargs):
                    with original_lock(*args,**kwargs) as value:
                        held[0]+=1
                        try:yield value
                        finally:held[0]-=1
                def read_status(*args,**kwargs):
                    self.assertEqual(held[0],0,'Cached replay re-entered the project lock')
                    return original_status(*args,**kwargs)
                with patch.object(store,'locked_project',tracked_lock),patch.object(wf,'get_status',side_effect=read_status),patch.object(scene_replay.scene_render_v2,'render',side_effect=AssertionError('Cached replay must not render again')):
                    scene_replay.generate(root,'scene_1','saved-replay')
            jobs=sg._jobs(root);self.assertEqual(jobs[-1]['calls'],[]);self.assertEqual(jobs[-1]['status'],'draft_ready')
            draft=scene_drafts.current(root)['scene_1'];self.assertEqual(draft['review']['status'],'not_run');scene_drafts.verify(root,draft)
            self.assertEqual(before,[(root/n).read_bytes() for n in ['project.json','reviews.json']])
            self.assertFalse(wf.get_status(root)['pending_reviews'])

    def test_dense_milestones_on_same_video_frames_do_not_block_render(self):
        from src.scene_design.scene_render_v2 import frame_times
        from tests.scene_v2_support import vehicle
        d=vehicle();ids=[o['id'] for o in d['objects']]
        d['timeline'] += [dict(object_id=ids[i%4],action='highlight',start=.03*i,duration=.045,dx=0,dy=0,value=0,easing='linear') for i in range(25)]
        c.compile_design(d)
        self.assertGreater(len(c.important_times(d,story())),64)
        frames=frame_times(d,story(),12)
        self.assertLessEqual(len(frames),64);self.assertEqual(frames[-1],35)

    def context(self,root,p,r):
        return wf._latest(p,'script',None),store.read_json(root/'input.json'),{'scene_1':3}

    def test_failed_quality_is_playable_in_app_and_continuous_preview_without_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            root=approved_project(Path(folder));router=DesignRouter();vision=VisionRouter([[dict(category='layout',beat_id='approach',object_ids=['B'],time=1,problem='Nhãn chưa rõ.',fix='Tăng tương phản.')]])
            with patch('src.scene_design.scene_generation._context',side_effect=self.context),patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router),patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision):
                with self.assertRaisesRegex(store.WorkflowError,'SCENE_QUALITY_FAILED'):wf.run_workflow(root,ai=True,scene_trial=True)
            before=[(root/n).read_bytes() for n in ['project.json','reviews.json']]
            draft=scene_drafts.current(root)['scene_1'];self.assertFalse(draft['review']['passed']);scene_drafts.verify(root,draft)
            self.assertFalse(wf.get_status(root)['pending_reviews']);self.assertFalse(any(a['stage']=='scene_design' for a in wf.get_status(root)['artifacts']))
            with patch('src.preview.continuous_preview.SYNTHESIZER',side_effect=AssertionError('No FPT for silent preview')):
                preview=continuous_preview.generate(root);self.assertTrue(preview['draft']);self.assertTrue(continuous_preview.status(root)['ready'])
            self.assertEqual(before,[(root/n).read_bytes() for n in ['project.json','reviews.json']])
            with patch('src.shared.review_server.load_local_env'):server=ReviewServer(root,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                x=json.load(urllib.request.urlopen(server.origin+'/api/state'));self.assertIn('scene_1',x['project']['scene_drafts'])
                from urllib.parse import quote
                request=urllib.request.Request(server.origin+'/media?path='+quote(draft['reference']['path']),headers={'Range':'bytes=0-99'})
                with urllib.request.urlopen(request) as response:self.assertEqual(response.status,206);self.assertEqual(len(response.read()),100)
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(server.origin+'/media?path=project.json')
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(server.origin+'/media?path=reviews.json')
            finally:server.shutdown();server.server_close()
            video=root/draft['reference']['path'];video.write_bytes(video.read_bytes()+b'changed')
            self.assertNotIn(video.resolve(),scene_drafts.media_files(root))
            with self.assertRaisesRegex(store.WorkflowError,'ARTIFACT_CHANGED'):continuous_preview.generate(root)

    def test_new_attempt_keeps_old_job_and_does_not_bypass_uncertain_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root=approved_project(Path(folder));router=DesignRouter()
            with patch('src.scene_design.scene_generation._context',side_effect=self.context),patch('src.scene_design.scene_generation.CLIENT_FACTORY',return_value=router),patch('src.scene_design.scene_generation._design',side_effect=store.WorkflowError('SCENE_QUALITY_FAILED','failure')):
                for attempt in [None,'retry-one']:
                    with self.assertRaisesRegex(store.WorkflowError,'failure'):wf.run_workflow(root,ai=True,scene_trial=True,attempt_id=attempt)
                jobs=sg._jobs(root);self.assertEqual(len(jobs),2);self.assertNotEqual(jobs[0]['job_id'],jobs[1]['job_id'])
                old=root/sg.BASE/jobs[0]['job_id']/'job.json';before=old.read_bytes()
                with self.assertRaisesRegex(store.WorkflowError,'failure'):wf.run_workflow(root,ai=True,scene_trial=True,attempt_id='retry-one')
                self.assertEqual(len(sg._jobs(root)),2);self.assertEqual(old.read_bytes(),before)
                uncertain=jobs[1];uncertain['calls']=[dict(call_id='unknown',phase='design',status='uncertain',reserved_cost_usd=.01)]
                store.atomic_write(root/sg.BASE/uncertain['job_id']/'job.json',uncertain)
                with self.assertRaisesRegex(store.WorkflowError,'API_RESULT_UNKNOWN'):wf.run_workflow(root,ai=True,scene_trial=True,attempt_id='retry-two')
                self.assertEqual(len(sg._jobs(root)),2)

if __name__=='__main__':unittest.main()
