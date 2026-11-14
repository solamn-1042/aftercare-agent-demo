"""Authoritative SQLite state; the model never receives a confirm/write tool."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from lab.retrieval import PolicyIndex

SCHEMA='case-tools-v2'
class BusinessError(ValueError):pass

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class Workflow:
    def __init__(self, db_path, index=None, clock=time.time, today=None):
        self.path=str(db_path); self.index=index or PolicyIndex(); self.clock=clock
        self.today=today or date(2026,11,14) # fictional demo timeline, not wall-clock merchant eligibility
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,owner TEXT,product TEXT,channel TEXT,purchased TEXT,delivered TEXT,status TEXT,revision INTEGER);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,owner TEXT,order_id TEXT,updated REAL);
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,owner TEXT,session_id TEXT,run_id TEXT,payload TEXT,digest TEXT,state TEXT,expires REAL,ticket_id TEXT);
            CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,action_id TEXT UNIQUE,owner TEXT,order_id TEXT,issue TEXT,status TEXT,UNIQUE(owner,order_id,issue));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,run_id TEXT,action_id TEXT,node TEXT,status TEXT,at REAL,details TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,owner TEXT,session_id TEXT,at REAL,status TEXT,trace TEXT,usage TEXT);
            ''')
            db.executemany('INSERT OR IGNORE INTO orders VALUES(?,?,?,?,?,?,?,?)',[
                ('A001','demo-alice','耳机','直营','2026-09-10','2026-11-10','delivered',1),
                ('A002','demo-alice','键盘','平台','2026-05-10','2026-05-15','delivered',1),
                ('A003','demo-alice','显示器','经销商','2026-09-20','2026-11-12','delivered',1),
                ('B001','demo-bob','耳机','直营','2026-09-10','2026-11-10','delivered',1)])
    @contextmanager
    def db(self,write=False):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=5000')
            if write:db.execute('BEGIN IMMEDIATE')
            yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
    def event(self,db,run,node,status,action=None,**details):
        db.execute('INSERT INTO events(run_id,action_id,node,status,at,details) VALUES(?,?,?,?,?,?)',
            (run,action,node,status,self.clock(),json.dumps(details,ensure_ascii=False)))
    def session(self,owner,session_id=None):
        with self.db(write=True) as db:
            if session_id:
                row=db.execute('SELECT * FROM sessions WHERE id=? AND owner=?',(session_id,owner)).fetchone()
                if not row or self.clock()-row['updated']>86400:raise BusinessError('SESSION_NOT_FOUND_OR_EXPIRED')
                db.execute('UPDATE sessions SET updated=? WHERE id=?',(self.clock(),session_id))
                return dict(row)
            sid=uuid.uuid4().hex
            db.execute('INSERT INTO sessions VALUES(?,?,NULL,?)',(sid,owner,self.clock()))
            return dict(id=sid,owner=owner,order_id=None)
    def order(self,owner,order_id,db=None):
        if db is None:
            with self.db() as conn:return self.order(owner,order_id,conn)
        row=db.execute('SELECT * FROM orders WHERE id=? AND owner=?',(order_id,owner)).fetchone()
        if not row:raise BusinessError('ORDER_NOT_FOUND_OR_FORBIDDEN')
        return dict(row)
    def bind(self,owner,session_id,order_id):
        self.session(owner,session_id);self.order(owner,order_id)
        with self.db(write=True) as db:db.execute('UPDATE sessions SET order_id=?,updated=? WHERE id=?',(order_id,self.clock(),session_id))
    def check(self,owner,order_id,issue):
        order=self.order(owner,order_id)
        if issue not in ('return','defect','shipping'):raise BusinessError('ISSUE_REQUIRES_HUMAN')
        rule=self.index.rule(order['product'],order['channel'],order['purchased'],issue)
        age=(self.today-date.fromisoformat(order['delivered'])).days
        eligible=order['status']=='delivered' and 0<=age<=rule['days']
        return {'eligible':eligible,'order':order,'policy':rule,'age_days':age,'reason':'within_window' if eligible else 'outside_window_or_status',
            'notice':'仅判断是否可提交演示工单，不承诺退款或审核通过。'}
    def prepare(self,owner,session_id,order_id,issue,description,run_id=None):
        self.session(owner,session_id)
        if not description.strip() or len(description)>500:raise BusinessError('DESCRIPTION_REQUIRED_MAX_500')
        checked=self.check(owner,order_id,issue)
        if not checked['eligible']:raise BusinessError('NOT_ELIGIBLE_FOR_AUTOMATIC_DRAFT')
        if issue=='shipping':raise BusinessError('PHOTOS_REQUIRE_HUMAN_REVIEW')
        run_id=run_id or uuid.uuid4().hex
        payload=dict(order_id=order_id,issue=issue,description=description.strip(),order_revision=checked['order']['revision'],
            policy_id=checked['policy']['id'],index_version=self.index.version,schema=SCHEMA)
        action=uuid.uuid4().hex
        with self.db(write=True) as db:
            # Draft preparation is reversible state, never a submitted ticket.
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,NULL)',(action,owner,session_id,run_id,json.dumps(payload,ensure_ascii=False),digest(payload),'PENDING',self.clock()+900))
            self.event(db,run_id,'prepare','PENDING',action,policy_id=payload['policy_id'],schema=SCHEMA)
        return self.action(owner,action)
    def action(self,owner,action_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM actions WHERE id=? AND owner=?',(action_id,owner)).fetchone()
            if not row:raise BusinessError('ACTION_NOT_FOUND_OR_FORBIDDEN')
            r=dict(row);r['payload']=json.loads(r['payload']);return r
    def confirm(self,owner,action_id,expected_digest,*,lose_response=False):
        # Fault injection is Python-test-only; HTTP input has no such field.
        error=None
        with self.db(write=True) as db:
            a=db.execute('SELECT * FROM actions WHERE id=? AND owner=?',(action_id,owner)).fetchone()
            if not a:raise BusinessError('ACTION_NOT_FOUND_OR_FORBIDDEN')
            if expected_digest!=a['digest']:raise BusinessError('CONFIRMATION_PAYLOAD_MISMATCH')
            if a['state']=='SUCCEEDED':return {'status':'SUCCEEDED','ticket_id':a['ticket_id'],'replayed':True,'action_id':action_id}
            if a['state']!='PENDING':raise BusinessError(a['state'])
            p=json.loads(a['payload']);order=self.order(owner,p['order_id'],db)
            if self.clock()>a['expires']:error='EXPIRED'
            elif (order['revision']!=p['order_revision'] or order['status']!='delivered' or p['index_version']!=self.index.version or p['schema']!=SCHEMA):error='STALE_RECONFIRM_REQUIRED'
            else:
                rule=self.index.rule(order['product'],order['channel'],order['purchased'],p['issue'])
                age=(self.today-date.fromisoformat(order['delivered'])).days
                if not 0<=age<=rule['days'] or rule['id']!=p['policy_id']:error='STALE_RECONFIRM_REQUIRED'
            if error:
                db.execute('UPDATE actions SET state=? WHERE id=?',(error,action_id));self.event(db,a['run_id'],'revalidate',error,action_id)
            else:
                prior=db.execute('SELECT id FROM tickets WHERE owner=? AND order_id=? AND issue=?',(owner,p['order_id'],p['issue'])).fetchone()
                ticket=prior['id'] if prior else 'T-'+uuid.uuid4().hex[:12]
                if not prior:db.execute('INSERT INTO tickets VALUES(?,?,?,?,?,?)',(ticket,action_id,owner,p['order_id'],p['issue'],'OPEN'))
                db.execute('UPDATE actions SET state=?,ticket_id=? WHERE id=?',('SUCCEEDED',ticket,action_id))
                self.event(db,a['run_id'],'confirm','SUCCEEDED',action_id,ticket_id=ticket,deduplicated=bool(prior))
                result={'status':'SUCCEEDED','ticket_id':ticket,'replayed':bool(prior),'action_id':action_id}
        if error:raise BusinessError(error)
        if lose_response:raise TimeoutError('COMMITTED_RESPONSE_LOST')
        return result
    def trace(self,owner,run_id):
        with self.db() as db:
            allowed=db.execute('SELECT 1 FROM actions WHERE owner=? AND run_id=?',(owner,run_id)).fetchone()
            run=db.execute('SELECT * FROM runs WHERE owner=? AND id=?',(owner,run_id)).fetchone()
            if not allowed and not run:raise BusinessError('RUN_NOT_FOUND_OR_FORBIDDEN')
            traces=json.loads(run['trace']) if run else []
            return traces+[dict(r) for r in db.execute('SELECT node,status,at,details FROM events WHERE run_id=? ORDER BY id',(run_id,))]
    def save_run(self,owner,session_id,result):
        # Store structured diagnostics only; never chain of thought, full prompts, or descriptions.
        safe=[{k:v for k,v in t.items() if k in ('node','step','tool_calls','name','tool_call_id','status','ms')} for t in result.get('trace',[])]
        with self.db(write=True) as db:
            db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?)',(result['run_id'],owner,session_id,self.clock(),result['status'],json.dumps(safe),json.dumps(result.get('usage',{}))))
            db.execute('DELETE FROM runs WHERE at < ?',(self.clock()-7*86400,))
    def cancel(self,owner,action_id):
        with self.db(write=True) as db:
            a=db.execute('SELECT * FROM actions WHERE id=? AND owner=?',(action_id,owner)).fetchone()
            if not a:raise BusinessError('ACTION_NOT_FOUND_OR_FORBIDDEN')
            if a['state']!='PENDING':raise BusinessError('ONLY_PENDING_CAN_CANCEL')
            db.execute("UPDATE actions SET state='CANCELLED' WHERE id=?",(action_id,))
            self.event(db,a['run_id'],'cancel','CANCELLED',action_id)
        return {'status':'CANCELLED'}
