"""Offline provider and public-source fixtures; never contacts a service."""

import copy
import hashlib
import json

from src.shared.ai_config import MODEL, SEARCH_RESERVE_USD
from src.shared.openrouter_client import OpenRouterClient


SOURCE_TEXT = ('A derivative describes the instantaneous rate of change. '
               'Earth rotates on its axis, so locations face toward the Sun or away from it. ' * 8)


def valid_script():
    words = ('Bạn hãy hình dung một thay đổi quen thuộc quanh mình. Ta cùng quan sát ví dụ này thật chậm '
             'để nhận ra điều quan trọng và nối kiến thức với trải nghiệm mỗi ngày. ').split()
    narration = ' '.join((words * 8)[:140])
    parts = narration.split()
    return dict(title='Một ý tưởng qua ví dụ trực quan', main_idea='Hiểu thay đổi qua một ví dụ',
                claims=[dict(claim_id='claim_01', statement='Một phát biểu có nguồn hỗ trợ',
                             evidence=[dict(source_id='source_01', quote='A derivative describes the instantaneous rate of change.')])],
                scenes=[dict(scene_id='scene_01', narration=' '.join(parts[:70]), visual_intent='Một điểm và mũi tên', claim_refs=['claim_01']),
                        dict(scene_id='scene_02', narration=' '.join(parts[70:]), visual_intent='So sánh trước và sau', claim_refs=['claim_01'])])


def fake_fetch(url):
    return dict(url=url, final_url=url, title='Authored educational source', text=SOURCE_TEXT,
                sha256=hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(), raw_sha256='a' * 64,
                retrieved_at='2026-10-23T00:00:00+00:00', truncated=False, status='success')


class FakeRouter(OpenRouterClient):
    def __init__(self):
        self.requests = []
        self.preflights = 0
        self.failures = []
        self.invalid_drafts = 0
        self.needs_research = False
        self.missing_usage = False
        self.wrong_model = False
        self.before_complete = None

    def preflight(self, *, search=True):
        self.preflights += 1
        return dict(model=MODEL, provider='test-provider', prompt='0.0000001', completion='0.0000005',
                    input_rate='0.0000002', tiers=[], search_reserve_usd=SEARCH_RESERVE_USD,
                    checked_at='2026-10-23T00:00:00+00:00')

    def search_pricing(self):
        return dict(search_reserve_usd=SEARCH_RESERVE_USD)

    def complete(self, payload):
        self.requests.append(copy.deepcopy(payload))
        if self.before_complete:
            self.before_complete(payload)
        if self.failures:
            raise self.failures.pop(0)
        annotations = []
        if 'tools' in payload:
            text = 'Sources retrieved with the search tool.'
            prefix = 'additional' if self.needs_research else 'original'
            annotations = [dict(type='url_citation', url_citation=dict(url=f'https://university.example/{prefix}/{i}',
                           title='Educational source', content=SOURCE_TEXT[:300])) for i in range(4)]
        elif 'needs_research' in payload['response_format']['json_schema']['schema']['properties']:
            text = json.dumps(dict(needs_research=self.needs_research, reason='Compare feedback against saved evidence', search_query='educational topic'))
        else:
            if self.invalid_drafts:
                self.invalid_drafts -= 1
                text = '{bad json'
            else:
                text = json.dumps(valid_script(), ensure_ascii=False)
        usage = dict(prompt_tokens=100, completion_tokens=200, cost=0.0001)
        if 'tools' in payload:
            usage['server_tool_use_details'] = dict(web_search_requests=1, tool_calls_requested=1, tool_calls_executed=1)
        if self.missing_usage:
            usage.pop('cost')
        return dict(id=f'fake-response-{len(self.requests)}', model='other/model' if self.wrong_model else MODEL,
                    usage=usage, choices=[dict(finish_reason='stop', message=dict(content=text, annotations=annotations))])
