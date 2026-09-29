import csv
import hashlib
import json
import platform
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path


def dump(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def append(path, obj):
    with Path(path).open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, allow_nan=False) + '\n')


def read_events(path):
    with Path(path).open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def csv_write(path, rows):
    if not rows:
        return
    with Path(path).open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def manifest(out, config, mode):
    packages = {}
    for name in ['numpy', 'scikit-learn', 'h2', 'hypercorn', 'PyYAML']:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    source = Path(__file__).parent
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.py')}
    dump(Path(out) / 'manifest.json', dict(mode=mode, config=config, python=platform.python_version(),
         platform=platform.platform(), packages=packages, source_sha256=hashes))
