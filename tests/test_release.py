from pathlib import Path
import pytest
from scripts.release import eligible,content_findings,audit,build
from scripts.verify_archive import verify

@pytest.mark.parametrize('name',['.env','frontend/.env.local','data/development/demo.sqlite3',
    'reports/stage6/source-before.tar.gz','frontend/node_modules/package/index.js',
    'src/campusbridge/__pycache__/api.pyc','.git/config'])
def test_release_rejects_private_and_generated_files(name):
    assert not eligible(Path(name))

def test_release_detects_credentials_and_host_paths():
    assert 'API_KEY_SHAPE' in content_findings('sk-'+'x'*32)
    assert 'BEARER_CREDENTIAL' in content_findings('Bearer '+'x'*32)
    assert 'LOCAL_ABSOLUTE_PATH' in content_findings('C'+':/'+'Users/example/project')
    assert 'SERIALIZED_LOGIN_TOKEN' in content_findings('{"access_token":"'+'x'*32+'"}')
    assert not content_findings('CAMPUSBRIDGE_API_KEY=\nhttps://api.deepseek.com')

def test_release_audit_and_refuse_overwrite(tmp_path):
    for name in ['LICENSE','README.md','requirements.lock','pyproject.toml','frontend/package-lock.json']:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic release fixture')
    assert audit(tmp_path)['passed']
    with pytest.raises(ValueError):build(tmp_path,tmp_path/'nested-output')
    (tmp_path/'README.md').write_text('sk-'+'x'*32)
    assert not audit(tmp_path)['passed']

def test_public_archive_integrity():
    root=Path(__file__).resolve().parents[1]
    assert verify(root)['original_bytes_unchanged']
