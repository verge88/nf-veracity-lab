import copy
import numpy as np
import pytest
from nf_lab.features import FeatureBuilder, MODELS
from nf_lab.simulate import simulate, SCENARIOS
from nf_lab.evaluate import split_runs, metrics, detection_delay
from nf_lab.pcap import decode


@pytest.fixture
def cfg():
    return dict(seed=7,runs_per_scenario=5,windows=8,window_seconds=5,attack_start=2,attack_end=6,peers=3,scenarios=SCENARIOS)


def test_reproducibility_and_controls(cfg):
    a=simulate(cfg);assert a==simulate(cfg)
    assert not any(r['label_attack'] for r in a if r['scenario']=='load-spike')
    assert any(r['label_falsification'] for r in a)
    assert not any(r['label_falsification'] for r in a if r['scenario']=='DoS')


def test_group_split_and_label_independence(cfg):
    rows=simulate(cfg);parts=split_runs(rows,7)
    assert not parts['train'] & parts['test']
    assert not parts['validation'] & parts['test']
    builder=FeatureBuilder().fit([r for r in rows if r['run_id'] in parts['train']])
    before=builder.transform(rows)
    changed=copy.deepcopy(rows)
    for r in changed:r['label_attack']=1-r['label_attack'];r['label_falsification']=1-r['label_falsification']
    after=builder.transform(changed)
    assert [[r[k] for k in MODELS['M4']] for r in before]==[[r[k] for k in MODELS['M4']] for r in after]


def test_causality_and_exact_consistency(cfg):
    rows=[r for r in simulate(cfg) if r['scenario']=='normal']
    builder=FeatureBuilder().fit(rows)
    before=builder.transform(rows)
    future=copy.deepcopy(rows)
    for r in future:
        r['rep_count']=r['obs_count'];r['rep_error']=r['obs_error'];r['rep_mix']=r['obs_mix']
        if r['window']==7:r['obs_count']*=100
    after=builder.transform(future)
    assert all(r['D_rep']==0 for r in after if r['window']<7)
    assert [r['ewma_rate'] for r in before if r['window']<7]==[r['ewma_rate'] for r in after if r['window']<7]


def test_missing_report_and_peer_minimum(cfg):
    rows=simulate(cfg);builder=FeatureBuilder().fit(rows)
    rows[0]['report_missing']=1
    out=builder.transform(rows)
    r=next(x for x in out if x['run_id']==rows[0]['run_id'] and x['window']==0 and x['nf']=='nf-0')
    assert r['D_rep']==0 and r['report_missing']==1
    with pytest.raises(ValueError):builder.transform([x for x in rows if x['nf']=='nf-0'])


def test_metrics_and_missed_delay():
    m=metrics([0,0,1,1],[.1,.8,.2,.9],.5)
    assert m['f1']==.5 and m['fpr']==.5 and m['auc']==.75
    assert metrics([0,0],[0,1],.5)['auc'] is None
    rows=[dict(run_id='r',nf='n',label_attack=1,window=w,seconds=5) for w in [2,3]]
    assert detection_delay(rows,[0,1],.5,'label_attack')['delay_mean_s']==10
    assert detection_delay(rows,[0,0],.5,'label_attack')['missed_episodes']==1


def packet(t,src,dst,tcp,node):
    return {'_source':{'layers':{'ip':{'ip.src':src,'ip.dst':dst},'frame':{'frame.time_epoch':str(t)},'tcp':{'tcp.stream':str(tcp)},'http2':{'http2.stream':node}}}}


def test_passive_pairing_no_double_count():
    req={'http2.streamid':'1','http2.headers.method':'GET','http2.headers.path':'/x'}
    res={'http2.streamid':'1','http2.headers.status':'200'}
    packets=[packet(1,'scp','nf',1,req),packet(2,'scp','nf',2,req),packet(3,'nf','scp',1,res),packet(4,'nf','scp',2,res),packet(4,'nf','scp',2,res)]
    events,quality=decode(packets,{'nf':'nf-1'},'scp')
    assert len(events)==2 and events[0]['latency_ms']==2000
    assert quality['unanswered_requests']==0
