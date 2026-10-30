import tempfile, os
from pathlib import Path
from src.preview import continuous_preview as preview
from src.shared import workflow as wf
from src.shared.review_server import ReviewServer
from tests.test_continuous_preview import fixture, fake_speech
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    if os.environ.get('EMPTY_PREVIEW'): wf.create_project(root, 'Dự án chưa có kịch bản')
    else: fixture(root)
    preview.SYNTHESIZER=fake_speech
    server=ReviewServer(root,0)
    print(server.origin,flush=True)
    try:server.serve_forever()
    finally:server.server_close()
