"""Dedicated v2 workbench; fixed demo identity, no public multitenant claims."""
import asyncio
import hmac
import json
import os
import uuid
from pathlib import Path
from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool
from lab.workflow import Workflow,BusinessError

class Input(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class SessionInput(Input):session_id:str|None=Field(default=None,max_length=64)
class Query(Input):
    question:str=Field(min_length=1,max_length=1000)
    order_id:str=Field(pattern=r'^[AB][0-9]{3}$')
class Check(Input):
    order_id:str=Field(pattern=r'^[AB][0-9]{3}$')
    issue:str=Field(pattern=r'^(return|defect|shipping)$')
class Draft(Check):
    session_id:str=Field(min_length=1,max_length=64)
    description:str=Field(min_length=1,max_length=500)
class Confirm(Input):digest:str=Field(pattern=r'^[a-f0-9]{64}$')
class Chat(Input):
    session_id:str=Field(min_length=1,max_length=64)
    question:str=Field(min_length=1,max_length=1000)

def create_app(workflow=None,token=None):
    app=FastAPI(title='Aftercare Case Lab',version='2.0.0',docs_url=None,redoc_url=None)
    wf=workflow or Workflow(os.getenv('CASE_DB','var/cases.sqlite3'))
    slots=asyncio.Semaphore(2)
    async def auth(authorization:str=Header(default='')):
        secret=token if token is not None else os.getenv('API_TOKEN','')
        if len(secret)<24:raise HTTPException(503,'Configure API_TOKEN with at least 24 characters')
        if not hmac.compare_digest(authorization,'Bearer '+secret):raise HTTPException(401,'Unauthorized')
        return 'demo-alice'
    @app.middleware('http')
    async def bounds(request,call_next):
        request.state.request_id=uuid.uuid4().hex
        if request.method=='POST':
            body=b''
            async for chunk in request.stream():
                body+=chunk
                if len(body)>8192:return JSONResponse({'detail':'Body too large'},status_code=413)
            request._body=body
        response=await call_next(request)
        response.headers.update({'X-Request-ID':request.state.request_id,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        return response
    @app.exception_handler(BusinessError)
    async def business_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)
    @app.get('/',response_class=HTMLResponse)
    def home():return Path(__file__).with_name('workbench.html').read_text(encoding='utf-8')
    @app.get('/health/ready')
    def health():return dict(status='ready',policies=len(wf.index.docs),index_version=wf.index.version,mode='synthetic',business_date=str(wf.today))
    @app.get('/api/evaluation')
    def evaluation():
        p=Path(__file__).resolve().parents[1]/'evals/lab_report.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'status':'run python -m lab.evaluate'}
    @app.post('/api/session')
    def session(body:SessionInput,owner=Depends(auth)):return wf.session(owner,body.session_id)
    @app.get('/api/orders/{order_id}')
    def order(order_id:str,owner=Depends(auth)):return wf.order(owner,order_id)
    @app.post('/api/search')
    def search(body:Query,owner=Depends(auth)):
        o=wf.order(owner,body.order_id)
        return {m:wf.index.search(body.question,o['product'],o['channel'],o['purchased'],mode=m) for m in ['baseline','scoped']}
    @app.post('/api/check')
    def check(body:Check,owner=Depends(auth)):return wf.check(owner,**body.model_dump())
    @app.post('/api/drafts')
    def draft(body:Draft,owner=Depends(auth)):return wf.prepare(owner,**body.model_dump())
    @app.get('/api/actions/{action_id}')
    def action(action_id:str,owner=Depends(auth)):return wf.action(owner,action_id)
    @app.post('/api/actions/{action_id}/confirm')
    def confirm(action_id:str,body:Confirm,owner=Depends(auth)):return wf.confirm(owner,action_id,body.digest)
    @app.post('/api/actions/{action_id}/cancel')
    def cancel(action_id:str,owner=Depends(auth)):return wf.cancel(owner,action_id)
    @app.get('/api/runs/{run_id}')
    def trace(run_id:str,owner=Depends(auth)):return wf.trace(owner,run_id)
    @app.post('/api/chat')
    async def chat(body:Chat,owner=Depends(auth)):
        try:await asyncio.wait_for(slots.acquire(),.1)
        except TimeoutError:raise HTTPException(429,'Busy',headers={'Retry-After':'2'})
        try:
            from lab.agent import CaseAgent
            return await run_in_threadpool(CaseAgent(wf,owner,body.session_id).ask,body.question)
        finally:slots.release()
    return app

app=create_app()
