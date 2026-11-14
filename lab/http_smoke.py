"""Real HTTP smoke against a running demo; writes one fictional ticket."""
import argparse,json
from pathlib import Path
import httpx

def main(url,token):
    with httpx.Client(base_url=url,timeout=15,trust_env=False) as c:
        h={'Authorization':'Bearer '+token}
        assert c.get('/health/ready').status_code==200
        assert c.post('/api/session',json={}).status_code==401
        assert c.get('/api/orders/B001',headers=h).status_code==409
        s=c.post('/api/session',json={},headers=h);s.raise_for_status()
        a=c.post('/api/drafts',json=dict(session_id=s.json()['id'],order_id='A001',issue='defect',description='Smoke test: synthetic headphones do not power on'),headers=h);a.raise_for_status();a=a.json()
        r=c.post('/api/actions/'+a['id']+'/confirm',json={'digest':a['digest']},headers=h);r.raise_for_status()
        replay=c.post('/api/actions/'+a['id']+'/confirm',json={'digest':a['digest']},headers=h);replay.raise_for_status()
        assert r.json()['ticket_id']==replay.json()['ticket_id'] and replay.json()['replayed']
        result=dict(health=200,unauthorized=401,cross_owner=409,confirm=r.json(),replay=replay.json(),request_id=r.headers['x-request-id'])
        Path('evals/lab_http_smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8001');p.add_argument('--token',required=True);args=p.parse_args();main(args.url,args.token)
