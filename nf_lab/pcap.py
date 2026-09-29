"""Offline independent SBI observation from tshark JSON, one selected SCP leg.

Capture must start before HTTP/2 connection prefaces/HPACK state. A request/response
pair is keyed by TCP stream and HTTP/2 stream, NOT by HTTP/2 stream alone.
"""
import json
import subprocess
from pathlib import Path
from .io import append, dump


def stream_nodes(obj):
    if isinstance(obj,dict):
        if 'http2.streamid' in obj:
            yield obj
        else:
            for value in obj.values():yield from stream_nodes(value)
    elif isinstance(obj,list):
        for value in obj:yield from stream_nodes(value)


def values(obj,name):
    found=[]
    if isinstance(obj,dict):
        for key,value in obj.items():
            if key==name:found.extend(value if isinstance(value,list) else [value])
            elif isinstance(value,(dict,list)):found.extend(values(value,name))
    elif isinstance(obj,list):
        for item in obj:found.extend(values(item,name))
    return found


def first(obj,name,default=None):
    v=values(obj,name)
    return v[0] if v else default


def decode(packets,nf_map,scp_ip):
    pending={};events=[];seen=set();orphan=0
    for packet in packets:
        layers=packet['_source']['layers']
        src=first(layers,'ip.src');dst=first(layers,'ip.dst')
        # Select producer-side SCP egress only; prevents two-leg double counting.
        if scp_ip not in (src,dst):continue
        other=dst if src==scp_ip else src
        if other not in nf_map:continue
        tcp=first(layers,'tcp.stream');timestamp=float(first(layers,'frame.time_epoch',0))
        for node in stream_nodes(layers.get('http2',{})):
            stream=first(node,'http2.streamid')
            if str(stream)=='0':continue
            key=(str(tcp),str(stream))
            method=first(node,'http2.headers.method');status=first(node,'http2.headers.status')
            if method and src==scp_ip:
                pending.setdefault(key,dict(nf=nf_map[other],method=method,path=first(node,'http2.headers.path',''),started=timestamp))
            if status and dst==scp_ip and key not in seen:
                if key not in pending:orphan+=1;continue
                req=pending.pop(key);seen.add(key)
                events.append(dict(kind='passive_sbi',**req,status=int(status),latency_ms=max(0,(timestamp-req['started'])*1000),tcp_stream=key[0],h2_stream=key[1]))
    return events,dict(unanswered_requests=len(pending),orphan_responses=orphan,matched=len(events),
        latency_semantics='request headers to response headers; not full response time')


def import_pcap(pcap,out,nf_map,scp_ip,port=7777,keylog=None):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    protocol='tls' if keylog else 'http2'
    cmd=['tshark','-r',str(pcap),'-d',f'tcp.port=={port},{protocol}','-Y','http2','-T','json','--no-duplicate-keys']
    if keylog:cmd+=['-o',f'tls.keylog_file:{keylog}']
    # Raw evidence retained for inspection; never interpolate shell commands.
    with (out/'tshark.json').open('w',encoding='utf-8') as f:
        subprocess.run(cmd,stdout=f,check=True,text=True)
    packets=json.loads((out/'tshark.json').read_text(encoding='utf-8'))
    events,quality=decode(packets,nf_map,scp_ip)
    for event in events:append(out/'observations.jsonl',event)
    quality['source']=str(pcap);quality['scp_leg']='producer-side'
    dump(out/'capture_quality.json',quality)
    if not events:raise ValueError('No matched HTTP/2 observations: check capture, IP map, TLS, preface and tshark fields')
    return quality
