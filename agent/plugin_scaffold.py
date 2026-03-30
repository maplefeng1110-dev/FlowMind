import argparse
import ast
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_PLUGINS_DIR = AGENT_DIR / "plugins"
ALLOWED_PARAM_TYPES = {"string", "integer", "number", "boolean", "object", "array"}
PLUGIN_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PLUGIN_DIR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ParamSpec:
    name: str
    schema_type: str | None = None
    required: bool = False
    default: Any = None


@dataclass
class SourceAnalysis:
    is_async: bool
    params: list[ParamSpec]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scaffold a FlowMind plugin or wrap an existing RPA Python file as a plugin."
    )
    parser.add_argument("plugin_id", help="Plugin id used in manifest.yaml")
    parser.add_argument(
        "--description",
        default="",
        help="Plugin description. Defaults to '<plugin_id> plugin'.",
    )
    parser.add_argument(
        "--dir-name",
        default="",
        help="Directory name under agent/plugins. Defaults to plugin_id.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_PLUGINS_DIR),
        help="Target plugins directory. Defaults to the current Agent's plugins directory.",
    )
    parser.add_argument(
        "--source-file",
        default="",
        help="Existing Python file containing a run() function to wrap as a plugin.",
    )
    parser.add_argument(
        "--version",
        default="1.0.0",
        help="Manifest version. Defaults to 1.0.0.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=60,
        help="Manifest timeout_sec. Defaults to 60.",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Repeatable plugin tag.",
    )
    parser.add_argument(
        "--capability",
        action="append",
        default=[],
        help="Repeatable plugin capability.",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Repeatable intent keyword. When present, enforcement block will be generated.",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Repeatable parameter definition in 'name:type' format for manual scaffolding.",
    )
    parser.add_argument(
        "--required",
        action="append",
        default=[],
        help="Repeatable required parameter name for manual scaffolding.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing generated plugin directory.",
    )
    return parser.parse_args(argv)


def _annotation_to_schema_type(annotation_text: str) -> str | None:
    normalized = (annotation_text or "").replace(" ", "")
    if not normalized:
        return None
    if "|" in normalized:
        parts = [part for part in normalized.split("|") if part != "None"]
        if len(parts) == 1:
            normalized = parts[0]
    if normalized.startswith("Optional[") and normalized.endswith("]"):
        normalized = normalized[9:-1]

    mapping = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "dict": "object",
        "Dict": "object",
        "list": "array",
        "List": "array",
    }
    if normalized in mapping:
        return mapping[normalized]
    if normalized.startswith(("list[", "List[")):
        return "array"
    if normalized.startswith(("dict[", "Dict[")):
        return "object"
    return None


def _literal_or_none(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _analyze_source_file(source_file: Path) -> SourceAnalysis:
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            params: list[ParamSpec] = []
            all_args = list(node.args.posonlyargs) + list(node.args.args)
            defaults = [None] * (len(all_args) - len(node.args.defaults)) + list(node.args.defaults)
            for arg, default_node in zip(all_args, defaults):
                if arg.arg == "self":
                    continue
                params.append(
                    ParamSpec(
                        name=arg.arg,
                        schema_type=_annotation_to_schema_type(ast.unparse(arg.annotation) if arg.annotation else ""),
                        required=default_node is None,
                        default=_literal_or_none(default_node),
                    )
                )
            for kw_arg, default_node in zip(node.args.kwonlyargs, node.args.kw_defaults):
                params.append(
                    ParamSpec(
                        name=kw_arg.arg,
                        schema_type=_annotation_to_schema_type(ast.unparse(kw_arg.annotation) if kw_arg.annotation else ""),
                        required=default_node is None,
                        default=_literal_or_none(default_node),
                    )
                )
            return SourceAnalysis(is_async=isinstance(node, ast.AsyncFunctionDef), params=params)
    raise ValueError(f"{source_file} must define a top-level run() function")


def _parse_manual_params(items: list[str], required_names: set[str]) -> list[ParamSpec]:
    params: list[ParamSpec] = []
    for item in items:
        name, separator, schema_type = str(item).partition(":")
        param_name = name.strip()
        param_type = schema_type.strip().lower()
        if not separator or not param_name or param_type not in ALLOWED_PARAM_TYPES:
            raise ValueError(
                f"Invalid --param '{item}'. Expected format name:type, type in {sorted(ALLOWED_PARAM_TYPES)}"
            )
        params.append(ParamSpec(name=param_name, schema_type=param_type, required=param_name in required_names))
    return params


def _build_params_schema(params: list[ParamSpec]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required = []
    for param in params:
        schema = {
            "type": param.schema_type or "string",
            "description": f"参数 {param.name}",
        }
        if param.default is not None:
            schema["default"] = param.default
        properties[param.name] = schema
        if param.required:
            required.append(param.name)
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        result["required"] = required
    return result


def _build_default_tool_profile(args: argparse.Namespace, params: list[ParamSpec]) -> dict[str, Any]:
    description = args.description.strip() or f"{args.plugin_id} plugin"
    required_params = [param.name for param in params if param.required]
    suitable_inputs = []
    if required_params:
        suitable_inputs.append(f"需要参数：{', '.join(required_params)}")
    if args.source_file:
        suitable_inputs.append("适合包装已有 Python RPA 的标准化入口")
    return {
        "solves": [description],
        "suitable_inputs": suitable_inputs or ["适合结构化业务参数输入"],
        "outputs": ["返回结构化执行状态和 data 结果"],
        "prerequisites": [],
        "common_failure_reasons": ["缺少关键参数", "输入文件不存在或格式不支持"],
        "supports_batch": any((param.schema_type or "").lower() == "array" for param in params),
        "has_side_effects": False,
        "requires_confirmation": False,
        "allowed_path_roots": ["data/uploads", "data/exports"],
        "max_retries": 1,
    }


def _build_manifest(args: argparse.Namespace, params: list[ParamSpec]) -> dict[str, Any]:
    description = args.description.strip() or f"{args.plugin_id} plugin"
    manifest: dict[str, Any] = {
        "id": args.plugin_id,
        "version": args.version,
        "description": description,
        "tags": args.tag or [args.plugin_id],
        "capabilities": args.capability or [args.plugin_id],
        "params_schema": _build_params_schema(params),
        "returns_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "执行状态"},
                "data": {"type": "object", "description": "插件返回数据"},
            },
        },
        "timeout_sec": args.timeout_sec,
        "owner": "system",
        "tool_profile": _build_default_tool_profile(args, params),
    }
    if args.keyword:
        manifest["enforcement"] = {
            "must_call_when_matched": True,
            "require_success_before_final": True,
            "intent_keywords": args.keyword,
        }
    return manifest


def _validate_plugin_names(plugin_id: str, dir_name: str) -> None:
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise ValueError("plugin_id 仅支持字母开头，后续可包含字母、数字和下划线")
    if not PLUGIN_DIR_RE.fullmatch(dir_name):
        raise ValueError("插件目录名仅支持字母或下划线开头，后续可包含字母、数字和下划线")


def _write_manifest(plugin_dir: Path, manifest: dict[str, Any]) -> None:
    (plugin_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_stub(plugin_dir: Path, plugin_id: str) -> None:
    (plugin_dir / "__init__.py").write_text(
        (
            "from utils.plugin_result import success_result\n\n\n"
            "async def run(**params):\n"
            "    return success_result(\n"
            f"        data={{\"plugin_id\": \"{plugin_id}\", \"received\": params}},\n"
            "        message=\"Scaffold plugin created. Replace this stub with your real RPA logic.\",\n"
            "    )\n"
        ),
        encoding="utf-8",
    )


def _write_wrapper(plugin_dir: Path, source_file: Path, is_async: bool) -> None:
    shutil.copy2(source_file, plugin_dir / "impl.py")
    if is_async:
        wrapper = (
            "from .impl import run as impl_run\n\n\n"
            "async def run(**params):\n"
            "    return await impl_run(**params)\n"
        )
    else:
        wrapper = (
            "import asyncio\n\n"
            "from .impl import run as impl_run\n\n\n"
            "async def run(**params):\n"
            "    return await asyncio.to_thread(lambda: impl_run(**params))\n"
        )
    (plugin_dir / "__init__.py").write_text(wrapper, encoding="utf-8")


def scaffold_plugin(argv: list[str] | None = None) -> Path:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    dir_name = args.dir_name.strip() or args.plugin_id
    _validate_plugin_names(args.plugin_id, dir_name)
    plugin_dir = output_dir / dir_name

    if plugin_dir.exists():
        if args.force:
            shutil.rmtree(plugin_dir)
        elif any(plugin_dir.iterdir()):
            raise FileExistsError(f"Plugin directory already exists and is not empty: {plugin_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "__init__.py").touch(exist_ok=True)
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if args.source_file:
        source_file = Path(args.source_file).resolve()
        if not source_file.exists():
            raise FileNotFoundError(f"Source file not found: {source_file}")
        analysis = _analyze_source_file(source_file)
        params = analysis.params
        _write_wrapper(plugin_dir, source_file, analysis.is_async)
    else:
        params = _parse_manual_params(args.param, set(args.required))
        _write_stub(plugin_dir, args.plugin_id)

    _write_manifest(plugin_dir, _build_manifest(args, params))
    return plugin_dir


def main(argv: list[str] | None = None) -> int:
    try:
        plugin_dir = scaffold_plugin(argv)
    except Exception as exc:
        print(f"[scaffold_plugin] ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"[scaffold_plugin] Plugin created: {plugin_dir}")
    print("[scaffold_plugin] Files:")
    print(f"  - {plugin_dir / 'manifest.yaml'}")
    print(f"  - {plugin_dir / '__init__.py'}")
    if (plugin_dir / "impl.py").exists():
        print(f"  - {plugin_dir / 'impl.py'}")
    print("[scaffold_plugin] Restart Agent to load the new plugin.")
    return 0
