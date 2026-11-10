import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from shrink.services.workbench import Workbench

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def work(tmp_path):
    plugins = tmp_path / "plugins"
    target = plugins / "astrbot_plugin_shrink"
    target.mkdir(parents=True)
    (target / "main.py").write_text("VALUE=1\n")
    (target / "metadata.yaml").write_text("name: astrbot_plugin_shrink\n")
    for relative in ("shrink/services/workbench.py", "shrink/adapters/notifications.py"):
        file = target / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, file)
    return Workbench(tmp_path / "work", plugins, AsyncMock(return_value={"ok": True}))


@pytest.mark.asyncio
async def test_direct_send_rejected_before_tests_or_deployment(work):
    name = "astrbot_plugin_shrink"
    await work.perform("checkout", name, "", "", None)
    code = "async def notify(event):\n    await event.send('正在处理')\n"
    await work.perform("write", name, "shrink/adapters/bad.py", code, None)
    for action in ("check", "test", "deploy"):
        with pytest.raises(ValueError, match="notifications.send_once"):
            await work.perform(action, name, "", "", None)
    work.tester.assert_not_called()
    code = "async def notify(notifications, event):\n    await notifications.send_once(event, 'stable', {'status':'pending'})\n"
    await work.perform("write", name, "shrink/adapters/bad.py", code, None)
    await work.perform("test", name, "", "", None)
    work.tester.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("relative,code", [
    ("shrink/services/workbench.py", "# disable checks\n"),
    ("shrink/adapters/notifications.py", "# bypass expression\n"),
    ("shrink/services/bad.py", "from astrbot.api import logger\n"),
    ("shrink/services/bad.py", "from ..adapters.host import PluginHost\n"),
    ("shrink/adapters/bad.py", "import data.plugins.astrbot_plugin_other\n"),
])
async def test_architecture_and_enforcement_cannot_be_disabled_in_draft(work, relative, code):
    name = "astrbot_plugin_shrink"
    await work.perform("checkout", name, "", "", None)
    await work.perform("write", name, relative, code, None)
    with pytest.raises(ValueError):
        await work.perform("test", name, "", "", None)
    work.tester.assert_not_called()
