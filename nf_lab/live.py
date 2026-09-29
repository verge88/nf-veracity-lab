"""Real Open5GS NRF/SCP transport, controlled surrogate AFs, external SBI observer."""
import asyncio
import hashlib
import json
import secrets
import time
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
from hypercorn.asyncio import serve
from hypercorn.config import Config
from .h2client import request
from .io import append, dump
from .simulate import SCENARIOS


async def body_of(receive):
    data=b''
    while True:
        event=await receive()
        if event['type']=='http.disconnect': raise ConnectionError('client disconnected')
        data+=event.get('body',b'')
        if len(data)>32768: raise ValueError('body limit')
        if not event.get('more_body'): return data


async def respond(send,status,obj):
    body=json.dumps(obj,separators=(',',':')).encode()
    await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
    await send({'type':'http.response.body','body':body})


class Producer:
    def __init__(self,nf):
        self.nf=nf;self.window=0;self.scenario='normal';self.active=False;self.intensity=0
        self.count=0;self.errors=0;self.reads=0

    def begin(self,window,scenario,active,intensity):
        self.window=window;self.scenario=scenario;self.active=active;self.intensity=intensity
        self.count=self.errors=self.reads=0

    def report(self):
        count,error,mix=self.count,self.errors/max(1,self.count),self.reads/max(1,self.count)
        if self.active and self.scenario=='falsified-report': count=int(count*.65);mix=.05
        if self.active and self.scenario=='low-and-slow': count=int(count/(1+.14*self.intensity));mix=max(0,mix-.16*self.intensity)
        return dict(nf=self.nf,window=self.window,count=count,error=error,mix=mix)

    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return
        raw=await body_of(receive)
        path=scope['path']
        if path=='/lab/v1/report':
            await respond(send,200,self.report());return
        if path=='/lab/v1/kie':
            challenge=json.loads(raw)
            if self.active and self.scenario=='DoS': await asyncio.sleep(.25)
            # Echo is evidence of freshness/reachability, not proof of honest software.
            await respond(send,200,dict(nf=self.nf,nonce=challenge['nonce'],digest=hashlib.sha256(raw).hexdigest()));return
        if path not in ['/lab/v1/read','/lab/v1/update']:
            await respond(send,404,{'error':'unknown lab service'});return
        self.count+=1;self.reads+=int(path.endswith('/read'))
        delay=.006
        status=200
        if self.active and self.scenario=='DoS':
            delay=.2
            if self.count%4==0:status=503
        elif self.scenario=='load-spike' and self.active:delay=.025
        self.errors+=int(status>=400)
        await asyncio.sleep(delay)
        await respond(send,status,dict(nf=self.nf,result='ok' if status==200 else 'overloaded'))


class Observer:
    """Separate trust domain. Logs own timers/status; never uses producer counters."""
    def __init__(self,scp,targets,path,timeout):
        self.scp=scp;self.targets=targets;self.path=path;self.timeout=timeout
        self.window=0;self.events=[]

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return
        headers={k.decode():v.decode() for k,v in scope['headers']}
        target=headers.get('3gpp-sbi-target-apiroot','')
        if target not in self.targets:
            await respond(send,403,{'error':'target outside laboratory allowlist'});return
        body=await body_of(receive)
        started=time.perf_counter()
        path=scope['path'];query=scope.get('query_string',b'').decode()
        error=None
        try:
            upstream=await request(self.scp+path+('?' + query if query else ''),scope['method'],headers,body,self.timeout)
            status,payload=upstream.status,upstream.body
        except (TimeoutError,ConnectionError,OSError) as exc:
            status,payload=504,b'{}';error=type(exc).__name__
        event=dict(kind='observation',window=self.window,nf=self.targets[target],path=path,
            status=status,latency_ms=(time.perf_counter()-started)*1000,
            bytes=len(body)+len(payload)+sum(len(k)+len(v) for k,v in headers.items()),
            error=error,unix_ns=time.time_ns())
        self.events.append(event);append(self.path,event)
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
        await send({'type':'http.response.body','body':payload})


async def start_server(app,url,stop):
    u=urlsplit(url);cfg=Config();cfg.bind=[f'{u.hostname}:{u.port}'];cfg.accesslog=None;cfg.errorlog=None
    cfg.use_reloader=False;cfg.graceful_timeout=2
    await serve(app,cfg,shutdown_trigger=stop.wait)


async def register(nrf,nf,endpoint):
    u=urlsplit(endpoint)
    profile=dict(nfInstanceId=nf,nfType='AF',nfStatus='REGISTERED',heartBeatTimer=5,
        ipv4Addresses=[u.hostname],allowedNfTypes=['AF'])
    r=await request(nrf+'/nnrf-nfm/v1/nf-instances/'+nf,'PUT',{'content-type':'application/json'},json.dumps(profile).encode())
    if r.status not in (200,201): raise RuntimeError(f'NRF registration failed: {r.status} {r.body[:200]!r}')
    return r.json().get('heartBeatTimer',5)


async def heartbeat(nrf,nf,interval,stop,log):
    patch=json.dumps([{'op':'replace','path':'/nfStatus','value':'REGISTERED'}]).encode()
    while not stop.is_set():
        try:await asyncio.wait_for(stop.wait(),max(1,interval))
        except TimeoutError:
            r=await request(nrf+'/nnrf-nfm/v1/nf-instances/'+nf,'PATCH',{'content-type':'application/json-patch+json'},patch)
            append(log,dict(kind='heartbeat',nf=nf,status=r.status,unix_ns=time.time_ns()))
            if r.status not in (200,204):raise RuntimeError(f'NRF heartbeat failed: {r.status}')


async def preflight(cfg):
    # NRF discovery over h2c, direct and routed through real SCP.
    path='/nnrf-disc/v1/nf-instances?target-nf-type=AF&requester-nf-type=AF'
    result={}
    for name,url,headers in [('nrf',cfg['nrf']+path,{}),('scp',cfg['scp']+path,{'3gpp-sbi-target-apiroot':cfg['nrf']})]:
        r=await request(url,headers=headers,timeout=cfg['timeout'])
        if r.status!=200:raise RuntimeError(f'{name} discovery returned {r.status}: {r.body[:200]!r}')
        result[name]=dict(status=r.status,instances=len(r.json().get('nfInstances',[])))
    return result


async def run_live(cfg,scenario,out,seed):
    if scenario not in SCENARIOS:raise ValueError(scenario)
    if cfg['peers']<3 or cfg['rate_per_nf']<=0 or cfg['max_rate_per_nf']>20:
        raise ValueError('Need >=3 peers and positive bounded laboratory rate <=20 per NF')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    raw=out/'events.jsonl';rng=np.random.default_rng(seed)
    run_id='live-'+scenario+'-'+uuid.uuid4().hex[:10]
    producers={str(uuid.uuid4()):Producer(None) for _ in range(cfg['peers'])}
    urls={nf:f"http://{cfg['producer_host']}:{cfg['producer_base_port']+i}" for i,nf in enumerate(producers)}
    observer=Observer(cfg['scp'],{v:k for k,v in urls.items()},raw,cfg['timeout'])
    stop=asyncio.Event();tasks=[];registered=[];rows=[]
    try:
        for nf,app in producers.items():app.nf=nf;tasks.append(asyncio.create_task(start_server(app,urls[nf],stop)))
        tasks.append(asyncio.create_task(start_server(observer,cfg['gateway'],stop)))
        await asyncio.sleep(.3)
        for nf in producers:
            interval=await register(cfg['nrf'],nf,urls[nf]);registered.append(nf)
            tasks.append(asyncio.create_task(heartbeat(cfg['nrf'],nf,interval,stop,raw)))
        dump(out/'preflight.json',await preflight(cfg))
        for window in range(cfg['windows']):
            for task in tasks:
                if task.done():task.result();raise RuntimeError('Lab service terminated unexpectedly')
            started=time.perf_counter();observer.window=window;observer.events=[]
            active=cfg['attack_start']<=window<cfg['attack_end']
            intensity=(window-cfg['attack_start']+1)/max(1,cfg['attack_end']-cfg['attack_start'])
            for i,(nf,app) in enumerate(producers.items()):app.begin(window,scenario,active and (i==0 or scenario=='load-spike'),intensity)
            async def workload(nf,index):
                target=active and index==0
                rate=cfg['rate_per_nf']*(2.4 if active and scenario=='load-spike' else 4 if target and scenario=='DoS' else 1)
                rate=min(rate,cfg['max_rate_per_nf'])
                mix=.70 if target and scenario=='compromised-NF' else .22+.16*intensity if target and scenario=='low-and-slow' else .22
                duration=cfg['window_seconds']*.7
                count=max(1,int(rate*duration))
                async def one(index):
                    await asyncio.sleep(index/rate)
                    path='/lab/v1/read' if rng.random()<mix else '/lab/v1/update'
                    try:await request(cfg['gateway']+path,'POST',{'3gpp-sbi-target-apiroot':urls[nf],'content-type':'application/json'},b'{}',cfg['timeout']+.5)
                    except (OSError,ConnectionError,TimeoutError) as exc:
                        append(raw,dict(kind='client_failure',nf=nf,window=window,error=type(exc).__name__))
                        raise RuntimeError('Observer ingress incomplete; abort run') from exc
                await asyncio.gather(*(one(k) for k in range(count)))
                return count
            attempted=await asyncio.gather(*(workload(nf,i) for i,nf in enumerate(producers)))
            for i,(nf,app) in enumerate(producers.items()):
                challenge=json.dumps(dict(nonce=secrets.token_hex(16),window=window)).encode()
                t=time.perf_counter();failed=0;kie_bytes=len(challenge)
                try:
                    r=await request(cfg['gateway']+'/lab/v1/kie','POST',{'3gpp-sbi-target-apiroot':urls[nf],'content-type':'application/json'},challenge,cfg['timeout']+.5)
                    kie_bytes+=len(r.body)
                    obj=r.json();failed=int(r.status!=200 or obj.get('nonce')!=json.loads(challenge)['nonce'] or obj.get('digest')!=hashlib.sha256(challenge).hexdigest() or obj.get('nf')!=nf)
                except (TimeoutError,OSError,ConnectionError,ValueError):failed=1
                kie_latency=(time.perf_counter()-t)*1000
                append(raw,dict(kind='kie',nf=nf,window=window,failed=failed,latency_ms=kie_latency,bytes=kie_bytes))
                missing=0
                try:
                    rr=await request(urls[nf]+'/lab/v1/report',timeout=cfg['timeout'])
                    report=rr.json()
                    if rr.status!=200 or report.get('window')!=window or report.get('nf')!=nf:raise ValueError('Invalid report binding')
                except (TimeoutError,OSError,ConnectionError,ValueError):missing=1;report=dict(count=0,error=0,mix=0)
                append(raw,dict(kind='report',window=window,nf=nf,missing=missing,report=report))
                ev=[e for e in observer.events if e['nf']==nf and e['path'] in ['/lab/v1/read','/lab/v1/update']]
                count=len(ev);bad=active and i==0 and scenario not in ['normal','load-spike']
                row=dict(run_id=run_id,mode='open5gs-surrogate',scenario=scenario,window=window,nf=nf,nf_type='AF',peer_group='lab-af',seconds=cfg['window_seconds'],attack_start=cfg['attack_start'],
                    label_attack=int(bad),label_falsification=int(active and i==0 and scenario in ['falsified-report','low-and-slow']),
                    obs_count=count,obs_error=sum(e['status']>=400 for e in ev)/max(1,count),obs_mix=sum(e['path'].endswith('/read') for e in ev)/max(1,count),obs_latency=float(np.mean([e['latency_ms'] for e in ev])) if ev else 0,
                    rep_count=report['count'],rep_error=report['error'],rep_mix=report['mix'],report_missing=missing,kie_latency=kie_latency,kie_failed=failed,kie_bytes=kie_bytes,traffic_bytes=sum(e['bytes'] for e in ev),observation_complete=int(count==attempted[i]))
                rows.append(row);append(out/'windows.jsonl',row)
            elapsed=time.perf_counter()-started
            if elapsed>cfg['window_seconds']:
                raise RuntimeError(f'Window overrun ({elapsed:.2f}s); increase window_seconds. Partial run not valid.')
            await asyncio.sleep(cfg['window_seconds']-elapsed)
        for task in tasks:
            if task.done():task.result();raise RuntimeError('Lab service terminated before completion')
        if any(not r['observation_complete'] for r in rows):
            raise RuntimeError('Observation coverage incomplete; run cannot be marked complete')
        dump(out/'completion.json',dict(complete=True,run_id=run_id,windows=len(rows)))
        return rows
    finally:
        stop.set()
        results=await asyncio.gather(*tasks,return_exceptions=True)
        for result in results:
            if isinstance(result,Exception):append(raw,dict(kind='service_error',error=str(result)))
        for nf in registered:
            try:
                r=await request(cfg['nrf']+'/nnrf-nfm/v1/nf-instances/'+nf,'DELETE')
                append(raw,dict(kind='deregister',nf=nf,status=r.status))
            except Exception as exc:append(raw,dict(kind='cleanup_error',nf=nf,error=str(exc)))
