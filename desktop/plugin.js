// Hephaestus Desktop half: renders `::hephaestus{...}` (appended by the Python hook) as a permission card.
// Attributes are untrusted model output: every field is validated, bad input degrades to a safe label.
import { Button, host } from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PERMS = {
  network: ['🌐', 'Uses the internet'], programs: ['🖥', 'Runs programs'], files: ['📁', 'Writes files'],
  env: ['🔑', 'Reads environment/secrets'], behaviour: ['🧠', 'Changes how the agent behaves'], desktop: ['🧩', 'Adds Desktop UI']
}
const STATES = { installed: 'Installed, off', enabled: 'Enabled', 'rolled-back': 'Rolled back', refused: 'Refused' }
const int = v => (/^\d{1,6}$/.test(v ?? '') ? Number(v) : null)

const CSS = `
.heph{--heph-ok:#1f9d57;--heph-bad:#d6453d;max-width:34rem;margin:.5rem 0;padding:.75rem .875rem;display:grid;gap:.5rem;font-size:.8125rem;color:var(--ui-text-primary);border-radius:.5rem;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--ui-stroke-secondary) 55%,transparent)}
.heph-row{display:flex;flex-wrap:wrap;align-items:center;gap:.375rem .5rem}
.heph-name{font-weight:600;font-size:.9375rem}
.heph-dim{color:var(--ui-text-tertiary)}
.heph-pill{padding:.0625rem .5rem;border-radius:999px;font-size:.75rem;background:var(--ui-bg-quaternary)}
.heph-pill[data-ok=true]{color:color-mix(in srgb,var(--heph-ok) 65%,var(--ui-text-primary));background:color-mix(in srgb,var(--heph-ok) 16%,transparent)}
.heph-pill[data-ok=false]{color:color-mix(in srgb,var(--heph-bad) 65%,var(--ui-text-primary));background:color-mix(in srgb,var(--heph-bad) 16%,transparent)}
.heph code{font-family:var(--font-mono,monospace);padding:.0625rem .375rem;border-radius:.25rem;background:var(--ui-bg-quaternary);user-select:all}
.heph [role=status]{color:var(--ui-text-secondary)}
.heph :is(button):focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px}`

function Chip({ code }) {
  const [icon, text] = PERMS[code]
  return jsxs('span', { className: 'heph-pill', title: text, children: [jsx('span', { 'aria-hidden': true, children: icon }), ` ${text}`] })
}

function Card({ attrs, copy }) {
  const a = attrs || {}
  const name = /^[a-z][a-z0-9-]{1,39}$/.test(a.name ?? '') ? a.name : null
  const m = /^(\d{1,4})\/(\d{1,4})$/.exec(a.checks ?? '')
  const [pass, total] = m ? [Number(m[1]), Number(m[2])] : [null, null]
  const warn = int(a.warn)
  const perms = (a.perms ?? '').split(',').filter(p => Object.hasOwn(PERMS, p))
  const refused = a.state === 'refused'
  const [on, setOn] = useState(a.state === 'enabled')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [cmd, setCmd] = useState('')
  const state = on ? STATES.enabled : STATES[a.state] ?? 'Unknown'

  const toggle = async () => {
    setBusy(true); setNote('')
    try {
      const r = await host.request('plugins.manage', { action: 'toggle', name, enable: !on })
      setOn(!on)
      const s = String(r?.plugin?.status ?? '').slice(0, 40)
      setNote(`${!on ? 'Enabled' : 'Disabled'}${s ? ` (status: ${s})` : ''}${r?.restart_required ? ', restart to apply' : ''}.`)
    } catch (e) { setNote(`Could not ${on ? 'disable' : 'enable'}: ${String(e?.message ?? e).slice(0, 160)}`) }
    setBusy(false)
  }
  // Never auto-send: put the slash command in the composer, or show it with a copy button.
  const stage = async verb => {
    const text = `/forge ${verb} ${name}`
    const ok = await Promise.resolve().then(() => host.composer.insertText(null, text, { mode: 'prefix' })).catch(() => false)
    setCmd(ok ? '' : text); setNote(ok ? `Placed “${text}” in the composer. Press Enter to run it.` : '')
  }
  const btn = (label, onClick, variant = 'outline') => jsx(Button, { type: 'button', size: 'sm', variant, disabled: busy, onClick, 'aria-label': `${label} ${name}`, children: label })
  const count = (n, w) => (int(n) === null ? null : `${n} ${w}`)

  return jsxs('section', { className: 'heph', 'aria-label': `Forged plugin ${name ?? 'unknown'}`, children: [
    jsx('style', { children: CSS }),
    jsxs('div', { className: 'heph-row', children: [
      jsx('span', { className: 'heph-name', children: name ?? 'Unreadable plugin card' }),
      a.version && int(a.version) !== null && jsx('span', { className: 'heph-dim', children: `v${a.version}` }),
      jsx('span', { className: 'heph-pill', 'data-ok': refused ? false : undefined, children: state }),
      pass !== null && jsx('span', { className: 'heph-pill', 'data-ok': total > 0 && pass === total, title: 'Validator and doctor checks passed', children: `${total > 0 && pass === total ? '✓' : '✗'} ${pass}/${total} checks` }),
      warn !== null && warn > 0 && jsx('span', { className: 'heph-pill', 'data-ok': false, children: `${warn} warning${warn === 1 ? '' : 's'}` })
    ] }),
    jsx('div', { className: 'heph-row', role: 'list', 'aria-label': 'Permissions', children: perms.length ? perms.map(p => jsx('span', { role: 'listitem', children: jsx(Chip, { code: p }) }, p)) : jsx('span', { className: 'heph-dim', children: 'No special permissions detected' }) }),
    jsx('div', { className: 'heph-dim', children: [count(a.tools, 'tools'), count(a.commands, 'commands'), count(a.hooks, 'hooks')].filter(Boolean).join(' · ') }),
    name && !refused && jsxs('div', { className: 'heph-row', children: [
      btn(on ? 'Disable' : 'Enable', toggle, on ? 'outline' : 'default'),
      btn('Roll back', () => stage('rollback')), btn('Remove', () => stage('remove')),
      // The Plugins hub's Agent switch also turns the Desktop half on; the public toggle door does not.
      on && perms.includes('desktop') && btn('Open Plugins (switch on its Desktop half)', () => host.navigate('/capabilities?tab=plugins'), 'ghost')
    ] }),
    cmd && jsxs('div', { className: 'heph-row', children: [jsx('code', { children: cmd }), btn('Copy', async () => setNote((await copy(cmd)) ? 'Copied. Paste it into the chat and press Enter.' : 'Copy failed. Select the command above.'), 'ghost')] }),
    note && jsx('div', { role: 'status', children: note })
  ] })
}

export default {
  id: 'hephaestus', name: 'Hephaestus',
  register(ctx) {
    ctx.register({ id: 'card', area: 'transcript.directives', data: { name: 'hephaestus', render: ({ attrs }) => jsx(Card, { attrs, copy: t => Promise.resolve().then(() => ctx.os.writeClipboard(t)).catch(() => false) }) } })
  }
}
