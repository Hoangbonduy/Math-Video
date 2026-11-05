"""Cooperative task pause with immediate termination of registered workers."""
from contextlib import contextmanager
import os
import signal
import threading
from src.shared._workflow_store import WorkflowError

_local=threading.local()

class TaskControl:
    def __init__(self):
        self.requested=threading.Event()
        self.lock=threading.RLock()
        self.processes=set()

    def check(self):
        if self.requested.is_set():
            raise WorkflowError('TASK_PAUSED','Đã tạm dừng theo yêu cầu của bạn.')

    @staticmethod
    def stop(process):
        try:
            if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
            else:process.terminate()
        except ProcessLookupError:pass

    def pause(self):
        with self.lock:
            self.requested.set()
            for process in self.processes:self.stop(process)

@contextmanager
def task_scope(control):
    previous=getattr(_local,'control',None)
    _local.control=control
    try:yield
    finally:_local.control=previous

def current_control():
    return getattr(_local,'control',None)

def checkpoint():
    control=getattr(_local,'control',None)
    if control:control.check()

@contextmanager
def watch_process(process):
    control=getattr(_local,'control',None)
    if control:
        with control.lock:
            control.processes.add(process)
            if control.requested.is_set():control.stop(process)
    try:
        yield
        checkpoint()
    finally:
        if control:
            with control.lock:control.processes.discard(process)
