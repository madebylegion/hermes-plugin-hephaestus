"""Tool part of {{name}}."""
import json

SCHEMA = {
    "name": "{{ident}}_hello",
    "description": "Say hello. Replace this with what {{name}} really does.",
    "parameters": {"type": "object", "properties": {"who": {"type": "string"}}, "required": []},
}


def _hello(args, **_kw):
    return json.dumps({"hello": (args or {}).get("who") or "world"})


def register_tool(ctx):
    ctx.register_tool(name="{{ident}}_hello", toolset="{{ident}}", schema=SCHEMA, handler=_hello)
