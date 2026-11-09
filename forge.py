"""Hephaestus core: scaffold, check, install, roll back and label forged plugins.

Stdlib + hermes_constants only. Hermes is driven through its public CLI (fixed argv, no shell).
Drafts: <data>/drafts/<name>/. Snapshots: <data>/versions/<name>/<n>/. Live: $HERMES_HOME/plugins/<name>/.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import hermes_constants

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
MARKER = ".forged-by-hephaestus"
KINDS = ("tool", "command", "desktop")
TEMPLATES = Path(__file__).parent / "templates"
BLOCK_MSG = "Only the user can enable a forged plugin. Ask them to click Enable."
CONTRACT = """Authoring contract (Hermes plugin, Python 3 stdlib only):
- Edit only files inside draft_dir. __init__.py must define register(ctx).
- ctx.register_tool(name=, toolset=, schema={name, description, parameters: JSON-schema}, handler=fn). Handler: fn(args: dict, **kw) -> JSON string. List tools under provides_tools in plugin.yaml.
- ctx.register_command("name", handler=fn(raw_args: str) -> str, description="...") adds /name.
- ctx.register_hook("post_tool_call", fn(**kw)); list hooks under provides_hooks in plugin.yaml. Behaviour-changing hooks (pre_tool_call, transform_llm_output, pre_llm_call) show on the permission label.
- Imports: stdlib and hermes_constants only. Never import hermes_cli.*, tools.*, agent.*, gateway.*, except `from agent.runtime_cwd import resolve_agent_cwd` = the user's folder (git, ls, cwd=); never os.getcwd(). Guard with try/except ImportError.
- No python_dependencies. No network or subprocess unless the user asked for it. Write files only under hermes_constants.get_hermes_home()/'plugin-data'/'<name>'. Never read secrets, ~/.ssh, .env files or API keys from os.environ.
- Keep description honest.
- Desktop kind: desktop/plugin.js is plain ESM, its id equals the plugin name, imports only @hermes/plugin-sdk, react, react/jsx-runtime.
Loop: edit files -> forge_check(name) -> fix each failed check -> green -> forge_install(name). Install leaves the plugin DISABLED. You cannot enable it: tell the user to click Enable or type /forge enable <name>."""

_data_dir: Path | None = None


def init(data_dir) -> None:
    global _data_dir
    _data_dir = Path(data_dir)


def home() -> Path:
    return Path(hermes_constants.get_hermes_home())


def data() -> Path:
    return _data_dir or home() / "plugin-data" / "hephaestus"


def _err(msg, **extra) -> dict:
    return {"ok": False, "error": msg, **extra}


# ---------------------------------------------------------------- CLI
# A home's first `plugins enable` builds Hermes' ~260 MB plugin runtime: 25 s idle, over 120 s when the machine is busy.
CLI_TIMEOUT = 600


def cli(*args) -> tuple[int, str, str]:
    """Run `hermes <args>` with an explicit HERMES_HOME. Returns (rc, stdout, stderr)."""
    sib = Path(sys.executable).parent / "hermes"
    exe = str(sib) if sib.is_file() and os.access(sib, os.X_OK) else shutil.which("hermes")
    if not exe:
        return 127, "", "hermes CLI not on PATH; add the folder that holds `hermes` to PATH and retry"
    cmd = [exe]
    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_YOLO_MODE", "HERMES_NONINTERACTIVE", "HERMES_INTERACTIVE")}
    env.update(HERMES_HOME=str(home()), NO_COLOR="1", COLUMNS="200")
    try:
        p = subprocess.run(cmd + [str(a) for a in args], env=env, capture_output=True, text=True,
                           timeout=CLI_TIMEOUT, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, "", f"hermes CLI failed: {exc}"
    return p.returncode, p.stdout or "", p.stderr or ""


def _json_out(text: str, opener: str):
    try:
        return json.loads(text[text.index(opener):])
    except (ValueError, json.JSONDecodeError):
        return None


def status(name: str) -> str | None:
    """'enabled' | 'not enabled' | None (unknown to Hermes), via `plugins list --json --user`."""
    rows = _json_out(cli("plugins", "list", "--json", "--user")[1], "[") or []
    for r in rows:
        if r.get("name") == name:
            return r.get("status")
    return None


# ---------------------------------------------------------------- files
def tree_sha(d: Path) -> str:
    h = hashlib.sha256()
    files = sorted((p for p in Path(d).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != MARKER),
                   key=lambda p: p.relative_to(d).as_posix())
    for f in files:
        h.update(f.relative_to(d).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def _copy(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _tmp() -> Path:
    (data() / "tmp").mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=data() / "tmp")) / "x"


def _swap(src: Path, dst: Path) -> None:
    """Put src at dst (same filesystem). The old dst moves aside first and is restored if the swap fails."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    old = None
    if dst.exists():
        old = _tmp()
        os.replace(dst, old)
    try:
        os.replace(src, dst)
    except OSError:
        if old:
            os.replace(old, dst)
        raise
    if old:
        shutil.rmtree(old.parent, ignore_errors=True)
    shutil.rmtree(src.parent, ignore_errors=True)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _marker(d: Path):
    return _read_json(d / MARKER)


def _versions(name: str) -> list[int]:
    root = data() / "versions" / name
    return sorted(int(p.name) for p in root.iterdir() if p.name.isdigit()) if root.is_dir() else []


def forged_names() -> list[str]:
    root = home() / "plugins"
    return sorted(p.name for p in root.iterdir() if (p / MARKER).is_file()) if root.is_dir() else []


# ---------------------------------------------------------------- scaffold
def new(name: str, kinds, purpose: str = "") -> dict:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return _err("name must match ^[a-z][a-z0-9-]{1,39}$")
    kinds = list(dict.fromkeys(kinds or ["tool"]))
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        return _err(f"unknown kinds {bad}; choose from {list(KINDS)}")
    target, draft = home() / "plugins" / name, data() / "drafts" / name
    if target.exists() and not (target / MARKER).exists():
        return _err(f"plugin {name!r} exists and was not forged by Hephaestus; pick another name")
    if "desktop" in kinds and not (TEMPLATES / "desktop").is_dir():
        return _err("desktop template is not available in this install")
    note = "draft already exists; edit it, nothing was overwritten"
    if not draft.exists():
        ident = name.replace("-", "_")
        sub = lambda s: s.replace("{{name}}", name).replace("{{ident}}", ident)  # noqa: E731
        stage = _tmp()
        if target.exists():  # new version of an installed forged plugin: start from what is live
            _copy(target, stage)
            (stage / MARKER).unlink()
            note = "draft seeded from the installed version"
        else:
            stage.mkdir()
            parts, manifest = [], ""
            for k in kinds:
                t = TEMPLATES / k
                if k == "desktop":
                    for f in sorted(p for p in t.rglob("*") if p.is_file()):
                        out = stage / f.relative_to(t)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_text(sub(f.read_text(encoding="utf-8")), encoding="utf-8")
                    continue
                (stage / f"{k}_part.py").write_text(sub((t / "part.py").read_text(encoding="utf-8")), encoding="utf-8")
                manifest += sub((t / "manifest.part").read_text(encoding="utf-8"))
                parts.append(k)
            desc = " ".join(str(purpose).split())[:200] or f"Forged plugin {name}"
            (stage / "plugin.yaml").write_text(
                f'name: {name}\nversion: 0.1.0\ndescription: {json.dumps(desc)}\nauthor: "Hephaestus (forged)"\n{manifest}',
                encoding="utf-8")
            imports = "".join(f"from .{k}_part import register_{k}\n" for k in parts)
            calls = "".join(f"    register_{k}(ctx)\n" for k in parts) or "    pass\n"
            (stage / "__init__.py").write_text(f'"""{name}: forged by Hephaestus."""\n{imports}\n\ndef register(ctx):\n{calls}',
                                               encoding="utf-8")
        _swap(stage, draft)
    files = sorted(p.relative_to(draft).as_posix() for p in draft.rglob("*") if p.is_file())
    return {"ok": True, "name": name, "draft_dir": str(draft), "files": files, "note": note, "contract": CONTRACT}


# ---------------------------------------------------------------- permission scan (a label for consent, not a sandbox)
_NET = {"urllib", "http", "socket", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "websockets", "urllib3", "xmlrpc", "telnetlib"}
_PROG_FN = {"system", "popen", "execv", "execve", "execl", "execlp", "execvp", "spawnl", "spawnv", "startfile"}
_FILE_FN = {"remove", "unlink", "rmdir", "rename", "replace", "makedirs", "mkdir", "removedirs"}
_FILE_METHOD = {"write_text", "write_bytes", "unlink", "mkdir", "rmdir", "touch"}
_BEHAVIOUR = {"pre_tool_call", "transform_llm_output", "pre_llm_call", "pre_verify", "transform_tool_result", "transform_terminal_output"}
_JS_NET = re.compile(r"\bfetch\s*\(|\bWebSocket\b|XMLHttpRequest|\bEventSource\b")


def _mode_writes(call: ast.Call, pos: int) -> bool:
    mode = call.args[pos] if len(call.args) > pos else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in "wax+")


def _scan_py(src: str, perms: set, n: dict) -> None:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                root = m.split(".")[0]
                perms.update({"network"} if root in _NET else {"programs"} if root in ("subprocess", "pty") else {"files"} if root == "shutil" else set())
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                for a in node.names:
                    perms.update({"env"} if a.name in ("environ", "getenv") else {"programs"} if a.name in _PROG_FN else {"files"} if a.name in _FILE_FN else set())
        elif isinstance(node, ast.Attribute):
            base = node.value.id if isinstance(node.value, ast.Name) else ""
            if base == "os":
                perms.update({"env"} if node.attr in ("environ", "getenv") else {"programs"} if node.attr in _PROG_FN or node.attr.startswith(("spawn", "exec")) else {"files"} if node.attr in _FILE_FN else set())
            elif base == "shutil" or node.attr in _FILE_METHOD:
                perms.add("files")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open" and _mode_writes(node, 1):
                perms.add("files")
            if not isinstance(f, ast.Attribute):
                continue
            if f.attr == "open" and _mode_writes(node, 0):
                perms.add("files")
            elif f.attr == "register_tool":
                n["tools"] += 1
            elif f.attr == "register_command":
                n["commands"] += 1
            elif f.attr == "register_hook":
                n["hooks"] += 1
                a0 = node.args[0] if node.args else None
                if isinstance(a0, ast.Constant) and a0.value in _BEHAVIOUR:
                    perms.add("behaviour")
            elif f.attr == "register_middleware":
                perms.add("behaviour")


def scan(d: Path) -> dict:
    perms, n = set(), {"tools": 0, "commands": 0, "hooks": 0}
    for f in sorted(Path(d).rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace") if f.suffix in (".py", ".js") else ""
        if f.suffix == ".py":
            _scan_py(text, perms, n)
        elif f.suffix == ".js" and _JS_NET.search(text):
            perms.add("network")
    if (Path(d) / "desktop" / "plugin.js").is_file():
        perms.add("desktop")
    return {"permissions": sorted(perms), **n}


# ---------------------------------------------------------------- check / install
def _draft(name: str):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return None, _err("name must match ^[a-z][a-z0-9-]{1,39}$")
    d = data() / "drafts" / name
    return (d, None) if d.is_dir() else (None, _err(f"no draft {name!r}; call forge_new first"))


def check(name: str) -> dict:
    d, e = _draft(name)
    if e:
        return e
    sha = tree_sha(d)
    _, out, err = cli("plugins", "validate", "--json", d)
    v = _json_out(out, "{") or {"checks": [{"name": "validate", "ok": False, "detail": (err or out)[-300:]}], "warnings": []}
    checks = list(v.get("checks", []))
    drc, dout, derr = cli("plugins", "doctor", "--ci", d)
    checks.append({"name": "doctor", "ok": drc == 0, "detail": "ok" if drc == 0 else (dout + derr)[-300:]})
    links = [p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_symlink()]
    checks.append({"name": "no symlinks", "ok": not links, "detail": ", ".join(links) or "none"})
    s = scan(d)
    m = re.search(r"registrations:\s*(\d+) tool\(s\),\s*(\d+) hook\(s\)", dout)
    reg = {"tools": int(m.group(1)) if m else s["tools"], "commands": s["commands"], "hooks": int(m.group(2)) if m else s["hooks"]}
    # the validator only WARNS on private core imports; Hephaestus refuses them
    priv = [w for w in v.get("warnings", []) if str(w).startswith("private core imports")]
    checks.append({"name": "public imports only", "ok": not priv, "detail": priv[0][:300] if priv else "none"})
    failed = [c["name"] for c in checks if not c.get("ok")]
    sec = next((c.get("detail", "") for c in checks if c.get("name") == "security scan"), "")
    res = {"ok": not failed, "name": name, "sha": sha, "checks": checks, "failed": failed, "warnings": v.get("warnings", []),
           "permissions": s["permissions"], "registrations": reg, "security": sec}
    _write_json(data() / "checks" / f"{name}.json", res)
    return res


def install(name: str, session_id: str = "") -> dict:
    d, e = _draft(name)
    if e:
        return e
    rec = _read_json(data() / "checks" / f"{name}.json")
    if not rec or not rec.get("ok") or rec.get("sha") != tree_sha(d):
        return _err("no green forge_check for the current draft; run forge_check again")
    if "dangerous" in rec.get("security", "") or rec.get("failed"):
        return _err("check has failures; fix them first")
    target = home() / "plugins" / name
    if target.exists() and not (target / MARKER).exists():
        return _err(f"plugin {name!r} exists and was not forged by Hephaestus; refusing to touch it")
    if status(name) == "enabled":
        rc, _, er = cli("plugins", "disable", name)
        if rc:
            return _err(f"could not disable the running version first: {er[-200:]}")
    prev = (_marker(target) or {}).get("version", 0) if target.exists() else 0
    version = max([prev, *_versions(name)]) + 1
    new_perms: list[str] = []
    if target.exists():
        new_perms = sorted(set(rec["permissions"]) - set(scan(target)["permissions"]))  # what v2 adds over what is live
        snap = _tmp()
        _copy(target, snap)
        _swap(snap, data() / "versions" / name / str(prev))
        for v in _versions(name):  # keep one previous version only
            if v != prev:
                shutil.rmtree(data() / "versions" / name / str(v), ignore_errors=True)
    stage = _tmp()
    _copy(d, stage)
    if tree_sha(stage) != rec["sha"]:  # staged-copy readback: the bytes about to go live are the bytes that passed the check
        shutil.rmtree(stage.parent, ignore_errors=True)
        return _err("staged copy differs from the checked draft; nothing installed")
    _write_json(stage / MARKER, {"version": version, "sha": rec["sha"], "installed_utc": datetime.now(timezone.utc).isoformat()})
    _swap(stage, target)
    r = rec["registrations"]
    _write_json(data() / "pending" / f"{session_id or 'default'}.json", {
        "name": name, "version": version, "passed": sum(1 for c in rec["checks"] if c.get("ok")), "total": len(rec["checks"]),
        "warn": len(rec["warnings"]), "perms": rec["permissions"], "new": new_perms, "tools": r["tools"], "commands": r["commands"],
        "hooks": r["hooks"], "t": time.time()})
    return {"ok": True, "name": name, "version": version, "state": "installed (disabled)", "permissions": rec["permissions"],
            "new_permissions": new_perms, "warnings": rec["warnings"], "next": f"Tell the user: only they can enable it. Click Enable on the card or type /forge enable {name}"}


# ---------------------------------------------------------------- list / slash command
def list_forged() -> dict:
    rows = {}
    for n in forged_names():
        m = _marker(home() / "plugins" / n) or {}
        rows[n] = {"name": n, "version": m.get("version"), "state": "enabled" if status(n) == "enabled" else "installed"}
    drafts = data() / "drafts"
    for p in sorted(drafts.iterdir()) if drafts.is_dir() else []:
        c = _read_json(data() / "checks" / f"{p.name}.json")
        row = rows.setdefault(p.name, {"name": p.name, "version": None, "state": "draft"})
        row["last_check"] = None if not c else ("green" if c.get("ok") else "failed")
        row["draft_changed"] = bool(c) and c.get("sha") != tree_sha(p)
    return {"ok": True, "plugins": list(rows.values())}


def _marked(name: str):
    t = home() / "plugins" / str(name)
    return (t, None) if NAME_RE.fullmatch(str(name)) and (t / MARKER).is_file() else (None, _err(f"{name!r} is not a plugin forged by Hephaestus"))


def _cli_result(rc, er, ok_msg) -> dict:
    return {"ok": True, "message": ok_msg} if rc == 0 else _err(er[-300:] or "hermes CLI failed")


def enable(name: str) -> dict:
    t, e = _marked(name)
    if e:
        return e
    rc, _, er = cli("plugins", "enable", name, "--no-allow-tool-override")
    return _cli_result(rc, er, f"{name} enabled (tools appear next session)")


def disable(name: str) -> dict:
    t, e = _marked(name)
    if e:
        return e
    rc, _, er = cli("plugins", "disable", name)
    return _cli_result(rc, er, f"{name} disabled")


def rollback(name: str) -> dict:
    t, e = _marked(name)
    if e:
        return e
    vs = _versions(name)
    if not vs:
        return _err("no earlier version to roll back to")
    snap = data() / "versions" / name / str(vs[-1])
    want = tree_sha(snap)
    stage = _tmp()
    _copy(snap, stage)
    _swap(stage, t)
    if tree_sha(t) != want:  # hash readback
        return _err("rollback readback failed: restored bytes differ from the snapshot")
    shutil.rmtree(snap, ignore_errors=True)
    # file-only: an already-enabled plugin is not reloaded by `enable`, so the caller toggles it off and on
    return {"ok": True, "sha": want, "message": f"{name} restored to version {(_marker(t) or {}).get('version')} (sha {want[:12]}). "
            "If it is enabled, switch it off and on to load the restored files"}


def remove(name: str) -> dict:
    t, e = _marked(name)
    if e:
        return e
    if status(name) == "enabled":
        cli("plugins", "disable", name)
    rc, _, er = cli("plugins", "remove", name)
    if t.exists() and (t / MARKER).is_file():
        shutil.rmtree(t, ignore_errors=True)  # CLI refused or failed: the marker proves we own this dir
    return {"ok": True, "message": f"{name} removed (snapshots kept)"} if not t.exists() else _err(er[-300:] or "remove failed")


def slash(raw_args: str = "", **_kw) -> str:
    parts = (raw_args or "").split()
    verbs = {"enable": enable, "disable": disable, "rollback": rollback, "remove": remove}
    if not parts or parts[0] == "list":
        rows = list_forged()["plugins"]
        return "\n".join(f"{r['name']}  v{r['version'] or '-'}  {r['state']}" + (f"  check:{r['last_check']}" if r.get("last_check") else "")
                         for r in rows) or "No forged plugins yet. Ask the agent: forge me a plugin that ..."
    if parts[0] in verbs and len(parts) == 2:
        r = verbs[parts[0]](parts[1])
        return r.get("message") or f"Error: {r.get('error')}"
    return "Usage: /forge list | enable <name> | disable <name> | rollback <name> | remove <name>"


# ---------------------------------------------------------------- guard (rule 2b) and card hook
# The patterns are assembled from pieces: this file must not itself look like the thing it guards against.
_ENABLE = re.compile(r"\bplugins?[\s\"',\[\]]+(?:enable|toggle)\b")  # also list argv: ["hermes","plugins","enable",...]
_CFG_FILE = "config" + r"\.ya?ml"
_CFG_SET = re.compile(r"\bconfig\s+set\b")
# a shell verb that writes: redirect, tee, in-place edit, copy/move; _CODE_W is the Python equivalent
_VERB = re.compile(r"(?<![\d&])>>?|\btee\b|\b(?:sed|yq|perl)\s+(?:-\w*i|--in-?place)|\b(?:cp|mv|ln|rsync|install)\b")
_CODE_W = re.compile(r"\bshutil\b|\.write\w*\(|\w*dump\(|\.(?:replace|rename|symlink_to)\(|\bopen\s*\([^)]*(?:,\s*|mode\s*=\s*)['\"][wa]")
PROTECT_MSG = "Hephaestus protects plugin settings and forged plugin files. Use forge_new, forge_check and forge_install, and ask the user to click Enable."


def _writes_to(text: str, tgt: str) -> bool:
    """True when the path regex `tgt` is the TARGET of a write in `text`: redirect, tee, sed/yq -i, last arg of cp/mv,
    a `cd` into it followed by any write verb, or Python code that writes and names it. Reads (grep, cat, cp out) pass."""
    t = rf"[^\s|;&<>]*{tgt}[^\s|;&<>]*"
    end = r"(?:\s+(?:\d*>&?\S*|#.*))*\s*(?:$|[|;&])"
    pats = [rf"(?<![\d&])>>?\s*{t}", rf"\btee\b[^|;&\n]*?{t}", rf"\b(?:sed|yq|perl)\s+(?:-\w*i|--in-?place)[^|;&\n]*?{t}",
            rf"\b(?:cp|mv|ln|rsync|install)\b[^|;&\n]*\s{t}{end}"]
    return (any(re.search(q, text, re.M) for q in pats) or bool(re.search(tgt, text) and _CODE_W.search(text))
            or bool(re.search(rf"\bcd\s+[^\s|;&]*{tgt}", text) and _VERB.search(text)))


def _is_hermes_config(q: Path) -> bool:
    """True for the active Hermes config (and the profile-root one), not any file that happens to share the name."""
    h = home()
    roots = [h] + ([h.parent.parent] if h.parent.name == "profiles" else [])
    return any(os.path.realpath(q) == os.path.realpath(r / "config.yaml") for r in roots)


def _protected(path, text: str) -> bool:
    """True when a file tool targets plugin settings, a forged plugin's files, or the desktop-plugins root."""
    names = forged_names()
    if not isinstance(path, str) or not path:
        return False
    q = Path(os.path.normpath(Path(os.path.expanduser(path)) if os.path.isabs(os.path.expanduser(path)) else Path.cwd() / os.path.expanduser(path)))
    if _is_hermes_config(q):
        return bool(names) and bool(re.search(r"\benabled\b|\bplugins\b", text) or any(n in text for n in names))
    for h in [home()] + ([home().parent.parent] if home().parent.name == "profiles" else []):
        parts = q.parts[len(h.parts):] if q.parts[:len(h.parts)] == h.parts else ()
        if parts[:1] == ("desktop-plugins",) or (parts[:1] == ("plugins",) and parts[1:2] and parts[1] in names):
            return True
    return False


def guard(tool_name: str = "", args=None, **_kw):
    """pre_tool_call: veto agent calls that would enable a forged plugin or edit its files. Regex + marker: it stops a
    confused agent, not a hostile one."""
    if tool_name not in ("terminal", "execute_code", "write_file", "patch", "process_manage") or not isinstance(args, dict):
        return None
    names = forged_names()
    if tool_name in ("write_file", "patch"):
        text = "\n".join(str(v) for v in args.values() if isinstance(v, str))
        paths = [args.get("path")] + re.findall(r"\*\*\* (?:Update|Add|Delete) File: (.+)", text)
        return {"action": "block", "message": PROTECT_MSG} if any(_protected(p, text) for p in paths) else None
    text = str(args.get("command") or args.get("code") or args.get("data") or "")
    hit = [n for n in names if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text)]
    cfg_write = _writes_to(text, _CFG_FILE) or bool(_CFG_SET.search(text) or re.search(r"\byq\s+(?:-\w*i|--in-?place)", text))
    if hit and (_ENABLE.search(text) or cfg_write):
        return {"action": "block", "message": BLOCK_MSG}
    if names and ((cfg_write and re.search(r"\benabled\b|\bplugins\b", text)) or _writes_to(text, "desktop-plugins")
                  or any(_writes_to(text, rf"plugins[/\\]{re.escape(n)}(?![\w-])") for n in names)):
        return {"action": "block", "message": PROTECT_MSG}
    return None


def _clean(v) -> str:
    return re.sub(r"[^a-z0-9,/.-]", "", str(v).lower())


def _desktop_half_ready(plugin: str) -> bool:
    h = home()
    roots = [h] + ([h.parent.parent] if h.parent.name == "profiles" else [])
    return any((r / "desktop-plugins" / plugin / "plugin.js").is_file() for r in roots)


def card_hook(response_text: str = "", session_id: str = "", platform: str = "", **_kw):
    """transform_llm_output: append the install card when forge_install ran in this session."""
    src = response_text
    if isinstance(src, str):  # only this hook may write a card: drop any the model wrote
        response_text = re.sub(r"(?m)^[ \t]*::hephaestus\{.*\}[ \t]*$\n?", "", src)
        if response_text != src:
            response_text = response_text.rstrip()
    changed = response_text != src
    f = data() / "pending" / f"{session_id or 'default'}.json"
    p = _read_json(f)
    if not p:
        return response_text if changed else None
    f.unlink(missing_ok=True)
    if time.time() - p.get("t", 0) > 3600 or not isinstance(response_text, str):
        return response_text if changed else None
    name, perms = _clean(p["name"]), ",".join(_clean(x) for x in p["perms"]) or "none"
    new = ",".join(_clean(x) for x in p.get("new", []))  # permissions this version adds over the installed one
    if platform == "desktop" and _desktop_half_ready(Path(__file__).parent.name):
        card = (f'::hephaestus{{name="{name}" version="{_clean(p["version"])}" state="installed" '
                f'checks="{_clean(p["passed"])}/{_clean(p["total"])}" warn="{_clean(p["warn"])}" perms="{perms}" '
                f'tools="{_clean(p["tools"])}" commands="{_clean(p["commands"])}" hooks="{_clean(p["hooks"])}"'
                + (f' new="{new}"' if new else "") + "}")
    else:
        card = (f"**Forged `{name}` v{_clean(p['version'])}**: {_clean(p['passed'])}/{_clean(p['total'])} checks passed, {_clean(p['warn'])} warning(s), "
                f"permissions: {perms}." + (f" v{_clean(p['version'])} adds {new}." if new else "") + f"\nInstalled but **disabled**. Only you can turn it on, by typing this line:\n/forge enable {name}")
    return f"{response_text}\n\n{card}"
