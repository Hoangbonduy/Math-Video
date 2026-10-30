import copy,json,unittest
from unittest.mock import patch
from src.scene_design.scene_vision import SceneReviewClient
from src.shared._workflow_store import WorkflowError

class VisionTests(unittest.TestCase):
    def test_text_only_model_rejected_before_paid_request(self):
        calls=[]
        def transport(method,url,headers,payload=None):
            calls.append((method,url));return dict(data=[dict(id='test/text-only',architecture=dict(input_modalities=['text']))])
        with patch.dict('os.environ',{'SCENE_REVIEW_MODEL':'test/text-only','OPENROUTER_API_KEY':'fake-key'}):
            client=SceneReviewClient(transport=transport)
            with self.assertRaisesRegex(WorkflowError,'SCENE_VISION_UNSUPPORTED'):client.preflight()
        self.assertTrue(all(method=='GET' for method,url in calls))
    def test_image_requests_have_extra_budget_reservation(self):
        with patch.dict('os.environ',{'OPENROUTER_API_KEY':'fake-key'}):client=SceneReviewClient()
        pricing=dict(model=client.model,provider='fake',prompt='.0000002',completion='.000001',input_rate='.0000002',tiers=[])
        basic=client.payload([dict(role='user',content='test')],pricing)
        images=client.payload([dict(role='user',content=[dict(type='image_url',image_url=dict(url='data:image/jpeg;base64,eA=='))])],pricing)
        self.assertGreater(client.reserve(images,pricing)-client.reserve(basic,pricing),32768*.0000002)
