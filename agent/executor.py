import asyncio
import importlib
import sys
from pathlib import Path

import yaml

from utils.plugin_result import error_result

DEFAULT_PLUGIN_TIMEOUT = 60.0


class LocalExecutor:
    def __init__(self, plugins_dir: str = "./plugins"):
        self.plugins = {}
        self.manifests = {}
        self._load_plugins(Path(plugins_dir))

    def _load_plugins(self, base: Path) -> None:
        if not base.exists():
            print(f"Plugins directory not found: {base}")
            return

        parent_dir = str(base.parent.absolute())
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        for directory in base.iterdir():
            if not directory.is_dir():
                continue

            manifest_path = directory / "manifest.yaml"
            init_path = directory / "__init__.py"
            if not (manifest_path.exists() and init_path.exists()):
                continue

            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                if not isinstance(manifest, dict) or not manifest.get("id"):
                    print(f"Skipping plugin {directory.name}: manifest.yaml must contain a non-empty id")
                    continue

                plugin_id = str(manifest["id"]).strip()
                if plugin_id in self.plugins:
                    print(f"Skipping plugin {directory.name}: duplicate plugin id {plugin_id}")
                    continue

                module_name = f"plugins.{directory.name}"
                module = importlib.import_module(module_name)
                self.plugins[plugin_id] = module
                self.manifests[plugin_id] = manifest
                print(f"  [plugin] loaded: {plugin_id} (dir: {directory.name})")
            except Exception as exc:
                print(f"Failed to load plugin {directory.name}: {exc}")

    def _get_timeout(self, rpa_id: str) -> float:
        manifest = self.manifests.get(rpa_id, {})
        try:
            timeout = float(manifest.get("timeout_sec", DEFAULT_PLUGIN_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_PLUGIN_TIMEOUT
        return timeout if timeout > 0 else DEFAULT_PLUGIN_TIMEOUT

    async def run(self, rpa_id: str, params: dict) -> dict:
        plugin = self.plugins.get(rpa_id)
        if not plugin:
            return error_result(f"Plugin not found: {rpa_id}")

        try:
            result = await asyncio.wait_for(plugin.run(**params), timeout=self._get_timeout(rpa_id))
            if isinstance(result, dict) and "status" in result:
                return result
            return {"status": "success", "data": result}
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "message": "Plugin execution timed out",
                "error": "Plugin execution timed out",
            }
        except Exception as exc:
            return error_result("Plugin execution failed", error=str(exc))
