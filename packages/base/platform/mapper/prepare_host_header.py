#!/usr/bin/env python3
"""Apply only transport/fence substitutions to the pinned official mapper."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
provenance = json.loads((root / 'provenance.json').read_text())
for record in provenance['files']:
    original = root / 'vendor' / record['path']
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    if digest != record['sha256']:
        raise RuntimeError(f'Vendored source digest mismatch: {original}')
src = root / 'vendor/include/gemmini.h'
text = src.read_text()
edits = [('#include "rocc-software/src/xcustom.h"', '#include "host_rocc.h"'),
         ('#define gemmini_fence() asm volatile("fence")', '#define gemmini_fence() host_fence()')]
for old, new in edits:
    if text.count(old) != 1:
        raise RuntimeError(f'Expected exactly one occurrence: {old}')
    text = text.replace(old, new)
dst = root / 'gemmini_host.h'
dst.write_text(text)
(root / 'host_header_edits.json').write_text(json.dumps({'source_sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
    'adapted_sha256': hashlib.sha256(dst.read_bytes()).hexdigest(), 'edits': [{'from': a, 'to': b} for a, b in edits],
    'tiling_algorithm_modified': False}, indent=2) + '\n')
