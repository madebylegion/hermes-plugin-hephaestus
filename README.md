# Hephaestus

Ask Hermes for **any** plugin your own agent can write: tools, slash commands, Desktop UI. The **official** Hermes validator and doctor are the hard gate. **You hold the switch:** the plugin installs off, the agent is blocked from enabling it, a consent card shows what it can touch and what a new version adds, and one click restores the old version byte for byte.

## How it works

You say "forge me a plugin that does X." Your agent writes the code from a starting template (`tool`, `command` or `desktop`). Hephaestus runs `hermes plugins validate --json` and `hermes plugins doctor --ci`, adds its own checks, builds a permission label, installs a green draft **disabled** after saving the previous version, and shows a card.

## Install

```bash
hermes plugins install https://github.com/madebylegion/hermes-plugin-hephaestus
hermes plugins enable hephaestus
hermes plugins doctor hephaestus
```

Use the full repository URL. A bare local path is read as a GitHub name and fails; to test a local clone, use `file:///abs/path/to/clone`. Tested on v0.22.5 and v0.23.0; needs v0.22.5 or newer.

**First run, once:** open Capabilities → Plugins and switch on Hephaestus's own Desktop part (press Rescan if it is not listed). Until then you get a plain text card.

## Quick start

1. `Forge me a /standup command that summarises my git commits since yesterday.`
2. `Forge a Desktop widget that shows the current time in three time zones.`

Then press **Enable** on the card. A new slash command works in the same chat; new **tools** arrive in the next session. A new Hermes home needs 40–60 s once, over install and first enable. If you type `/forge enable <name>` in a chat instead, it goes through the CLI and applies from the next session.

## The card

- **Enable / Disable** switches the forged plugin.
- **Roll back** switches it off, runs `/forge rollback <name>`, and switches it on again, so the restored files load. If the card cannot run the command, it puts it in the composer for you to send.
- A red **"vN adds: …"** chip names each permission a re-forge adds.
- **Turn on in Plugins** appears for a forged Desktop part, which starts off.
- There is no Remove button. Use `/forge remove <name>`.

## What only a plugin can do

A skill cannot enforce anything. Hephaestus is code, so it can gate the install by hash (`forge_install` accepts only a draft whose current bytes passed), refuse on red checks, block agent calls with a `pre_tool_call` guard, show a consent card, and roll back byte for byte.

## Safety model

- **Installed disabled.** No forged tool, hook or command is live until you enable it. Only a human enables, and enable always passes `--no-allow-tool-override`.
- **Hard gate.** `forge_check` fails on any failed validator or doctor check, on symlinks, and on private Hermes imports (the official validator only warns on those). A `dangerous` security scan also stops the install.
- **Rollback.** One previous version is kept. Rollback restores it and checks the hash.
- **Marker file.** Hephaestus only touches plugins with `.forged-by-hephaestus`.

## Limits

The guard **stops a confused agent, not a hostile one.** It is a pattern check.

- It covers the `terminal`, `execute_code`, `write_file`, `patch` and `process_manage` tools. It blocks enabling a forged plugin, editing `plugins.enabled`, and writing into a forged plugin or `desktop-plugins/`.
- It does **not** cover `cronjob`, browser code, subagents (`delegate_task`), `computer_use`, or MCP tools.
- It misses a `cd` to the folder followed by an edit in a second step.
- It misses a plugin the agent writes by hand outside Hephaestus.
- A dynamic import such as `importlib.import_module('hermes_' + …)` gets past the private-import check.
- The permission label comes from a heuristic code scan. It is for consent, not a sandbox, and can miss code that hides what it does.
- Checking a draft runs a little of its code. The official validator imports the draft once in a throwaway child process (scratch Hermes home, same user and environment) to see what it registers. Top-level and `register()` code runs then; the agent's own terminal can already do as much.
- Tested on Linux only.

## Surfaces

Tools `forge_new`, `forge_check`, `forge_install`; command `/forge list | enable | disable | rollback | remove <name>`; hooks `pre_tool_call` (guard) and `transform_llm_output` (adds the card); Desktop `desktop/plugin.js`.

## Tests

```bash
python -m pytest -q tests
```

Needs `pytest`; takes 4–5 minutes. The tests call the real `hermes` CLI in a temporary `HERMES_HOME`.

## Disclosure

- Starts the `hermes` CLI (validate, doctor, enable, disable, remove). The child process does not inherit `HERMES_YOLO_MODE`, `HERMES_NONINTERACTIVE` or `HERMES_INTERACTIVE`.
- Writes forged plugins to `$HERMES_HOME/plugins/` and drafts, snapshots and check records to its own data directory.
- No network. No telemetry. No LLM calls. Python standard library only.

## License

MIT. See `LICENSE`.
