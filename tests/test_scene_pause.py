"""A pause stops active/queued work and allows notes on unpublished scenes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from src.shared.task_control import TaskControl, task_scope, watch_process, checkpoint
from src.shared._workflow_store import WorkflowError
from src.shared import _workflow_store as store
from src.shared import workflow as wf
from src.shared.review_server import ReviewServer
from src.shared.job_session import Session
from src.scene_design import scene_generation as sg
from tests.test_scene_v2_workflow import approved_project
from tests.scene_v2_support import DesignRouter

class ScenePauseTests(unittest.TestCase):
    def wait_for(self,condition):
        until=time.monotonic()+5
        while not condition():
            if time.monotonic()>until:self.fail('Task did not settle')
            time.sleep(.01)

    def test_pause_terminates_active_child_process(self):
        control=TaskControl();ready=threading.Event();outcome=[];workers=[]
        def work():
            try:
                with task_scope(control):
                    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
                    workers.append(process)
                    with watch_process(process):
                        ready.set();process.wait(timeout=5)
            except WorkflowError as exc:outcome.append(exc.code)
        thread=threading.Thread(target=work);thread.start()
        self.assertTrue(ready.wait(3));control.pause();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertIsNotNone(workers[0].poll())
        self.assertEqual(outcome,['TASK_PAUSED'])

    def test_interrupted_provider_call_does_not_become_an_uncertain_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            control=TaskControl()
            class Client:
                billing_source='codex_subscription'
                def payload(self,*a,**kw):return {}
                def reserve(self,*a):return 0
                def complete(self,*a):control.pause();checkpoint()
            job=dict(calls=[],pricing={},job_id='paused')
            with task_scope(control):
                with self.assertRaisesRegex(WorkflowError,'TASK_PAUSED'):
                    Session(Path(folder),job,Client()).call('design',[])
            self.assertEqual(job['calls'][0]['status'],'interrupted')

    def test_server_pause_persists_and_accepts_review_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=approved_project(Path(folder));ready=threading.Event()
            def context(root,p,reviews):return wf._latest(p,'script',None),store.read_json(root/'input.json'),{'scene_1':3}
            def design(*args):
                ready.set()
                while True:checkpoint();time.sleep(.01)
            with patch('src.shared.review_server.load_local_env'),patch.object(sg,'_context',side_effect=context),patch.object(sg,'CLIENT_FACTORY',return_value=DesignRouter()),patch.object(sg,'_design',side_effect=design),patch('src.scene_design.manim_runtime.info'):
                server=ReviewServer(root,0)
                try:
                    server.enqueue('generate:scene_1',lambda:sg.run_scenes(root,scene_id='scene_1'))
                    self.assertTrue(ready.wait(3));server.pause_scene('scene_1')
                    self.wait_for(lambda:server.jobs['generate:scene_1']['state']=='paused')
                    job=sg._jobs(root)[-1];self.assertEqual(job['status'],'paused');self.assertIsNone(job['last_error'])
                    sg.request_failed_feedback(root,'scene_1',job['job_id'],'Giữ nhãn rõ hơn')
                    self.assertEqual(store.read_json(root/'project.json')['failed_scene_feedback']['scene_1']['feedback'],'Giữ nhãn rõ hơn')
                    server.pause_scene('scene_1') # repeated clicks are harmless
                finally:server.server_close()

    def test_queued_scene_can_be_paused_before_callback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=approved_project(Path(folder));block=threading.Event();started=threading.Event();called=[]
            with patch('src.shared.review_server.load_local_env'):
                server=ReviewServer(root,0)
                try:
                    server.enqueue('other',lambda:(started.set(),block.wait(3)))
                    self.assertTrue(started.wait(2))
                    server.enqueue('generate:scene_1',lambda:called.append(True))
                    server.pause_scene('scene_1');block.set()
                    self.wait_for(lambda:server.jobs['generate:scene_1']['state']=='paused')
                    self.assertEqual(called,[])
                    sg.request_failed_feedback(root,'scene_1',None,'Tạo hình rõ hơn')
                    self.assertEqual(store.read_json(root/'project.json')['failed_scene_feedback']['scene_1']['feedback'],'Tạo hình rõ hơn')
                finally:block.set();server.server_close()

if __name__=='__main__':unittest.main()
