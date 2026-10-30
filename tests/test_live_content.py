"""Opt-in paid acceptance. Human approval/reading is deliberately not simulated."""

import json
import os
from pathlib import Path
import unittest

from src.shared import _workflow_store as store
from src.idea.content import validate_script
from src.shared.review import record_review
from src.shared.workflow import create_project, get_status, run_workflow


ENABLED = os.environ.get('RUN_LIVE_AI_TESTS') == '1' and bool(os.environ.get('OPENROUTER_API_KEY'))


@unittest.skipUnless(ENABLED, 'Paid live acceptance requires RUN_LIVE_AI_TESTS=1 and OPENROUTER_API_KEY')
class LiveContentTests(unittest.TestCase):
    def test_two_topics_revision_and_restart(self):
        root = Path(os.environ.get('LIVE_AI_PROJECT_ROOT', 'data/projects/checkpoint_4_live')).resolve()
        outcomes = []
        for folder, topic in (('dao_ham', 'Đạo hàm giúp hiểu tốc độ thay đổi tức thời như thế nào?'),
                              ('ngay_dem', 'Vì sao có ngày và đêm?')):
            directory = root / folder
            if not (directory / 'project.json').exists():
                create_project(directory, topic)
            self.assertEqual(get_status(directory)['topic'], topic, 'Use a different output root; never replace another project')
            state = run_workflow(directory, ai=True)
            self.assertEqual(state['stage'], 'script', 'Human approval must be performed separately')
            if folder == 'dao_ham':
                first = next(item for item in state['artifacts'] if item['version'] == 'v001')
                if state['pending_reviews'][0]['version'] == 'v001':
                    record_review(directory, 'script', 'v001', 'request_changes',
                                  feedback='Giữ đúng kiến thức và ví dụ, dùng câu ngắn hơn cho người chưa biết đạo hàm.')
                state = run_workflow(directory, ai=True)
                self.assertEqual(state['pending_reviews'][0]['version'], 'v002')
                self.assertEqual(store.digest(Path(first['path'])), first['sha256'])
            snapshot = {name:(directory / name).read_bytes() for name in ('project.json','reviews.json')}
            again = run_workflow(directory, ai=True)
            self.assertEqual(again, state)
            self.assertEqual(snapshot, {name:(directory / name).read_bytes() for name in snapshot})
            item = state['pending_reviews'][0]
            data = store.read_json(directory / item['bundle']['script.json']['path'])
            research = store.read_json(directory / item['bundle']['research.json']['path'])
            validate_script(data['script'], research['sources'], data['brief'])
            self.assertLessEqual(state['generation']['budget_used_usd'], 0.1)
            self.assertEqual(state['integrity_issues'], [])
            outcomes.append(dict(topic=topic, project_dir=str(directory), version=item['version'],
                                 generation=state['generation'], human_read_check_pending=True,
                                 script_path=item['path'], awaiting_review=True))
        root.mkdir(parents=True, exist_ok=True)
        store.atomic_write(root / 'acceptance.json', dict(live=True, outcomes=outcomes,
                                                         human_review_required=True, auto_approved=False))
