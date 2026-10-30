"""Isolated music UI fixture: real files/FFmpeg, synthetic speech, no network TTS."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from src.preview import continuous_preview as preview, av_workflow
from src.shared.review_server import ReviewServer
from tests.test_continuous_preview import fixture, fake_speech

with tempfile.TemporaryDirectory(prefix='music_browser_') as directory:
    root=fixture(Path(directory)/'project')
    av_workflow.configure(root,dict(provider='edge',voice='vi-VN-NamMinhNeural'))
    calls=root/'speech_calls.json';calls.write_text('[]')
    def synth(*args):
        count=json.loads(calls.read_text());count.append(args[1]);calls.write_text(json.dumps(count))
        return fake_speech(*args)
    with patch('src.preview.continuous_preview.SYNTHESIZER',side_effect=synth):
        preview.generate(root,False);preview.generate(root,True)
        server=ReviewServer(root,0)
        print(json.dumps(dict(origin=server.origin,calls=str(calls))),flush=True)
        try:server.serve_forever()
        finally:server.server_close()
