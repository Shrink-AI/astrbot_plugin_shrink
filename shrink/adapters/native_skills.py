"""Keep Quartz experience history while publishing executable guidance as native Skills."""
import asyncio
import hashlib
import json
import re
import time

import yaml


class NativeSkills:
    def __init__(self, app, native):
        self.app, self.native = app, native
        self.lock = asyncio.Lock()
        self.worker = None
        self.app.database.connection.execute("""
        CREATE TABLE IF NOT EXISTS native_skill_links(
          id INTEGER PRIMARY KEY,name TEXT NOT NULL,local_hash TEXT NOT NULL,
          native_hash TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0)
        """)
        self.app.database.connection.execute("""
        CREATE TABLE IF NOT EXISTS native_skill_resolutions(
          id INTEGER PRIMARY KEY,skill_id INTEGER NOT NULL,created REAL NOT NULL,
          choice TEXT NOT NULL,local_json TEXT NOT NULL,native_json TEXT NOT NULL)
        """)
        self.app.database.connection.commit()

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def fields(row):
        return {k: row[k] for k in ("title", "trigger", "steps", "status")}

    def document(self, row, name):
        return "---\n" + yaml.safe_dump(
            {"name": name, "description": row["trigger"]}, allow_unicode=True, sort_keys=False
        ) + f"---\n# {row['title']}\n\n{row['steps']}\n"

    async def start(self):
        self.worker = asyncio.create_task(self.loop())

    async def install_bundled(self):
        from pathlib import Path
        inventory = await self.native.call("skills", "list")
        existing = {r["name"] for r in inventory.get("skills", [])}
        for file in (Path(__file__).resolve().parents[2] / "bundled_skills").glob("*/SKILL.md"):
            if file.parent.name in existing:
                continue
            await self.native.call("skills", "create",
                                   {"name": file.parent.name, "content": file.read_text("utf8")})
            await self.native.call("skills", "update", {"name": file.parent.name, "active": True})

    async def planning_guidance(self, text):
        if not re.search(r"grill|推敲|压力测试|(?:开发|编写|写|做|修改|改|实现|制作|构建|设计).{0,24}"
                         r"(?:插件|游戏|需求|功能|软件|程序|agent)|(?:插件|游戏|agent).{0,24}(?:修改|开发|需求)",
                         text or "", re.I):
            return ""
        inventory = await self.native.call("skills", "list")
        if not any(r.get("name") == "grilling" and r.get("active") for r in inventory.get("skills", [])):
            return ""
        document = await self.native.call("skills", "read", {"name": "grilling", "path": "SKILL.md"})
        return document["content"]

    async def migrate_names(self):
        # AstrBot's install and file APIs disagree on Unicode names. Keep the
        # human title in Markdown and use stable ASCII names for native routing.
        from pathlib import Path
        from astrbot.core.skills.skill_manager import SkillManager
        root = Path(SkillManager().skills_root).resolve()
        for link in self.app.database.rows("SELECT * FROM native_skill_links"):
            if re.fullmatch(r"[a-zA-Z0-9_-]+", link["name"]):
                continue
            source = (root / link["name"] / "SKILL.md").resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError("旧经验Skill源文件不存在")
            content = source.read_text("utf-8")
            backup = self.app.state.root / "native-skills-before-migration"
            backup.mkdir(exist_ok=True)
            (backup / f"{link['id']}.md").write_text(content, encoding="utf-8")
            parts = content.split("---", 2)
            metadata = yaml.safe_load(parts[1])
            name = f"shrink-experience-{link['id']}"
            metadata["name"] = name
            updated = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---" + parts[2]
            inventory = await self.native.call("skills", "list")
            current = {s["name"]: s for s in inventory["skills"]}
            if name not in current:
                await self.native.call("skills", "create", {"name": name, "content": updated})
            active = current.get(link["name"], {}).get("active", False)
            await self.native.call("skills", "update", {"name": name, "active": active})
            file = await self.native.call("skills", "read", {"name": name, "path": "SKILL.md"})
            if file["content"] != updated:
                raise ValueError("迁移后的Skill正文不一致")
            await self.native.call("skills", "delete", {"name": link["name"]}, True)
            with self.app.database.transaction() as con:
                con.execute("UPDATE native_skill_links SET name=?,native_hash=? WHERE id=?",
                            (name, self.digest([updated, active]), link["id"]))

    async def loop(self):
        while True:
            try:
                await self.install_bundled()
                await self.migrate_names()
                await self.sync()
                self.app.state.put("native_skills_error", "")
            except Exception as exc:
                self.app.state.put("native_skills_error", str(exc)[:500])
            await asyncio.sleep(10)

    async def sync(self):
        async with self.lock:
            conflicts = []
            inventory = await self.native.call("skills", "list")
            native_rows = {s["name"]: s for s in inventory.get("skills", [])}
            links = {r["id"]: r for r in self.app.database.rows("SELECT * FROM native_skill_links")}
            skills = {r["id"]: r for r in self.app.database.rows("SELECT * FROM skills")}
            for skill_id, link in links.items():
                if skill_id not in skills:
                    if link["name"] in native_rows:
                        await self.native.call("skills", "delete", {"name": link["name"]}, True)
                    with self.app.database.transaction() as con:
                        con.execute("DELETE FROM native_skill_links WHERE id=?", (skill_id,))
            for skill_id, row in skills.items():
                link = links.get(skill_id)
                name = link["name"] if link else f"shrink-experience-{skill_id}"
                info = native_rows.get(name)
                local_hash = self.digest(self.fields(row))
                content = ""
                if info:
                    file = await self.native.call("skills", "read", {"name": name, "path": "SKILL.md"})
                    content = file["content"]
                native_hash = self.digest([content, bool(info and info.get("active"))])
                local_changed = not link or local_hash != link["local_hash"]
                native_changed = bool(link and native_hash != link["native_hash"])
                if link and local_changed and native_changed and not link["deleted"]:
                    conflicts.append(row["title"])
                    continue
                if local_changed:
                    content = self.document(row, name)
                    if info:
                        await self.native.call("skills", "write", {
                            "name": name, "path": "SKILL.md", "content": content})
                    else:
                        await self.native.call("skills", "create", {"name": name, "content": content})
                    await self.native.call("skills", "update", {
                        "name": name, "active": row["status"] == "active"})
                    native_hash = self.digest([content, row["status"] == "active"])
                    deleted = 0
                elif native_changed:
                    if info:
                        parts = content.split("---", 2)
                        if len(parts) != 3:
                            raise ValueError(f"{name} 缺少Skill元数据")
                        meta = yaml.safe_load(parts[1])
                        lines = parts[2].strip().splitlines()
                        title = lines[0].removeprefix("# ").strip() if lines else row["title"]
                        steps = "\n".join(lines[1:]).strip()
                        if not isinstance(meta, dict) or not meta.get("description") or not steps:
                            raise ValueError(f"{name} 需要使用条件和具体步骤")
                        self.app.skill_repository.revise(
                            skill_id, "原生Skill更新", origin="native_skill",
                            title=title, trigger=str(meta["description"]), steps=steps,
                            status="active" if info.get("active") else "retired")
                        await self.app.skills.index(skill_id)
                    else:
                        self.app.skill_repository.revise(
                            skill_id, "原生Skill已删除", origin="native_skill", status="retired")
                    local_hash = self.digest(self.fields(self.app.skill_repository.get(skill_id)))
                    deleted = int(not info)
                else:
                    continue
                with self.app.database.transaction() as con:
                    con.execute("INSERT OR REPLACE INTO native_skill_links VALUES(?,?,?,?,?)",
                                (skill_id, name, local_hash, native_hash, deleted))
            if conflicts:
                raise ValueError(f"经验“{'、'.join(conflicts)}”与原生Skill同时修改，请在经验页面选择版本")

    async def inspect(self, skill_id):
        row = self.app.skill_repository.get(skill_id)
        if not row:
            raise ValueError("经验不存在")
        links = self.app.database.rows("SELECT * FROM native_skill_links WHERE id=?", (skill_id,))
        link = links[0] if links else None
        name = link["name"] if link else f"shrink-experience-{skill_id}"
        inventory = await self.native.call("skills", "list")
        info = next((x for x in inventory.get("skills", []) if x["name"] == name), None)
        content = (await self.native.call("skills", "read",
                   {"name": name, "path": "SKILL.md"}))["content"] if info else ""
        local = self.fields(row)
        native = {"content": content, "active": bool(info and info.get("active")), "exists": info is not None}
        conflict = bool(link and not link["deleted"] and
                        self.digest(local) != link["local_hash"] and
                        self.digest([content, native["active"]]) != link["native_hash"])
        return {"name": name, "local": local, "native": native, "conflict": conflict,
                "snapshot": self.digest([local, native, link])}

    async def resolve(self, skill_id, choice, snapshot):
        if choice not in ("local", "native"):
            raise ValueError("请选择经验版本或原生Skill版本")
        async with self.lock:
            current = await self.inspect(skill_id)
            if current["snapshot"] != snapshot:
                raise ValueError("版本已变化，请刷新后重新核对")
            if not current["conflict"]:
                raise ValueError("当前没有需要解决的版本冲突")
            # Keep both versions before choosing a direction. The ordinary sync
            # path retains native validation and the experience revision history.
            with self.app.database.transaction() as con:
                con.execute(
                    "INSERT INTO native_skill_resolutions(skill_id,created,choice,local_json,native_json)"
                    " VALUES(?,?,?,?,?)",
                    (skill_id, time.time(), choice, json.dumps(current["local"], ensure_ascii=False),
                     json.dumps(current["native"], ensure_ascii=False)))
                if choice == "local":
                    con.execute("UPDATE native_skill_links SET native_hash=? WHERE id=?",
                                (self.digest([current["native"]["content"], current["native"]["active"]]), skill_id))
                else:
                    con.execute("UPDATE native_skill_links SET local_hash=? WHERE id=?",
                                (self.digest(current["local"]), skill_id))
        await self.sync()
        return await self.inspect(skill_id)

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
