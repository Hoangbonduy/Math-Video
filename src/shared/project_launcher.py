"""Local project picker; each open project keeps its own review server and jobs."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import threading
from urllib.parse import urlsplit
import uuid

from src.shared import workflow as wf
from src.shared._workflow_store import WorkflowError, read_json
from src.shared.paths import PROJECT_ROOT
from src.shared.review_server import ReviewServer


class ManagedReviewServer(ReviewServer):
    # Wait for in-flight HTTP handlers before deleting a project directory.
    daemon_threads = False


class ProjectLauncher(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, projects_dir=None):
        self.root = Path(projects_dir or PROJECT_ROOT / 'data/projects').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.projects = {}
        self.lock = threading.RLock()
        super().__init__(('127.0.0.1', port), LauncherHandler)
        self.origin = f'http://127.0.0.1:{self.server_port}'

    def project_path(self, identifier):
        if not isinstance(identifier, str) or not identifier or '\\' in identifier:
            raise ValueError('Project không hợp lệ.')
        relative = Path(identifier)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Project không hợp lệ.')
        root = (self.root / relative).resolve()
        if root == self.root or not root.is_relative_to(self.root):
            raise ValueError('Project không hợp lệ.')
        if not (root / 'project.json').is_file():
            raise ValueError('Không tìm thấy project.')
        return root

    def list_projects(self):
        result = []
        for path in sorted(self.root.rglob('project.json')):
            try:
                identifier = path.parent.relative_to(self.root).as_posix()
                self.project_path(identifier)
                project = read_json(path)
                result.append(dict(id=identifier, topic=project['topic']))
            except (WorkflowError, ValueError, KeyError, TypeError, OSError):
                continue
        return result

    def create_project(self, topic, request_id):
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 200:
            raise ValueError('Nhập tên project từ 1 đến 200 ký tự.')
        # A retry of the same browser request must not create another project.
        identifier = 'video_' + uuid.UUID(request_id).hex
        with self.lock:
            root = self.root / identifier
            if not root.exists():
                wf.create_project(root, topic.strip())
                from src.idea.service import default_values, save_draft
                values = default_values()
                values['topic'] = topic.strip()
                save_draft(root, values)
        return identifier

    def open_project(self, identifier):
        with self.lock:
            root = self.project_path(identifier)
            if root not in self.projects:
                server = ManagedReviewServer(root, port=0)
                server.launcher_url = self.origin
                try:
                    server.recover()
                except Exception:
                    server.server_close()
                    raise
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.projects[root] = (server, thread)
            return self.projects[root][0].origin + '/#idea'

    def delete_project(self, identifier):
        with self.lock:
            root = self.project_path(identifier)
            # Refuse aliases and protect other projects contained in this folder.
            path = self.root
            for part in Path(identifier).parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError('Không thể xóa project qua đường dẫn liên kết.')
            if any(path != root / 'project.json' for path in root.rglob('project.json')):
                raise ValueError('Project này chứa project khác; hãy xóa các project bên trong trước.')
            if root in self.projects:
                server, thread = self.projects[root]
                with server.jobs_lock:
                    if server.codex_auth.busy or any(job['state'] in ('queued', 'working') for job in server.jobs.values()):
                        raise ValueError('Project đang chạy tác vụ; hãy đợi hoàn tất rồi xóa.')
                server.shutdown()
                server.server_close()
                thread.join()
                del self.projects[root]
            # Check again immediately before recursive deletion.
            if self.project_path(identifier) != root:
                raise ValueError('Đường dẫn project đã thay đổi.')
            shutil.rmtree(root)

    def server_close(self):
        super().server_close()
        for server, thread in self.projects.values():
            server.shutdown()
            server.server_close()
            thread.join()


class LauncherHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_body(self, status, content, kind):
        payload = content.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status, data):
        self.send_body(status, json.dumps(data, ensure_ascii=False), 'application/json')

    def valid_host(self):
        return self.headers.get('Host') == self.server.origin.removeprefix('http://')

    def do_GET(self):
        if not self.valid_host():
            self.send_json(403, dict(error='Invalid host'))
            return
        path = urlsplit(self.path).path
        if path == '/':
            self.send_body(200, HTML, 'text/html')
        elif path == '/launcher.js':
            self.send_body(200, JAVASCRIPT, 'text/javascript')
        elif path == '/launcher.css':
            self.send_body(200, CSS, 'text/css')
        elif path == '/api/projects':
            self.send_json(200, dict(projects=self.server.list_projects(), token=self.server.token))
        else:
            self.send_json(404, dict(error='Not found'))

    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin') != self.server.origin:
            self.send_json(403, dict(error='Invalid local session', code='INVALID_LOCAL_ORIGIN'))
            return
        if self.headers.get('X-Review-Token') != self.server.token:
            self.send_json(403, dict(error='Phiên cục bộ đã đổi. Hãy tải lại trang.', code='LOCAL_SESSION_EXPIRED'))
            return
        if self.path not in ('/api/projects/create', '/api/projects/open', '/api/projects/delete'):
            self.send_json(404, dict(error='Not found'))
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('Yêu cầu không hợp lệ.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Yêu cầu không hợp lệ.')
            if self.path.endswith('/create'):
                identifier = self.server.create_project(data['topic'], data['request_id'])
                self.send_json(200, dict(id=identifier))
            elif self.path.endswith('/open'):
                self.send_json(200, dict(url=self.server.open_project(data['id'])))
            else:
                self.server.delete_project(data['id'])
                self.send_json(200, dict(ok=True))
        except (WorkflowError, ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
            self.send_json(409, dict(error=str(exc)))


def serve_projects(port=8765):
    server = ProjectLauncher(port)
    print('Danh sách project: ' + server.origin, flush=True)
    print('Dữ liệu project: ' + str(server.root), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


HTML = '''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Project video toán học</title><link rel="stylesheet" href="/launcher.css"><script src="/launcher.js" defer></script></head>
<body><main><p class="eyebrow">MATH VIDEO</p><h1>Project của bạn</h1>
<p>Tạo video mới hoặc tiếp tục một project đã lưu.</p>
<form id="create"><label for="topic">Tên project mới</label><div class="row">
<input id="topic" name="topic" required maxlength="200" placeholder="Ví dụ: Đạo hàm là gì?">
<button type="submit">Tạo project</button></div></form>
<p id="message" role="status" aria-live="polite"></p>
<h2>Project đã lưu</h2><div id="projects">Đang tải…</div>
<p class="hint">Các project được lưu riêng trong data/projects. Chọn “Ý tưởng mới” trong project để quay về đây.</p>
</main></body></html>'''

CSS = '''*{box-sizing:border-box}body{margin:0;background:#f2f6f7;color:#18384a;font:16px/1.6 system-ui,sans-serif}
main{max-width:850px;margin:48px auto;padding:24px}h1{font-size:36px;margin:0;color:#18384a}h2{font-size:22px;color:#18384a}
p{color:#607f90}.eyebrow{color:#298a90;letter-spacing:.15em;font-weight:700}form{background:#fff;border:1px solid #d7e4e9;padding:24px;border-radius:12px;margin-top:32px}
label{display:block;font-weight:600;margin-bottom:8px}.row{display:flex;gap:12px}input{flex:1;min-width:0;background:#fff;color:#18384a;border:1px solid #d3e1e8;border-radius:8px;padding:12px;font:inherit}input:focus{outline:2px solid #9ed6d2;outline-offset:1px}
button{background:#278b90;color:#fff;border:1px solid #278b90;border-radius:8px;padding:12px 20px;font:inherit;font-weight:600;cursor:pointer}button:hover{background:#1e777d}button:disabled{opacity:.5;cursor:wait}
.project{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px;background:#fff;border:1px solid #d7e4e9;border-radius:12px;margin-bottom:12px}
.project strong{display:block;overflow-wrap:anywhere}.project small{color:#607f90;overflow-wrap:anywhere}.project-actions{display:flex;gap:10px;flex-shrink:0}.delete{background:#fff;color:#b33b3b;border-color:#e6bebe}.delete:hover{background:#fff0f0;color:#942d2d}.hint{font-size:14px}#message{color:#a14b31;min-height:26px}
@media(max-width:600px){main{margin:12px auto;padding:16px}.row{flex-direction:column}.project{align-items:stretch;flex-direction:column}.project-actions button{flex:1}}
'''

JAVASCRIPT = '''let token;
const message = document.getElementById('message');
async function projectSnapshot() {
  const response = await fetch('/api/projects', {cache:'no-store'});
  if (!response.ok) throw new Error('Không tải được phiên project. Hãy tải lại trang.');
  const data = await response.json();
  if (!data.token) throw new Error('Không đọc được phiên project.');
  token = data.token;
  return data;
}
async function action(path, data) {
  // A page can outlive the server that issued its token (restart/back-forward).
  // Refresh before mutations; retry only an explicit rejection before execution.
  const body = JSON.stringify(data);
  for (let attempt = 0; attempt < 2; attempt++) {
    const session = await projectSnapshot();
    const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-Review-Token':session.token}, body});
    const result = await response.json();
    if (response.status === 403 && result.code === 'LOCAL_SESSION_EXPIRED' && attempt === 0) continue;
    if (!response.ok) throw new Error(result.error || 'Không thể thực hiện yêu cầu.');
    return result;
  }
}
async function openProject(id) {
  const result = await action('/api/projects/open', {id});
  window.location.assign(result.url);
}
async function load() {
  const data = await projectSnapshot();
  const list = document.getElementById('projects'); list.replaceChildren();
  if (!data.projects.length) list.textContent = 'Chưa có project. Hãy tạo project đầu tiên ở trên.';
  for (const project of data.projects) {
    const row = document.createElement('div'); row.className = 'project';
    const info = document.createElement('div');
    const title = document.createElement('strong'); title.textContent = project.topic;
    const path = document.createElement('small'); path.textContent = project.id;
    info.append(title, path);
    const button = document.createElement('button'); button.textContent = 'Mở project';
    button.onclick = async () => {
      button.disabled = true; message.textContent = 'Đang mở project…';
      try { await openProject(project.id); }
      catch (error) { message.textContent = error.message; button.disabled = false; }
    };
    const remove = document.createElement('button'); remove.className = 'delete'; remove.textContent = 'Xóa project';
    remove.setAttribute('aria-label', 'Xóa project ' + project.topic);
    remove.onclick = async () => {
      if (!window.confirm('Xóa project “' + project.topic + '” và toàn bộ dữ liệu của project? Hành động này không thể hoàn tác.')) return;
      button.disabled = true; remove.disabled = true; message.textContent = 'Đang xóa project…';
      try {
        await action('/api/projects/delete', {id:project.id});
        await load(); message.textContent = 'Đã xóa project và dữ liệu.';
      } catch (error) { message.textContent = error.message; button.disabled = false; remove.disabled = false; }
    };
    const actions = document.createElement('div'); actions.className = 'project-actions'; actions.append(button, remove);
    row.append(info, actions); list.append(row);
  }
}
let pendingCreate;
document.getElementById('create').onsubmit = async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button'); button.disabled = true;
  const topic = document.getElementById('topic').value.trim();
  if (!pendingCreate || pendingCreate.topic !== topic) pendingCreate = {topic, request_id:crypto.randomUUID()};
  message.textContent = 'Đang tạo project…';
  try {
    const result = await action('/api/projects/create', pendingCreate);
    await openProject(result.id);
    pendingCreate = null;
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
};
window.addEventListener('pageshow', () => {
  message.textContent = '';
  load().catch(error => { message.textContent = error.message; });
});
'''

