import sys
from pathlib import Path

import pytest

from agent.executor import LocalExecutor


def _write_plugin(base: Path, directory_name: str, manifest_text: str, module_text: str) -> None:
    plugins_dir = base / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / "__init__.py").write_text("", encoding="utf-8")

    plugin_dir = plugins_dir / directory_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.yaml").write_text(manifest_text, encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(module_text, encoding="utf-8")


def _clear_plugin_modules(*module_names: str) -> None:
    for module_name in module_names:
        sys.modules.pop(module_name, None)


def test_local_executor_uses_manifest_id_as_plugin_key(tmp_path):
    _write_plugin(
        tmp_path,
        "word_tool_dir",
        """
id: "word_processor"
version: "1.0.0"
description: "Word Processor"
timeout_sec: 5
""".strip(),
        """
async def run(**kwargs):
    return {"status": "success", "data": kwargs}
""".strip(),
    )

    _clear_plugin_modules("plugins", "plugins.word_tool_dir")
    executor = LocalExecutor(tmp_path / "plugins")

    assert "word_processor" in executor.plugins
    assert "word_tool_dir" not in executor.plugins
    assert executor.manifests["word_processor"]["timeout_sec"] == 5

    _clear_plugin_modules("plugins.word_tool_dir", "plugins")


@pytest.mark.asyncio
async def test_local_executor_respects_manifest_timeout(tmp_path):
    _write_plugin(
        tmp_path,
        "slow_tool_dir",
        """
id: "slow_tool"
version: "1.0.0"
description: "Slow Tool"
timeout_sec: 0.01
""".strip(),
        """
import asyncio

async def run(**kwargs):
    await asyncio.sleep(0.05)
    return {"status": "success", "data": kwargs}
""".strip(),
    )

    _clear_plugin_modules("plugins", "plugins.slow_tool_dir")
    executor = LocalExecutor(tmp_path / "plugins")

    result = await executor.run("slow_tool", {})

    assert result["status"] == "timeout"
    assert "timed out" in result["message"].lower()

    _clear_plugin_modules("plugins.slow_tool_dir", "plugins")
