#!/usr/bin/env python3
"""Stage browser dependencies; retain source PDFs and audit data in the repo."""
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]

def build(destination=None):
    target = Path(destination) if destination else ROOT / '_site'
    if target.exists() and any(target.iterdir()):
        if target.resolve() != (ROOT / '_site').resolve():
            raise ValueError('Custom build destination must be empty')
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'index.html', target / 'index.html')
    shutil.copytree(ROOT / 'assets', target / 'assets', dirs_exist_ok=True)
    config = (ROOT / 'assets/app.js').read_text().split('};', 1)[0]
    sources = sorted(set(re.findall(r"['\"](data/[^'\"]+)['\"]", config)))
    if not sources:
        raise ValueError('No public data dependencies found')
    for relative in sources:
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(relative)
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    (target / '.nojekyll').touch()
    print(f'Public site: {len(sources)} data files; {target}')
    return target

if __name__ == '__main__':
    build()
