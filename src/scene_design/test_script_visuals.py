"""Idea visual format must reach planning, codegen and visual review."""

import copy
import json
from pathlib import Path
import unittest

from src.idea.test_guideline_pipeline import script_fixture, visual_script_fixture
from src.scene_design import scene_generation, scene_pipeline, scene_code_pipeline, scene_vision
from src.scene_design.script_visuals import visual_handoff, validate_plan_criteria
from src.shared._workflow_store import WorkflowError


class ScriptVisualHandoffTests(unittest.TestCase):
    def setUp(self):
        self.script = visual_script_fixture(script_fixture())
        self.context = dict(script=self.script, scene=self.script['scenes'][1], target_seconds=5, assets=dict(assets=[]))

    def plan(self):
        scene = self.context['scene']
        handoff = visual_handoff(self.script, scene['scene_id'])
        return dict(scene_id=scene['scene_id'], narration=scene['narration'], duration=5,
                    goal='Giữ trạng thái và cập nhật nhãn.', visual_strategy='Kế thừa đúng giấy.',
                    beats=[dict(id='beat_01',start=0,end=5,narration_excerpt=scene['narration'],
                                show='paper ở giữa',motion='Giữ bố cục',understand='Độ dày tăng',
                                criteria=handoff['scene']['acceptance_criteria'],required_capabilities=[])],
                    objects=[dict(id='paper',role='shape')],recipe_ids=['explanatory_motion'],
                    required_apis=['Dot'])

    def test_first_scene_and_later_scene_get_the_correct_intended_state(self):
        first = visual_handoff(self.script, 's1')
        self.assertIsNone(first['previous_scene'])
        self.assertEqual(first['global_visuals'], self.script['global_visuals'])
        later = visual_handoff(self.script, 's2')
        self.assertEqual(later['previous_scene']['scene_id'], 's1')
        self.assertEqual(later['previous_scene']['end_state'], first['scene']['end_state'])
        self.assertEqual(later['scene']['timeline'][0]['cue'], 'Khi bắt đầu lời đọc')
        self.assertEqual(later['scene']['timeline'][0]['target'], 'paper')
        self.assertIn('Hiện 0,1 mm', later['scene']['timeline'][0]['text_and_emphasis'])
        later['global_visuals']['objects'] = 'changed'
        self.assertEqual(first['global_visuals'], self.script['global_visuals'])

    def test_all_prompt_entrypoints_receive_structured_brief_and_rules(self):
        for build in (scene_generation._messages, scene_pipeline._messages):
            messages = build('Plan or implement.', self.context)
            payload = json.loads(messages[1]['content'])
            self.assertEqual(payload['visual_handoff'], visual_handoff(self.script, 's2'))
            self.assertIn('LOCAL seconds', messages[0]['content'])
            self.assertIn('verbatim into beats.criteria', messages[0]['content'])
            self.assertNotIn('visual_handoff', self.context)

    def test_direct_plan_cannot_drop_approved_criteria(self):
        plan = self.plan()
        scene_code_pipeline.validate_plan(plan, self.context)
        broken = copy.deepcopy(plan)
        broken['beats'][0]['criteria'] = ['Chỉ kiểm tra màu sắc.']
        with self.assertRaisesRegex(WorkflowError, 'bỏ tiêu chí'):
            scene_code_pipeline.validate_plan(broken, self.context)
        with self.assertRaises(WorkflowError):
            validate_plan_criteria(broken, self.context)

    def test_v3_planner_and_codegen_requests_contain_original_handoff(self):
        outer = self
        class Session:
            requests = []
            def call(self, phase, messages, schema):
                self.requests.append((phase, messages))
                result = outer.plan() if 'plan' in phase else dict(
                    source='from manim import *\nclass GeneratedScene(Scene):\n    def construct(self):\n        self.wait(5)\n',
                    requires_design_change=False, reason='')
                from src.shared.ai_config import get_model
                return dict(model=get_model(),choices=[dict(finish_reason='stop',message=dict(content=json.dumps(result)))])
        session = Session()
        plan = scene_code_pipeline._plan(session, self.context, 0, None, [])
        refs = dict(component_apis={})
        scene_code_pipeline._code(session, self.context, plan, refs, 0, None, [])
        self.assertEqual(len(session.requests), 2)
        for phase, messages in session.requests:
            self.assertEqual(json.loads(messages[1]['content'])['visual_handoff']['previous_scene']['scene_id'], 's1')

    def test_review_receives_global_rules_and_original_visual_criteria(self):
        handoff = visual_handoff(self.script, 's2')
        messages = scene_vision.messages(Path('.'), self.plan(), dict(schema_version=3),
                                        dict(frames=[],contact_sheets=[]), {}, visual_handoff=handoff)
        payload = json.loads(messages[1]['content'][0]['text'])
        self.assertEqual(payload['visual_handoff'], handoff)
        self.assertIn('original approved visual_handoff', messages[0]['content'])

    def test_legacy_scripts_are_read_without_inventing_a_handoff(self):
        self.context['script'] = script_fixture()
        self.assertIsNone(visual_handoff(self.context['script'], 's1'))
        messages = scene_pipeline._messages('Legacy plan.', self.context)
        self.assertNotIn('visual_handoff', json.loads(messages[1]['content']))
        self.assertNotIn('APPROVED VISUAL HANDOFF', messages[0]['content'])

    def test_malformed_new_format_fails_before_model_calls(self):
        self.script['scenes'][0]['visual_intent'] = 'Mô tả thiếu cấu trúc'
        with self.assertRaises(WorkflowError):
            scene_pipeline._messages('Plan.', self.context)


if __name__ == '__main__':
    unittest.main()
