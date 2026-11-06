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
const codes = s => (s ?? '').split(',').filter(p => Object.hasOwn(PERMS, p))
const label = c => `${PERMS[c][0]} ${PERMS[c][1]}`
const errText = e => String(e?.message ?? e).slice(0, 160)
const WAIT_MS = 90_000 // the first enable took 42 s cold on the test box (ARCH S2)
const withTimeout = p => new Promise((ok, no) => {
  const t = setTimeout(() => no(new Error('Still working after 90 s. Check Capabilities → Plugins before trying again.')), WAIT_MS)
  p.then(ok, no).finally(() => clearTimeout(t))
})

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
  const perms = codes(a.perms), added = codes(a.new)
  const refused = a.state === 'refused'
  const [on, setOn] = useState(a.state === 'enabled')
  const [phase, setPhase] = useState('') // non-empty while something runs: disables buttons, feeds the status line
  const [note, setNote] = useState('')
  const [cmd, setCmd] = useState('')
  const state = on ? STATES.enabled : STATES[a.state] ?? 'Unknown'
  const desk = on && perms.includes('desktop') && !refused

  const flip = async enable => { // plugins.manage toggle: in-process, never grants tool override (F3)
    setPhase(enable ? 'Enabling… (the first time can take up to a minute)' : 'Disabling…')
    const r = await withTimeout(host.request('plugins.manage', { action: 'toggle', name, enable }))
    setOn(enable); return r
  }
  const toggle = async () => {
    const enable = !on; setNote('')
    try {
      const r = await flip(enable)
      const s = String(r?.plugin?.status ?? '').slice(0, 40)
      setNote(`${enable ? 'Enabled' : 'Disabled'}${s ? ` (status: ${s})` : ''}${r?.restart_required ? ', restart to apply' : ''}.`)
    } catch (e) { setNote(`Could not ${enable ? 'enable' : 'disable'}: ${errText(e)}`) }
    setPhase('')
  }
  // Never auto-send: seat the slash command in the composer, or show it with a copy button.
  const stage = async verb => {
    const text = `/forge ${verb} ${name}`
    const ok = await Promise.resolve().then(() => host.composer.insertText(null, text, { mode: 'prefix' })).catch(() => false)
    setCmd(ok ? '' : text); setNote(ok ? `Placed “${text}” in the composer. Press Enter to run it.` : '')
  }
  // Roll back = off → /forge rollback (file-only in forge.py) → on, so the restored bytes load. A disabled plugin skips the toggles.
  const rollback = async () => {
    const sid = host.state?.focusedSessionId?.get?.()
    if (!sid) return stage('rollback') // no chat to run the command in
    const was = on; setNote(''); setCmd('')
    let off = false
    try {
      if (was) { await flip(false); off = true }
      setPhase('Restoring the previous version…')
      const r = await withTimeout(host.request('command.dispatch', { name: 'forge', arg: `rollback ${name}`, session_id: sid }))
      const out = String(r?.output ?? '')
      if (r?.type !== 'plugin' || /^Error:/.test(out)) throw new Error(out || 'no answer from /forge')
      if (was) { await flip(true); off = false }
      setNote(out.slice(0, 200))
    } catch (e) {
      let msg = `Roll back stopped: ${errText(e)}`
      if (off) msg += await flip(true).then(() => ' The plugin is on again.', () => ' The plugin is off now; use Enable.')
      setNote(msg)
    }
    setPhase('')
  }
  const btn = (text, onClick, variant = 'outline') => jsx(Button, { type: 'button', size: 'sm', variant, disabled: !!phase, onClick, 'aria-label': `${text} ${name}`, children: text })
  const count = (n, w) => (int(n) === null ? null : `${n} ${w}`)

  return jsxs('section', { className: 'heph', 'aria-label': `Forged plugin ${name ?? 'unknown'}`, 'aria-busy': !!phase, children: [
    jsx('style', { children: CSS }),
    jsxs('div', { className: 'heph-row', children: [
      jsx('span', { className: 'heph-name', children: name ?? 'Unreadable plugin card' }),
      a.version && int(a.version) !== null && jsx('span', { className: 'heph-dim', children: `v${a.version}` }),
      jsx('span', { className: 'heph-pill', 'data-ok': refused ? false : undefined, children: state }),
      pass !== null && jsx('span', { className: 'heph-pill', 'data-ok': total > 0 && pass === total, title: 'Validator and doctor checks passed', children: `${total > 0 && pass === total ? '✓' : '✗'} ${pass}/${total} checks` }),
      warn !== null && warn > 0 && jsx('span', { className: 'heph-pill', 'data-ok': false, children: `${warn} warning${warn === 1 ? '' : 's'}` })
    ] }),
    added.length > 0 && int(a.version) !== null && jsx('div', { className: 'heph-row', children: jsx('span', { className: 'heph-pill', 'data-ok': false, children: `v${a.version} adds: ${added.map(label).join(', ')}` }) }),
    jsx('div', { className: 'heph-row', role: 'list', 'aria-label': 'Permissions', children: perms.length ? perms.map(p => jsx('span', { role: 'listitem', children: jsx(Chip, { code: p }) }, p)) : jsx('span', { className: 'heph-dim', children: 'No special permissions detected' }) }),
    jsx('div', { className: 'heph-dim', children: [count(a.tools, 'tools'), count(a.commands, 'commands'), count(a.hooks, 'hooks')].filter(Boolean).join(' · ') }),
    name && !refused && jsxs('div', { className: 'heph-row', children: [
      btn(on ? 'Disable' : 'Enable', toggle, on ? 'outline' : 'default'),
      btn('Roll back', rollback),
      // The Plugins hub's Agent switch also turns the Desktop half on; the public toggle door does not (ARCH M2).
      desk && btn('Turn on in Plugins', () => host.navigate('/capabilities?tab=plugins'), 'default')
    ] }),
    desk && jsx('div', { className: 'heph-dim', children: 'Desktop parts start off. Switch it on in Plugins (press Rescan if it is not listed).' }),
    cmd && jsxs('div', { className: 'heph-row', children: [jsx('code', { children: cmd }), btn('Copy', async () => setNote((await copy(cmd)) ? 'Copied. Paste it into the chat and press Enter.' : 'Copy failed. Select the command above.'), 'ghost')] }),
    (phase || note) && jsx('div', { role: 'status', 'aria-live': 'polite', children: phase || note })
  ] })
}

export default {
  id: 'hephaestus', name: 'Hephaestus',
  register(ctx) {
    ctx.register({ id: 'card', area: 'transcript.directives', data: { name: 'hephaestus', render: ({ attrs }) => jsx(Card, { attrs, copy: t => Promise.resolve().then(() => ctx.os.writeClipboard(t)).catch(() => false) }) } })
  }
}
