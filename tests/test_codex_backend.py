import base64,json,os,subprocess,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from src.shared._workflow_store import WorkflowError,read_json
from src.shared.codex_client import CodexClient,clean_environment
from src.shared.ai_client import create_text_client
from src.shared.job_session import Session, _used
from src.idea.research import citations

class CodexBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ,dict(AI_BACKEND='codex',CODEX_MODEL='test-codex',OPENAI_API_KEY='private-openai',CODEX_API_KEY='private-codex',OPENROUTER_API_KEY='private-router',FPT_API_KEY='private-fpt')).start()
        patch('src.shared.codex_client.executable',return_value='/installed/codex').start()
    def process(self,value,*,search=False):
        test=self
        class Process:
            pid=999999;returncode=0
            def communicate(self,prompt=None,timeout=None):
                events=[dict(type='thread.started',thread_id='real-format-session')]
                if search:events.append(dict(type='item.completed',item=dict(id='search_1',type='web_search',query='derivative')))
                events.append(dict(type='turn.completed',usage=dict(input_tokens=83,cached_input_tokens=12,output_tokens=9)))
                return '\n'.join(json.dumps(e) for e in events),'CLI progress'
        def launch(argv,**kwargs):
            test.argv=argv;test.kwargs=kwargs
            final=Path(argv[argv.index('--output-last-message')+1]);final.write_text(json.dumps(value))
            return Process()
        return launch
    def job(self):return dict(job_id='test',calls=[],pricing=dict(model='test-codex',billing_source='codex_subscription'))
    def test_factory_selects_codex_for_text_and_vision_but_keeps_fpt(self):
        from src.scene_design import scene_vision
        from src.preview import av_workflow
        from src.preview import audio
        self.assertIsInstance(create_text_client(),CodexClient)
        self.assertTrue(scene_vision.create_review_client().vision)
        self.assertIsInstance(av_workflow.CLIENT_FACTORY(),CodexClient)
        self.assertIs(av_workflow.audio.synthesize,audio.synthesize)
    def test_api_key_login_rejected_before_any_model_call(self):
        with patch('src.shared.codex_client.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','Logged in using API key')):
            with self.assertRaisesRegex(WorkflowError,'CODEX_LOGIN_REQUIRED'):CodexClient().preflight()
    def test_chatgpt_login_preflight_never_uses_model_api(self):
        with patch('src.shared.codex_client.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','Logged in using ChatGPT')) as run:
            result=CodexClient().preflight()
        self.assertEqual(result['billing_source'],'codex_subscription');self.assertEqual(run.call_args.args[0][-2:],['login','status'])
    def test_structured_result_logs_tokens_caches_without_fabricating_dollars(self):
        client=CodexClient();job=self.job();session=Session(self.root,job,client,budget_usd=0,other_used=100)
        with patch('src.shared.codex_client.subprocess.Popen',side_effect=self.process(dict(answer='ok'))) as launch:
            result=session.call('draft',[dict(role='user',content='Return JSON')],schema=dict(type='object'))
            self.assertEqual(result,session.call('draft',[]));self.assertEqual(launch.call_count,1)
        self.assertEqual(job['calls'][0]['usage']['prompt_tokens'],83)
        self.assertIsNone(job['calls'][0]['actual_cost_usd']);self.assertEqual(_used(job),0)
        self.assertNotIn('FPT_API_KEY',self.kwargs['env']);self.assertNotIn('OPENROUTER_API_KEY',self.kwargs['env']);self.assertNotIn('OPENAI_API_KEY',self.kwargs['env']);self.assertNotIn('CODEX_API_KEY',self.kwargs['env'])
        self.assertIn('read-only',self.argv);self.assertIn('shell_tool',self.argv);self.assertIn('--ignore-user-config',self.argv)
        trace=self.root/'codex_calls'/job['calls'][0]['call_id'];self.assertTrue((trace/'events.jsonl').is_file());self.assertTrue((trace/'prompt.txt').is_file())
    def test_vision_uses_actual_image_attachment(self):
        client=CodexClient(vision=True);job=self.job()
        content=[dict(type='text',text='Inspect image'),dict(type='image_url',image_url=dict(url='data:image/png;base64,'+base64.b64encode(b'image fixture').decode()))]
        with patch('src.shared.codex_client.subprocess.Popen',side_effect=self.process(dict(passed=True))):
            result=Session(self.root,job,client).call('vision',[dict(role='user',content=content)],schema=dict(type='object'))
        self.assertIn('--image',self.argv);self.assertEqual(result['codex']['image_count'],1)
    def test_search_requires_completed_search_event_and_keeps_provenance(self):
        value=dict(notes='Notes',sources=[dict(url='https://university.example/derivatives',title='Source',excerpt='Actual candidate')])
        client=CodexClient();job=self.job()
        with patch('src.shared.codex_client.subprocess.Popen',side_effect=self.process(value,search=True)):
            result=Session(self.root,job,client).call('search',[dict(role='user',content='Research')],search=True)
        self.assertEqual(citations(result)[0]['discovery'],'codex_search_candidate')
        result['codex']['search_performed']=False
        with self.assertRaisesRegex(WorkflowError,'RESEARCH_UNVERIFIED'):citations(result)
    def test_usage_limit_is_a_definite_rejection_and_not_an_uncertain_result(self):
        class Failed:
            pid=999999;returncode=1
            def communicate(self,*args,**kwargs):return json.dumps(dict(type='turn.failed',error=dict(message="You have hit your usage limit"))),''
        client=CodexClient();job=self.job()
        with patch('src.shared.codex_client.subprocess.Popen',return_value=Failed()) as launch:
            with self.assertRaisesRegex(WorkflowError,'CODEX_USAGE_LIMIT'):Session(self.root,job,client).call('draft',[])
        self.assertEqual(launch.call_count,1);self.assertEqual(job['calls'][0]['status'],'rejected');self.assertEqual(job['calls'][0]['error_code'],'CODEX_USAGE_LIMIT')

    def test_timeout_is_not_automatically_retried(self):
        client=CodexClient();job=self.job();session=Session(self.root,job,client)
        with patch.object(client,'complete',side_effect=WorkflowError('CODEX_TIMEOUT','timeout')) as run:
            with self.assertRaisesRegex(WorkflowError,'CODEX_TIMEOUT'):session.call('draft',[])
            with self.assertRaisesRegex(WorkflowError,'CODEX_RESULT_UNKNOWN'):session.call('draft',[])
            self.assertEqual(run.call_count,1)
    def test_historical_api_cost_is_not_erased_by_subscription_accounting(self):
        job=self.job();job['calls']=[dict(status='complete',actual_cost_usd=.25,reserved_cost_usd=.3),dict(status='complete',actual_cost_usd=None,reserved_cost_usd=0,billing_source='codex_subscription')]
        self.assertEqual(_used(job),.25)

    def test_backend_switch_archives_old_uncertain_job_without_retry_or_erasing_cost(self):
        from src.shared import workflow as wf
        from src.shared import _workflow_store as store
        project=self.root/'project';wf.create_project(project,'A topic')
        directory=project/'generation/old-api';directory.mkdir(parents=True)
        old=dict(job_id='old-api',version='v001',input=dict(model='test-codex'),status='failed',pricing=dict(provider='old'),calls=[dict(call_id='old-call',phase='search',status='uncertain',reserved_cost_usd=.03,actual_cost_usd=None)])
        store.atomic_write(directory/'job.json',old);original=(directory/'job.json').read_bytes()
        with store.locked_project(project) as locked:
            state,reviews=wf._load(locked);state['generation']=dict(job_id='old-api',path='generation/old-api/job.json');wf._save(locked,state,reviews)
        client=CodexClient()
        with patch('src.idea.generation.CLIENT_FACTORY',return_value=client),patch.object(client,'preflight',return_value=dict(model='test-codex',billing_source='codex_subscription')),patch('src.idea.generation._produce',side_effect=WorkflowError('TEST_STOP','no inference')):
            with self.assertRaisesRegex(WorkflowError,'TEST_STOP'):wf.run_workflow(project,ai=True)
        state=store.read_json(project/'project.json')
        self.assertEqual(state['generation_history'][0]['job_id'],'old-api')
        self.assertNotEqual(state['generation']['job_id'],'old-api')
        self.assertEqual((directory/'job.json').read_bytes(),original)
        self.assertEqual(_used(old),.03)
