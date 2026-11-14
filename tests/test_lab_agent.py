import json,tempfile,unittest
from pathlib import Path
from lab.agent import CaseAgent,TOOLS
from lab.workflow import Workflow

def call(name,args):return dict(role='assistant',content=None,tool_calls=[dict(id='call-1',type='function',function=dict(name=name,arguments=json.dumps(args)))])
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.w=Workflow(Path(self.t.name)/'test.db');self.sid=self.w.session('demo-alice')['id']
    def tearDown(self):self.t.cleanup()
    def test_tools_do_not_expose_confirmation(self):
        self.assertNotIn('confirm',[x['function']['name'] for x in TOOLS])
    def test_repeated_tool_is_stopped_and_calls_bounded(self):
        agent=CaseAgent(self.w,'demo-alice',self.sid,completion=lambda m:call('read_order',{'order_id':'A001'}))
        r=agent.ask('循环查询');self.assertEqual(r['status'],'budget_exhausted')
        self.assertTrue(any(t.get('error')=='NO_PROGRESS_REPEATED_TOOL' for t in r['trace']))
        self.assertLessEqual(len([t for t in r['trace'] if t['node']=='model']),4)
    def test_model_cannot_override_owner(self):
        scripted=iter([call('read_order',{'order_id':'B001'}),dict(role='assistant',content='无法查询')])
        r=CaseAgent(self.w,'demo-alice',self.sid,completion=lambda m:next(scripted)).ask('查询他人订单')
        self.assertTrue(any(t.get('error')=='ORDER_NOT_FOUND_OR_FORBIDDEN' for t in r['trace']))
    def test_draft_is_not_reported_as_submitted(self):
        scripted=iter([call('prepare_draft',{'order_id':'A001','issue':'defect','description':'坏了'}),dict(role='assistant',content='已退款成功')])
        r=CaseAgent(self.w,'demo-alice',self.sid,completion=lambda m:next(scripted)).ask('帮我申请')
        self.assertEqual(r['action']['state'],'PENDING');self.assertIn('尚未提交',r['answer'])
        self.assertTrue(self.w.trace('demo-alice',r['run_id']))
        with self.w.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM tickets').fetchone()[0],0)
    def test_schema_injection_rejected(self):
        scripted=iter([call('read_order',{'order_id':'A001','owner':'demo-bob'}),dict(role='assistant',content='参数无效')])
        r=CaseAgent(self.w,'demo-alice',self.sid,completion=lambda m:next(scripted)).ask('越权')
        self.assertTrue(any(t.get('status')=='error' for t in r['trace']))
    def test_photo_case_does_not_advertise_nonexistent_upload(self):
        scripted=iter([call('check_eligibility',{'order_id':'A003','issue':'shipping'}),dict(role='assistant',content='请在页面上传照片，我会自动提交')])
        r=CaseAgent(self.w,'demo-alice',self.sid,completion=lambda m:next(scripted)).ask('运输破损')
        self.assertIn('不支持上传',r['answer']);self.assertNotIn('我会自动提交',r['answer'])
    def test_fake_citation_or_execution_claim_is_rejected(self):
        for answer in ['政策如此[政策:FAKE]','已退款成功']:
            r=CaseAgent(self.w,'demo-alice',self.sid,completion=lambda m:dict(role='assistant',content=answer)).ask('退款')
            self.assertEqual(r['status'],'evidence_rejected')

if __name__=='__main__':unittest.main()
