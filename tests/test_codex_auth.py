"""Exercise login with an actual fake CLI process; never use real credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from src.shared.codex_auth import CodexAuth
from src.shared._workflow_store import WorkflowError
from src.shared.review_server import ReviewServer

FAKE = r"""
import os,sys,time
from pathlib import Path
root=Path(__file__).parent
args=sys.argv[1:]
with (root/'calls').open('a') as f: f.write(' '.join(args)+'\n')
if args==['login','status']:
 if (root/'signed-in').exists(): print('Logged in using ChatGPT',file=sys.stderr);sys.exit(0)
 print('Not logged in',file=sys.stderr);sys.exit(1)
if args==['logout']:
 (root/'signed-in').unlink(missing_ok=True);sys.exit(0)
if args and args[0]=='login':
 (root/'pid').write_text(str(os.getpid()))
 if (root/'fail').exists(): print('device authentication is disabled',flush=True);sys.exit(1)
 if '--device-auth' in args:
  print('https://auth.openai.com/codex/device',flush=True)
  print('\x1b[1mABCD-EFGHI\x1b[0m',flush=True)
 else: print('https://auth.openai.com/oauth/authorize?state=fake-state',flush=True)
 while not (root/'accept').exists(): time.sleep(.02)
 (root/'signed-in').touch();print('Successfully logged in',flush=True);sys.exit(0)
sys.exit(3)
"""


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cli=self.root/'codex'
        self.cli.write_text('#!'+sys.executable+'\n'+FAKE);self.cli.chmod(0o700)
        self.patch=patch('src.shared.codex_auth.executable',return_value=str(self.cli));self.patch.start();self.addCleanup(self.patch.stop)
        self.auth=CodexAuth();self.addCleanup(self.auth.close)
        (self.root/'signed-in').touch()

    def wait_for(self, predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=self.auth.snapshot()
            if predicate(value): return value
            time.sleep(.02)
        self.fail('Timed out waiting for auth state: '+repr(self.auth.snapshot()))

    def test_device_success_replaces_login_once_and_clears_transient_code(self):
        self.assertTrue(self.auth.snapshot()['logged_in'])
        self.auth.start('one');value=self.wait_for(lambda v: v['user_code'])
        self.assertEqual(value['user_code'],'ABCD-EFGHI');self.assertFalse(value['logged_in'])
        self.auth.start('one')
        with self.assertRaisesRegex(WorkflowError,'CODEX_AUTH_BUSY'):self.auth.start('two')
        (self.root/'accept').touch();value=self.wait_for(lambda v: not v['busy'])
        self.assertEqual(value['state'],'complete');self.assertTrue(value['logged_in'])
        self.assertIsNone(value['user_code']);self.assertIsNone(value['verification_url'])
        self.assertEqual((self.root/'calls').read_text().splitlines().count('logout'),1)
        self.assertEqual((self.root/'calls').read_text().splitlines().count('login --device-auth'),1)

    def test_browser_login_and_logout_do_not_run_inference(self):
        self.auth.start('browser',method='browser')
        value=self.wait_for(lambda v: v['verification_url'])
        self.assertIn('/oauth/authorize?',value['verification_url']);self.assertIsNone(value['user_code'])
        (self.root/'accept').touch();self.wait_for(lambda v:not v['busy'])
        self.auth.start('logout',logout=True);value=self.wait_for(lambda v:not v['busy'])
        self.assertFalse(value['logged_in']);self.assertEqual(value['state'],'complete')
        self.assertNotIn('exec',(self.root/'calls').read_text())

    def test_cancel_kills_login_process_and_clears_code(self):
        self.auth.start('cancel');self.wait_for(lambda v:v['user_code'])
        pid=int((self.root/'pid').read_text());self.auth.cancel()
        value=self.wait_for(lambda v:not v['busy'])
        self.assertEqual(value['state'],'cancelled');self.assertIsNone(value['user_code'])
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        self.auth.start('again');self.wait_for(lambda v:v['user_code'])
        self.auth.close();self.assertFalse(self.auth.busy)

    def test_login_error_is_actionable_and_allows_retry(self):
        (self.root/'fail').touch();self.auth.start('failure')
        value=self.wait_for(lambda v:not v['busy'])
        self.assertEqual(value['state'],'failed');self.assertIn('bật đăng nhập',value['message'])
        (self.root/'fail').unlink();self.auth.start('retry');self.wait_for(lambda v:v['user_code'])

    def test_expiration_stops_process_and_clears_login_link(self):
        real_timer=threading.Timer
        with patch('src.shared.codex_auth.threading.Timer',side_effect=lambda seconds,fn:real_timer(.15,fn)):
            self.auth.start('expires');value=self.wait_for(lambda v:not v['busy'])
        self.assertEqual(value['state'],'failed');self.assertIn('hết thời gian',value['message'])
        self.assertIsNone(value['verification_url']);self.assertIsNone(value['user_code'])

    def test_untrusted_links_and_invalid_methods_are_rejected(self):
        self.auth._read_line('https://auth.openai.com.evil.example/codex/device')
        self.auth._read_line('https://auth.openai.com/password/reset')
        self.assertIsNone(self.auth.value['verification_url'])
        with self.assertRaisesRegex(WorkflowError,'INVALID_INPUT'):self.auth.start('bad',method='shell')
        self.assertFalse((self.root/'calls').exists())

    def test_routes_require_local_session_and_leave_project_bytes_unchanged(self):
        with patch('src.shared.review_server.load_local_env'):
            server=ReviewServer(self.root/'project',0)
        server.codex_auth.close();server.codex_auth=self.auth
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close()))
        before={p.name:p.read_bytes() for p in (self.root/'project').glob('*.json')}
        def post(route,request_id,**kwargs):
            data=json.dumps(dict(request_id=request_id,**kwargs)).encode()
            req=urllib.request.Request(server.origin+'/api/'+route,data,headers={'Origin':server.origin,'X-Review-Token':server.token,'Content-Type':'application/json'})
            return json.load(urllib.request.urlopen(req))
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(server.origin+'/api/codex-login',b'{"request_id":"bad"}'))
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(server.origin+'/api/codex-auth')
        server.jobs['render']=dict(state='working')
        with self.assertRaises(urllib.error.HTTPError) as error:post('codex-login','blocked')
        self.assertEqual(error.exception.code,409);self.assertFalse((self.root/'calls').exists())
        server.jobs.clear();post('codex-login','login');self.wait_for(lambda v:v['user_code'])
        with self.assertRaises(urllib.error.HTTPError):post('continue','blocked-generation')
        with self.assertRaisesRegex(WorkflowError,'CODEX_AUTH_BUSY'):server.enqueue('test',lambda:None)
        post('codex-login-cancel','cancel');self.wait_for(lambda v:not v['busy'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in (self.root/'project').glob('*.json')})
        self.assertFalse(server.jobs)


if __name__=='__main__':unittest.main()
