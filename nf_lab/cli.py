import argparse
import asyncio
import json
from pathlib import Path
import yaml
from .io import append, dump, manifest, read_events
from .simulate import simulate, SCENARIOS
from .evaluate import evaluate


def config(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


def main():
    p=argparse.ArgumentParser(description='NF reporting consistency laboratory')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('simulate');s.add_argument('--config',default='configs/experiment.yaml');s.add_argument('--out',required=True)
    e=sub.add_parser('evaluate');e.add_argument('--inputs',nargs='+',required=True);e.add_argument('--out',required=True);e.add_argument('--config',default='configs/experiment.yaml')
    f=sub.add_parser('preflight');f.add_argument('--config',default='configs/live.yaml')
    l=sub.add_parser('live');l.add_argument('--config',default='configs/live.yaml');l.add_argument('--scenario',choices=SCENARIOS,required=True);l.add_argument('--out',required=True);l.add_argument('--seed',type=int,default=731)
    c=sub.add_parser('pcap');c.add_argument('--input',required=True);c.add_argument('--out',required=True);c.add_argument('--map',required=True);c.add_argument('--scp-ip',default='127.0.0.200');c.add_argument('--port',type=int,default=7777);c.add_argument('--keylog')
    args=p.parse_args()
    if args.command=='simulate':
        cfg=config(args.config);out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
        rows=simulate(cfg)
        for row in rows:append(out/'windows.jsonl',row)
        manifest(out,cfg,'synthetic');evaluate(rows,out,cfg)
        dump(out/'completion.json',dict(complete=True))
    elif args.command=='evaluate':
        cfg=config(args.config);out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
        rows=[]
        for name in args.inputs:
            path=Path(name)
            completion=path.parent/'completion.json'
            if not completion.exists() or not json.loads(completion.read_text())['complete']:
                raise ValueError(f'Run is incomplete: {name}')
            rows.extend(read_events(path))
        keys=[(r['run_id'],r['window'],r['nf']) for r in rows]
        if len(set(keys))!=len(keys):raise ValueError('Duplicate windows in inputs')
        evaluate(rows,out,cfg);manifest(out,cfg,next(iter({r['mode'] for r in rows})))
    elif args.command=='preflight':
        from .live import preflight
        print(json.dumps(asyncio.run(preflight(config(args.config))),indent=2))
    elif args.command=='live':
        from .live import run_live
        cfg=config(args.config)
        asyncio.run(run_live(cfg,args.scenario,args.out,args.seed))
        manifest(args.out,{**cfg,'scenario':args.scenario,'seed':args.seed},'open5gs-surrogate')
    elif args.command=='pcap':
        from .pcap import import_pcap
        print(import_pcap(args.input,args.out,json.loads(Path(args.map).read_text()),args.scp_ip,args.port,args.keylog))


if __name__=='__main__':main()
