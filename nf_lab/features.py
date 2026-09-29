"""Causal temporal features; fixed robust scales fitted only on normal training rows."""
from collections import defaultdict
import numpy as np

LOCAL = ['rate', 'error', 'mix', 'latency']
TEMPORAL = ['delta_rate', 'delta_mix', 'ewma_rate', 'ewma_mix']
ACTIVE = ['kie_latency', 'kie_failed']
PEER = ['D_peer']
REPORT = ['D_rep', 'report_missing']
MODELS = {
    'M0': LOCAL,
    'M1': LOCAL + TEMPORAL,
    'M2': LOCAL + TEMPORAL + ACTIVE,
    'M3': LOCAL + TEMPORAL + ACTIVE + PEER,
    'M4': LOCAL + TEMPORAL + ACTIVE + PEER + REPORT,
    'M4-no-KIE': LOCAL + TEMPORAL + PEER + REPORT,
    'M4-no-peer': LOCAL + TEMPORAL + ACTIVE + REPORT,
    'M4-no-time': LOCAL + ACTIVE + PEER + REPORT,
    'Drep-only': REPORT,
}


def vector(r):
    return np.array([r['obs_count'] / r['seconds'], r['obs_error'], r['obs_mix']], dtype=float)


class FeatureBuilder:
    def fit(self, rows):
        self.scales = {}
        groups = defaultdict(list)
        for r in rows:
            if r['scenario'] == 'normal':
                groups[r['peer_group']].append(vector(r))
        for group, values in groups.items():
            a = np.asarray(values)
            self.scales[group] = np.maximum(1.4826 * np.median(abs(a - np.median(a, axis=0)), axis=0), [.5, .01, .02])
        if not self.scales:
            raise ValueError('Normal training runs required to fit MAD scales')
        return self

    def transform(self, rows):
        history, windows, output = {}, defaultdict(list), []
        for r in rows:
            windows[(r['run_id'], r['window'], r['peer_group'])].append(r)
        for r in sorted(rows, key=lambda x: (x['run_id'], x['window'], x['nf'])):
            if r['peer_group'] not in self.scales:
                raise ValueError('Uncalibrated peer group: ' + r['peer_group'])
            scale = self.scales[r['peer_group']]
            v = vector(r)
            key = (r['run_id'], r['nf'])
            prev, ema = history.get(key, (v, v))
            peers = [vector(p) for p in windows[(r['run_id'], r['window'], r['peer_group'])] if p['nf'] != r['nf']]
            if len(peers) < 2:
                raise ValueError('D_peer requires at least two other comparable NF instances')
            peer = np.median(peers, axis=0)
            report = np.array([r['rep_count']/r['seconds'], r['rep_error'], r['rep_mix']])
            ema_new = .3*v + .7*ema
            f = dict(rate=v[0], error=v[1], mix=v[2], latency=r['obs_latency'],
                delta_rate=abs(v[0]-prev[0]), delta_mix=abs(v[2]-prev[2]),
                ewma_rate=abs(v[0]-ema[0]), ewma_mix=abs(v[2]-ema[2]),
                kie_latency=r['kie_latency'], kie_failed=r['kie_failed'],
                D_peer=float(np.mean(abs(v-peer)/scale)),
                D_rep=float(np.mean(abs(report-v)/scale)) if not r['report_missing'] else 0.,
                report_missing=r['report_missing'])
            history[key] = (v, ema_new)
            output.append({**r, **f})
        return output
