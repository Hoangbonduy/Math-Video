"""Regression tests for the approved global and per-scene visual format."""

import copy
import unittest

from src.idea.content import (VISUAL_SCRIPT_SCHEMA, check_schema, markdown, validate_script)
from src.idea.test_guideline_pipeline import (script_fixture, visual_script_fixture,
                                              brief_fixture, sources)
from src.idea.visuals import validate_visuals, scene_sections
from src.shared._workflow_store import WorkflowError


class VisualFormatTests(unittest.TestCase):
    def test_new_schema_requires_globals_but_old_script_still_validates(self):
        legacy = script_fixture()
        validate_script(legacy, sources(), brief_fixture())
        with self.assertRaises(WorkflowError):
            check_schema(legacy, VISUAL_SCRIPT_SCHEMA)
        script = visual_script_fixture(legacy)
        check_schema(script, VISUAL_SCRIPT_SCHEMA)
        validate_script(script, sources(), brief_fixture())

    def test_invalid_sections_inheritance_events_and_criteria_are_rejected(self):
        script = visual_script_fixture(script_fixture())
        for old, new in (
            ('1. Kế thừa & thay đổi', 'Kế thừa'),
            ('Kế thừa: Khởi tạo theo khai báo toàn cục:', 'Kế thừa: Tự tạo:'),
            ('Thay đổi: Giữ nguyên bố cục, cập nhật nhãn theo lời đọc.', 'Thay đổi:'),
            (' → paper → ', ' →  → '),
            (' → Giữ rõ nhãn và giấy.', ''),
            ('Kết cảnh: paper ở giữa, giữ giá trị bộ đếm hiện tại.', 'Kết cảnh:'),
            ('- Hiện thông tin đúng thứ tự lời đọc, không tiết lộ đáp án.', ''),
        ):
            candidate = copy.deepcopy(script)
            candidate['scenes'][0]['visual_intent'] = candidate['scenes'][0]['visual_intent'].replace(old, new)
            with self.subTest(old=old), self.assertRaises(WorkflowError):
                validate_visuals(candidate)
        candidate = copy.deepcopy(script)
        candidate['scenes'][1]['visual_intent'] = candidate['scenes'][1]['visual_intent'].replace(
            'Trạng thái cuối s1:', 'Trạng thái cuối s10:')
        with self.assertRaises(WorkflowError):
            validate_visuals(candidate)

    def test_literal_arrow_in_caption_is_preserved(self):
        from src.idea.visuals import timeline_cells
        row = '- 0s → counter → tăng từ 0 lên 1 → “Bộ đếm: 0 → 1” → Hiện 1'
        cells = timeline_cells(row)
        self.assertEqual(len(cells), 5)
        self.assertEqual(cells[3], '“Bộ đếm: 0 → 1”')

    def test_markdown_globals_once_before_narration_and_three_sections_per_scene(self):
        script = visual_script_fixture(script_fixture())
        duration = validate_script(script, sources(), brief_fixture())
        text = markdown(script, sources(), duration)
        self.assertEqual(text.count('## Khai báo toàn cục'), 1)
        self.assertLess(text.index('## Khai báo toàn cục'), text.index('## Lời đọc'))
        for heading in ('1. Kế thừa & thay đổi', '2. Timeline hình ảnh', '3. Tiêu chí đạt'):
            self.assertEqual(text.count('#### ' + heading), len(script['scenes']))
        self.assertIn('Kết cảnh:', text)
        self.assertEqual(len(scene_sections(script['scenes'][0]['visual_intent'])), 3)


if __name__ == '__main__':
    unittest.main()
