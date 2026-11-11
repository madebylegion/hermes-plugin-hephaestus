"""Hephaestus: Hermes forges its own plugins. Drafts and checks by tool; only the user enables (/forge enable)."""
from __future__ import annotations

import json
from pathlib import Path

from . import forge

_NAME = {"type": "string", "description": "Plugin name: ^[a-z][a-z0-9-]{1,39}$"}


def _schema(name, desc, props, required):
    return {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required}}


def _tool(fn):
    return lambda args, **kw: json.dumps(fn(args or {}, kw), ensure_ascii=False)


def register(ctx):
    try:
        forge.init(Path(ctx.state.data_dir))
    except Exception:  # state facade unavailable (validator probe): fall back to the default data dir
        pass
    tools = [
        ("forge_new", "Start a plugin draft from templates. Returns the draft path, file list and the authoring contract.",
         {"name": _NAME, "kinds": {"type": "array", "items": {"type": "string", "enum": list(forge.KINDS)}},
          "purpose": {"type": "string", "description": "One sentence: what the plugin does"}}, ["name", "purpose"],
         lambda a, kw: forge.new(a.get("name"), a.get("kinds"), a.get("purpose", ""))),
        ("forge_check", "Run the official validator and doctor on a draft plus a permission scan. This imports the draft's Python, so the user is asked to approve each exact draft first. Repeat until ok.",
         {"name": _NAME}, ["name"], lambda a, kw: forge.check(a.get("name"))),
        ("forge_install", "Install a green-checked draft into Hermes DISABLED. You cannot enable it; the user does.",
         {"name": _NAME}, ["name"], lambda a, kw: forge.install(a.get("name"), kw.get("session_id", ""))),
    ]
    for name, desc, props, req, fn in tools:
        ctx.register_tool(name=name, toolset="hephaestus", schema=_schema(name, desc, props, req), handler=_tool(fn))
    ctx.register_command("forge", handler=forge.slash, description="Manage forged plugins",
                         args_hint="list | enable|disable|rollback|remove <name>")
    ctx.register_hook("pre_tool_call", forge.guard)
    ctx.register_hook("transform_llm_output", forge.card_hook)
