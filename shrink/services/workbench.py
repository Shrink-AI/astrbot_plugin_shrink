import ast
import asyncio
import json
import re
import shutil
import hashlib
from pathlib import Path


PREFIX = 'astrbot_plugin_shirnk_ext_'


class Workbench:
    def __init__(self, root, plugins, tester=None, runtime=None, guide=None):
        self.root, self.plugins = Path(root), Path(plugins)
        self.lock = asyncio.Lock()
        self.tester = tester
        self.runtime = runtime
        self.guide = guide

    def name(self, value):
        if re.fullmatch(r'astrbot_plugin_[a-zA-Z0-9_]{1,80}', value):
            candidate = self.plugins / value
            if candidate.is_dir() and not candidate.is_symlink():
                return value
            raise ValueError('该现有插件目录不存在')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', value):
            raise ValueError('扩展短名格式无效')
        return PREFIX + value

    def source_files(self, directory):
        for file in directory.rglob('*'):
            relative = file.relative_to(directory)
            if any(part.startswith('.') or part in {'__pycache__', 'node_modules', 'runtime', 'data', 'logs'} for part in relative.parts):
                continue
            if file.is_symlink():
                raise ValueError('插件包含符号链接，不能自动复制')
            if file.is_file() and file.suffix in {'.py', '.md', '.yaml', '.yml', '.txt', '.html', '.css', '.js', '.ts', '.tsx', '.vue'}:
                yield file, relative

    def fingerprints(self, directory):
        return {str(relative): hashlib.sha256(file.read_bytes()).hexdigest() for file, relative in self.source_files(directory)}

    def restore_source(self, backup, target):
        previous = dict((str(relative), source) for source, relative in self.source_files(backup))
        for current, relative in list(self.source_files(target)):
            if str(relative) not in previous:
                current.unlink()
        for relative, source in previous.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def file(self, directory, value):
        path = Path(value)
        if not value or path.is_absolute() or '..' in path.parts or '\\' in value:
            raise ValueError('只允许扩展内相对路径')
        target = directory / path
        if not target.resolve().is_relative_to(directory.resolve()):
            raise ValueError('文件路径越界')
        if target.suffix not in {'.py', '.yaml', '.yml', '.md', '.txt', '.html', '.css', '.js', '.ts', '.tsx', '.vue'}:
            raise ValueError('不支持该文件类型')
        return target

    def check(self, draft, full_name):
        if not (draft / 'main.py').is_file() or not (draft / 'metadata.yaml').is_file():
            raise ValueError('缺少main.py或metadata.yaml')
        import yaml
        metadata = yaml.safe_load((draft / 'metadata.yaml').read_text('utf-8'))
        if not isinstance(metadata, dict) or metadata.get('name') != full_name:
            raise ValueError('metadata.yaml中的name必须与扩展名一致')
        trees = []
        for path in draft.rglob('*.py'):
            trees.append((path, ast.parse(path.read_text('utf-8'), filename=str(path))))
        main_tree = next(tree for path, tree in trees if path.name == 'main.py')
        registered = [d.args[0].value for node in main_tree.body if isinstance(node, ast.ClassDef)
            for d in node.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
            and d.func.id == 'register' and d.args and isinstance(d.args[0], ast.Constant)]
        expected = metadata.get('name')
        if registered and registered != [expected]:
            raise ValueError('main.py必须注册且只注册本扩展名')
        return {'ok': True, 'python_files': len(trees), 'validation': 'syntax_only', 'functional_test': 'not_run'}

    async def activate(self, manager, name):
        if self.runtime is None:
            raise RuntimeError('工作台未连接插件运行时，不能启用插件')
        await self.runtime.activate(manager, name)

    async def perform(self, action, name, path, content, manager):
        if action == 'guide':
            if self.guide is None:
                raise RuntimeError('工作台未配置使用说明')
            return self.guide()
        if action in {'docs', 'core_docs'}:
            if self.runtime is None:
                raise RuntimeError('工作台未连接源码运行时')
            return self.runtime.read_source(action, path)
        if action == 'list':
            return json.dumps({'drafts': [p.name for p in (self.root / 'drafts').glob('astrbot_plugin_*')], 'installed': [p.name for p in self.plugins.glob('astrbot_plugin_*') if p.is_dir()]}, ensure_ascii=False)
        full_name = self.name(name)
        draft = self.root / 'drafts' / full_name
        target = self.plugins / full_name
        backup = self.root / 'backups' / full_name
        baseline_file = self.root / 'baselines' / (full_name + '.json')
        test_file = self.root / 'tests' / (full_name + '.json')
        async with self.lock:
            if action == 'status':
                evidence = json.loads(test_file.read_text('utf-8')) if test_file.exists() else {}
                return json.dumps({
                    'plugin': full_name,
                    'draft_files': [str(r).replace('\\', '/') for _, r in self.source_files(draft)],
                    'test': evidence.get('result'),
                    'test_current': bool(evidence and evidence.get('files') == self.fingerprints(draft)),
                    'rollback_available': backup.is_dir(),
                    'conflict': bool(target.is_dir() and baseline_file.exists() and
                                     json.loads(baseline_file.read_text('utf-8')) != self.fingerprints(target)),
                }, ensure_ascii=False)
            if action == 'checkout':
                if not target.is_dir(): raise ValueError('现有插件不存在')
                if draft.exists(): raise ValueError('已有草稿，请继续修改，避免覆盖未部署内容')
                for source, relative in self.source_files(target):
                    destination = draft / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                baseline_file.parent.mkdir(parents=True, exist_ok=True)
                baseline_file.write_text(json.dumps(self.fingerprints(target)), encoding='utf-8')
                return json.dumps({'ok': True, 'draft': str(draft), 'activated': False})
            if action == 'diff':
                import difflib
                changes = []
                for source, relative in self.source_files(draft):
                    old = target / relative
                    before = old.read_text('utf-8', errors='replace') if old.is_file() else ''
                    after = source.read_text('utf-8', errors='replace')
                    if before != after:
                        changes.extend(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile=str(relative), tofile=str(relative), lineterm=''))
                return '\n'.join(changes)[:30000] or '没有代码差异'
            if action == 'write':
                if target.is_dir() and not baseline_file.is_file(): raise ValueError('修改现有插件前先checkout')
                if len(content.encode()) > 256 * 1024: raise ValueError('单文件上限256KB')
                file = self.file(draft, path)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content, encoding='utf-8')
                return json.dumps({'ok': True, 'draft': str(file), 'activated': False})
            if action == 'read': return self.file(draft, path).read_text('utf-8')
            if action == 'check': return json.dumps(self.check(draft, full_name))
            if action == 'test':
                self.check(draft, full_name)
                if self.tester is None: raise ValueError('隔离测试服务未配置，不能在生产环境代测')
                fingerprints = self.fingerprints(draft)
                result = await self.tester({str(r).replace('\\', '/'): s.read_text('utf-8')
                                           for s, r in self.source_files(draft)
                                           if s.suffix in {'.py', '.yaml', '.yml', '.txt'}})
                test_file.parent.mkdir(parents=True, exist_ok=True)
                test_file.write_text(json.dumps({'files': fingerprints, 'result': result}), encoding='utf-8')
                return json.dumps(result, ensure_ascii=False)
            if action == 'deploy':
                result = self.check(draft, full_name)
                evidence = json.loads(test_file.read_text('utf-8')) if test_file.exists() else {}
                if evidence.get('files') != self.fingerprints(draft) or not evidence.get('result', {}).get('ok'):
                    raise ValueError('当前草稿尚未通过隔离功能测试；先test，修复后重测')
                if target.is_dir():
                    if not baseline_file.is_file(): raise ValueError('缺少checkout基线，不能覆盖现有插件')
                    if json.loads(baseline_file.read_text('utf-8')) != self.fingerprints(target):
                        raise ValueError('线上代码已改变，不能覆盖；请核对差异')
                if target.is_symlink(): raise ValueError('扩展目录不能是符号链接')
                if target.exists():
                    if backup.exists(): shutil.rmtree(backup)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(target, backup)
                target.mkdir(parents=True, exist_ok=True)
                for source, relative in self.source_files(draft):
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                try:
                    await self.activate(manager, full_name)
                except Exception:
                    if backup.exists():
                        self.restore_source(backup, target)
                        await self.activate(manager, full_name)
                    elif target.exists():
                        shutil.rmtree(target)
                    raise
                baseline_file.parent.mkdir(parents=True, exist_ok=True)
                baseline_file.write_text(json.dumps(self.fingerprints(target)), encoding='utf-8')
                return json.dumps({**result, 'functional_test': 'isolated_unittest_passed',
                                   'activated': True, 'plugin': full_name})
            if action == 'rollback':
                if not backup.is_dir(): raise ValueError('没有上一版备份')
                self.restore_source(backup, target)
                await self.activate(manager, full_name)
                baseline_file.parent.mkdir(parents=True, exist_ok=True)
                baseline_file.write_text(json.dumps(self.fingerprints(target)), encoding='utf-8')
                return json.dumps({'ok': True, 'restored': full_name})
            raise ValueError('不支持的操作')
