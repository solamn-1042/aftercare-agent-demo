"""BM25 baseline vs scope-first retrieval. Cache holds public evidence, never orders."""
import copy
import hashlib
import json
import math
import re
import threading
from collections import Counter,OrderedDict
from datetime import date
from pathlib import Path
from time import perf_counter

def tokens(text):
    words=re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+',text.lower())
    return [t for w in words for t in ([w] if w.isascii() else [w[i:i+2] for i in range(max(1,len(w)-1))])]

class PolicyIndex:
    def __init__(self, path=None, capacity=256):
        raw=Path(path or Path(__file__).with_name('policies.json')).read_bytes()
        self.version=hashlib.sha256(raw).hexdigest()[:16]
        self.docs=json.loads(raw); self.capacity=capacity; self.cache=OrderedDict(); self.lock=threading.Lock()
        self.bags=[Counter(tokens(d['title']+' '+d['text']+' '+d['keywords'])) for d in self.docs]
        df=Counter(t for bag in self.bags for t in bag)
        self.idf={t:math.log(1+(len(self.docs)-n+.5)/(n+.5)) for t,n in df.items()}
        self.avg=sum(map(lambda b:sum(b.values()),self.bags))/len(self.bags)
    def search(self, question, product, channel, purchased, *, mode='scoped', top_k=3, use_cache=True):
        date.fromisoformat(purchased)
        if mode not in ('baseline','scoped'):raise ValueError('UNKNOWN_RETRIEVAL_MODE')
        if not 1<=top_k<=10:raise ValueError('INVALID_TOP_K')
        started=perf_counter()
        key=(self.version,question,product,channel,purchased,mode,top_k)
        with self.lock:
            cached=copy.deepcopy(self.cache.get(key)) if use_cache else None
            if cached is not None:self.cache.move_to_end(key)
        if cached is not None:
            return {**cached,'cache_hit':True,'elapsed_ms':(perf_counter()-started)*1000}
        query=Counter(tokens(question)); ranked=[]
        for d,bag in zip(self.docs,self.bags):
            # ACL is applied even in baseline. No unsafe baseline exposed to the browser.
            if d['audience']!='public':continue
            if mode=='scoped' and not (d['product']==product and d['channel']==channel and d['valid_from']<=purchased<d['valid_until']):continue
            length=sum(bag.values())
            score=sum(self.idf.get(t,0)*bag.get(t,0)*2.5/(bag.get(t,0)+1.5*(.25+.75*length/self.avg)) for t in query)
            if score>0:ranked.append((score,d))
        ranked.sort(key=lambda x:(-x[0],x[1]['id']))
        result={'hits':[dict(d,score=round(score,5)) for score,d in ranked[:top_k]],'index_version':self.version,'mode':mode}
        if use_cache:
            with self.lock:
                self.cache[key]=copy.deepcopy(result);self.cache.move_to_end(key)
                while len(self.cache)>self.capacity:self.cache.popitem(last=False)
        return {**result,'cache_hit':False,'elapsed_ms':(perf_counter()-started)*1000}

    def rule(self, product, channel, purchased, issue):
        matched=[d for d in self.docs if d['audience']=='public' and d['product']==product and d['channel']==channel and d['issue']==issue and d['valid_from']<=purchased<d['valid_until']]
        if len(matched)!=1:raise ValueError('POLICY_MISSING_OR_AMBIGUOUS')
        return matched[0]
