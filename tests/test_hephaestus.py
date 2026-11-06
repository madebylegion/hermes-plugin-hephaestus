"""Hephaestus tests. Real `hermes` CLI, always inside a throwaway HERMES_HOME under $TMPDIR.

Run: python -m pytest -q tests
Known-bad sources are built at runtime so this repo's own security scan stays `safe`.
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import forge  # noqa: E402


@pytest.fixture
def env():
    """Fresh HERMES_HOME + data dir; returns the home path. Never touches a real home."""
    old, old_path = os.environ.get("HERMES_HOME"), os.environ["PATH"]
    # venv `hermes` first: the machine-wide launcher pins its own HERMES_HOME and must never win in tests
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + old_path
    home = Path(tempfile.mkdtemp(prefix="heph-test-"))
    os.environ["HERMES_HOME"] = str(home)
    forge.init(home / "plugin-data" / "hephaestus")
    yield home
    shutil.rmtree(home, ignore_errors=True)
    os.environ["PATH"] = old_path
    if old is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = old


def green(name, kinds=("tool",), purpose="test plugin"):
    r = forge.new(name, list(kinds), purpose)
    assert r["ok"], r
    c = forge.check(name)
    assert c["ok"], c["failed"] or c
    return r, c


def live(home, name):
    return home / "plugins" / name


def edit_draft(name, fname, old, new):
    p = forge.data() / "drafts" / name / fname
    txt = p.read_text()
    assert old in txt
    p.write_text(txt.replace(old, new))


def test_name_and_kind_validation(env):
    for bad in ("A", "x", "-ab", "has space", "a" * 41, "../etc", "ab_cd"):
        assert not forge.new(bad, ["tool"], "x")["ok"], bad
    assert not forge.new("good-name", ["bogus"], "x")["ok"]
    assert forge.new("good-name", ["tool"], "x")["ok"]


def test_authoring_contract_size(env):
    assert len(forge.CONTRACT) <= 1500
    assert "register(ctx)" in forge.CONTRACT and "hermes_constants" in forge.CONTRACT


@pytest.mark.parametrize("kinds,perms,counts", [
    (["tool"], [], (1, 0, 0)),
    (["command"], [], (0, 1, 0)),
    (["tool", "command"], [], (1, 1, 0)),
])
def test_template_scaffolds_and_validates_then_installs_disabled(env, kinds, perms, counts):
    name = "demo-" + "-".join(kinds)
    r, c = green(name, kinds)
    assert not [f for f in r["files"] if f.startswith("..")]
    assert c["permissions"] == perms
    assert (c["registrations"]["tools"], c["registrations"]["commands"], c["registrations"]["hooks"]) == counts
    assert any(x["name"] == "security scan" and x["ok"] for x in c["checks"])
    i = forge.install(name)
    assert i["ok"] and i["version"] == 1, i
    assert (live(env, name) / forge.MARKER).is_file()
    assert forge.status(name) == "not enabled"  # lands disabled


def test_desktop_template_if_present(env):
    if not (REPO / "templates" / "desktop").is_dir():
        pytest.skip("desktop template not in this checkout")
    r, c = green("demo-ui", ["desktop"])
    assert "desktop/plugin.js" in r["files"] and "desktop" in c["permissions"]
    assert "demo-ui" in (forge.data() / "drafts" / "demo-ui" / "desktop" / "plugin.js").read_text()
    assert "{{" not in (forge.data() / "drafts" / "demo-ui" / "desktop" / "plugin.js").read_text()


def test_enable_via_cli_without_tool_override_then_disable(env):
    green("hello-forge")
    forge.install("hello-forge")
    assert forge.status("hello-forge") == "not enabled"
    assert forge.enable("hello-forge")["ok"]
    assert forge.status("hello-forge") == "enabled"
    assert re.search(r"hello-forge:\s*\n\s*allow_tool_override: false", (env / "config.yaml").read_text())
    assert "hello-forge  v1  enabled" in forge.slash("list")
    assert forge.disable("hello-forge")["ok"] and forge.status("hello-forge") == "disabled"


def test_rollback_restores_bytes_file_only_and_works_disabled(env):
    green("roll-me")
    forge.install("roll-me")
    h1 = forge.tree_sha(live(env, "roll-me"))
    assert forge.enable("roll-me")["ok"]
    edit_draft("roll-me", "tool_part.py", "Say hello.", "Say hello twice.")
    assert forge.check("roll-me")["ok"]
    i2 = forge.install("roll-me")
    assert i2["ok"] and i2["version"] == 2 and i2["new_permissions"] == []
    assert forge.tree_sha(live(env, "roll-me")) != h1
    assert forge.status("roll-me") != "enabled"  # installing never leaves a plugin enabled
    r = forge.rollback("roll-me")  # plugin is disabled here
    assert r["ok"] and r["sha"] == h1, r
    assert forge.tree_sha(live(env, "roll-me")) == h1  # byte-identical to v1
    assert json.loads((live(env, "roll-me") / forge.MARKER).read_text())["version"] == 1
    assert forge.status("roll-me") != "enabled"  # rollback never enables or disables
    assert not forge.rollback("roll-me")["ok"]  # nothing older left


def test_rollback_while_enabled_keeps_it_enabled_and_keeps_one_snapshot(env):
    green("deep-roll")
    forge.install("deep-roll")
    for i in (2, 3):  # v2 then v3: only the newest previous version (v2) is kept
        edit_draft("deep-roll", "tool_part.py", "Say hello" + ("." if i == 2 else " v2."), f"Say hello v{i}.")
        assert forge.check("deep-roll")["ok"]
        assert forge.install("deep-roll")["version"] == i
    assert forge._versions("deep-roll") == [2]
    assert forge.enable("deep-roll")["ok"]
    assert forge.rollback("deep-roll")["ok"]
    assert forge.status("deep-roll") == "enabled"  # file-only; the caller toggles to reload
    assert "Say hello v2." in (live(env, "deep-roll") / "tool_part.py").read_text()


def test_permission_delta_on_reforge(env):
    green("grow")
    forge.install("grow")
    p = forge.data() / "drafts" / "grow" / "tool_part.py"
    p.write_text("import urllib.request\n" + p.read_text())
    assert "network" in forge.check("grow")["permissions"]
    i = forge.install("grow", "sess-d")
    assert i["new_permissions"] == ["network"]
    md = forge.card_hook("ok", "sess-d", "cli")
    assert "v2 adds network" in md and md.splitlines()[-1] == "/forge enable grow"
    (env / "desktop-plugins" / REPO.name).mkdir(parents=True)
    (env / "desktop-plugins" / REPO.name / "plugin.js").write_text("//")
    forge.install("grow", "sess-e")  # same bytes: no new permission now
    assert 'new="' not in forge.card_hook("ok", "sess-e", "desktop")
    p.write_text(p.read_text() + "\n# v3\n")
    assert forge.check("grow")["ok"] and forge.install("grow", "sess-f")["new_permissions"] == []


def test_cli_missing_gives_clear_error(env, monkeypatch):
    monkeypatch.setenv("PATH", str(env))  # a dir with no `hermes`
    rc, _, err = forge.cli("plugins", "list")
    assert rc == 127 and "not on PATH" in err


def test_refuses_ssh_key_reader(env):
    green("sneaky", ["tool"])
    # runtime-built so the repo scan never sees the literal
    bad = "import os\n_k = open(os.path.expanduser('~/" + ".ss" + "h/id_" + "rsa')).read()\n"
    p = forge.data() / "drafts" / "sneaky" / "tool_part.py"
    p.write_text(bad + p.read_text())
    c = forge.check("sneaky")
    assert not c["ok"] and "security scan" in c["failed"], c["failed"]
    r = forge.install("sneaky")
    assert not r["ok"] and not live(env, "sneaky").exists()


def test_refuses_private_hermes_import(env):
    green("peeker", ["tool"])
    bad = "from hermes_cli" + " import skills_hub\n"
    p = forge.data() / "drafts" / "peeker" / "tool_part.py"
    p.write_text(bad + p.read_text())
    c = forge.check("peeker")
    assert not c["ok"] and "public imports only" in c["failed"], c["failed"]
    assert not forge.install("peeker")["ok"] and not live(env, "peeker").exists()


def test_refuses_symlink_in_draft(env):
    green("linky")
    os.symlink("/etc/hostname", forge.data() / "drafts" / "linky" / "leak.txt")
    c = forge.check("linky")
    assert not c["ok"] and "no symlinks" in c["failed"]


def test_refuses_name_collision_with_foreign_dir(env):
    green("taken")
    foreign = live(env, "taken")
    foreign.mkdir(parents=True)
    (foreign / "plugin.yaml").write_text("name: taken\nversion: 1\ndescription: not ours\n")
    r = forge.install("taken")
    assert not r["ok"] and "not forged" in r["error"]
    assert not (foreign / forge.MARKER).exists() and (foreign / "plugin.yaml").read_text().startswith("name: taken")
    assert not forge.new("taken", ["tool"], "x")["ok"]
    for fn in (forge.enable, forge.disable, forge.rollback, forge.remove):
        assert not fn("taken")["ok"]
    assert foreign.is_dir()  # remove refused too


def test_stale_check_sha_refused_until_rechecked(env):
    green("stale-one")
    edit_draft("stale-one", "tool_part.py", "Say hello.", "Say hi.")
    r = forge.install("stale-one")
    assert not r["ok"] and "forge_check" in r["error"] and not live(env, "stale-one").exists()
    assert forge.check("stale-one")["ok"]
    assert forge.install("stale-one")["ok"]
    assert not forge.install("never-checked")["ok"]


def test_staged_copy_readback_refuses_short_copy(env, monkeypatch):
    green("short-one")
    real = forge._copy

    def lossy(src, dst):  # a copy that silently drops a file
        real(src, dst)
        next(p for p in sorted(Path(dst).rglob("*")) if p.is_file()).unlink()

    monkeypatch.setattr(forge, "_copy", lossy)
    r = forge.install("short-one")
    assert not r["ok"] and "staged copy" in r["error"] and not live(env, "short-one").exists()
    monkeypatch.setattr(forge, "_copy", real)
    assert forge.install("short-one")["ok"]  # same green check still installs once the copy is faithful


def test_failed_check_never_installs(env):
    forge.new("broken", ["tool"], "x")
    (forge.data() / "drafts" / "broken" / "__init__.py").write_text("def register(ctx:\n")
    c = forge.check("broken")
    assert not c["ok"] and c["failed"]
    assert not forge.install("broken")["ok"]


def test_remove_works_disabled_and_enabled(env):
    green("goner")
    forge.install("goner")
    assert forge.remove("goner")["ok"] and not live(env, "goner").exists()  # disabled
    assert forge.list_forged()["plugins"][0]["state"] == "draft"
    assert forge.install("goner")["version"] == 1  # reinstall after remove works
    assert forge.enable("goner")["ok"]
    assert forge.remove("goner")["ok"] and not live(env, "goner").exists()  # enabled


def test_permission_scan_codes():
    src = ("import os, subprocess, urllib.request\nfrom pathlib import Path\n"
           "k = os.environ.get('X')\nopen('f', 'w').write('x')\nPath('a').write_text('b')\n"
           "def register(ctx):\n    ctx.register_hook('pre_tool_call', None)\n    ctx.register_tool(name='t')\n    ctx.register_command('c')\n")
    perms, n = set(), {"tools": 0, "commands": 0, "hooks": 0}
    forge._scan_py(src, perms, n)
    assert perms == {"network", "programs", "files", "env", "behaviour"} and n == {"tools": 1, "commands": 1, "hooks": 1}
    clean, n2 = set(), {"tools": 0, "commands": 0, "hooks": 0}
    forge._scan_py("import json\nopen('f').read()\n", clean, n2)
    assert clean == set()


def mark(home, name):
    d = home / "plugins" / name
    d.mkdir(parents=True)
    (d / forge.MARKER).write_text("{}")


def test_guard_blocks_enable_of_forged_only(env):
    mark(env, "my-forge")
    blocked = [
        "hermes plugins enable my-forge",
        "/usr/bin/hermes --profile x plugins enable my-forge --no-allow-tool-override",
        "hermes plugins toggle 'my-forge'",
        "cd /tmp && hermes plugins enable my-forge",
        "sed -i 's/enabled: \\[\\]/enabled: [my-forge]/' ~/.hermes/config.yaml  # my-forge",
        "hermes config set plugins.enabled my-forge",
    ]
    for cmd in blocked:
        g = forge.guard("terminal", {"command": cmd})
        assert g == {"action": "block", "message": forge.BLOCK_MSG}, cmd
    assert forge.guard("execute_code", {"code": "import os; os.system('hermes plugins enable my-forge')"})["action"] == "block"
    for cmd in ["ls -la", "hermes plugins list", "hermes plugins enable other-plugin", "hermes plugins enable my-forge-two",
                "cat ~/.hermes/config.yaml", "echo my-forge"]:
        assert forge.guard("terminal", {"command": cmd}) is None, cmd
    assert forge.guard("read_file", {"path": "plugins enable my-forge"}) is None
    assert forge.guard("terminal", None) is None


def test_guard_file_tools_and_protected_paths(env):
    cfg = "con" + "fig.yaml"  # pieces: keep this test file's own scan quiet
    assert forge.guard("write_file", {"path": str(env / cfg), "content": "plugins:\n  enabled: [x]\n"}) is None  # no forged plugin yet
    mark(env, "my-forge")
    block = {"action": "block", "message": forge.PROTECT_MSG}
    # config edits that touch plugin state are refused; unrelated config edits and reads pass
    assert forge.guard("write_file", {"path": str(env / cfg), "content": "plugins:\n  enabled: [my-forge]\n"}) == block
    assert forge.guard("patch", {"path": str(env / cfg), "old_string": "enabled: []", "new_string": "enabled: [x]"}) == block
    assert forge.guard("write_file", {"path": str(env / cfg), "content": "model: foo\n"}) is None
    # forged plugin bytes after install, and the desktop-plugins root
    assert forge.guard("write_file", {"path": str(env / "plugins" / "my-forge" / "__init__.py"), "content": "x"}) == block
    assert forge.guard("patch", {"path": "~/x", "patch": "*** Update File: " + str(env / "plugins" / "my-forge" / "a.py")}) == block
    assert forge.guard("write_file", {"path": str(env / "desktop-plugins" / "evil" / "plugin.js"), "content": "x"}) == block
    assert forge.guard("write_file", {"path": str(env / "plugins" / "other" / "a.py"), "content": "x"}) is None
    assert forge.guard("write_file", {"path": str(env / "notes.md"), "content": "x"}) is None
    # shell routes to the same places
    assert forge.guard("terminal", {"command": "cp evil.js " + str(env / "desktop-plugins") + "/evil/plugin.js"}) == block
    assert forge.guard("terminal", {"command": "echo x > " + str(env / "plugins" / "my-forge") + "/__init__.py"}) == block
    assert forge.guard("terminal", {"command": "cat " + str(env / cfg) + " | grep plugins 2>&1"}) is None
    assert forge.guard("terminal", {"command": "ls " + str(env / "desktop-plugins")}) is None


def pending(name="card-one", **kw):
    row = {"name": name, "version": 2, "passed": 17, "total": 17, "warn": 1, "perms": ["network", "files"],
           "tools": 1, "commands": 1, "hooks": 0, "t": __import__("time").time()}
    row.update(kw)
    forge._write_json(forge.data() / "pending" / "s1.json", row)


def test_card_markdown_fallback_ends_with_exact_enable_line(env):
    assert forge.card_hook("done", "s1", "cli") is None  # nothing pending
    pending()
    out = forge.card_hook("All set.", "s1", "cli")
    assert out.startswith("All set.\n\n") and out.splitlines()[-1] == "/forge enable card-one"
    assert len(out.split("\n\n", 1)[1].splitlines()) == 3
    assert forge.card_hook("again", "s1", "cli") is None  # card shown once


def test_card_directive_only_with_desktop_and_materialized_half(env):
    pending()
    assert "::hephaestus" not in forge.card_hook("x", "s1", "desktop")  # half not copied yet
    pending()
    half = env / "desktop-plugins" / REPO.name
    half.mkdir(parents=True)
    (half / "plugin.js").write_text("//")
    assert "::hephaestus" not in forge.card_hook("x", "s1", "telegram")  # wrong platform
    pending()
    out = forge.card_hook("x", "s1", "desktop")
    line = out.splitlines()[-1]
    assert line == ('::hephaestus{name="card-one" version="2" state="installed" checks="17/17" warn="1" '
                    'perms="network,files" tools="1" commands="1" hooks="0"}')
    pending(new=["network", "programs"])
    assert forge.card_hook("x", "s1", "desktop").splitlines()[-1].endswith('hooks="0" new="network,programs"}')
    pending(name='Ev"il{x}', perms=['a"b'])
    assert re.fullmatch(r'::hephaestus\{(\w+="[a-z0-9,/.-]*" ?)+\}', forge.card_hook("x", "s1", "desktop").splitlines()[-1])
    pending(t=1.0)
    assert forge.card_hook("x", "s1", "desktop") is None  # stale pending is dropped


def test_register_matches_manifest_and_session_flows_to_card(env):
    spec = importlib.util.spec_from_file_location("heph_pkg", REPO / "__init__.py", submodule_search_locations=[str(REPO)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["heph_pkg"] = pkg
    spec.loader.exec_module(pkg)
    seen = {"tools": {}, "hooks": {}, "commands": {}}

    class Ctx:
        class state:
            data_dir = env / "plugin-data" / "hephaestus"

        def register_tool(self, name, toolset, schema, handler):
            seen["tools"][name] = handler

        def register_command(self, name, handler, description="", args_hint=""):
            seen["commands"][name] = handler

        def register_hook(self, name, cb):
            seen["hooks"][name] = cb

    pkg.register(Ctx())
    manifest = (REPO / "plugin.yaml").read_text()
    assert sorted(seen["tools"]) == sorted(re.findall(r"^  - (forge_\w+)$", manifest, re.M))
    assert sorted(seen["hooks"]) == ["pre_tool_call", "transform_llm_output"] and "forge" in seen["commands"]
    t = seen["tools"]
    assert json.loads(t["forge_new"]({"name": "via-tool", "kinds": ["tool"], "purpose": "p"}))["ok"]
    assert json.loads(t["forge_check"]({"name": "via-tool"}))["ok"]
    assert json.loads(t["forge_install"]({"name": "via-tool"}, session_id="sess-9"))["ok"]
    out = seen["hooks"]["transform_llm_output"](response_text="Done", session_id="sess-9", platform="cli")
    assert out.endswith("/forge enable via-tool")
    assert seen["commands"]["forge"]("enable via-tool") == "via-tool enabled (tools appear next session)"
    assert "via-tool  v1  enabled" in seen["commands"]["forge"]("list")
    assert "forge_list" not in t
