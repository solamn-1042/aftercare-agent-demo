"""Frozen family holdout, paired retrieval ablation and exact-query cache microbenchmark."""
import hashlib,json,platform,statistics,time
from pathlib import Path
from datetime import datetime,timezone
from lab.retrieval import PolicyIndex

def percentile(xs,q):return sorted(xs)[max(0,int((len(xs)-1)*q))]
def main():
    root=Path(__file__).resolve().parent;index=PolicyIndex()
    raw=(root/'cases.json').read_bytes();cases=json.loads(raw);rows=[]
    for c in cases:
        for mode in ['baseline','scoped']:
            r=index.search(c['question'],c['product'],c['channel'],c['purchased'],mode=mode,use_cache=False)
            ids=[h['id'] for h in r['hits']]
            rows.append(dict(id=c['id'],split=c['split'],kind=c['kind'],mode=mode,gold=c['gold'],hits=ids,
                hit1=ids[:1]==[c['gold']],hit3=c['gold'] in ids,rr=1/(ids.index(c['gold'])+1) if c['gold'] in ids else 0,
                scope_violations=sum(not(h['product']==c['product'] and h['channel']==c['channel'] and h['valid_from']<=c['purchased']<h['valid_until']) for h in r['hits']),
                acl_violations=sum(h['audience']!='public' for h in r['hits']),elapsed_ms=r['elapsed_ms']))
    summary={}
    for split in ['dev','test']:
        summary[split]={}
        for mode in ['baseline','scoped']:
            selected=[r for r in rows if r['split']==split and r['mode']==mode and r['gold']]
            summary[split][mode]=dict(n=len(selected),hit1=statistics.mean(r['hit1'] for r in selected),hit3=statistics.mean(r['hit3'] for r in selected),
                mrr3=statistics.mean(r['rr'] for r in selected),scope_violations=sum(r['scope_violations'] for r in selected),
                acl_violations=sum(r['acl_violations'] for r in selected),p50_ms=statistics.median(r['elapsed_ms'] for r in selected),p95_ms=percentile([r['elapsed_ms'] for r in selected],.95))
    # Keep cache test separate: same query/context, different traversal cost, no LLM latency claims.
    sample=cases[0];uncached=[];cached=[];identical=True
    for i in range(100):
        a=index.search(sample['question'],sample['product'],sample['channel'],sample['purchased'],use_cache=False)
        b=index.search(sample['question'],sample['product'],sample['channel'],sample['purchased'])
        identical &= a['hits']==b['hits'];uncached.append(a['elapsed_ms']);cached.append(b['elapsed_ms'])
    negative=[r for r in rows if r['gold'] is None and r['mode']=='scoped']
    report=dict(at_utc=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),python=platform.python_version(),
        policy_count=len(index.docs),case_count=len(cases),index_version=index.version,cases_sha256=hashlib.sha256(raw).hexdigest(),
        split='dev: first 5 product families (150 positives); test: last 3 families (90 positives) + 12 unanswerable probes',
        summary=summary,negative_probes=dict(n=len(negative),returned_candidates=sum(bool(r['hits']) for r in negative),
            note='检索候选不等于可回答；本评测未实现生成层无答案评分，不能报告拒答准确率。'),
        cache=dict(repeats=100,identical_evidence=identical,uncached_mean_ms=statistics.mean(uncached),warm_mean_ms=statistics.mean(cached[1:]),
            uncached_p95_ms=percentile(uncached,.95),warm_p95_ms=percentile(cached[1:],.95)),
        limitations='自建合成政策与模板/口语用例；按商品族留出但由同一作者构建，未独立人工标注。比较范围过滤前后，不是模型答案准确率、生产性能或Reranker收益。缓存实验仅统计检索CPU耗时。',
        failures=[r for r in rows if r['mode']=='scoped' and r['gold'] and not r['hit3']],records=rows)
    out=root.parent/'evals/lab_report.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['summary','cache','negative_probes']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
