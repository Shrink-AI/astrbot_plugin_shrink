"""AstrBot plugin activation and source access for the host-independent workbench."""
from pathlib import Path


class AstrBotWorkbenchRuntime:
    async def activate(self, manager, name):
        from astrbot.core.star.star import star_registry
        if any(item.name == name for item in star_registry):
            result = await manager.reload(name)
        else:
            result = await manager.load(specified_dir_name=name)
        if not result[0]:
            raise RuntimeError(str(result[1]))

    def read_source(self, action, path):
        if action == 'docs':
            import astrbot
            base = Path(astrbot.__file__).parent
        else:
            base = Path(__file__).parents[2]
        relative = Path(path.removeprefix('astrbot/'))
        target = (base / relative).resolve()
        if not target.is_relative_to(base.resolve()) or target.suffix != '.py':
            raise ValueError('只允许读取所选源码目录内的Python文件')
        if not target.is_file():
            raise ValueError('源码文件不存在')
        return target.read_text('utf-8')[:40000]
