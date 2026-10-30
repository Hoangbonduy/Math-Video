"""Temporary C7 project with fake paid services and real 1080p media."""
from tests.test_av import AVTests
from src.preview import av_workflow as av
from src.shared.review_server import ReviewServer
flow=AVTests('test_timing_validation');flow.setUp()
av.generate(flow.root)
server=ReviewServer(flow.root,0)
print(server.origin,flush=True)
try: server.serve_forever()
finally: server.server_close();flow.doCleanups()
