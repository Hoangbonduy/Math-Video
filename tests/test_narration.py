"""Narration-first generation, editorial regressions, UI duration and resume behavior."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from src.idea.content import writing_messages
from src.idea.narration import (NARRATION_SCHEMA, _sentences, validate_editorial,
                                validate_narration, narration_messages, editing_messages)
from src.idea.service import inputs, apply_idea
from src.idea.generation import _produce
from src.shared.ai_config import normalize_brief
from src.shared._workflow_store import WorkflowError
from src.shared.job_session import Session
from src.shared import _workflow_store as store
from src.shared.review import record_review
from src.shared.workflow import create_project, get_status, run_workflow
from tests.ai_support import FakeRouter, fake_fetch, valid_script
from tests.workflow_support import WorkflowCase


def sources():
    return [dict(fake_fetch(f'https://university.example/{i}'), source_id=f'source_{i+1:02}') for i in range(2)]


def short_script():
    script = valid_script()
    lines = [
        'Gấp tờ giấy 42 lần là tới Mặt Trăng. Nghe khó tin đúng không?',
        'Nhưng mỗi lần gấp, độ dày lại tăng gấp đôi.',
        'Cứ thế, những lần sau làm nó lớn lên rất nhanh.',
        'Rồi đến lần 30, nó đã vươn ra ngoài không gian.',
        'Gấp tiếp tới lần 42, nó vượt qua Mặt Trăng luôn.',
        'Ngoài đời, giấy sẽ kẹt cứng sau vài lần gấp. Bạn còn nghĩ được điều gì tăng nhanh như thế?',
    ]
    script['scenes'] = [dict(scene_id=f's{i+1}', narration=line,
        visual_intent='Hiện 440.000 km so với 384.400 km; mô hình giả định.',
        claim_refs=['claim_01']) for i, line in enumerate(lines)]
    return script


class NarrationTests(WorkflowCase):
    def test_ui_duration_controls_budget_without_changing_defaults(self):
        from src.shared.ai_config import DEFAULT_BRIEF
        before = copy.deepcopy(DEFAULT_BRIEF)
        for duration, limits in [('45-60 giây', (158, 210)), ('30–35 giây', (105, 122))]:
            _, brief = inputs(dict(topic='Tăng trưởng', duration=duration))
            messages = writing_messages('Tăng trưởng', brief, sources())
            context = json.loads(messages[1]['content'])
            self.assertEqual(tuple(context['narration_budget'].values()), limits)
            self.assertEqual(context['brief'], brief)
        self.assertEqual(DEFAULT_BRIEF, before)

    def test_narration_draft_has_no_scene_boundaries_and_preserves_evidence(self):
        script = valid_script()
        draft = dict(title=script['title'], main_idea=script['main_idea'], claims=script['claims'],
                     narration=' '.join(scene['narration'] for scene in script['scenes']))
        result = validate_narration(draft, sources(), normalize_brief())
        self.assertNotIn('scenes', result)
        self.assertEqual(result, draft)
        draft['claims'][0]['evidence'][0]['quote'] = 'Invented evidence'
        with self.assertRaisesRegex(WorkflowError, 'verbatim'):
            validate_narration(draft, sources(), normalize_brief())

    def test_editor_receives_whole_draft_previous_script_and_feedback(self):
        brief = normalize_brief(dict(min_seconds=45, max_seconds=60))
        previous = short_script()
        messages = editing_messages('Gấp giấy', brief, sources(), {'narration':'Một mạch kể'},
                                    previous=previous, feedback='Không lặp ý gấp đôi')
        context = json.loads(messages[1]['content'])
        self.assertEqual(context['narration_draft']['narration'], 'Một mạch kể')
        self.assertEqual(context['previous_script'], previous)
        self.assertEqual(context['feedback'], 'Không lặp ý gấp đôi')
        self.assertEqual(context['narration_budget'], dict(min_units=158, max_units=210))
        self.assertIn('biên tập viên', messages[0]['content'])

    def test_visual_numbers_are_not_counted_as_spoken_numbers(self):
        script = short_script()
        validate_editorial(script)
        script['scenes'][0]['narration'] = 'Đến lần 23, nó cao hơn Landmark 81. Rồi lần 30, nó vượt mốc tiếp theo.'
        validate_editorial(script)
        self.assertEqual(len(_sentences('Ban đầu là 0,1 mm. Rồi lên 107.000 m.')), 2)

    def test_long_numbers_are_detected_in_digits_and_spelled_vietnamese(self):
        for text in (
            'Nó dày 440.000 km, vượt mốc 384.400 km.',
            'Nó dày 440000 km, vượt mốc 384400 km.',
            'Nó dày 440 000 km, vượt mốc 384 400 km.',
            'Nó dày bốn trăm bốn mươi nghìn ki lô mét, vượt mốc ba trăm tám mươi tư nghìn ki lô mét.',
            'Khả năng là 50,7 phần trăm, rồi tới 99,9 phần trăm.',
        ):
            with self.subTest(text=text):
                script = short_script(); script['scenes'][2]['narration'] = text
                with self.assertRaisesRegex(WorkflowError, 'tối đa một số dài'):
                    validate_editorial(script)
        script['scenes'][2]['narration'] = 'Nó đã cao tới 107.000 mét.'
        validate_editorial(script)

    def test_filler_repeated_sentences_and_too_many_sentences_are_rejected(self):
        for text in ('Hãy xem điều gì xảy ra.', 'Cùng tìm hiểu nhé!',
                     'Như bạn thấy trên màn hình, nó lớn lên.',
                     short_script()['scenes'][1]['narration'],
                     'Ban đầu rất nhỏ. Rồi nó lớn lên. Sau đó to hơn nữa.'):
            with self.subTest(text=text):
                script = short_script(); script['scenes'][3]['narration'] = text
                with self.assertRaisesRegex(WorkflowError, 'Biên tập lời đọc'):
                    validate_editorial(script)

    def test_follow_is_optional_short_and_separate_from_caveat(self):
        for text in ('Đây là mô hình giả định, follow Axiora nhé.', 'Follow Axiora để biết thêm thật nhiều chi tiết.'):
            script = short_script(); script['scenes'][-1]['narration'] = text
            with self.assertRaisesRegex(WorkflowError, 'follow phải là câu riêng'):
                validate_editorial(script)
        for text in ('Ngoài đời, giấy sẽ kẹt sau vài lần gấp. Follow Axiora để xem tiếp nhé.',
                     'Nhớ follow Axiora để xem tiếp nhé.'):
            script['scenes'][-1]['narration'] = text
            validate_editorial(script)

    def setup_provider(self):
        router = FakeRouter()
        patch('src.idea.generation.CLIENT_FACTORY', return_value=router).start()
        patch('src.idea.research.fetch_html', side_effect=fake_fetch).start()
        self.addCleanup(patch.stopall)
        return router

    def test_new_generation_persists_narration_before_editorial_split(self):
        router = self.setup_provider()
        create_project(self.project, 'Ví dụ')
        state = run_workflow(self.project, ai=True)
        schemas = [request['response_format']['json_schema']['schema'] for request in router.requests if 'tools' not in request]
        self.assertIn('narration', schemas[0]['properties'])
        self.assertNotIn('scenes', schemas[0]['properties'])
        self.assertIn('scenes', schemas[1]['properties'])
        directory = Path(state['generation']['path']).parent
        draft = store.read_json(directory / 'narration_draft.json')
        final_context = json.loads(router.requests[-1]['messages'][1]['content'])
        self.assertEqual(final_context['narration_draft'], draft)
        self.assertEqual(store.read_json(directory / 'generation.json')['prompt_version'], 2)

    def test_resume_between_phases_reuses_saved_narration_call(self):
        router = self.setup_provider()
        create_project(self.project, 'Resume narration')
        with patch('src.idea.generation.editing_messages', side_effect=WorkflowError('TEST_STOP', 'Pause after narration')):
            self.error('TEST_STOP', run_workflow, self.project, ai=True)
        self.assertEqual(len(router.requests), 2)
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(len(router.requests), 3)
        self.assertEqual(router.preflights, 1)

    def test_failed_editorial_repairs_do_not_submit_or_repeat_calls(self):
        router = self.setup_provider()
        complete = router.complete
        def bad_editor(payload):
            result = complete(payload)
            if 'tools' not in payload and 'scenes' in payload['response_format']['json_schema']['schema']['properties']:
                script = json.loads(result['choices'][0]['message']['content'])
                script['scenes'][1]['narration'] = script['scenes'][0]['narration']
                result['choices'][0]['message']['content'] = json.dumps(script)
            return result
        router.complete = bad_editor
        create_project(self.project, 'Bad editorial')
        self.error('INVALID_SCRIPT', run_workflow, self.project, ai=True)
        self.assertFalse(get_status(self.project)['artifacts'])
        count = len(router.requests)
        self.error('INVALID_SCRIPT', run_workflow, self.project, ai=True)
        self.assertEqual(len(router.requests), count)
        self.assertEqual(count, 5)

    def test_revision_with_research_and_two_repairs_fits_six_call_limit(self):
        router = self.setup_provider()
        create_project(self.project, 'Revised narration')
        run_workflow(self.project, ai=True)
        record_review(self.project, 'script', 'v001', 'request_changes', feedback='Thêm dữ kiện mới')
        router.needs_research = True
        failures = {'narration': 1, 'scenes': 1}
        complete = router.complete
        def fail_once_per_phase(payload):
            response = complete(payload)
            if 'tools' not in payload:
                properties = payload['response_format']['json_schema']['schema']['properties']
                for key in failures:
                    if key in properties and failures[key]:
                        failures[key] -= 1
                        response['choices'][0]['message']['content'] = '{broken json'
                        break
            return response
        router.complete = fail_once_per_phase
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(state['generation']['request_count'], 6)
        self.assertEqual(state['pending_reviews'][0]['version'], 'v002')

    def test_pre_upgrade_job_uses_original_phases_and_schema(self):
        router = self.setup_provider()
        directory = self.project; directory.mkdir()
        job = dict(job_id='legacy', input=dict(model=router.preflight()['model']),
                   pricing=router.preflight(), calls=[], base_version=None)
        session = Session(directory, job, router)
        _produce(session, 'Legacy', normalize_brief())
        self.assertEqual([call['phase'] for call in job['calls']], ['search', 'draft'])
        self.assertEqual(store.read_json(directory / 'generation.json')['prompt_version'], 1)
        self.assertFalse((directory / 'narration_draft.json').exists())

    def test_ui_45_to_60_seconds_reaches_both_phases_and_final_validation(self):
        router = self.setup_provider()
        complete = router.complete
        extra = 'để hiểu rõ hơn qua một ví dụ quen thuộc'
        def longer_script(payload):
            response = complete(payload)
            if 'tools' not in payload:
                value = json.loads(response['choices'][0]['message']['content'])
                if 'narration' in value:
                    value['narration'] += (' ' + extra) * 4
                elif 'scenes' in value:
                    for scene in value['scenes']:
                        scene['narration'] = ' '.join(sentence.rstrip('.') + ' ' + extra + '.'
                                                    for sentence in _sentences(scene['narration']))
                response['choices'][0]['message']['content'] = json.dumps(value)
            return response
        router.complete = longer_script
        create_project(self.project, 'UI duration')
        apply_idea(self.project, dict(topic='Tốc độ tức thời', duration='45-60 giây'))
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        duration = state['pending_reviews'][0]['duration_estimate']
        self.assertEqual(duration['whitespace_units'], 180)
        self.assertTrue(45 <= duration['estimated_seconds'] <= 60)
        for request in router.requests[1:]:
            context = json.loads(request['messages'][1]['content'])
            self.assertEqual((context['brief']['min_seconds'], context['brief']['max_seconds']), (45, 60))

    def test_truncated_narration_is_repaired_before_editing(self):
        router = self.setup_provider()
        complete = router.complete
        truncated = []
        def cut_first_narration(payload):
            response = complete(payload)
            if 'tools' not in payload and not truncated:
                truncated.append(True)
                response['choices'][0]['finish_reason'] = 'length'
            return response
        router.complete = cut_first_narration
        create_project(self.project, 'Truncated narration')
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(len(router.requests), 4)

    def test_legacy_fingerprint_is_resumed_and_next_revision_uses_new_pipeline(self):
        import hashlib
        from src.shared import workflow as wf
        from src.shared.job_session import _json_bytes
        from src.shared.ai_config import MODEL
        router = self.setup_provider()
        create_project(self.project, 'Legacy fingerprint')
        project, reviews = wf._load(self.project)
        fingerprint = dict(project_id=project['project_id'], topic=project['topic'],
                           brief=normalize_brief(project.get('brief')), base_version=None,
                           base_sha256=None, feedback=None, model=MODEL)
        job_id = 'script_v001_' + hashlib.sha256(_json_bytes(fingerprint)).hexdigest()[:16]
        directory = self.project / 'generation' / job_id
        directory.mkdir(parents=True)
        store.atomic_write(directory / 'job.json', dict(job_id=job_id, input=fingerprint,
            version='v001', base_version=None, status='planned', calls=[], last_error=None))
        project['generation'] = dict(job_id=job_id, path=str((directory / 'job.json').relative_to(self.project)))
        wf._save(self.project, project, reviews)
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['generation']['job_id'], job_id)
        self.assertEqual(len(router.requests), 2)
        self.assertNotIn('prompt_version', store.read_json(directory / 'job.json')['input'])
        record_review(self.project, 'script', 'v001', 'request_changes', feedback='Sửa lời đọc liền mạch')
        state = run_workflow(self.project, ai=True)
        new_directory = Path(state['generation']['path']).parent
        self.assertEqual(store.read_json(new_directory / 'generation.json')['prompt_version'], 2)
        self.assertTrue((new_directory / 'narration_draft.json').exists())
