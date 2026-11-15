"""Keep capture files in pytest storage when the system temp directory is unavailable."""
from pathlib import Path
import tempfile

import pytest


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_load_initial_conftests(early_config):
    # This wrapper runs before pytest opens stdout/stderr capture, unlike conftest.
    if Path(tempfile.gettempdir()).resolve() == early_config.rootpath.resolve():
        factory = pytest.TempPathFactory(
            given_basetemp=None,
            retention_count=int(early_config.getini("tmp_path_retention_count")),
            retention_policy=early_config.getini("tmp_path_retention_policy"),
            trace=early_config.trace.get("tmpdir"), _ispytest=True)
        original = tempfile.tempdir
        tempfile.tempdir = str(factory.mktemp("capture"))
        early_config.add_cleanup(lambda: setattr(tempfile, "tempdir", original))
    return (yield)
