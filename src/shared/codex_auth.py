"""Local Codex CLI sign-in. Login codes stay in memory; no inference calls."""
import os
import re
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit

from src.shared.codex_client import clean_environment, executable
from src.shared._workflow_store import WorkflowError

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
BUSY = {'starting', 'waiting', 'verifying', 'cancelling'}


class CodexAuth:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.worker = None
        self.cancelled = threading.Event()
        self.checked = 0
        self.requests = set()
        self.value = dict(state='unknown', available=True, logged_in=False,
                          message='Đang kiểm tra đăng nhập Codex…', verification_url=None,
                          user_code=None, method=None)

    @property
    def busy(self):
        with self.lock:
            return self.value['state'] in BUSY

    def _check(self):
        try:
            result = subprocess.run([executable(), 'login', 'status'], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=10, env=clean_environment())
            logged = result.returncode == 0 and 'chatgpt' in (result.stdout + result.stderr).lower()
            if result.returncode == 0 and not logged:
                message = 'Đang dùng API key. Đăng nhập ChatGPT để dùng hạn mức Codex.'
            else:
                message = 'Đã đăng nhập Codex bằng ChatGPT.' if logged else 'Chưa đăng nhập Codex bằng ChatGPT.'
            return dict(available=True, logged_in=logged, message=message)
        except WorkflowError:
            return dict(available=False, logged_in=False, message='Chưa tìm thấy Codex CLI. Kiểm tra CODEX_COMMAND trong WSL.')
        except (OSError, subprocess.TimeoutExpired):
            return dict(available=True, logged_in=False, message='Không kiểm tra được phiên Codex. Hãy kiểm tra lại.')

    def snapshot(self, force=False):
        with self.lock:
            if not self.busy and (force or time.monotonic() - self.checked > 30):
                result = self._check()
                self.value.update(result)
                if self.value['state'] not in ('failed', 'cancelled', 'complete'):
                    self.value['state'] = 'signed_in' if result['logged_in'] else 'signed_out'
                self.checked = time.monotonic()
            return dict(self.value, busy=self.busy)

    def start(self, request_id, *, method='device', logout=False):
        if method not in ('device', 'browser'):
            raise WorkflowError('INVALID_INPUT', 'Cách đăng nhập Codex không hợp lệ.')
        with self.lock:
            if request_id in self.requests:
                return self.snapshot()
            if self.busy:
                raise WorkflowError('CODEX_AUTH_BUSY', 'Đang đăng nhập Codex. Hoàn tất hoặc hủy phiên hiện tại.')
            executable()  # Fail before changing any credentials if CLI is absent.
            self.requests.add(request_id)
            if len(self.requests) > 200:
                self.requests = {request_id}
            self.cancelled = threading.Event()
            self.value.update(state='starting', method=method, verification_url=None, user_code=None,
                message='Đang đăng xuất Codex…' if logout else 'Đang chuẩn bị đăng nhập tài khoản Codex mới…')
            self.worker = threading.Thread(target=self._login, args=(logout,), daemon=True)
            self.worker.start()
            return self.snapshot()

    @staticmethod
    def _terminate(process):
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        except ProcessLookupError:
            pass

    def cancel(self):
        with self.lock:
            if not self.busy:
                return self.snapshot()
            self.cancelled.set()
            self.value.update(state='cancelling', message='Đang hủy đăng nhập…', verification_url=None, user_code=None)
            process = self.process
        self._terminate(process)
        return self.snapshot()

    def close(self):
        if self.busy:
            self.cancel()
        if self.worker:
            self.worker.join(timeout=5)

    def _read_line(self, line):
        text = ANSI.sub('', line)
        with self.lock:
            if self.cancelled.is_set():
                return
            for match in re.findall(r"https://[^\s<>]+", text):
                url = match.rstrip(').,')
                parsed = urlsplit(url)
                # Only CLI-issued OpenAI login pages, never arbitrary output links.
                if parsed.netloc == 'auth.openai.com' and parsed.path in ('/codex/device', '/oauth/authorize'):
                    self.value.update(verification_url=url, state='waiting',
                        message='Mở trang đăng nhập OpenAI và chọn tài khoản mới.')
            if self.value['method'] == 'device':
                match = re.search(r"\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b", text)
                if match:
                    self.value.update(user_code=match.group(), state='waiting')

    def _login(self, logout):
        output = ''
        error = None
        timer = None
        timed_out = threading.Event()
        try:
            command = executable()
            result = subprocess.run([command, 'logout'], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=15, env=clean_environment())
            if result.returncode:
                raise RuntimeError('logout')
            with self.lock:
                self.value['logged_in'] = False
            if self.cancelled.is_set() or logout:
                return
            args = [command, 'login']
            if self.value['method'] == 'device':
                args.append('--device-auth')
            env = clean_environment()
            env['BROWSER'] = '/bin/true'  # The user opens the link in the Windows browser.
            with self.lock:
                if self.cancelled.is_set():
                    return
                self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                    bufsize=1, env=env, start_new_session=True)
                process = self.process
                self.value.update(state='waiting', message='Đang lấy đường dẫn đăng nhập từ Codex…')
            def expire():
                timed_out.set()
                self._terminate(process)
            timer = threading.Timer(900, expire)
            timer.daemon = True
            timer.start()
            for line in process.stdout:
                output = (output + ANSI.sub('', line))[-8192:]
                self._read_line(line)
            process.wait(timeout=5)
            if timed_out.is_set():
                error = 'Phiên đăng nhập đã hết thời gian. Bấm đăng nhập để lấy mã mới.'
            elif process.returncode and not self.cancelled.is_set():
                if 'device' in output.lower() and any(s in output.lower() for s in ('disabled', 'enable', 'not enabled')):
                    error = 'Hãy bật đăng nhập bằng mã thiết bị trong bảo mật ChatGPT, hoặc chọn đăng nhập bằng trình duyệt.'
                elif 'address already in use' in output.lower():
                    error = 'Cổng đăng nhập đang được sử dụng. Chọn đăng nhập bằng mã thiết bị hoặc đóng phiên đăng nhập khác.'
                else:
                    error = 'Codex chưa đăng nhập thành công. Thử lại hoặc chọn cách đăng nhập khác.'
        except (OSError, RuntimeError, WorkflowError, subprocess.TimeoutExpired):
            error = 'Không hoàn tất được đăng nhập Codex. Kiểm tra kết nối và thử lại.'
        finally:
            if timer:
                timer.cancel()
            self._terminate(self.process)
            if self.process and self.process.stdout:
                self.process.stdout.close()
            with self.lock:
                result = self._check()
                self.checked = time.monotonic()
                cancelled = self.cancelled.is_set()
                success = not error and (logout or result['logged_in'])
                if cancelled:
                    message = 'Đã hủy đăng nhập. Bạn có thể bắt đầu lại.'
                elif success:
                    message = 'Đã đăng xuất Codex.' if logout else 'Đã đăng nhập Codex. Bạn có thể thử lại tác vụ bị dừng.'
                else:
                    message = error or 'Chưa xác nhận được đăng nhập ChatGPT. Hãy thử lại.'
                self.value.update(result, state='cancelled' if cancelled else 'complete' if success else 'failed',
                    message=message, verification_url=None, user_code=None)
                self.process = None
