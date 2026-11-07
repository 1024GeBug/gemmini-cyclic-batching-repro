"""Read-only byte/hash verification; no build or simulation."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    m=json.loads((ROOT/'MANIFEST.json').read_text())
    for r in m['files']:
        p=ROOT/r['path']
        if not p.is_file() or p.stat().st_size!=r['bytes'] or sha(p)!=r['sha256']:
            raise RuntimeError('package file mismatch: '+r['path'])
    print(json.dumps({'status':'pass','files':len(m['files']),'bytes':sum(r['bytes'] for r in m['files']),
      'manifest_sha256':sha(ROOT/'MANIFEST.json'),'build_or_simulation_executed':False},indent=2))
if __name__=='__main__':main()
