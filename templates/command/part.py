"""Slash command part of {{name}}."""
from pathlib import Path

try:
    from agent.runtime_cwd import resolve_agent_cwd  # the user's working folder, not the gateway's
except ImportError:
    resolve_agent_cwd = Path.cwd


def _run(raw_args):
    cwd = resolve_agent_cwd()  # use for git/ls/file reads and subprocess cwd=; never os.getcwd()
    return "{{name}} says hello from " + str(cwd) + (f": {raw_args.strip()}" if raw_args and raw_args.strip() else "")


def register_command(ctx):
    ctx.register_command("{{name}}", handler=_run, description="Replace with what /{{name}} does")
