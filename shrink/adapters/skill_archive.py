"""Snapshot local native Skills, including support files, without touching plugin-owned Skills."""
import base64
import io
from pathlib import Path
import zipfile


class SkillArchive:
    def __init__(self, native):
        self.native = native

    def root(self, name):
        from astrbot.core.skills.skill_manager import SkillManager
        root = Path(SkillManager().skills_root).resolve()
        path = root / name
        if not name or path.name != name or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("不是可托管的本地 Skill")
        return path

    async def snapshot(self, name):
        path = self.root(name)
        info = next((s for s in (await self.native.call("skills", "list"))["skills"]
                     if s["name"] == name), None)
        if not info or info.get("source_type") in {"plugin", "sandbox"} or not path.is_dir():
            raise ValueError("只维护本地安装的 Skill，不修改插件或沙箱预置内容")
        files = {}
        total = 0
        for file in sorted(path.rglob("*")):
            if file.is_symlink() or not file.resolve().is_relative_to(path.resolve()):
                raise ValueError("Skill 含外部链接，不能自动维护")
            if file.is_file():
                total += file.stat().st_size
                if total > 16 * 1024 * 1024:
                    raise ValueError("Skill 超过16MiB，请手动维护")
                files[file.relative_to(path).as_posix()] = base64.b64encode(file.read_bytes()).decode()
        if "SKILL.md" not in files:
            raise ValueError("Skill 缺少入口文件")
        return {"files": files, "active": bool(info.get("active"))}

    async def retire(self, name, expected):
        if await self.snapshot(name) != expected:
            raise ValueError("Skill 在维护前发生变化")
        await self.native.call("skills", "update", {"name": name, "active": False})

    async def restore(self, name, snapshot):
        path = self.root(name)
        if path.exists():
            current = await self.snapshot(name)
            if current["files"] != snapshot["files"]:
                raise ValueError("Skill 内容已变化，保留双方版本等待核对")
        else:
            from astrbot.core.skills.skill_manager import SkillManager
            import tempfile
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                for filename, content in snapshot["files"].items():
                    archive.writestr(name + "/" + filename, base64.b64decode(content))
            with tempfile.TemporaryDirectory() as folder:
                package = Path(folder) / (name + ".zip")
                package.write_bytes(payload.getvalue())
                SkillManager().install_skill_from_zip(str(package), overwrite=False, skill_name_hint=name)
        await self.native.call("skills", "update", {"name": name, "active": snapshot["active"]})

    async def purge(self, name, snapshot):
        if self.root(name).exists():
            current = await self.snapshot(name)
            if current["active"] or current["files"] != snapshot["files"]:
                raise ValueError("Skill 已恢复或内容变化，不能清除")
            await self.native.call("skills", "delete", {"name": name}, True)
