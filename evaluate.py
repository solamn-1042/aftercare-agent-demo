"""Frozen self-authored demo set: retrieval and real model/tool execution are separate."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import statistics
import time
from retriever import get_embedder, load_chunks
from vector_store import get_collection, search_index, fingerprint
from hybrid_search import bm25_search, rrf_fuse
from service.ticket_engine import TicketEngine

ROOT=Path(__file__).resolve().parent

def main(with_llm=False):
    cases=json.loads((ROOT/'evals/cases.json').read_text(encoding='utf-8'))
    cold=time.perf_counter()
    embedder,col,chunks=get_embedder(),get_collection(),load_chunks()
    search_index('退货规则',3,col,embedder)
    warmup=(time.perf_counter()-cold)*1000
    records=[]
    for case in cases:
        if case['kind']!='policy': continue
        for method in ['vector','bm25','hybrid']:
            start=time.perf_counter()
            if method=='vector': hits=search_index(case['question'],3,col,embedder)
            elif method=='bm25': hits=bm25_search(case['question'],3,chunks)
            else: hits=rrf_fuse(search_index(case['question'],10,col,embedder),bm25_search(case['question'],10,chunks),3)
            records.append(dict(id=case['id'],method=method,hit=any(case['gold_text'] in h['text'] for h in hits),
                ms=round((time.perf_counter()-start)*1000,3),chunk_ids=[h['chunk_id'] for h in hits]))
    summary={}
    for method in ['vector','bm25','hybrid']:
        rows=[r for r in records if r['method']==method]
        times=sorted(r['ms'] for r in rows)
        summary[method]=dict(n=len(rows),hits=sum(r['hit'] for r in rows),hit_at_3=statistics.mean(r['hit'] for r in rows),
            mean_ms=statistics.mean(times),p95_ms=times[int(.95*(len(times)-1))])
    meta=dict(at_utc=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),python=platform.python_version(),
        embedding='BAAI/bge-small-zh-v1.5',warmup_ms=warmup,chunks=len(chunks),corpus_fingerprint=fingerprint(chunks),
        cases_sha256=hashlib.sha256((ROOT/'evals/cases.json').read_bytes()).hexdigest(),
        limitations='Self-authored synthetic shop: 40 policy, 10 order, 10 negative cases. No production accuracy claim. Warmup excluded.')
    (ROOT/'evals/retrieval.json').write_text(json.dumps(dict(**meta,summary=summary,records=records),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)
    if not with_llm: return
    engine=TicketEngine(col,embedder)
    if engine.client is None: raise RuntimeError('No configured API key')
    def run(case):
        start=time.perf_counter()
        result=engine.ask(case['question'])
        used=[x['tool'] for x in result['trace'] if x['node']=='tools']
        return dict(id=case['id'],kind=case['kind'],question=case['question'],status=result['status'],answer=result['answer'],
            citations=result.get('citations',[]),trace=result['trace'],elapsed_ms=round((time.perf_counter()-start)*1000,2),
            expected_tools_present=all(t in used for t in case['expected_tools']) if case['kind']!='negative' else None,
            policy_evidence_hit=any(case['gold_text'] in h['text'] for h in result['hits']) if case['kind']=='policy' else None)
    results=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs={pool.submit(run,c):c for c in cases}
        for job in as_completed(jobs):
            r=job.result();results.append(r)
            print(r['id'],r['status'],flush=True)
            payload=dict(**meta,model=engine.model,concurrency=2,completed=len(results),
                statuses=dict(Counter(x['status'] for x in results)),records=sorted(results,key=lambda x:x['id']),
                factual_accuracy='Requires explicit human evidence review; citation presence is not factual correctness.')
            (ROOT/'evals/agent.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    engine.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--with-llm',action='store_true');main(p.parse_args().with_llm)
