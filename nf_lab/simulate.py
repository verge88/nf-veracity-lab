"""Synthetic controls, never presented as measured Open5GS traffic."""
import numpy as np

SCENARIOS = ['normal', 'load-spike', 'DoS', 'compromised-NF', 'falsified-report', 'low-and-slow']


def simulate(cfg):
    rows = []
    for scenario_index, scenario in enumerate(cfg['scenarios']):
        if scenario not in SCENARIOS:
            raise ValueError(scenario)
        for run in range(cfg['runs_per_scenario']):
            rng = np.random.default_rng(np.random.SeedSequence([cfg['seed'], scenario_index, run]))
            capacity = rng.uniform(.85, 1.15, cfg['peers'])
            for t in range(cfg['windows']):
                active = cfg['attack_start'] <= t < cfg['attack_end']
                shared = rng.lognormal(0, .08)
                for i in range(cfg['peers']):
                    target = active and i == 0
                    bad = target and scenario not in ['normal', 'load-spike']
                    intensity = (t - cfg['attack_start'] + 1) / max(1, cfg['attack_end'] - cfg['attack_start'])
                    rate = 14 * shared * capacity[i]
                    error, mix, delay = .015, .22, 12.
                    if active and scenario == 'load-spike':
                        rate *= 2.4
                        delay *= 2
                    if target and scenario == 'DoS':
                        rate *= 4
                        error, delay = .22, 85
                    if target and scenario == 'compromised-NF':
                        mix, error = .70, .06
                    if target and scenario == 'low-and-slow':
                        mix += .16 * intensity
                        rate *= 1 + .14 * intensity
                    count = int(rng.poisson(rate * cfg['window_seconds']))
                    errors = int(rng.binomial(count, error))
                    reads = int(rng.binomial(count, mix))
                    latency = max(1., rng.normal(delay, delay * .08))
                    # Self report has realistic small capture/clock discrepancies, independent of labels.
                    rep_count = max(0, count + int(rng.normal(0, 1.5)))
                    rep_error = np.clip(errors / max(count, 1) + rng.normal(0, .004), 0, 1)
                    rep_mix = np.clip(reads / max(count, 1) + rng.normal(0, .006), 0, 1)
                    if target and scenario == 'falsified-report':
                        rep_count = int(count * .65)
                        rep_mix = .05
                    if target and scenario == 'low-and-slow':
                        rep_count = max(0, int(count / (1 + .14 * intensity)))
                        rep_mix = max(0, rep_mix - .16 * intensity)
                    row = dict(run_id=f'sim-{scenario}-{run:03}', mode='synthetic', scenario=scenario,
                        window=t, nf=f'nf-{i}', nf_type='AF', peer_group='lab-af',
                        seconds=cfg['window_seconds'], attack_start=cfg['attack_start'],
                        label_attack=int(bad), label_falsification=int(target and scenario in ['falsified-report','low-and-slow']),
                        obs_count=count, obs_error=errors / max(count, 1), obs_mix=reads / max(count, 1),
                        obs_latency=latency, rep_count=rep_count, rep_error=float(rep_error), rep_mix=float(rep_mix),
                        report_missing=0, kie_latency=max(1., rng.normal(delay, delay*.12)),
                        kie_failed=int(rng.random() < (.15 if target and scenario == 'DoS' else .002)),
                        kie_bytes=320, traffic_bytes=count * 500, observation_complete=1)
                    rows.append(row)
    return rows
