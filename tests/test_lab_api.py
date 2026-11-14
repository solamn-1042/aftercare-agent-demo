import tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from lab.app import create_app
from lab.workflow import Workflow

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workflow(Path(self.tmp.name)/'api.db')
        self.c=TestClient(create_app(self.w,token='x'*24));self.h={'Authorization':'Bearer '+'x'*24}
    def tearDown(self):self.c.close();self.tmp.cleanup()
    def test_http_demo_complete(self):
        self.assertEqual(self.c.get('/').status_code,200)
        s=self.c.post('/api/session',json={},headers=self.h).json()
        a=self.c.post('/api/drafts',headers=self.h,json={'session_id':s['id'],'order_id':'A001','issue':'defect','description':'耳机坏了'}).json()
        r=self.c.post('/api/actions/'+a['id']+'/confirm',headers=self.h,json={'digest':a['digest']})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['status'],'SUCCEEDED')
        self.assertIn('x-request-id',r.headers)
        self.assertEqual(len(self.c.get('/api/runs/'+a['run_id'],headers=self.h).json()),2)
    def test_auth_schema_and_identity_injection(self):
        self.assertEqual(self.c.post('/api/session',json={}).status_code,401)
        self.assertEqual(self.c.post('/api/session',headers=self.h,json={'owner':'demo-bob'}).status_code,422)
        self.assertEqual(self.c.get('/api/orders/B001',headers=self.h).status_code,409)
        self.assertEqual(self.c.post('/api/check',headers=self.h,json={'order_id':'A001','issue':'refund'}).status_code,422)
    def test_body_limit(self):
        self.assertEqual(self.c.post('/api/chat',headers=self.h,content='x'*9000).status_code,413)

if __name__=='__main__':unittest.main()
