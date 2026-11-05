"""Regression tests for optional endings in the revised Axiora guideline."""
import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.idea.guidelines import parse_idea_goal, load_guidelines, guideline_digest
from src.idea.narration import check_continuous_narration_rules, validate_editorial, validate_narration
from src.idea.generation import _produce
from src.shared import _workflow_store as store
from src.shared.job_session import Session
from src.shared.ai_config import MODEL
from src.idea.test_guideline_pipeline import (GOAL, QUESTION, LINES, brief_fixture,
    draft_fixture, script_fixture, sources, GuidelineRouter, source_fetch)


class RevisedEndings(unittest.TestCase):
    def brief(self, ending):
        return dict(brief_fixture(), requirements='', goal_raw='\n'.join(GOAL.splitlines()[:4]) + '\n' + ending)

    def test_original_project_question_before_multisentence_closing(self):
        brief = self.brief('5. Twist: Số nhỏ vẫn chia được. Vậy 1 chia 0,0000001 bằng bao nhiêu?\n'
                           '6. Câu chốt: Comment đáp án nhé. Đăng ký kênh Axiora để biết thêm chi tiết.')
        text = ('Bấm một chia không, sao máy tính báo lỗi ngay? '
                'Chia cho số dương càng gần không, kết quả càng lớn. '
                'Cứ thế, kết quả vượt Landmark 81, rồi đến Fansipan. '
                'Nhưng từ phía âm, kết quả chạy theo chiều ngược lại. '
                'Tại không, phép chia không có kết quả xác định. '
                'Số nhỏ vẫn chia được. Vậy 1 chia 0,0000001 bằng bao nhiêu? '
                'Comment đáp án nhé. Đăng ký kênh Axiora để biết thêm chi tiết.')
        draft = dict(draft_fixture(), narration=text)
        validate_narration(draft, sources(), brief)
        for broken in (text.replace('Comment đáp án nhé. ', ''), text + ' Hết.',
                       text.replace('Vậy 1 chia 0,0000001 bằng bao nhiêu? ', '')):
            self.assertTrue(check_continuous_narration_rules(broken, brief))

    def test_optional_lines_are_independent_including_numbered_gaps(self):
        body = ' '.join(LINES[:7])
        for ending, suffix in (
            ('', ''),
            ('5. Câu hỏi: ' + QUESTION, ' ' + QUESTION),
            ('6. Câu chốt: Hẹn gặp bạn nhé.', ' Hẹn gặp bạn nhé.'),
            ('5. Câu hỏi: ' + QUESTION + '\n6. Câu chốt: Hẹn gặp bạn nhé.',
             ' ' + QUESTION + ' Hẹn gặp bạn nhé.'),
            ('5.\n6. Câu chốt: Hẹn gặp bạn nhé.', ' Hẹn gặp bạn nhé.'),
        ):
            with self.subTest(ending=ending):
                self.assertEqual(check_continuous_narration_rules(body + suffix, self.brief(ending)), [])
        goal = parse_idea_goal('1. Hook: Vì sao?\n6. Câu chốt: Hẹn gặp bạn nhé.')
        self.assertFalse(goal['has_line5'])
        self.assertTrue(goal['has_line6'])
        self.assertEqual(goal['closing_text'], 'Hẹn gặp bạn nhé.')

    def test_line5_without_complete_question_requires_generated_question(self):
        brief = self.brief('5. Twist: Ngoài đời giấy sẽ kẹt sau vài lần gấp.')
        body = ' '.join(LINES[:8])
        self.assertEqual(check_continuous_narration_rules(body + ' ' + QUESTION, brief), [])
        self.assertTrue(check_continuous_narration_rules(body, brief))

    def test_question_has_no_special_word_limit(self):
        question = ' '.join(['tiếng'] * 22) + '?'
        text = ' '.join(LINES[:7]) + ' ' + question
        self.assertEqual(check_continuous_narration_rules(text, self.brief('5. Câu hỏi: ' + question)), [])

    def test_detailed_numbers_and_generic_landmarks_are_allowed(self):
        text = draft_fixture()['narration'].replace(LINES[3], 'Nó vượt những đỉnh núi, từ 1.000 tới 10.000 mét.')
        self.assertEqual(check_continuous_narration_rules(text, brief_fixture()), [])

    def test_decimal_period_question_and_no_default_closing(self):
        goal = parse_idea_goal('1. Hook: Vì sao?\n5. Câu hỏi: Vậy 1 chia 0.0000001 bằng bao nhiêu?')
        self.assertEqual(goal['final_question'], 'Vậy 1 chia 0.0000001 bằng bao nhiêu?')
        self.assertEqual(goal['line6'], '')

    def test_final_scene_allows_more_than_four_sentences(self):
        closing = 'Cùng bình luận nhé. Bạn đoán được chứ. Hẹn gặp bạn. Cảm ơn đã xem.'
        brief = self.brief('5. Twist: ' + LINES[-3] + ' ' + QUESTION + '\n6. Câu chốt: ' + closing)
        script = script_fixture()
        script['scenes'][-1]['narration'] = ' '.join([LINES[-3], QUESTION, closing])
        validate_editorial(script, brief=brief)
        script['scenes'][-1]['narration'] = ' '.join([LINES[-3], QUESTION] + closing.split('. ')[:1])
        with self.assertRaises(store.WorkflowError):
            validate_editorial(script, brief=brief)

    def test_no_ending_needs_neither_closing_motion_nor_question_visual(self):
        script = script_fixture()
        script['scenes'] = script['scenes'][:7]
        for scene in script['scenes']:
            scene['visual_intent'] = 'Hiện phép tính.'
        validate_editorial(script, brief=self.brief(''))

    def test_generated_question_must_be_displayed_without_answer(self):
        brief = self.brief('5. Twist: ' + LINES[-3])
        script = script_fixture()
        script['scenes'][-1]['narration'] = LINES[-3] + ' ' + QUESTION
        validate_editorial(script, brief=brief)
        script['scenes'][-1]['visual_intent'] = 'Hiện chữ: ' + QUESTION
        with self.assertRaisesRegex(store.WorkflowError, 'không hiển thị đáp án'):
            validate_editorial(script, brief=brief)

    def test_answer_operand_in_question_is_not_a_reveal(self):
        brief = self.brief('5. Câu hỏi: 1 chia 1 bằng bao nhiêu? (đáp án: 1)')
        body = ' '.join(LINES[:7])
        self.assertEqual(check_continuous_narration_rules(body + ' 1 chia 1 bằng bao nhiêu?', brief), [])
        self.assertTrue(check_continuous_narration_rules(body + ' Đáp án là 1. 1 chia 1 bằng bao nhiêu?', brief))

    def test_line5_question_can_be_followed_by_supplied_comment_prompt(self):
        question = 'Vậy nếu cứ cắt kiểu này mãi, cuối cùng thanh socola sẽ ra sao?'
        brief = self.brief('5. Twist/Câu hỏi cuối: ' + question + ' Comment đáp án nhé.\n'
                           '6. Câu chốt: Đăng ký kênh Axiora để biết thêm chi tiết.')
        text = ' '.join(LINES[:7]) + ' ' + question + ' Comment đáp án nhé. ' + LINES[-1]
        self.assertEqual(parse_idea_goal(brief['goal_raw'])['question_followup'], 'Comment đáp án nhé.')
        self.assertEqual(check_continuous_narration_rules(text, brief), [])
        script = script_fixture()
        script['scenes'][-1].update(narration=question + ' Comment đáp án nhé. ' + LINES[-1],
            visual_intent='Hiện chữ: ' + question + ' Không hiển thị đáp án. Hình vẫn xoay khi đọc câu chốt.')
        validate_editorial(script, brief=brief)
        for broken in (text.replace(question, 'Thanh socola sẽ thế nào?'),
                       text.replace(question + ' ', ''),
                       text.replace('Comment đáp án nhé.', 'Đáp án là thanh sẽ biến mất.')):
            self.assertTrue(check_continuous_narration_rules(broken, brief))

    def test_line5_followup_without_line6_and_multiple_followup_sentences(self):
        followup = 'Comment đáp án nhé. Cùng thử đoán xem.'
        brief = self.brief('5. Câu hỏi: ' + QUESTION + ' ' + followup)
        text = ' '.join(LINES[:7]) + ' ' + QUESTION + ' ' + followup
        self.assertEqual(check_continuous_narration_rules(text, brief), [])
        self.assertEqual(check_continuous_narration_rules(' '.join(LINES[:7]) + ' ' + QUESTION, brief), [])

    def test_question_without_supplied_followup_rejects_inserted_answer(self):
        text = draft_fixture()['narration'].replace(QUESTION, QUESTION + ' Đáp án là một lần.')
        self.assertTrue(check_continuous_narration_rules(text, brief_fixture()))

    def test_spoken_length_policy_comes_from_current_guideline(self):
        from src.idea.narration import critic_messages, narration_messages, validate_critique
        from src.idea.test_guideline_pipeline import critique_fixture
        draft = draft_fixture()
        draft['narration'] = draft['narration'].replace(LINES[3], 'Khi 1 chia 0,0000001, kết quả tăng lên rất nhanh.')
        for messages in (narration_messages('Giấy', brief_fixture(), sources()),
                         critic_messages('Giấy', brief_fixture(), draft)):
            prompt = messages[0]['content']
            self.assertIn('chuyển chữ số và phép tính thành lời', prompt)
            self.assertNotIn('8–14 tiếng', prompt)
        # Even when local structural checks pass, rule 5 still requires approval.
        critique = critique_fixture(draft['narration'])
        critique['all_passed'] = False
        for check in critique['checks']:
            if check['rule'] == 'rule_5':
                check.update(passed=False, reason='Lời đọc sau khi mở rộng phép tính vượt mức tối đa của guideline')
        with self.assertRaisesRegex(store.WorkflowError, 'Critic chưa duyệt: rule_5'):
            validate_critique(critique, draft, sources(), brief_fixture())

    def test_rule6_can_reject_empty_lead_in_without_phrase_blacklist(self):
        from src.idea.narration import validate_critique
        from src.idea.test_guideline_pipeline import critique_fixture
        draft = draft_fixture()
        critique = critique_fixture(draft['narration'])
        critique['all_passed'] = False
        for check in critique['checks']:
            if check['rule'] == 'rule_6':
                check.update(passed=False, reason='Câu dẫn lặp ý, không phục vụ mạch kể')
        with self.assertRaisesRegex(store.WorkflowError, 'Critic chưa duyệt: rule_6'):
            validate_critique(critique, draft, sources(), brief_fixture())

    def test_outdated_job_is_rejected_before_any_request(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            router = GuidelineRouter()
            text, digest = load_guidelines()
            for version, saved_digest in ((4, digest), (5, 'outdated')):
                job = dict(input=dict(prompt_version=version, guidelines_sha256=saved_digest), calls=[])
                with self.assertRaisesRegex(store.WorkflowError, 'GUIDELINE_CHANGED'):
                    _produce(Session(directory, job, router), 'Giấy', brief_fixture())
            self.assertEqual(router.requests, [])


if __name__ == '__main__':
    unittest.main()
