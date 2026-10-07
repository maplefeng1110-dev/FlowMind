import asyncio
import importlib
import os
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import yaml

from utils.paths import UPLOAD_DIR
from utils.plugin_result import error_result

DEFAULT_PLUGIN_TIMEOUT = 60.0
PATH_PARAM_NAMES = {"file_path", "document_path", "invoice_path", "output_path", "flow_path", "db_path", "source_file"}
PATH_PARAM_SUFFIXES = ("_path", "_paths", "_file", "_files")


def _resolve_path(path_text: str) -> Path:
    return Path(os.path.expanduser(path_text)).resolve(strict=False)


def _iter_path_params(params: dict) -> Iterator[Tuple[str, str]]:
    for name, value in (params or {}).items():
        lowered = str(name).lower()
        if lowered not in PATH_PARAM_NAMES and not lowered.endswith(PATH_PARAM_SUFFIXES):
            continue
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(item, str) and item.strip():
                yield str(name), item.strip()


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

    def _allowed_path_roots(self, rpa_id: str) -> List[Path]:
        """Directories a plugin may read/write: the roots its manifest declares, plus
        the upload directory and FLOWMIND_AGENT_ALLOWED_PATHS. Plugins that declare no
        roots are not restricted."""
        profile = self.manifests.get(rpa_id, {}).get("tool_profile") or {}
        declared = [str(root).strip() for root in profile.get("allowed_path_roots") or [] if str(root).strip()]
        if not declared:
            return []
        extra = [root.strip() for root in os.getenv("FLOWMIND_AGENT_ALLOWED_PATHS", "").split(os.pathsep) if root.strip()]
        return [_resolve_path(root) for root in declared + extra] + [UPLOAD_DIR.resolve()]

    def _find_disallowed_path(self, rpa_id: str, params: dict) -> Optional[str]:
        roots = self._allowed_path_roots(rpa_id)
        if not roots:
            return None
        for name, value in _iter_path_params(params):
            resolved = _resolve_path(value)
            if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
                return f"{name}={value}"
        return None

    async def run(self, rpa_id: str, params: dict) -> dict:
        plugin = self.plugins.get(rpa_id)
        if not plugin:
            return error_result(f"Plugin not found: {rpa_id}")

        # Enforced here on the Agent: the chat-side constraint layer only guides the model.
        disallowed = self._find_disallowed_path(rpa_id, params or {})
        if disallowed:
            return error_result(
                "Path is outside the directories this plugin may access",
                error=f"{disallowed} is not under the plugin's allowed_path_roots",
            )

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
