"""Allowlisted, audited public-source export. Does not publish or call models."""
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

DIRECTORIES = {'src', 'scripts', 'tests', 'docs', 'eval', 'experiments', '.github', 'frontend'}
ROOT_FILES = {'README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml',
              'requirements.lock', 'requirements-rag.lock', 'requirements-mysql.lock', 'requirements-redis.lock', '.env.example', '.gitignore', '.gitattributes', 'SECURITY.md', 'CONTRIBUTING.md'}
EXCLUDED_PARTS = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules',
                  'build', 'dist', 'coverage', '.cache', '.redis'}
EXCLUDED_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.pyc', '.log', '.pem', '.key', '.gguf',
                     '.safetensors', '.tsbuildinfo', '.zip', '.gz', '.onnx', '.bin', '.npy', '.npz', '.rdb', '.aof'}
TEXT_SUFFIXES = {'.py', '.md', '.json', '.jsonl', '.txt', '.toml', '.yml', '.yaml', '.ts',
                 '.tsx', '.css', '.html', '.svg', '.drawio', '.lock'}
CHECKS = (
    ('LOCAL_ABSOLUTE_PATH', re.compile(r'(?i)\b[a-z]:[\\/]|/(?:Users|home)/[^\s/]+')),
    ('API_KEY_SHAPE', re.compile(r'\bsk-[A-Za-z0-9_-]{20,}')),
    ('BEARER_CREDENTIAL', re.compile(r'Bearer\s+[A-Za-z0-9_-]{20,}')),
    ('SERIALIZED_LOGIN_TOKEN', re.compile(r'"access_token"\s*:\s*"[^"\s]{16,}"')),
    ('PRIVATE_KEY', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def content_findings(text):
    return [code for code,pattern in CHECKS if pattern.search(text)]

def eligible(relative):
    if any(part in EXCLUDED_PARTS or part.endswith('.egg-info') for part in relative.parts):
        return False
    if relative.name.startswith('.env') and relative.name != '.env.example':
        return False
    if relative.suffix.lower() in EXCLUDED_SUFFIXES or relative.name in {'Thumbs.db', '.DS_Store'}:
        return False
    return relative.as_posix() in ROOT_FILES or (len(relative.parts)>1 and relative.parts[0] in DIRECTORIES)

def candidate_files(root):
    root = Path(root).resolve()
    paths=[root/name for name in ROOT_FILES if (root/name).is_file()]
    for directory in sorted(DIRECTORIES):
        start=root/directory
        if not start.exists():continue
        for parent,children,names in os.walk(start,followlinks=False):
            children[:]=[name for name in children if name not in EXCLUDED_PARTS and not name.endswith('.egg-info')]
            paths.extend(Path(parent)/name for name in names)
    for path in sorted(paths):
        relative=path.relative_to(root)
        if not eligible(relative) or not path.is_file():continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('RELEASE_SYMLINK_OR_EXTERNAL_FILE')
        yield path

def audit(root):
    root=Path(root).resolve();files=list(candidate_files(root));findings=[]
    for path in files:
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in ROOT_FILES:
            try:text=path.read_text(encoding='utf-8-sig')
            except UnicodeDecodeError:
                findings.append({'path':path.relative_to(root).as_posix(),'codes':['INVALID_TEXT_ENCODING']});continue
            codes=content_findings(text)
            if codes:findings.append({'path':path.relative_to(root).as_posix(),'codes':codes})
    required=['LICENSE','README.md','requirements.lock','pyproject.toml','frontend/package-lock.json']
    missing=[name for name in required if not (root/name).is_file()]
    return {'passed':not findings and not missing,'file_count':len(files),'findings':findings,'missing':missing}

def build(root,output):
    root,output=Path(root).resolve(),Path(output).resolve()
    if output.exists() or output.is_relative_to(root):raise ValueError('CHOOSE_NEW_EXTERNAL_RELEASE_DIRECTORY')
    result=audit(root)
    if not result['passed']:return result
    output.mkdir(parents=True)
    for source in candidate_files(root):
        target=output/source.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    files=sorted(p for p in output.rglob('*') if p.is_file())
    (output/'RELEASE_SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+p.relative_to(output).as_posix()+'\n' for p in files),encoding='utf-8',newline='\n')
    return {**result,'output_created':True,'sha256_entries':len(files)}

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('audit');check.add_argument('--root',type=Path,default=Path('.'))
    export=sub.add_parser('build');export.add_argument('--root',type=Path,default=Path('.'));export.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=audit(args.root) if args.command=='audit' else build(args.root,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['passed']:raise SystemExit(1)

if __name__=='__main__':main()
