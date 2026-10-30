"""End-to-end AI workflow using fake model and fetched-source boundaries."""

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from unittest.mock import patch

from src.shared import _workflow_store as store
from src.idea.generation import _produce
from src.shared.review import record_review
from src.shared.workflow import create_project, get_status, run_workflow, submit_artifact
from tests.ai_support import FakeRouter, fake_fetch
from tests.workflow_support import WorkflowCase, FIXTURES


class GenerationTests(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.router = FakeRouter()
        self.factory = patch('src.idea.generation.CLIENT_FACTORY', return_value=self.router).start()
        self.fetch = patch('src.idea.research.fetch_html', side_effect=fake_fetch).start()
        self.addCleanup(patch.stopall)

    def generate(self):
        create_project(self.project, 'Đạo hàm qua một ví dụ quen thuộc')
        return run_workflow(self.project, ai=True)

    def test_four_long_multibyte_sources_stay_within_budget_and_keep_full_research(self):
        from tests.ai_support import SOURCE_TEXT
        from src.idea.content import SOURCE_PROMPT_BYTES
        from src.shared.openrouter_client import OpenRouterClient
        preflight=self.router.preflight
        def prices(**kwargs):
            value=preflight(**kwargs)
            return dict(value,input_rate='0.0000003',tiers=[dict(min_prompt_tokens=100000,
                prompt='0.0000005',completion='0.0000025',input_rate='0.0000015')])
        self.router.preflight=prices
        def long_source(url):
            value=fake_fetch(url)
            value['text']=(SOURCE_TEXT + ' Đạo hàm thể hiện tốc độ thay đổi tức thời. '*700)[:20000]
            value['sha256']=hashlib.sha256(value['text'].encode()).hexdigest()
            return value
        self.fetch.side_effect=long_source
        state=self.generate()
        self.assertEqual(state['status'],'awaiting_review')
        artifact=state['pending_reviews'][0]
        research=store.read_json(self.project/artifact['bundle']['research.json']['path'])
        self.assertEqual(len(research['sources']),4)
        self.assertTrue(all(len(source['text'])==20000 for source in research['sources']))
        sent=json.loads(self.router.requests[1]['messages'][1]['content'])['sources']
        for excerpt,source in zip(sent,research['sources']):
            self.assertLessEqual(len(excerpt['text'].encode()),SOURCE_PROMPT_BYTES)
            self.assertIn(excerpt['text'],source['text'])
            self.assertEqual(hashlib.sha256(source['text'].encode()).hexdigest(),source['sha256'])
        pricing=prices()
        # The previous full-source payload crosses the expensive pricing tier.
        unbounded=copy.deepcopy(self.router.requests[1])
        context=json.loads(unbounded['messages'][1]['content']);context['sources']=research['sources']
        unbounded['messages'][1]['content']=json.dumps(context,ensure_ascii=False)
        client=OpenRouterClient()
        self.assertGreater(client.reserve(unbounded,pricing),0.1)
        self.assertLess(client.reserve(self.router.requests[1],pricing),0.03)
        calls=len(self.router.requests)
        run_workflow(self.project,ai=True)
        self.assertEqual(len(self.router.requests),calls)

    def test_real_pipeline_boundary_produces_complete_bundle_and_waits(self):
        state = self.generate()
        self.assertEqual((state['stage'], state['status']), ('script', 'awaiting_review'))
        item = state['pending_reviews'][0]
        self.assertEqual(item['origin'], 'ai')
        self.assertEqual(len(item['bundle']), 3)
        for component in item['bundle'].values():
            self.assertEqual(store.digest(self.project / component['path']), component['sha256'])
        data = store.read_json(self.project / item['bundle']['script.json']['path'])
        self.assertEqual(data['duration_estimate']['estimated_seconds'], 40)
        self.assertEqual(len(self.router.requests), 2)
        self.assertLessEqual(state['generation']['budget_used_usd'], 0.1)
        self.assertFalse((self.project / 'designs').exists())

    def test_waiting_and_restart_do_not_even_inspect_credentials_or_catalog(self):
        state = self.generate()
        before = self.snapshots()
        with patch('src.idea.generation.CLIENT_FACTORY', side_effect=AssertionError('must not create a client')):
            self.assertEqual(run_workflow(self.project, ai=True), state)
            self.assertEqual(get_status(self.project), state)
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(self.router.preflights, 1)

    def test_measured_reading_is_required_and_bound_to_this_version(self):
        state = self.generate()
        before = self.snapshots()
        for seconds, code in ((None,'READ_DURATION_REQUIRED'), (29,'READ_DURATION_OUT_OF_RANGE'),
                              (61,'READ_DURATION_OUT_OF_RANGE'), (float('nan'),'INVALID_READ_CHECK'), (True,'INVALID_READ_CHECK')):
            with self.subTest(seconds=seconds):
                self.error(code, record_review, self.project, 'script', 'v001', 'approve', read_seconds=seconds)
                self.assertEqual(self.snapshots(), before)
        approved = record_review(self.project, 'script', 'v001', 'approve', read_seconds=45)
        self.assertEqual(approved['stage'], 'scene_design')
        request = approved['review_history'][0]
        self.assertEqual(request['read_check']['seconds'], 45)
        self.assertEqual(request['read_check']['sha256'], state['pending_reviews'][0]['sha256'])
        self.assertEqual(record_review(self.project, 'script', 'v001', 'approve', read_seconds=45), approved)
        self.error('REVIEW_CONFLICT', record_review, self.project, 'script', 'v001', 'approve', read_seconds=46)
        calls = len(self.router.requests)
        # C4's entrypoint remains a no-op after script approval; workflow --ai
        # now deliberately dispatches scene_design to the C5 provider.
        from src.idea.generation import run_ai
        run_ai(self.project)
        self.assertEqual(len(self.router.requests), calls)
        self.assertFalse((self.project / 'designs').exists())

    def test_revision_reuses_sources_preserves_feedback_and_resets_read_check(self):
        state = self.generate()
        original = state['pending_reviews'][0]
        old_files = {path:store.digest(self.project / path) for path in [str(Path(original['path']).relative_to(self.project)),
                     *[c['path'] for c in original['bundle'].values()]]}
        record_review(self.project, 'script', 'v001', 'request_changes', feedback='Giải thích dễ hiểu hơn', read_seconds=65)
        self.fetch.reset_mock()
        revised = run_workflow(self.project, ai=True)
        self.fetch.assert_not_called()
        self.assertEqual(revised['pending_reviews'][0]['version'], 'v002')
        self.assertEqual(revised['review_history'][0]['read_check']['seconds'], 65)
        self.assertNotIn('read_check', revised['review_history'][1])
        for path, checksum in old_files.items():
            self.assertEqual(store.digest(self.project / path), checksum)
        last_prompt = self.router.requests[-1]['messages'][1]['content']
        self.assertIn('Giải thích dễ hiểu hơn', last_prompt)
        self.assertIn('previous_script', last_prompt)
        self.error('READ_DURATION_REQUIRED', record_review, self.project, 'script', 'v002', 'approve')

    def test_revision_can_research_additional_facts(self):
        self.generate()
        record_review(self.project, 'script', 'v001', 'request_changes', feedback='Bổ sung một kiến thức mới có nguồn')
        self.router.needs_research = True
        self.fetch.reset_mock()
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['pending_reviews'][0]['version'], 'v002')
        self.assertGreater(self.fetch.call_count, 0)
        self.assertLessEqual(self.fetch.call_count, 4)

    def test_invalid_model_output_uses_one_repair_when_it_succeeds(self):
        self.router.invalid_drafts = 1
        state = self.generate()
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(len(self.router.requests), 3)

    def test_failed_repair_keeps_diagnostics_without_submitting_script(self):
        self.router.invalid_drafts = 3
        create_project(self.project, 'Invalid output')
        self.error('INVALID_MODEL_OUTPUT', run_workflow, self.project, ai=True)
        state = get_status(self.project)
        self.assertEqual(state['status'], 'ready')
        self.assertEqual(state['artifacts'], [])
        directory = Path(state['generation']['path']).parent
        self.assertTrue((directory / 'validation_error.json').is_file())
        self.assertEqual(len(list((directory / 'calls').glob('*.json'))), 4)
        # Completed invalid responses remain cached, not regenerated on restart.
        self.error('INVALID_MODEL_OUTPUT', run_workflow, self.project, ai=True)
        self.assertEqual(len(self.router.requests), 4)

    def test_sources_missing_does_not_register_script(self):
        self.fetch.side_effect = store.WorkflowError('SOURCE_FETCH_FAILED', '404')
        create_project(self.project, 'No sources')
        self.error('RESEARCH_INSUFFICIENT', run_workflow, self.project, ai=True)
        state = get_status(self.project)
        self.assertEqual(state['artifacts'], [])
        self.assertEqual(state['status'], 'ready')
        self.assertIn('RESEARCH_INSUFFICIENT', state['generation']['last_error'])

    def test_retrying_source_reads_reuses_paid_search_response(self):
        self.fetch.side_effect = store.WorkflowError('SOURCE_FETCH_FAILED', 'temporary network error')
        create_project(self.project, 'Source retry')
        self.error('RESEARCH_INSUFFICIENT', run_workflow, self.project, ai=True)
        self.assertEqual(len(self.router.requests), 1)
        self.fetch.side_effect = fake_fetch
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(len(self.router.requests), 2)  # cached search + one new draft
        self.assertIsNone(state['generation']['last_error'])

    def test_bundle_changes_block_approval(self):
        state = self.generate()
        research = self.project / state['pending_reviews'][0]['bundle']['research.json']['path']
        research.write_text('{}')
        before = self.snapshots()
        self.error('ARTIFACT_CHANGED', record_review, self.project, 'script', 'v001', 'approve', read_seconds=40)
        self.assertEqual(self.snapshots(), before)

    def test_network_work_does_not_hold_the_project_lock_and_stale_result_is_not_applied(self):
        create_project(self.project, 'Concurrent manual submission')
        changed = []
        def concurrent_submit(payload):
            # A status/manual action can take the project lock during the POST.
            state = get_status(self.project)
            self.assertEqual(state['stage'], 'script')
            if not changed:
                submit_artifact(self.project, 'script', 'manual', FIXTURES / 'script.md', scene_ids=['scene_01', 'scene_02'])
                changed.append(True)
        self.router.before_complete = concurrent_submit
        self.error('STALE_JOB', run_workflow, self.project, ai=True)
        state = get_status(self.project)
        self.assertEqual(state['pending_reviews'][0]['version'], 'manual')
        self.assertNotIn('origin', state['pending_reviews'][0])

    def test_cached_generation_after_interruption_does_not_repeat_paid_calls(self):
        create_project(self.project, 'Resume complete responses')
        from src.shared import workflow
        real_submit = workflow._submit
        with patch('src.shared.workflow._submit', side_effect=OSError('interrupted before registration')):
            self.error('IO_ERROR', run_workflow, self.project, ai=True)
        calls = len(self.router.requests)
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(len(self.router.requests), calls)
        self.assertEqual(self.router.preflights, 1)

    def test_two_topics_use_same_code_and_jobs_do_not_share_budget_or_assets(self):
        first = self.generate()
        other = self.project.parent / 'day_night'
        create_project(other, 'Vì sao có ngày và đêm?')
        second = run_workflow(other, ai=True)
        self.assertNotEqual(first['generation']['job_id'], second['generation']['job_id'])
        self.assertNotEqual(first['pending_reviews'][0]['path'], second['pending_reviews'][0]['path'])
        self.assertEqual(first['generation']['request_count'], second['generation']['request_count'])

    def test_cli_flags_are_mutually_exclusive_and_brief_is_persisted(self):
        brief = self.project.parent / 'brief.json'
        brief.write_text(json.dumps({'audience':'Người mới'}))
        self.cli('create', '--topic', 'CLI', '--brief', str(brief))
        self.assertEqual(get_status(self.project)['brief']['audience'], 'Người mới')
        result = self.cli('run', '--ai', '--mock', expected=1)
        self.assertEqual(result['error']['code'], 'INVALID_INPUT')

    def test_missing_environment_key_reports_failure_without_losing_progress(self):
        create_project(self.project, 'Missing key')
        with patch('src.idea.generation.CLIENT_FACTORY', __import__('src.shared.openrouter_client', fromlist=['OpenRouterClient']).OpenRouterClient), patch.dict(os.environ, {}, clear=True):
            self.error('API_KEY_MISSING', run_workflow, self.project, ai=True)
        state = get_status(self.project)
        self.assertEqual((state['stage'],state['status']), ('script','ready'))
        self.assertEqual(state['generation']['request_count'], 0)
        self.assertEqual(state['artifacts'], [])

    def test_missing_job_ledger_cannot_reset_budget(self):
        self.router.invalid_drafts = 3
        create_project(self.project, 'Ledger loss')
        self.error('INVALID_MODEL_OUTPUT', run_workflow, self.project, ai=True)
        ledger = Path(get_status(self.project)['generation']['path'])
        ledger.unlink()
        count = len(self.router.requests)
        self.error('STATE_MISSING', run_workflow, self.project, ai=True)
        self.assertEqual(len(self.router.requests), count)
        self.assertFalse(ledger.exists())

    def test_model_change_cannot_reset_unfinished_jobs_budget(self):
        self.router.invalid_drafts = 3
        create_project(self.project, 'Pinned model after failure')
        self.error('INVALID_MODEL_OUTPUT', run_workflow, self.project, ai=True)
        before = self.snapshots()
        state = get_status(self.project)
        ledger = Path(state['generation']['path'])
        ledger_before = ledger.read_bytes()
        calls = len(self.router.requests)
        with patch.dict(os.environ, {'OPENROUTER_MODEL': 'test/another-model'}):
            self.error('MODEL_MISMATCH', run_workflow, self.project, ai=True)
        self.assertEqual(len(self.router.requests), calls)
        self.assertEqual(ledger.read_bytes(), ledger_before)
        self.assertEqual(self.snapshots(), before)
