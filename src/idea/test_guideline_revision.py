"""Regression coverage for phase-specific Axiora guidelines and independent repairs."""

import json
import unittest

from src.idea import guidelines as rules
from src.idea.content import narration_budget, validate_script, writing_messages, markdown
from src.idea.generation import MAX_PIPELINE_REQUESTS, _produce
from src.idea.narration import (CRITIQUE_RULES, CRITIQUE_SCHEMA, critic_messages,
    narration_messages, validate_editorial, validate_critique)
from src.idea.service import apply_idea
import src.idea.test_guideline_pipeline as fixture_support
from src.idea.test_guideline_pipeline import (GOAL, QUESTION, LINES,
    brief_fixture, draft_fixture, script_fixture, sources, critique_fixture)
from src.shared import _workflow_store as store
from src.shared.ai_config import normalize_brief
from src.shared.job_session import Session
from src.shared.review import record_review
from src.shared.workflow import get_status, run_workflow


class PhaseSpecificChecks(unittest.TestCase):
    def test_all_prompts_share_the_same_pause_aware_budget(self):
        for minimum, maximum, expected in ((30, 60, 75), (45, 60, 81), (30, 35, 75)):
            brief = normalize_brief(dict(min_seconds=minimum, max_seconds=maximum))
            budget = narration_budget(brief)
            self.assertEqual(budget, dict(min_units=expected, max_units=int(maximum * 3.5)))
            for messages in (writing_messages('Giấy', brief, sources()),
                             narration_messages('Giấy', brief, sources()),
                             critic_messages('Giấy', brief, draft_fixture())):
                self.assertEqual(json.loads(messages[1]['content'])['narration_budget'], budget)

    def test_95_units_pass_default_and_45_second_briefs_without_padding(self):
        for minimum in (30, 45):
            script = script_fixture()
            script['scenes'] = script['scenes'][:1]
            script['scenes'][0]['narration'] = ' '.join(['tiếng'] * 95)
            brief = normalize_brief(dict(min_seconds=minimum, max_seconds=60))
            duration = validate_script(script, sources(), brief)
            self.assertEqual(duration['whitespace_units'], 95)
            self.assertAlmostEqual(duration['estimated_seconds'], 95 / 3.5)
            self.assertAlmostEqual(duration['visual_pause_seconds'], minimum - 95 / 3.5)
            self.assertEqual(duration['estimated_video_seconds'], minimum)

    def test_minimum_and_maximum_are_still_enforced(self):
        script = script_fixture()
        script['scenes'] = script['scenes'][:1]
        for count in (74, 211):
            script['scenes'][0]['narration'] = ' '.join(['tiếng'] * count)
            with self.assertRaisesRegex(store.WorkflowError, 'narration budget'):
                validate_script(script, sources(), brief_fixture())

    def test_user_mathematical_premise_can_cite_the_original_idea(self):
        brief = brief_fixture()
        script = script_fixture()
        script['claims'].append(dict(claim_id='user_milestone', statement='Mốc toán học người dùng cung cấp',
            evidence=[dict(source_id='user_idea', quote='Payoff: Vượt Mặt Trăng.')]))
        script['scenes'][-1]['claim_refs'].append('user_milestone')
        duration = validate_script(script, sources(), brief)
        exported = markdown(script, sources(), duration)
        self.assertIn('user_idea: Dữ kiện toán học do người dùng', exported)
        context = json.loads(writing_messages('Giấy', brief, sources())[1]['content'])
        self.assertIn('Payoff: Vượt Mặt Trăng.', context['user_idea_source']['text'])
        self.assertNotIn('user_idea', [source['source_id'] for source in context['sources']])

    def test_user_provenance_does_not_allow_fabricated_quotes(self):
        script = script_fixture()
        script['claims'][0]['evidence'] = [dict(source_id='user_idea', quote='Một mốc không có trong ý tưởng')]
        with self.assertRaisesRegex(store.WorkflowError, 'verbatim'):
            validate_script(script, sources(), brief_fixture())
        script['claims'][0]['evidence'][0]['quote'] = 'Payoff: Vượt Mặt Trăng.'
        with self.assertRaisesRegex(store.WorkflowError, 'verbatim'):
            validate_script(script, sources(), normalize_brief())

    def test_interactive_question_does_not_require_a_news_source(self):
        script = script_fixture()
        script['scenes'][-1]['claim_refs'] = []
        validate_script(script, sources(), brief_fixture())
        validate_editorial(script, draft_fixture()['narration'], brief_fixture())
        prompt = writing_messages('Giấy', brief_fixture(), sources())[0]['content']
        self.assertIn('Câu hỏi tương tác không cần trích dẫn nguồn', prompt)

    def test_critic_rubric_and_schema_exclude_phase_two(self):
        self.assertEqual(len(CRITIQUE_RULES), 17)
        self.assertNotIn('rule_13', CRITIQUE_RULES)
        enum = CRITIQUE_SCHEMA['properties']['checks']['items']['properties']['rule']['enum']
        self.assertNotIn('rule_13', enum)
        for messages in (critic_messages('Giấy', brief_fixture(), draft_fixture()),
                         narration_messages('Giấy', brief_fixture(), sources())):
            self.assertNotIn('## PHẦN B', messages[0]['content'])
            self.assertNotIn('- [Pha 2]', messages[0]['content'])
        draft = draft_fixture()
        critique = critique_fixture(draft['narration'])
        critique['checks'][0]['rule'] = 'rule_13'
        with self.assertRaisesRegex(store.WorkflowError, 'không có rule_13'):
            validate_critique(critique, draft, sources(), brief_fixture())

    def test_closing_visual_requires_display_text_and_motion(self):
        for visual in (QUESTION, 'Hiện chữ: ' + QUESTION,
                       'Hiện chữ: ' + QUESTION + ' Hình tiếp tục chuyển động.',
                       'Hiện chữ: ' + QUESTION + ' Hình tĩnh trong lúc đọc câu chốt.',
                       'Hiện chữ: ' + QUESTION + ' Không chuyển động khi đọc câu chốt.'):
            script = script_fixture(); script['scenes'][-1]['visual_intent'] = visual
            with self.subTest(visual=visual), self.assertRaisesRegex(store.WorkflowError, 'Quy tắc 13'):
                validate_editorial(script, draft_fixture()['narration'], brief_fixture())
        script = script_fixture()
        script['scenes'][-1]['visual_intent'] = 'Hiện chữ: ' + QUESTION + ' Quả cầu vẫn xoay khi đọc câu chốt.'
        validate_editorial(script, draft_fixture()['narration'], brief_fixture())

    def test_no_question_ending_still_requires_motion_at_closing(self):
        script = script_fixture(False)
        script['scenes'][-1]['visual_intent'] = 'Hình tĩnh.'
        with self.assertRaisesRegex(store.WorkflowError, 'chuyển động'):
            validate_editorial(script, brief=brief_fixture(False))
        script['scenes'][-1]['visual_intent'] = 'Mô hình vẫn xoay khi đọc câu chốt.'
        validate_editorial(script, brief=brief_fixture(False))

    def test_edited_guideline_is_the_canonical_path(self):
        self.assertEqual(rules.GUIDELINE_PATH.parts[-2:], ('guidelines', 'guidelines.md'))


class PhaseRepairTests(unittest.TestCase):
    # Reuse the isolated filesystem/provider setup, without inheriting duplicate tests.
    setUp = fixture_support.GuidelinePipeline.setUp
    directory = fixture_support.GuidelinePipeline.directory

    def install_failures(self, *, narration=0, critic=0, draft=0):
        remaining = dict(narration=narration, critic=critic, draft=draft)
        complete = self.router.complete
        def corrupt(payload):
            result = complete(payload)
            if 'tools' not in payload:
                schema = payload['response_format']['json_schema']['schema']['properties']
                phase = ('critic' if 'all_passed' in schema else 'draft' if 'scenes' in schema else
                         'narration' if 'claims' in schema else None)
                if phase and remaining[phase]:
                    remaining[phase] -= 1
                    result['choices'][0]['message']['content'] = '{bad json'
            return result
        self.router.complete = corrupt

    def test_short_narration_passes_45_to_60_video_duration(self):
        apply_idea(self.project, dict(topic='Gấp giấy', goal=GOAL, duration='45–60 giây'))
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        duration = store.read_json(self.directory(state) / 'script.json')['duration_estimate']
        self.assertLess(duration['estimated_seconds'], 45)
        self.assertGreater(duration['visual_pause_seconds'], 0)
        self.assertEqual(duration['estimated_video_seconds'], 45)

    def test_search_receives_all_user_milestones(self):
        goal = GOAL.replace('Twist + Câu hỏi cuối:', 'Twist + Câu hỏi cuối: Mốc 103 lần;')
        apply_idea(self.project, dict(topic='Gấp giấy', goal=goal, duration='30–60 giây'))
        run_workflow(self.project, ai=True)
        context = json.loads(self.router.requests[0]['messages'][1]['content'])
        self.assertIn('103', context['requirements'])
        self.assertIn('Landmark 81', context['requirements'])
        self.assertIn('Fansipan', context['requirements'])

    def test_both_phases_receive_two_independent_repairs_and_replay_safely(self):
        self.install_failures(narration=2, draft=2)
        state = run_workflow(self.project, ai=True)
        directory = self.directory(state)
        job = store.read_json(directory / 'job.json')
        self.assertEqual([call['phase'] for call in job['calls']], [
            'search', 'narration', 'narration_repair_1', 'narration_repair_2', 'critic',
            'draft', 'draft_repair_1', 'draft_repair_2'])
        self.assertEqual(job['max_requests'], MAX_PIPELINE_REQUESTS)
        self.assertEqual(len(self.router.requests), 8)
        _produce(Session(directory, job, self.router, max_requests=job['max_requests']),
                 'Gấp giấy', brief_fixture())
        self.assertEqual(len(self.router.requests), 8)

    def test_revision_can_use_both_full_quotas_within_nine_requests(self):
        run_workflow(self.project, ai=True)
        record_review(self.project, 'script', 'v001', 'request_changes', feedback='Bổ sung nguồn')
        self.router.needs_research = True
        self.install_failures(narration=2, draft=2)
        state = run_workflow(self.project, ai=True)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertEqual(state['generation']['request_count'], 9)
        self.assertEqual(state['generation']['max_requests'], 9)

    def test_narration_and_critic_share_only_the_phase_one_quota(self):
        self.install_failures(narration=1, critic=1, draft=2)
        state = run_workflow(self.project, ai=True)
        job = store.read_json(self.directory(state) / 'job.json')
        phases = [call['phase'] for call in job['calls']]
        self.assertIn('narration_repair_1', phases)
        self.assertIn('narration_repair_2', phases)
        self.assertIn('draft_repair_1', phases)
        self.assertIn('draft_repair_2', phases)
        self.assertEqual(len(phases), 8)

    def test_more_than_two_repairs_in_one_phase_stops_without_repeating_calls(self):
        for phase in ('narration', 'draft'):
            with self.subTest(phase=phase):
                # Each subcase gets a new idea cycle and distinct job ledger.
                apply_idea(self.project, dict(topic='Gấp giấy ' + phase, goal=GOAL, duration='30–60 giây'), force_new=True)
                self.install_failures(**{phase: 3})
                for _ in range(2):
                    with self.assertRaises(store.WorkflowError):
                        run_workflow(self.project, ai=True)
                    state = get_status(self.project)
                    job = store.read_json(self.directory(state) / 'job.json')
                    relevant = [call['phase'] for call in job['calls'] if call['phase'].startswith(phase)]
                    self.assertEqual(relevant, [phase, phase + '_repair_1', phase + '_repair_2'])
                    self.assertFalse(state['pending_reviews'])


if __name__ == '__main__':
    unittest.main()
