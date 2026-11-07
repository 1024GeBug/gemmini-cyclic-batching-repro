"""Verify byte-exact relocatable source/fixture package (standard library only)."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def verify():
    manifest=ROOT/'source_manifest.json'
    data=json.loads(manifest.read_text())
    for r in data['files']:
        p=ROOT/r['path']
        if not p.is_file() or p.stat().st_size!=r['bytes'] or sha(p)!=r['sha256']:
            raise RuntimeError('source/fixture hash mismatch: '+r['path'])
    return {'files':len(data['files']),'bytes':sum(r['bytes'] for r in data['files']),
            'source_manifest_sha256':sha(manifest)}
if __name__=='__main__':print(json.dumps({'status':'pass',**verify()},indent=2))
