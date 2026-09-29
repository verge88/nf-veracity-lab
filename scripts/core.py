"""Minimal real NRF+SCP lifecycle; no MongoDB/UE needed for the AF/SBI experiment."""
import signal
import subprocess
import time
from pathlib import Path

stop=False
def shutdown(*_):
    global stop
    stop=True

signal.signal(signal.SIGTERM,shutdown)
signal.signal(signal.SIGINT,shutdown)
processes=[]
try:
    for name in ['nrf','scp']:
        processes.append(subprocess.Popen([f'/opt/open5gs/bin/open5gs-{name}d','-c',str(Path('configs/open5gs',name+'.yaml').resolve())]))
    while not stop:
        if any(p.poll() is not None for p in processes):raise RuntimeError('Open5GS daemon stopped')
        time.sleep(.5)
finally:
    for p in processes:
        if p.poll() is None:p.terminate()
    for p in processes:
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill();p.wait()
