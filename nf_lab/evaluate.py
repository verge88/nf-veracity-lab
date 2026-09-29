import time
from collections import defaultdict
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, confusion_matrix
from .features import FeatureBuilder, MODELS
from .io import dump, csv_write


def split_runs(rows, seed):
    by_scenario = defaultdict(set)
    for r in rows:
        by_scenario[r['scenario']].add(r['run_id'])
    rng = np.random.default_rng(seed)
    splits = dict(train=set(), validation=set(), test=set())
    for scenario, ids in sorted(by_scenario.items()):
        ids = sorted(ids)
        if len(ids) < 5:
            raise ValueError(f'{scenario}: need >=5 independent runs, found {len(ids)}')
        rng.shuffle(ids)
        a, b = max(1, int(.6*len(ids))), max(1, int(.2*len(ids)))
        splits['train'].update(ids[:a])
        splits['validation'].update(ids[a:a+b])
        splits['test'].update(ids[a+b:])
    return splits


def metrics(y, score, threshold):
    y, score = np.asarray(y), np.asarray(score)
    tn, fp, fn, tp = confusion_matrix(y, score >= threshold, labels=[0, 1]).ravel()
    def ratio(a,b):
        return float(a/b) if b else None
    return dict(n=len(y), tp=int(tp), fp=int(fp), tn=int(tn), fn=int(fn),
        precision=ratio(tp,tp+fp), recall=ratio(tp,tp+fn), fpr=ratio(fp,fp+tn),
        f1=ratio(2*tp,2*tp+fp+fn),
        auc=float(roc_auc_score(y,score)) if len(set(y)) == 2 else None,
        average_precision=float(average_precision_score(y,score)) if sum(y) else None)


def threshold_on_validation(y, scores):
    candidates = np.unique(np.r_[0., 1.000001, np.quantile(scores,np.linspace(0,1,201))])
    def objective(t):
        p=scores>=t
        tp=np.sum(p & (y==1)); fp=np.sum(p & (y==0)); fn=np.sum(~p & (y==1))
        return (2*tp/max(1,2*tp+fp+fn),t)
    return float(max(candidates,key=objective))


def detection_delay(rows, scores, threshold, target):
    episodes = defaultdict(list)
    for r, s in zip(rows, scores):
        episodes[(r['run_id'],r['nf'])].append((r,s))
    delays, missed = [], 0
    for series in episodes.values():
        positives = [(r,s) for r,s in series if r[target]]
        if not positives:
            continue
        start = min(r['window'] for r,s in positives)
        hits = [r for r,s in positives if s >= threshold]
        if hits:
            # End-of-window decision: zero windows still costs one full window.
            delays.append((min(r['window'] for r in hits)-start+1)*positives[0][0]['seconds'])
        else:
            missed += 1
    return dict(detected_episodes=len(delays), missed_episodes=missed,
        delay_mean_s=float(np.mean(delays)) if delays else None,
        delay_p95_s=float(np.quantile(delays,.95)) if delays else None)


def evaluate(rows, out, cfg):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    modes = {r['mode'] for r in rows}
    if len(modes) != 1:
        raise ValueError('Synthetic and Open5GS runs must be evaluated separately')
    if any(not r['observation_complete'] for r in rows):
        raise ValueError('Incomplete observation: fix coverage before evaluation')
    splits = split_runs(rows, cfg['seed'])
    dump(out/'split.json', {k:sorted(v) for k,v in splits.items()})
    builder = FeatureBuilder().fit([r for r in rows if r['run_id'] in splits['train']])
    features = builder.transform(rows)
    dump(out/'scales.json', {k:v.tolist() for k,v in builder.scales.items()})
    csv_write(out/'dataset.csv', features)
    data = {k:[r for r in features if r['run_id'] in ids] for k,ids in splits.items()}
    reports, predictions, timings, curves = [], [], [], []
    target_scores = {}
    for target in ['label_attack', 'label_falsification']:
        y = {k:np.array([r[target] for r in rr]) for k,rr in data.items()}
        if len(set(y['train'])) != 2:
            raise ValueError(f'Train split must contain both classes for {target}')
        for model, names in MODELS.items():
            x = {k:np.array([[r[n] for n in names] for r in rr]) for k,rr in data.items()}
            classifier = RandomForestClassifier(n_estimators=100, min_samples_leaf=5,
                max_depth=10, class_weight='balanced_subsample', random_state=cfg['seed'], n_jobs=-1)
            start = time.perf_counter()
            classifier.fit(x['train'], y['train'])
            train_s = time.perf_counter()-start
            scores_val = classifier.predict_proba(x['validation'])[:,1]
            threshold = threshold_on_validation(y['validation'], scores_val)
            start = time.perf_counter()
            scores = classifier.predict_proba(x['test'])[:,1]
            timings.append(dict(target=target,model=model,train_s=train_s,
                predict_us_per_row=(time.perf_counter()-start)*1e6/len(scores)))
            target_scores[(target,model)] = (y['test'],scores,threshold)
            for scenario in ['ALL', *sorted({r['scenario'] for r in data['test']})]:
                indices = [i for i,r in enumerate(data['test']) if scenario=='ALL' or r['scenario']==scenario]
                subset = [data['test'][i] for i in indices]
                m = metrics(y['test'][indices], scores[indices], threshold)
                reports.append(dict(target=target,model=model,scenario=scenario,threshold=threshold,**m,
                    **detection_delay(subset,scores[indices],threshold,target)))
            for r, score in zip(data['test'],scores):
                predictions.append(dict(target=target,model=model,run_id=r['run_id'],nf=r['nf'],
                    window=r['window'],scenario=r['scenario'],label=r[target],score=float(score),
                    threshold=threshold,prediction=int(score>=threshold)))
            for cut in np.linspace(0,1,101):
                m=metrics(y['test'],scores,cut)
                curves.append(dict(target=target,model=model,threshold=float(cut),fpr=m['fpr'],recall=m['recall'],precision=m['precision']))
    # Paired cluster bootstrap: resample complete test runs, never individual windows.
    rng=np.random.default_rng(cfg['seed'])
    ids=sorted(splits['test'])
    index={run:np.array([i for i,r in enumerate(data['test']) if r['run_id']==run]) for run in ids}
    ablations=[]
    for target in ['label_attack','label_falsification']:
        for baseline in ['M3','M4-no-KIE','M4-no-peer','M4-no-time','Drep-only']:
            y,s4,t4=target_scores[(target,'M4')]
            _,sb,tb=target_scores[(target,baseline)]
            delta=[]
            for _ in range(cfg.get('bootstrap_samples',300)):
                ii=np.concatenate([index[k] for k in rng.choice(ids,len(ids),replace=True)])
                f4,fb=metrics(y[ii],s4[ii],t4)['f1'],metrics(y[ii],sb[ii],tb)['f1']
                if f4 is not None and fb is not None: delta.append(f4-fb)
            ablations.append(dict(target=target,comparison='M4 - '+baseline,
                delta_f1=metrics(y,s4,t4)['f1']-metrics(y,sb,tb)['f1'],
                ci95_low=float(np.quantile(delta,.025)) if delta else None,
                ci95_high=float(np.quantile(delta,.975)) if delta else None))
    csv_write(out/'metrics.csv',reports)
    csv_write(out/'predictions.csv',predictions)
    csv_write(out/'curves.csv',curves)
    csv_write(out/'timings.csv',timings)
    csv_write(out/'ablation.csv',ablations)
    overhead=sum(r['kie_bytes'] for r in rows)/max(1,sum(r['traffic_bytes'] for r in rows))
    dump(out/'summary.json', dict(mode=next(iter(modes)),rows=len(rows),runs=len({r['run_id'] for r in rows}),
        kie_application_byte_ratio=overhead,models=MODELS,ablation=ablations,
        note='Application payload/header estimate; excludes TCP/TLS/IP framing and report/NRF traffic.'))
    return reports
