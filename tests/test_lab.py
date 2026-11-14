import json,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from lab.workflow import Workflow,BusinessError
from lab.retrieval import PolicyIndex

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=[1000.0]
        self.w=Workflow(Path(self.temp.name)/'db.sqlite3',clock=lambda:self.now[0]);self.owner='demo-alice'
        self.sid=self.w.session(self.owner)['id']
    def tearDown(self):self.temp.cleanup()
    def draft(self):return self.w.prepare(self.owner,self.sid,'A001','defect','耳机不通电')
    def test_confirmation_required_and_persisted_resume(self):
        a=self.draft()
        with self.w.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0],0)
        reopened=Workflow(self.w.path,clock=lambda:self.now[0]);self.assertEqual(reopened.action(self.owner,a['id'])['state'],'PENDING')
        r=reopened.confirm(self.owner,a['id'],a['digest']);self.assertEqual(r['status'],'SUCCEEDED')
    def test_response_loss_then_recovery_returns_one_ticket(self):
        a=self.draft()
        with self.assertRaises(TimeoutError):self.w.confirm(self.owner,a['id'],a['digest'],lose_response=True)
        state=self.w.action(self.owner,a['id']);r=self.w.confirm(self.owner,a['id'],a['digest'])
        self.assertEqual(state['ticket_id'],r['ticket_id']);self.assertTrue(r['replayed'])
        with self.w.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0],1)
    def test_concurrent_confirm_and_multiple_drafts_deduplicate(self):
        a=self.draft();b=self.draft()
        with ThreadPoolExecutor(max_workers=8) as pool:
            r=list(pool.map(lambda x:self.w.confirm(self.owner,x['id'],x['digest']),[a,b]*4))
        self.assertEqual(len({x['ticket_id'] for x in r}),1)
    def test_payload_conflict_is_rejected(self):
        a=self.draft()
        with self.assertRaisesRegex(BusinessError,'MISMATCH'):self.w.confirm(self.owner,a['id'],'0'*64)
    def test_expiry_commits_terminal_status(self):
        a=self.draft();self.now[0]+=901
        with self.assertRaisesRegex(BusinessError,'EXPIRED'):self.w.confirm(self.owner,a['id'],a['digest'])
        self.assertEqual(self.w.action(self.owner,a['id'])['state'],'EXPIRED')
    def test_order_revision_changed_requires_reconfirmation(self):
        a=self.draft()
        with self.w.db(write=True) as db:db.execute("UPDATE orders SET revision=2 WHERE id='A001'")
        with self.assertRaisesRegex(BusinessError,'STALE'):self.w.confirm(self.owner,a['id'],a['digest'])
    def test_policy_version_changed_requires_reconfirmation(self):
        a=self.draft();self.w.index.version='new-index'
        with self.assertRaisesRegex(BusinessError,'STALE'):self.w.confirm(self.owner,a['id'],a['digest'])
    def test_owner_boundaries_cover_session_order_action_trace(self):
        a=self.draft()
        for fn in [lambda:self.w.session('demo-bob',self.sid),lambda:self.w.order(self.owner,'B001'),lambda:self.w.action('demo-bob',a['id']),lambda:self.w.trace('demo-bob',a['run_id']),lambda:self.w.confirm('demo-bob',a['id'],a['digest'])]:
            with self.assertRaises(BusinessError):fn()
    def test_outside_window_and_photo_case_do_not_create_draft(self):
        for order,issue in [('A002','defect'),('A001','shipping')]:
            with self.assertRaises(BusinessError):self.w.prepare(self.owner,self.sid,order,issue,'需要处理')
    def test_cancel_blocks_confirmation(self):
        a=self.draft();self.w.cancel(self.owner,a['id'])
        with self.assertRaisesRegex(BusinessError,'CANCELLED'):self.w.confirm(self.owner,a['id'],a['digest'])
    def test_audit_does_not_store_description(self):
        a=self.draft();self.w.confirm(self.owner,a['id'],a['digest'])
        self.assertNotIn('耳机不通电',json.dumps(self.w.trace(self.owner,a['run_id']),ensure_ascii=False))
    def test_session_binding_persists_and_is_isolated(self):
        self.w.bind(self.owner,self.sid,'A001');other=self.w.session(self.owner)
        self.assertEqual(self.w.session(self.owner,self.sid)['order_id'],'A001');self.assertIsNone(other['order_id'])

class RetrievalTests(unittest.TestCase):
    def setUp(self):self.index=PolicyIndex()
    def test_scope_before_ranking(self):
        r=self.index.search('耳机故障坏了','耳机','平台','2026-05-10')
        self.assertTrue(r['hits'])
        for h in r['hits']:self.assertEqual((h['channel'],h['version'],h['audience']),('平台',1,'public'))
    def test_internal_exact_match_is_never_exposed(self):
        for m in ['baseline','scoped']:
            r=self.index.search('耳机 内部赔付限额 INTERNAL_ONLY','耳机','直营','2026-09-10',mode=m)
            self.assertTrue(all(h['audience']=='public' for h in r['hits']))
    def test_cache_key_accounts_for_scope_and_copies_results(self):
        a=self.index.search('耳机故障','耳机','直营','2026-09-10');a['hits'].clear()
        b=self.index.search('耳机故障','耳机','直营','2026-09-10');self.assertTrue(b['cache_hit']);self.assertTrue(b['hits'])
        c=self.index.search('耳机故障','耳机','平台','2026-09-10');self.assertFalse(c['cache_hit'])
    def test_cache_bound_and_index_version(self):
        self.index.capacity=2
        for q in ['故障','退货','发票']:self.index.search(q,'耳机','直营','2026-09-10')
        self.assertEqual(len(self.index.cache),2);self.index.version='changed'
        self.assertFalse(self.index.search('发票','耳机','直营','2026-09-10')['cache_hit'])

if __name__=='__main__':unittest.main()
