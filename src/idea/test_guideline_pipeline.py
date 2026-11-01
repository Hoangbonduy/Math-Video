"""Offline acceptance/regression tests; kept in src/idea to respect the edit scope.

Run: python -m unittest src.idea.test_guideline_pipeline
"""

import copy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.idea import guidelines as rules
from src.idea.content import writing_messages, validate_script, narration_budget, markdown
from src.idea.generation import _produce
from src.idea.narration import (CRITIQUE_RULES, CRITIQUE_SCHEMA, _sentences,
    check_continuous_narration_rules, critic_messages, editing_messages,
    validate_critique, validate_editorial, validate_narration)
from src.idea.service import inputs
from src.shared import _workflow_store as store
from src.shared.ai_config import MODEL, normalize_brief
from src.shared.job_session import Session
from src.shared.review import record_review
from src.shared.workflow import create_project, get_status, run_workflow
from tests.ai_support import FakeRouter, fake_fetch

QUESTION = 'Bạn cần gấp thêm mấy lần để đi xa gấp đôi?'
LINES = [
    'Một tờ giấy có thể vươn xa tới Mặt Trăng?',
    'Nhưng mỗi lần gấp, độ dày của nó lại tăng gấp đôi.',
    'Rồi đến lần 23, tờ giấy đã cao hơn Landmark 81.',
    'Cứ thế, những lần gấp tiếp theo khiến độ dày tăng nhanh.',
    'Đến lần 25, nó đã vượt qua cả đỉnh Fansipan.',
    'Rồi khoảng cách giữa các mốc tiếp tục giãn ra rất nhanh.',
    'Vậy đến lần 42, độ dày ấy đã vượt tới Mặt Trăng.',
    'Nhưng ngoài đời, tờ giấy sẽ kẹt sau vài lần gấp.',
    QUESTION,
    rules.CLOSING_SENTENCE,
]
GOAL = '\n'.join([
    '1. Hook: ' + LINES[0], '2. Xuất phát: Gấp đôi độ dày.',
    '3. Leo: Landmark 81 rồi Fansipan.', '4. Payoff: Vượt Mặt Trăng.',
    '5. Twist + Câu hỏi cuối: ' + LINES[-3] + ' ' + QUESTION + ' (đáp án: 1)',
    '6. Câu chốt: ' + rules.CLOSING_SENTENCE,
])
SOURCE = 'Each fold doubles the thickness. After 42 folds, 0.1 mm exceeds the distance to the Moon.'


def source_fetch(url):
    source = fake_fetch(url)
    source.update(text=SOURCE, sha256=hashlib.sha256(SOURCE.encode()).hexdigest())
    return source


def sources():
    return [dict(source_fetch(f'https://university.example/{index}'), source_id=f'source_{index+1:02}')
            for index in range(2)]


def script_fixture(with_question=True):
    lines = LINES[:] if with_question else LINES[:5] + ['Rồi cứ tiếp tục gấp, mỗi mốc mới lại xa hơn.'] + LINES[5:-2] + LINES[-1:]
    groups = [[line] for line in lines[:-3 if with_question else -2]]
    groups.append(lines[-3 if with_question else -2:])
    return dict(title='Gấp giấy', main_idea='Tăng trưởng gấp đôi',
        claims=[dict(claim_id='claim_01', statement='Gấp đôi độ dày',
                     evidence=[dict(source_id='source_01', quote='Each fold doubles the thickness.')])],
        scenes=[dict(scene_id=f's{index+1}', narration=' '.join(group),
                     visual_intent='Hiện 0,1 mm và các mốc so sánh. ' + (QUESTION if with_question else '') +
                                   ' Tờ giấy tiếp tục chuyển động khi đọc câu chốt.', claim_refs=['claim_01'])
                for index, group in enumerate(groups)])


def draft_fixture(with_question=True):
    script = script_fixture(with_question)
    return dict(title=script['title'], main_idea=script['main_idea'], claims=script['claims'],
                narration=' '.join(scene['narration'] for scene in script['scenes']))


def brief_fixture(with_question=True):
    goal = GOAL if with_question else GOAL.replace(QUESTION + ' (đáp án: 1)', '')
    return inputs(dict(topic='Gấp giấy', goal=goal, duration='30–60 giây'))[1]


def critique_fixture(narration, passed=True):
    return dict(all_passed=passed,
                checks=[dict(rule=rule, passed=passed, reason='Đã kiểm tra theo guideline') for rule in CRITIQUE_RULES],
                narration=narration)


class GuidelineRouter(FakeRouter):
    def complete(self, payload):
        result = super().complete(payload)
        if 'tools' not in payload:
            schema = payload['response_format']['json_schema']['schema']['properties']
            if 'all_passed' in schema:
                context = json.loads(payload['messages'][1]['content'])
                narration = context['draft']['narration']
                for message in payload['messages'][2:]:
                    if message['role'] == 'assistant':
                        try:
                            narration = json.loads(message['content'])['narration']
                        except (ValueError, KeyError):
                            pass
                value = critique_fixture(narration)
                expected_rules = schema['checks']['items']['properties']['rule']['enum']
                value['checks'] = [dict(rule=rule, passed=True, reason='Đã kiểm tra theo guideline') for rule in expected_rules]
            elif 'scenes' in schema:
                context = json.loads(payload['messages'][1]['content'])
                draft = context['narration_draft']
                value = script_fixture()
                value.update({key: draft[key] for key in ('title', 'main_idea', 'claims')})
                sentences = _sentences(draft['narration'])
                groups = [[sentence] for sentence in sentences[:-3]] + [sentences[-3:]]
                value['scenes'] = [dict(scene_id=f's{index+1}', narration=' '.join(group),
                    visual_intent='Hiện chữ: ' + QUESTION + ' Hình tiếp tục chuyển động trong lúc đọc câu chốt.', claim_refs=['claim_01'])
                    for index, group in enumerate(groups)]
            elif 'needs_research' in schema:
                return result
            else:
                value = draft_fixture()
            result['choices'][0]['message']['content'] = json.dumps(value, ensure_ascii=False)
        return result


class GuidelineChecks(unittest.TestCase):
    def test_dynamic_loading_precedence_missing_empty_and_hash_changes(self):
        with TemporaryDirectory() as temp:
            main, supplied = Path(temp) / 'guidelines.md', Path(temp) / 'supplied.md'
            with patch.object(rules, 'GUIDELINE_PATH', main), patch.object(rules, 'SUPPLIED_GUIDELINE_PATH', supplied):
                with self.assertRaisesRegex(store.WorkflowError, 'GUIDELINE_MISSING'):
                    rules.load_guidelines()
                supplied.write_text('Quy tắc được cung cấp', encoding='utf-8')
                fallback, digest = rules.load_guidelines()
                self.assertEqual(len(digest), 64)
                main.write_text('Quy tắc gốc', encoding='utf-8')
                self.assertNotEqual(rules.load_guidelines()[1], digest)
                self.assertEqual(rules.load_guidelines()[0], 'Quy tắc gốc')
                main.write_text('  ', encoding='utf-8')
                with self.assertRaisesRegex(store.WorkflowError, 'GUIDELINE_EMPTY'):
                    rules.load_guidelines()

    def test_goal_preserved_through_normalized_brief_and_all_prompt_phases(self):
        brief = brief_fixture()
        goal = rules.extract_goal_info(normalize_brief(brief))
        self.assertEqual(goal['final_question'], QUESTION)
        self.assertEqual(goal['forbidden_spoken_answers'], ['1'])
        self.assertEqual(len(goal['raw_lines']), 6)
        for messages in (writing_messages('Giấy', brief, sources()),
                         critic_messages('Giấy', brief, draft_fixture()),
                         editing_messages('Giấy', brief, sources(), draft_fixture())):
            context = json.loads(messages[1]['content'])
            self.assertEqual(context['structured_goal'], goal)
            applied = rules.load_guidelines()[0]
            if 'Pha 1 CHỈ chấm' in messages[0]['content']:
                applied = rules.guidelines_for_phase(applied, 'narration')
            self.assertIn(applied, messages[0]['content'])
            self.assertEqual(context['narration_budget'], dict(min_units=75, max_units=210))

    def test_goal_without_question_and_parenthesized_question_mark(self):
        self.assertFalse(rules.extract_goal_info(brief_fixture(False))['has_final_question'])
        goal = GOAL.replace(QUESTION + ' (đáp án: 1)', '(ghi chú: có thể hỏi?)')
        self.assertFalse(rules.parse_idea_goal(goal)['has_final_question'])

    def test_question_mark_is_preserved_without_reading_the_answer(self):
        draft = draft_fixture()
        validate_narration(draft, sources(), brief_fixture())
        for text in (draft['narration'].replace(QUESTION, 'Bạn cần gấp thêm bao nhiêu lần?'),
                     draft['narration'].replace(QUESTION, ''),
                     draft['narration'].replace(QUESTION, QUESTION + ' Đáp án là 1.')):
            with self.subTest(text=text), self.assertRaises(store.WorkflowError):
                validate_narration(dict(draft, narration=text), sources(), brief_fixture())
        validate_narration(draft_fixture(False), sources(), brief_fixture(False))
        with self.assertRaisesRegex(store.WorkflowError, 'không tự thêm'):
            validate_narration(draft, sources(), brief_fixture(False))

    def test_exact_separate_closing_sentence_is_mandatory(self):
        draft = draft_fixture()
        for closing in ('Đăng ký Axiora nhé.', rules.CLOSING_SENTENCE + ' Hết.',
                        rules.CLOSING_SENTENCE[:-1], 'Vậy ' + rules.CLOSING_SENTENCE):
            with self.subTest(closing=closing), self.assertRaises(store.WorkflowError):
                validate_narration(dict(draft, narration=draft['narration'].replace(rules.CLOSING_SENTENCE, closing)),
                                   sources(), brief_fixture())

    def test_evidence_and_duration_checked_before_and_after_critic(self):
        draft = draft_fixture()
        wrong = copy.deepcopy(draft)
        wrong['claims'][0]['evidence'][0]['quote'] = 'invented quotation'
        with self.assertRaisesRegex(store.WorkflowError, 'verbatim'):
            validate_narration(wrong, sources(), brief_fixture())
        with self.assertRaisesRegex(store.WorkflowError, 'duration'):
            validate_critique(critique_fixture(rules.CLOSING_SENTENCE, False), draft, sources(), brief_fixture())

    def test_fillers_long_numbers_and_sentence_length(self):
        for sentence in ('Đó là sức mạnh của phép toán.', 'Nó vượt qua những đỉnh núi.',
                         'Nó dày 440.000 km, vượt mốc 384.400 km.',
                         'Nó dày bốn trăm bốn mươi nghìn mét, vượt ba trăm tám mươi tư nghìn mét.',
                         ' '.join(['tiếng'] * 19) + '.'):
            with self.subTest(sentence=sentence):
                text = draft_fixture()['narration'].replace(LINES[3], sentence)
                self.assertTrue(check_continuous_narration_rules(text, brief_fixture()))
        self.assertTrue(check_continuous_narration_rules(
            draft_fixture()['narration'].replace(LINES[3], ' '.join(['tiếng'] * 17) + '.'), brief_fixture()))

    def test_last_scene_allows_two_three_or_four_sentences(self):
        for count in (2, 3, 4):
            script = script_fixture()
            lines = LINES[:]
            if count == 2:
                script = script_fixture(False)
                brief = brief_fixture(False)
            elif count == 4:
                last = script['scenes'].pop()
                last['narration'] = script['scenes'].pop()['narration'] + ' ' + last['narration']
                last['scene_id'] = f"s{len(script['scenes'])+1}"
                script['scenes'].append(last)
                brief = brief_fixture()
            else:
                brief = brief_fixture()
            narration = ' '.join(scene['narration'] for scene in script['scenes'])
            validate_editorial(script, narration, brief)
            validate_script(script, sources(), brief)

    def test_reject_nonsequential_ids_wrong_scene_sizes_or_missing_question_visual(self):
        for mutate in (lambda s: s['scenes'][0].update(scene_id='scene_01'),
                       lambda s: s['scenes'][2].update(scene_id='s9'),
                       lambda s: s['scenes'][0].update(narration='Câu một. Câu hai. Câu ba.'),
                       lambda s: s['scenes'][-1].update(narration=rules.CLOSING_SENTENCE),
                       lambda s: s['scenes'][-1].update(visual_intent='Chỉ có hình')):
            script = script_fixture(); mutate(script)
            with self.assertRaises(store.WorkflowError):
                validate_editorial(script, brief=brief_fixture())

    def test_slicing_preserves_every_character_and_only_sentence_boundaries(self):
        draft = draft_fixture()
        for old, new in (('Nhưng', 'Tuy nhiên'), ('gấp,', 'gấp'), ('độ dày', 'độ  dày')):
            script = script_fixture()
            script['scenes'][1]['narration'] = script['scenes'][1]['narration'].replace(old, new)
            with self.assertRaisesRegex(store.WorkflowError, 'từng ký tự'):
                validate_editorial(script, draft['narration'], brief_fixture())
        script = script_fixture()
        words = script['scenes'][0]['narration'].split()
        script['scenes'][0]['narration'] = ' '.join(words[:4])
        script['scenes'][1]['narration'] = ' '.join(words[4:]) + ' ' + script['scenes'][1]['narration']
        with self.assertRaisesRegex(store.WorkflowError, 'ranh giới câu'):
            validate_editorial(script, draft['narration'], brief_fixture())

    def test_critic_rejects_incomplete_duplicate_or_inconsistent_checks(self):
        for mutate in (lambda c: c['checks'].pop(),
                       lambda c: c['checks'][0].update(rule='unknown'),
                       lambda c: c['checks'][0].update(rule='rule_2'),
                       lambda c: c['checks'][0].update(passed=False),
                       lambda c: c.update(narration=c['narration'].replace('Nhưng', 'Rồi'))):
            draft = draft_fixture(); critique = critique_fixture(draft['narration']); mutate(critique)
            with self.assertRaises(store.WorkflowError):
                validate_critique(critique, draft, sources(), brief_fixture())

    def test_critic_revision_requires_a_second_judgment(self):
        draft = draft_fixture()
        revised = draft['narration'].replace(LINES[3], 'Cứ thế, độ dày tăng nhanh sau những lần gấp tiếp theo.')
        with self.assertRaisesRegex(store.WorkflowError, 'chấm lại'):
            validate_critique(critique_fixture(revised, False), draft, sources(), brief_fixture())
        self.assertEqual(draft['narration'], revised)
        validate_critique(critique_fixture(revised), draft, sources(), brief_fixture())

    def test_ui_explains_closing_and_vietnamese_landmarks(self):
        text = Path(__file__).with_name('web.js').read_text()
        self.assertIn('6. Câu chốt:', text)
        self.assertIn('Landmark 81, Fansipan', text)
        self.assertNotIn('6. Số hiện hình:', text)


class GuidelinePipeline(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='guideline-pipeline-')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'video'
        self.router = GuidelineRouter()
        self.patches = [patch.dict(os.environ, AI_BACKEND='openrouter', OPENROUTER_MODEL=MODEL),
                        patch('src.idea.generation.CLIENT_FACTORY', return_value=self.router),
                        patch('src.idea.research.fetch_html', side_effect=source_fetch)]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
        create_project(self.project, 'Gấp giấy', brief=brief_fixture())

    def directory(self, state):
        return Path(state['generation']['path']).parent

    def test_three_phases_persist_evidence_review_and_approved_narration(self):
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        directory = self.directory(state)
        job = store.read_json(directory / 'job.json')
        self.assertEqual([call['phase'] for call in job['calls']], ['search', 'narration', 'critic', 'draft'])
        self.assertEqual(job['input']['guidelines_sha256'], rules.load_guidelines()[1])
        approved = store.read_json(directory / 'approved_narration.json')
        script = store.read_json(directory / 'script.json')['script']
        self.assertEqual(' '.join(scene['narration'] for scene in script['scenes']), approved['narration'])
        self.assertTrue(store.read_json(directory / 'critique.json')['all_passed'])
        self.assertEqual(store.read_json(directory / 'generation.json')['prompt_version'], 4)
        run_workflow(self.project, ai=True)
        self.assertEqual(len(self.router.requests), 4)

    def test_resume_after_critic_uses_snapshot_and_never_repeats_calls(self):
        with patch('src.idea.generation.editing_messages', side_effect=store.WorkflowError('TEST_STOP', 'Pause')):
            with self.assertRaisesRegex(store.WorkflowError, 'TEST_STOP'):
                run_workflow(self.project, ai=True)
        self.assertEqual(len(self.router.requests), 3)
        with patch('src.idea.generation.load_guidelines', side_effect=AssertionError('Use saved snapshot')):
            state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(len(self.router.requests), 4)
        self.assertEqual(self.router.preflights, 1)

    def test_new_guidelines_change_next_job_fingerprint(self):
        first = run_workflow(self.project, ai=True)
        old = store.read_json(self.directory(first) / 'job.json')['input']['guidelines_sha256']
        record_review(self.project, 'script', 'v001', 'request_changes', feedback='Giữ mạch kể')
        text = rules.load_guidelines()[0] + '\nQuy tắc bổ sung cho lượt mới.'
        with patch('src.idea.generation.load_guidelines', return_value=(text, rules.guideline_digest(text))):
            second = run_workflow(self.project, ai=True)
        new_job = store.read_json(self.directory(second) / 'job.json')
        self.assertNotEqual(new_job['input']['guidelines_sha256'], old)
        self.assertEqual((self.directory(second) / 'guidelines_snapshot.md').read_text(), text)

    def test_slicer_mutation_cannot_submit_even_after_repair_or_resume(self):
        complete = self.router.complete
        def mutate(payload):
            result = complete(payload)
            if 'tools' not in payload and 'scenes' in payload['response_format']['json_schema']['schema']['properties']:
                value = json.loads(result['choices'][0]['message']['content'])
                value['scenes'][1]['narration'] = value['scenes'][1]['narration'].replace('Nhưng', 'Rồi')
                result['choices'][0]['message']['content'] = json.dumps(value)
            return result
        self.router.complete = mutate
        for _ in range(2):
            with self.assertRaisesRegex(store.WorkflowError, 'từng ký tự'):
                run_workflow(self.project, ai=True)
            self.assertFalse(get_status(self.project)['artifacts'])
            self.assertEqual(len(self.router.requests), 6)

    def test_critic_failure_and_revised_candidate_are_rejudged_before_slicing(self):
        complete = self.router.complete
        rejected = []
        def reject_once(payload):
            result = complete(payload)
            if 'tools' not in payload and 'all_passed' in payload['response_format']['json_schema']['schema']['properties'] and not rejected:
                rejected.append(True)
                draft = draft_fixture()
                revised = draft['narration'].replace(LINES[3], 'Cứ thế, độ dày tăng nhanh sau những lần gấp tiếp theo.')
                result['choices'][0]['message']['content'] = json.dumps(critique_fixture(revised, False))
            return result
        self.router.complete = reject_once
        state = run_workflow(self.project, ai=True)
        directory = self.directory(state)
        job = store.read_json(directory / 'job.json')
        self.assertEqual([call['phase'] for call in job['calls']], ['search', 'narration', 'critic', 'narration_repair_1', 'draft'])
        self.assertNotEqual(store.read_json(directory / 'narration_draft.json')['narration'],
                            store.read_json(directory / 'approved_narration.json')['narration'])
        # Replay the entire pipeline from cached calls, including the rejected critique.
        from src.idea.generation import _produce
        _produce(Session(directory, job, self.router), 'Gấp giấy', brief_fixture())
        self.assertEqual(len(self.router.requests), 5)

    def test_revision_with_research_and_one_repair_uses_six_calls(self):
        run_workflow(self.project, ai=True)
        record_review(self.project, 'script', 'v001', 'request_changes', feedback='Thêm nguồn mới')
        self.router.needs_research = True
        complete = self.router.complete
        failed = []
        def break_once(payload):
            result = complete(payload)
            if 'tools' not in payload and 'claims' in payload['response_format']['json_schema']['schema']['properties'] and not failed:
                failed.append(True)
                result['choices'][0]['message']['content'] = '{bad json'
            return result
        self.router.complete = break_once
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(state['generation']['request_count'], 6)
        self.assertEqual(state['pending_reviews'][0]['version'], 'v002')

    def test_legacy_v1_v2_sessions_keep_original_phases_and_schemas(self):
        for version in (1, 2, 3):
            directory = Path(self.temp.name) / f'legacy_{version}'; directory.mkdir()
            router = GuidelineRouter() if version == 3 else FakeRouter()
            fingerprint = dict(model=MODEL, prompt_version=version)
            if version == 3:
                fingerprint['guidelines_sha256'] = rules.load_guidelines()[1]
            job = dict(job_id='legacy', input=fingerprint,
                       pricing=router.preflight(), calls=[], base_version=None)
            with patch('src.idea.research.fetch_html', side_effect=source_fetch if version == 3 else fake_fetch):
                _produce(Session(directory, job, router), 'Legacy', brief_fixture() if version == 3 else normalize_brief())
            expected = (['search', 'draft'] if version == 1 else ['search', 'narration', 'draft']
                        if version == 2 else ['search', 'narration', 'critic', 'draft'])
            self.assertEqual([call['phase'] for call in job['calls']], expected)
            self.assertEqual((directory / 'critique.json').exists(), version == 3)


if __name__ == '__main__':
    unittest.main()
