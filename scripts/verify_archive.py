"""Verify unchanged public historical artifact bytes; no live model requests."""
import hashlib
import json
from pathlib import Path

def verify(root):
    root=Path(root);index=json.loads((root/'experiments/ARCHIVE_INDEX.json').read_text(encoding='utf-8'))
    for item in index['files']:
        target=(root/item['public_path']).resolve()
        if not target.is_relative_to(root.resolve()):raise ValueError('ARCHIVE_PATH_OUTSIDE_ROOT')
        if hashlib.sha256(target.read_bytes()).hexdigest()!=item['original_sha256']:
            raise ValueError('ARCHIVE_BYTE_HASH_MISMATCH:'+item['public_path'])
    return {'passed':True,'historical_files_verified':len(index['files']),'original_bytes_unchanged':True,'model_calls':0}

if __name__=='__main__':
    print(json.dumps(verify(Path(__file__).resolve().parents[1]),indent=2))
