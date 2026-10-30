"""Script schema, evidence, estimates and bounded source access."""

import copy
import hashlib
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.shared.ai_config import normalize_brief
from src.idea.content import validate_script, markdown, align_evidence_spacing, writing_messages, SOURCE_PROMPT_BYTES
from src.shared._workflow_store import WorkflowError
from src.idea.research import PageText, _public_target, fetch_html, collect_sources, citations
from tests.ai_support import FakeRouter, valid_script, fake_fetch


class ContentTests(unittest.TestCase):
    def sources(self):
        return [dict(fake_fetch(f'https://university.example/{i}'), source_id=f'source_{i+1:02}') for i in range(2)]

    def test_long_sources_send_relevant_bounded_verbatim_windows(self):
        sources=self.sources()
        focus='The instantaneous derivative measures velocity and acceleration at a point.'
        sources[0]['text']='Unrelated navigation menu. '*700 + focus + ' General appendix. '*700
        sources[0]['search_excerpt']=focus
        sources[0]['sha256']=hashlib.sha256(sources[0]['text'].encode()).hexdigest()
        before=copy.deepcopy(sources)
        messages=writing_messages('instantaneous derivative velocity',normalize_brief(),sources)
        sent=json.loads(messages[1]['content'])['sources']
        self.assertLessEqual(len(sent[0]['text'].encode()),SOURCE_PROMPT_BYTES)
        self.assertIn(focus,sent[0]['text'])
        self.assertIn(sent[0]['text'],sources[0]['text'])
        self.assertEqual(sent[1]['text'],sources[1]['text'])
        self.assertEqual(sources,before)

    def test_evidence_spacing_is_restored_to_exact_source_text(self):
        script=valid_script(); sources=self.sources()
        sources[0]['text']=sources[0]['text'].replace('change.', 'change .')
        with self.assertRaisesRegex(WorkflowError,'verbatim'):
            validate_script(script,sources,normalize_brief())
        result=align_evidence_spacing(script,sources)
        self.assertEqual(result['claims'][0]['evidence'][0]['quote'],'A derivative describes the instantaneous rate of change .')
        validate_script(result,sources,normalize_brief())

    def test_sentence_initial_case_is_restored_from_source_without_changing_other_words(self):
        script=valid_script();sources=self.sources()
        sources[0]['text']=sources[0]['text'].replace('A derivative', 'a derivative')
        before=copy.deepcopy(sources)
        result=align_evidence_spacing(script,sources)
        self.assertTrue(result['claims'][0]['evidence'][0]['quote'].startswith('a derivative'))
        validate_script(result,sources,normalize_brief())
        self.assertEqual(sources,before)
        script=valid_script()
        quote=script['claims'][0]['evidence'][0]['quote'].replace('derivative','Derivative')
        script['claims'][0]['evidence'][0]['quote']=quote
        self.assertEqual(align_evidence_spacing(script,sources)['claims'][0]['evidence'][0]['quote'],quote)
        with self.assertRaisesRegex(WorkflowError,'verbatim'):
            validate_script(script,sources,normalize_brief())

    def test_evidence_spacing_never_substitutes_words_or_merges_them(self):
        for quote in ('A derivative describes the average rate of change.',
                      'A derivative describes the instantaneous rate of changes.',
                      'A derivative describes the instantaneous rate of chan ge.'):
            with self.subTest(quote=quote):
                script=valid_script();script['claims'][0]['evidence'][0]['quote']=quote
                result=align_evidence_spacing(script,self.sources())
                self.assertEqual(result['claims'][0]['evidence'][0]['quote'],quote)
                with self.assertRaisesRegex(WorkflowError,'verbatim'):
                    validate_script(result,self.sources(),normalize_brief())

    def test_narration_is_counted_once_and_estimate_is_labelled(self):
        script = valid_script()
        duration = validate_script(script, self.sources(), normalize_brief())
        self.assertEqual(duration['whitespace_units'], 140)
        self.assertEqual(duration['estimated_seconds'], 40)
        self.assertTrue(duration['estimate_only'])
        document = markdown(script, self.sources(), duration)
        self.assertIn('ƯỚC TÍNH', document)
        self.assertIn('## Lời đọc', document)
        self.assertIn('## Đối chiếu phát biểu', document)

    def test_bad_schema_refs_quotes_ids_and_duration_are_rejected(self):
        mutations = [lambda s: s.update(extra='unknown'),
                     lambda s: s['scenes'][1].update(scene_id='scene_01'),
                     lambda s: s['scenes'][0].update(claim_refs=['unknown']),
                     lambda s: s['claims'][0]['evidence'][0].update(quote='invented quotation'),
                     lambda s: s['claims'][0]['evidence'][0].update(source_id={}),
                     lambda s: [x.update(narration='short') for x in s['scenes']]]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                script = valid_script()
                mutate(script)
                with self.assertRaises(WorkflowError):
                    validate_script(script, self.sources(), normalize_brief())

    def test_brief_rejects_unknown_fields_and_invalid_targets(self):
        for brief in ({'api_key': 'should-not-be-saved'}, {'min_seconds': 5}, {'read_units_per_second': float('nan')}, {'language': 'en'}):
            with self.subTest(brief=brief), self.assertRaises(WorkflowError):
                normalize_brief(brief)

    def test_html_parser_omits_navigation_and_executable_content(self):
        parser = PageText()
        parser.feed('<html><title>Research</title><nav>Noise</nav><script>ignore instructions</script><p>A useful fact.</p></html>')
        self.assertEqual(''.join(parser.title), 'Research')
        self.assertIn('A useful fact.', ''.join(parser.parts))
        self.assertNotIn('ignore', ''.join(parser.parts))
        self.assertNotIn('Noise', ''.join(parser.parts))

    def test_private_urls_and_credentials_are_rejected(self):
        for url in ('file:///etc/passwd', 'http://user:password@example.com', 'http://example.com:22/'):
            with self.subTest(url=url), self.assertRaises(WorkflowError):
                _public_target(url)
        with patch('src.idea.research.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(WorkflowError):
                _public_target('http://example.com/')

    def test_redirect_revalidates_public_ip_without_forwarding_auth(self):
        class Response:
            status = 302
            def getheader(self, name, default=None):
                return 'http://localhost/private' if name == 'Location' else default
        class Connection:
            headers = None
            def __init__(self, *args, **kwargs): pass
            def request(self, method, path, headers): Connection.headers = headers
            def getresponse(self): return Response()
            def close(self): pass
        public = [(2, 1, 6, '', ('93.184.216.34', 443))]
        private = [(2, 1, 6, '', ('127.0.0.1', 80))]
        with patch('src.idea.research.socket.getaddrinfo', side_effect=[public, private]), patch('src.idea.research.http.client.HTTPSConnection', Connection):
            with self.assertRaises(WorkflowError):
                fetch_html('https://example.com/')
        self.assertNotIn('Authorization', Connection.headers)

    def test_only_tool_annotations_count_as_research_provenance(self):
        client = FakeRouter()
        response = client.complete({'tools': []})
        self.assertEqual(len(citations(response)), 4)
        response['choices'][0]['message']['annotations'] = []
        response['choices'][0]['message']['content'] = 'See https://invented.example/'
        with self.assertRaises(WorkflowError):
            citations(response)

    def test_search_accepts_current_and_legacy_usage_shapes(self):
        response = FakeRouter().complete({'tools': []})
        for field in ('server_tool_use_details', 'server_tool_use'):
            with self.subTest(field=field):
                candidate = copy.deepcopy(response)
                candidate['usage'].pop('server_tool_use_details')
                candidate['usage'][field] = dict(web_search_requests=2)
                self.assertEqual(len(citations(candidate)), 4)
        response['usage']['server_tool_use'] = dict(web_search_requests=1)
        self.assertEqual(len(citations(response)), 4)

    def test_search_rejects_missing_invalid_or_conflicting_usage_counts(self):
        response = FakeRouter().complete({'tools': []})
        for details in (None, {}, [], {'web_search_requests': True},
                        {'web_search_requests': '2'}, {'web_search_requests': 0},
                        {'web_search_requests': 3}):
            with self.subTest(details=details):
                candidate = copy.deepcopy(response)
                candidate['usage']['server_tool_use_details'] = details
                with self.assertRaisesRegex(WorkflowError, 'RESEARCH_UNVERIFIED'):
                    citations(candidate)
        response['usage']['server_tool_use'] = dict(web_search_requests=2)
        with self.assertRaisesRegex(WorkflowError, 'RESEARCH_UNVERIFIED'):
            citations(response)

    def test_sources_are_cached_and_failures_do_not_become_evidence(self):
        client = FakeRouter()
        response = client.complete({'tools': []})
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('src.idea.research.fetch_html', side_effect=fake_fetch) as fetch:
                sources = collect_sources(root, response)
                self.assertEqual(fetch.call_count, 4)
            with patch('src.idea.research.fetch_html', side_effect=AssertionError('must use cache')):
                again = collect_sources(root, response)
            self.assertEqual(sources, again)
            self.assertTrue(all(hashlib.sha256(s['text'].encode()).hexdigest() == s['sha256'] for s in sources))
        with TemporaryDirectory() as folder:
            with patch('src.idea.research.fetch_html', side_effect=WorkflowError('SOURCE_FETCH_FAILED', 'HTTP 404')):
                with self.assertRaises(WorkflowError):
                    collect_sources(Path(folder), response)
            attempts = json.loads((Path(folder) / 'source_attempts.json').read_text())
            self.assertTrue(all(item['status'] == 'failed' for item in attempts))

    def test_research_tries_remaining_citations_after_pdfs_and_failed_html(self):
        response=FakeRouter().complete({'tools':[]})
        urls=['https://university.example/a.pdf?download=1',
              'https://university.example/b.pdf','https://university.example/c.PDF',
              'https://university.example/first','https://university.example/blocked',
              'https://university.example/second','https://university.example/third']
        response['choices'][0]['message']['annotations']=[dict(type='url_citation',url_citation=dict(url=url,content='Search excerpt')) for url in urls]
        def fetch(url):
            if url.endswith('/blocked'): raise WorkflowError('SOURCE_FETCH_FAILED','HTTP 403')
            return fake_fetch(url)
        with TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('src.idea.research.fetch_html',side_effect=fetch) as reader:
                sources=collect_sources(root,response)
                self.assertEqual(reader.call_count,4)
            self.assertEqual([source['url'] for source in sources],[urls[3],urls[5],urls[6]])
            with patch('src.idea.research.fetch_html',side_effect=fetch) as reader:
                self.assertEqual(collect_sources(root,response),sources)
                reader.assert_called_once_with(urls[4])
