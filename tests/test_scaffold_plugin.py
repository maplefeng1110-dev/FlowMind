import sys
from pathlib import Path

import pytest
import yaml

from agent.executor import LocalExecutor
from agent.scaffold_plugin import scaffold_plugin


def _clear_plugin_modules(*module_names: str) -> None:
    for module_name in module_names:
        sys.modules.pop(module_name, None)


def test_scaffold_plugin_creates_stub_manifest_from_manual_params(tmp_path):
    plugins_dir = tmp_path / "plugins"

    plugin_dir = scaffold_plugin(
        [
            "weather_api",
            "--description",
            "天气查询插件",
            "--output-dir",
            str(plugins_dir),
            "--param",
            "city:string",
            "--required",
            "city",
            "--keyword",
            "天气",
        ]
    )

    manifest = yaml.safe_load((plugin_dir / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["id"] == "weather_api"
    assert manifest["params_schema"]["required"] == ["city"]
    assert manifest["params_schema"]["properties"]["city"]["type"] == "string"
    assert manifest["tool_profile"]["supports_batch"] is False
    assert manifest["tool_profile"]["allowed_path_roots"] == ["data/uploads", "data/exports"]
    assert (plugin_dir / "__init__.py").exists()


def test_scaffold_plugin_rejects_invalid_plugin_id(tmp_path):
    plugins_dir = tmp_path / "plugins"

    with pytest.raises(ValueError, match="plugin_id"):
        scaffold_plugin(
            [
                "weather-api",
                "--output-dir",
                str(plugins_dir),
            ]
        )


@pytest.mark.asyncio
async def test_scaffold_plugin_wraps_existing_sync_source_and_infers_params(tmp_path):
    source_file = tmp_path / "weather_rpa.py"
    source_file.write_text(
        (
            "def run(city: str, days: int = 1):\n"
            "    return {\"status\": \"success\", \"data\": {\"city\": city, \"days\": days}}\n"
        ),
        encoding="utf-8",
    )
    plugins_dir = tmp_path / "plugins"

    plugin_dir = scaffold_plugin(
        [
            "weather_sync",
            "--description",
            "同步天气插件",
            "--output-dir",
            str(plugins_dir),
            "--source-file",
            str(source_file),
        ]
    )

    manifest = yaml.safe_load((plugin_dir / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["params_schema"]["properties"]["city"]["type"] == "string"
    assert manifest["params_schema"]["properties"]["days"]["type"] == "integer"
    assert manifest["params_schema"]["properties"]["days"]["default"] == 1
    assert manifest["params_schema"]["required"] == ["city"]
    assert (plugin_dir / "impl.py").exists()

    _clear_plugin_modules("plugins", "plugins.weather_sync")
    executor = LocalExecutor(plugins_dir)
    result = await executor.run("weather_sync", {"city": "Shanghai"})

    assert result["status"] == "success"
    assert result["data"]["days"] == 1
    _clear_plugin_modules("plugins.weather_sync", "plugins")
