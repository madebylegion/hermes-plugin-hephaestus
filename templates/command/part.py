"""Slash command part of {{name}}."""


def _run(raw_args):
    return "{{name}} says hello" + (f": {raw_args.strip()}" if raw_args and raw_args.strip() else "")


def register_command(ctx):
    ctx.register_command("{{name}}", handler=_run, description="Replace with what /{{name}} does")
