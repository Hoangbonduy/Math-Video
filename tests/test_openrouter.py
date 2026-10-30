"""Provider capability checks and durable cost accounting, no live requests."""

import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.shared._workflow_store import WorkflowError, read_json, atomic_write
from src.shared.ai_config import MODEL
from src.shared.job_session import Session, _used
from src.shared.openrouter_client import OpenRouterClient, HTTPFailure, redact, response_text
from tests.ai_support import FakeRouter


def catalog():
    return dict(data=dict(id=MODEL, endpoints=[dict(provider_name='TestProvider',
        supported_parameters=['tools', 'response_format', 'structured_outputs', 'max_tokens'],
        pricing=dict(prompt='0.0000001', completion='0.0000005', input_cache_read='0.00000001',
                     input_cache_write='0.000000125', discount=0,
                     overrides=[dict(min_prompt_tokens=100000, prompt='0.0000005', completion='0.0000025')]))]))


def tool_catalog():
    return dict(data=dict(id='openrouter:web_search', status='active', engines=[
        dict(id='exa', byok='none', pricing=[dict(mode='auto', price='0.007', unit='request')])]))


class ClientTests(unittest.TestCase):
    def test_preflight_skips_outage_and_disabled_endpoints(self):
        data=catalog()
        healthy=copy.deepcopy(data['data']['endpoints'][0])
        healthy.update(provider_name='HealthyProvider',uptime_last_30m=100,uptime_last_5m=100)
        outage=copy.deepcopy(healthy)
        outage.update(provider_name='CheapOutage',uptime_last_30m=0,uptime_last_5m=None)
        outage['pricing'].update(prompt='0.00000001',completion='0.00000001')
        disabled=copy.deepcopy(outage)
        disabled.update(provider_name='DisabledProvider',status=-5,uptime_last_30m=100)
        day_outage=copy.deepcopy(outage)
        day_outage.update(provider_name='OutageWithoutRecentSamples',uptime_last_30m=None,uptime_last_5m=None,uptime_last_1d=0)
        data['data']['endpoints']=[outage,day_outage,disabled,healthy]
        with patch.dict(os.environ,{'OPENROUTER_API_KEY':'dummy-test'}):
            client=OpenRouterClient(transport=lambda *a:data)
            self.assertEqual(client.preflight(search=False)['provider'],'HealthyProvider')

    def test_missing_key_does_not_send_and_keys_are_redacted(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(WorkflowError, 'API_KEY_MISSING'):
                OpenRouterClient(transport=lambda *a: self.fail('must not send')).preflight()
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'unit-test-credential'}):
            value = redact({'Authorization': 'Bearer unit-test-credential', 'message': 'failure unit-test-credential'})
            self.assertNotIn('unit-test-credential', json.dumps(value))
            self.assertNotIn('Authorization', value)

    def test_real_catalog_shape_pricing_tiers_and_supported_token_parameter(self):
        def transport(method, url, headers, payload):
            self.assertEqual(method, 'GET')
            self.assertEqual(headers['Authorization'], 'Bearer dummy-test')
            return tool_catalog() if '/tools/' in url else catalog()
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'dummy-test'}):
            client = OpenRouterClient(transport=transport)
            pricing = client.preflight()
        self.assertEqual(pricing['model'], MODEL)
        self.assertEqual(len(pricing['tiers']), 1)
        payload = client.payload([{'role':'user','content':'topic'}], pricing, search=True)
        self.assertEqual(payload['model'], MODEL)
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertFalse(payload['provider']['allow_fallbacks'])
        self.assertEqual(payload['max_tool_calls'], 2)
        self.assertLess(client.reserve(payload, pricing), 0.1)
        huge = dict(payload, messages=[dict(role='user', content='x' * 200000)])
        self.assertGreater(client.reserve(huge, pricing), 0.1)

    def test_scene_preflight_does_not_require_search_tool_metadata(self):
        def transport(method,url,headers,payload):
            self.assertNotIn('/tools/',url)
            return catalog()
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'dummy-test'}):
            pricing=OpenRouterClient(transport=transport).preflight(search=False)
        self.assertEqual(pricing['model'],MODEL)
        self.assertNotIn('search_reserve_usd',pricing)

    def test_unsupported_model_or_capabilities_never_fall_back(self):
        for data, code in ((dict(data=dict(id='another/model')), 'MODEL_NOT_FOUND'),
                           (dict(data=dict(id=MODEL, endpoints=[])), 'MODEL_UNSUPPORTED')):
            with self.subTest(code=code), patch.dict(os.environ, {'OPENROUTER_API_KEY':'dummy-test'}):
                with self.assertRaisesRegex(WorkflowError, code):
                    OpenRouterClient(transport=lambda *a: data).preflight()

    def test_http_error_codes_and_model_mismatch(self):
        for status, code in ((401,'API_AUTH'), (402,'API_CREDITS'), (404,'MODEL_NOT_FOUND'), (429,'API_RATE_LIMIT'), (503,'API_SERVICE')):
            self.assertEqual(HTTPFailure(status, 'error').code, code)
        client = FakeRouter()
        client.wrong_model = True
        with self.assertRaisesRegex(WorkflowError, 'MODEL_MISMATCH'):
            response_text(client.complete({'tools': []}))

    def test_configured_model_drives_catalog_payload_and_response_validation(self):
        model = 'test/configured-model'
        def transport(method, url, headers, payload):
            if '/tools/' in url:
                return tool_catalog()
            self.assertTrue(url.endswith('/models/' + model + '/endpoints'))
            result = catalog()
            result['data']['id'] = model
            return result
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'dummy-test', 'OPENROUTER_MODEL': model}):
            client = OpenRouterClient(transport=transport)
            pricing = client.preflight()
            self.assertEqual(client.payload([], pricing)['model'], model)
            response = dict(model=model, choices=[dict(finish_reason='stop', message=dict(content='Saved text'))])
            self.assertEqual(response_text(response), 'Saved text')
            response['model'] = MODEL
            with self.assertRaisesRegex(WorkflowError, 'MODEL_MISMATCH'):
                response_text(response)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeRouter()
        self.job = dict(job_id='test-job', status='planned', calls=[], pricing=self.client.preflight())
        self.session = Session(self.root, self.job, self.client)
        self.messages = [dict(role='user', content='topic')]

    def test_reservation_is_durable_before_post_and_success_is_reused(self):
        def check(payload):
            disk = read_json(self.root / 'job.json')
            self.assertEqual(disk['calls'][-1]['status'], 'in_flight')
            self.assertGreater(_used(disk), 0)
        self.client.before_complete = check
        response = self.session.call('draft', self.messages, schema={'properties':{}})
        self.assertEqual(self.session.call('draft', self.messages, schema={'properties':{}}), response)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.job['calls'][0]['usage']['prompt_tokens'], 100)

    def test_rate_limit_retries_once_and_auth_does_not_auto_retry(self):
        self.client.failures = [HTTPFailure(429, 'slow down')]
        self.session.call('draft', self.messages, schema={'properties':{}})
        self.assertEqual(len(self.client.requests), 2)
        self.client.failures = [HTTPFailure(401, 'bad key')]
        with self.assertRaisesRegex(WorkflowError, 'API_AUTH'):
            self.session.call('another', self.messages, schema={'properties':{}})
        self.assertEqual(len(self.client.requests), 3)
        # A subsequent explicit invocation can continue after credentials are fixed.
        self.session.call('another', self.messages, schema={'properties':{}})
        self.assertEqual(len(self.client.requests), 4)

    def test_timeout_is_not_replayed_without_explicit_selection(self):
        self.client.failures = [TimeoutError('connection lost')]
        with self.assertRaisesRegex(WorkflowError, 'API_RESULT_UNKNOWN'):
            self.session.call('draft', self.messages, schema={'properties':{}})
        entry = self.job['calls'][0]
        held = _used(self.job)
        with self.assertRaisesRegex(WorkflowError, 'API_RESULT_UNKNOWN'):
            Session(self.root, self.job, self.client).call('draft', self.messages, schema={'properties':{}})
        self.assertEqual(len(self.client.requests), 1)
        restarted = Session(self.root, self.job, self.client, retry_uncertain=entry['call_id'])
        restarted.call('draft', self.messages, schema={'properties':{}})
        self.assertGreater(_used(self.job), held)
        restarted.call('draft', self.messages, schema={'properties':{}})
        self.assertEqual(len(self.client.requests), 2)

    def test_response_saved_before_ledger_update_recovers_without_post(self):
        entry = dict(call_id='saved', phase='draft', status='in_flight', reserved_cost_usd=0.01, actual_cost_usd=None)
        self.job['calls'].append(entry)
        path = self.root / 'calls/saved.json'
        path.parent.mkdir()
        response = self.client.complete({'tools': []})
        atomic_write(path, response)
        previous = len(self.client.requests)
        self.session.call('draft', self.messages)
        self.assertEqual(len(self.client.requests), previous)
        self.assertEqual(entry['status'], 'complete')

    def test_missing_usage_retains_reservation_and_does_not_invent_counts(self):
        self.client.missing_usage = True
        with self.assertRaisesRegex(WorkflowError, 'USAGE_UNAVAILABLE'):
            self.session.call('draft', self.messages, schema={'properties':{}})
        self.assertIsNone(self.job['calls'][0]['actual_cost_usd'])
        self.assertGreater(_used(self.job), 0)
        with self.assertRaisesRegex(WorkflowError, 'USAGE_UNAVAILABLE'):
            self.session.call('draft', self.messages, schema={'properties':{}})
        self.assertEqual(len(self.client.requests), 1)

    def test_budget_and_request_limits_stop_before_send(self):
        self.client.reserve = lambda *a: self.session.budget_usd + 0.00001
        with self.assertRaisesRegex(WorkflowError, 'BUDGET_EXCEEDED') as result:
            self.session.call('draft', self.messages)
        self.assertIn(f'{self.session.budget_usd:.2f} USD',str(result.exception))
        self.assertIn('draft',str(result.exception))
        self.assertIn('chưa gửi',str(result.exception).lower())
        self.assertEqual(self.client.requests, [])
        self.job['calls'] = [dict(phase=str(i), status='rejected', actual_cost_usd=None, reserved_cost_usd=0) for i in range(6)]
        with self.assertRaisesRegex(WorkflowError, 'REQUEST_LIMIT'):
            self.session.call('draft', self.messages)
        self.assertEqual(self.client.requests, [])
