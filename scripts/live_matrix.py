"""Run from project root, with NRF/SCP already running. No parallel port reuse."""
import subprocess
import sys
import json
import random
import time
from pathlib import Path


def fmt_duration(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def print_progress(done: int, total: int, started: float, label: str) -> None:
    if total <= 0:
        return
    elapsed = time.monotonic() - started
    percent = done / total
    bar_width = 30
    filled = int(bar_width * percent)
    bar = '█' * filled + '░' * (bar_width - filled)
    eta = (elapsed / max(done, 1)) * max(total - done, 0)
    eta_label = f"ETA {fmt_duration(eta)}" if done < total else "ETA 00:00"
    print(f"\r{label}: [{bar}] {done}/{total} ({percent * 100:5.1f}%) | elapsed {fmt_duration(elapsed)} | {eta_label}", end='', flush=True)
    if done == total:
        print()


scenarios=['normal','load-spike','DoS','compromised-NF','falsified-report','low-and-slow']
root=Path(sys.argv[1] if len(sys.argv)>1 else 'runs/live')
root.mkdir(parents=True,exist_ok=False)
inputs=[]
schedule=[]
randomizer=random.Random(731)
for run in range(12):
    order=scenarios.copy()
    randomizer.shuffle(order)
    for scenario in order:
        schedule.append(dict(run=run,scenario=scenario,seed=731+run))
(root/'schedule.json').write_text(json.dumps(schedule,indent=2),encoding='utf-8')
started = time.monotonic()
total_steps = len(schedule) + 1
for index, item in enumerate(schedule, start=1):
    scenario,run=item['scenario'],item['run']
    out=root/f'{scenario}-{run:03}'
    print(f"\n[{index}/{total_steps}] running {scenario}-{run:03}")
    subprocess.run([sys.executable,'-m','nf_lab.cli','live','--scenario',scenario,'--seed',str(item['seed']),'--out',str(out)],check=True)
    inputs.append(str(out/'windows.jsonl'))
    print_progress(index, total_steps, started, 'matrix')
subprocess.run([sys.executable,'-m','nf_lab.cli','evaluate','--inputs',*inputs,'--out',str(root/'evaluation')],check=True)
print_progress(total_steps, total_steps, started, 'matrix')
