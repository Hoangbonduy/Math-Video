"""Render one deterministic new-format handoff through the real v3 pipeline."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.scene_design import scene_code_pipeline as pipeline, scene_generation as sg
from src.scene_design.script_visuals import visual_handoff
from src.shared import _workflow_store as store
from src.shared.job_session import Session
from tests.test_scene_code_pipeline import CodeRouter
from tests.scene_v2_support import VisionRouter


class VisualHandoffRenderTests(unittest.TestCase):
    def test_new_format_reaches_real_render_and_original_brief_review(self):
        narration = 'Một điểm chuyển động từ trái sang phải.'
        script = dict(global_visuals=dict(
            objects='point: một chấm tròn ở (-1,0); title: chữ “Chuyển động” ở (0,5).',
            layout='Nhìn chính diện, nền tối, chữ trắng và chấm xanh.',
            math='Tọa độ Manim, một đơn vị là một đơn vị cảnh; t là giây cục bộ.',
            continuity='Giữ ID point và title, không nhân bản hoặc reset chấm.'),
            scenes=[dict(scene_id='s1', narration=narration, visual_intent=(
                '1. Kế thừa & thay đổi\nKế thừa: Khởi tạo theo khai báo toàn cục.'
                '\nThay đổi: Giữ nguyên cấu tạo, dịch chuyển point.'
                '\n2. Timeline hình ảnh'
                '\n- 0–2s → point → Trượt từ (-1,0) tới (1,0) trong 2 giây → Không có → point ở bên phải'
                '\n- 2–3s → point và title → Giữ nguyên → “Chuyển động” → Kết thúc ở 3 giây'
                '\nKết cảnh: point ở (1,0), title ở (0,5), chữ “Chuyển động”.'
                '\n3. Tiêu chí đạt'
                '\n- Điểm di chuyển sang phải và luôn nằm trong vùng an toàn.'
                '\n- Chữ “Chuyển động” hiện rõ trong suốt cảnh.'))])
        handoff = visual_handoff(script, 's1')
        class Router(CodeRouter):
            def complete(self, payload):
                result = super().complete(payload)
                if 'beats' in payload['response_format']['json_schema']['schema']['properties']:
                    plan = json.loads(result['choices'][0]['message']['content'])
                    plan['scene_id'] = 's1'
                    plan['beats'][0]['criteria'] = handoff['scene']['acceptance_criteria']
                    result['choices'][0]['message']['content'] = json.dumps(plan)
                return result
        router, vision = Router(), VisionRouter()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'generation' / 'handoff'
            directory.mkdir(parents=True)
            job = dict(job_id='handoff', input=dict(stage='scene_design',max_repairs=0),
                       calls=[],status='working',pricing=router.preflight())
            session = Session(directory,job,router,None,budget_usd=.5,other_used=0)
            context = dict(script=script,scene=script['scenes'][0],brief={},
                           config=copy.deepcopy(sg.DEFAULT_CONFIG),target_seconds=3)
            with patch('src.scene_design.scene_vision.CLIENT_FACTORY',return_value=vision), patch(
                    'src.scene_design.scene_vision.review_model',return_value=vision.model):
                path, extra = pipeline.generate(root,directory,session,context)
            self.assertTrue(extra['quality_passed'])
            self.assertEqual(store.read_json(directory/'visual_handoff.json'), handoff)
            self.assertEqual(extra['manim_render']['video']['fps'], 30)
            review = json.loads(vision.requests[0]['messages'][1]['content'][0]['text'])
            self.assertEqual(review['visual_handoff'], handoff)
            for payload in router.requests:
                self.assertEqual(json.loads(payload['messages'][1]['content'])['visual_handoff'], handoff)
            self.assertTrue(path.is_file())


if __name__ == '__main__':
    unittest.main()
