import subprocess
import zipfile

from tools.bundle import build


def test_release_excludes_ignored_files_even_if_previously_tracked(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text('.env\nruntime/\nintegrations/\n*.db\n', encoding='utf-8')
    (tmp_path / 'main.py').write_text('VALUE = 1\n', encoding='utf-8')
    (tmp_path / '.env').write_text('DUMMY=test-only\n', encoding='utf-8')
    subprocess.run(['git', 'add', '-f', '.env'], cwd=tmp_path, check=True)
    for name in ('runtime/local.txt', 'integrations/example/main.py', 'private.db'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('test-only', encoding='utf-8')
    target = tmp_path / 'runtime/release.zip'
    build(tmp_path, target)
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {'.gitignore', 'main.py'}
