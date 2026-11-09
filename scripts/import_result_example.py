"""Install the checked compact result sample without executing models or checkpoints."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config.project_paths import PROJECT_ROOT, PUBLIC_RESULTS_ROOT
from core.public_artifacts import is_public_artifact


def import_example():
    archive=PROJECT_ROOT/'examples/fd4_review.zip'
    expected=(archive.with_suffix('.sha256')).read_text().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
        raise ValueError('Example archive checksum mismatch')
    destination=PUBLIC_RESULTS_ROOT/'runs/fd4-review'
    if destination.exists():raise ValueError('The example already exists; choose a separate publication root to reinstall')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='example-',dir=destination.parent))
    try:
        with zipfile.ZipFile(archive) as contents:
            manifest_bytes=contents.read('fd4-review/public_manifest.json')
            manifest=json.loads(manifest_bytes)
            for name,record in manifest['files'].items():
                target=temporary/name
                if not is_public_artifact(target,temporary):raise ValueError('Invalid example member')
                payload=contents.read('fd4-review/'+name)
                if hashlib.sha256(payload).hexdigest()!=record['sha256']:raise ValueError('Example member checksum mismatch')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
            (temporary/'public_manifest.json').write_bytes(manifest_bytes)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return destination

if __name__=='__main__':print(import_example())
