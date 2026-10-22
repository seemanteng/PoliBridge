import { useState } from 'react'
import { Link } from 'react-router-dom'
import { fmt, useApi } from '../api'
import { Badge, Loading } from '../ui'
import { OrgChart, OwnershipMap } from './RegistryCharts'

const TABS = ['People', 'Organisations', 'Systems', 'Findings'] as const
const ROLE: Record<string, string> = {
  ciso: 'CISO', risk_owner: 'Risk owner', senior_mgmt: 'Senior management', compliance: 'Compliance', legal: 'Legal',
  cco: 'Compliance officer', reviewer: 'Independent reviewer', internal_audit: 'Internal audit', manager: 'Manager',
  bu_head: 'BU head', owner: 'System owner', executive: 'Executive',
}
// What each role is responsible for in PoliBridge, shown for people who own no systems.
const DUTY: Record<string, string> = {
  executive: 'Accountable to the board for the programme', senior_mgmt: 'Receives missed-deadline escalations',
  ciso: 'Escalation point at 48h; owns findings on unregistered assets', risk_owner: 'Approves time-limited risk acceptances',
  cco: 'Oversees obligation approval', compliance: 'Approves candidate obligations', legal: 'Decides regulatory applicability',
  internal_audit: 'Independent assurance over the programme', reviewer: 'Independent evidence review',
  manager: 'Escalation point at 24h for owners', bu_head: 'Reroute target when an owner is invalid',
  owner: 'Submits evidence and remediation work',
}

const has = (q: string, ...fields: unknown[]) => !q || fields.some((f) => String(f ?? '').toLowerCase().includes(q.toLowerCase()))
const uniq = (xs: (string | null | undefined)[]) => [...new Set(xs.filter(Boolean) as string[])].sort()

type Opt = [string, string] | string
function Controls({ q, setQ, selects, shown, total, onClear }: {
  q: string; setQ: (v: string) => void; shown: number; total: number; onClear: () => void
  selects: { label: string; value: string; set: (v: string) => void; options: Opt[] }[]
}) {
  const dirty = q || selects.some((x) => x.value)
  return (
    <div className="card" style={{ marginTop: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
      <input style={{ flex: '1 1 240px', width: 'auto' }} placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      {selects.map((x) => (
        <select key={x.label} style={{ width: 'auto' }} value={x.value} onChange={(e) => x.set(e.target.value)} aria-label={x.label}>
          <option value="">{x.label}: all</option>
          {x.options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return <option key={v} value={v}>{l}</option> })}
        </select>))}
      <span className="faint">{shown} of {total}</span>
      {dirty && <button className="btn sm" onClick={onClear}>Clear</button>}
    </div>
  )
}

function System({ a }: { a: any }) {
  const inv = a.inventory
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="sec"><div><h2>{a.name}</h2>
        <div className="faint">{[a.internet_facing && 'Internet-facing', a.critical && 'Critical', a.customer_facing && 'Customer-facing', a.data_sensitivity].filter(Boolean).join(' · ')}</div></div>
        <Badge kind={a.owner_valid ? 'info' : 'over'}>{a.owner_valid ? 'ownership valid' : 'ownership invalid'}</Badge></div>
      <div className="grid g2" style={{ marginTop: 8 }}>
        <div>
          <div className="faint">Accountability chain (who gets routed and escalated to)</div>
          <p style={{ margin: '4px 0' }}>
            <b style={a.owner_active ? {} : { textDecoration: 'line-through' }}>{a.owner || 'no named owner'}</b> → {a.manager || '—'} → {a.bu_head || '—'}
          </p>
          {a.owner_issue && <p className="err" style={{ margin: 0 }}>{a.owner_issue}{a.legacy_contact ? ` · legacy contact: ${a.legacy_contact}` : ''}</p>}
          <div className="faint" style={{ marginTop: 8 }}>Business unit</div><div>{a.business_unit || '—'}</div>
          <div className="faint" style={{ marginTop: 8 }}>Ownership attested</div><div>{a.ownership_attested_at ? fmt(a.ownership_attested_at) : 'never'}</div>
          {!a.in_scanner_targets && <p className="err">Not in scanner targets (coverage gap)</p>}
        </div>
        <div>
          <div className="faint">Components <Badge kind={inv.state === 'current' ? 'info' : 'over'}>inventory {inv.state}</Badge></div>
          {a.components.length ? a.components.map((c: any) => <div key={c.name} className="mono">{c.name} {c.version} <span className="faint">{c.path}</span></div>)
            : <div className="muted">No component data on record</div>}
          {inv.verified_at && <div className="faint">verified {fmt(inv.verified_at)} · {inv.method} · {inv.scope.join(', ')}</div>}
        </div>
      </div>
      <div className="grid g2" style={{ marginTop: 8 }}>
        <div><div className="faint">Open tasks</div>
          {a.open_tasks.length ? a.open_tasks.map((t: any) => <div key={t.id}><Link to={`/tasks/${t.id}`}>{t.ref}</Link> {t.kind} · {t.impact} · {t.status.replace(/_/g, ' ')} · {t.owner}</div>) : <div className="muted">none</div>}</div>
        <div><div className="faint">Claims</div>
          {a.claims.length ? a.claims.map((c: any) => <div key={c.id}><Link to={`/claims/${c.id}`}>{c.type}</Link> {c.value?.replace(/_/g, ' ')} · <Badge kind={c.status === 'active' ? 'info' : 'over'}>{c.status}</Badge></div>) : <div className="muted">none</div>}</div>
      </div>
    </div>
  )
}

export default function Registry() {
  const { data: r, error } = useApi('/registry')
  const [tab, setTab] = useState<(typeof TABS)[number]>(() => TABS.find((t) => t === new URLSearchParams(location.search).get('tab')) || 'People')
  // search text and filters are kept per tab, so switching tabs does not lose them
  const [f, setF] = useState<Record<string, string>>({})
  const g = (k: string) => f[`${tab}.${k}`] || ''
  const set = (k: string) => (v: string) => setF((x) => ({ ...x, [`${tab}.${k}`]: v }))
  const clear = () => setF((x) => Object.fromEntries(Object.entries(x).filter(([k]) => !k.startsWith(tab + '.'))))
  if (!r) return <Loading error={error} />
  const q = g('q')
  const units = uniq(r.business_units.map((b: any) => b.name))

  const people = r.people.filter((p: any) => has(q, p.name, p.directory_id, p.title, ROLE[p.role], p.manager, p.business_unit, p.owns.join(' '))
    && (!g('bu') || p.business_unit === g('bu')) && (!g('role') || p.role === g('role'))
    && (!g('status') || (g('status') === 'active' ? p.active && p.deliverable : g('status') === 'left' ? !p.active : p.active && !p.deliverable)))

  const assets = r.assets.filter((a: any) => has(q, a.name, a.owner, a.manager, a.bu_head, a.business_unit, a.legacy_contact,
      a.components.map((c: any) => `${c.name} ${c.version}`).join(' '), a.open_tasks.map((t: any) => t.ref).join(' '))
    && (!g('bu') || a.business_unit === g('bu'))
    && (!g('owner') || (g('owner') === 'valid') === a.owner_valid)
    && (!g('inv') || a.inventory.state === g('inv'))
    && (!g('exposure') || (g('exposure') === 'internet' ? a.internet_facing : a.critical))
    && (!g('work') || (g('work') === 'tasks' ? a.open_tasks.length > 0 : a.open_tasks.length === 0)))

  const bus = r.business_units.filter((b: any) => has(q, b.name, b.head) && (!g('bu') || b.name === g('bu')))
  // keep matching people and everyone above them so the reporting line stays readable
  const byId = new Map<number, any>(r.people.map((p: any) => [p.id, p]))
  const show = new Set<number>()
  r.people.filter((p: any) => has(q, p.name, p.title, ROLE[p.role], p.owns.join(' ')) && (!g('bu') || p.business_unit === g('bu')))
    .forEach((p: any) => { for (let x = p; x && !show.has(x.id); x = byId.get(x.manager_id)) show.add(x.id) })

  const findings = r.findings.filter((x: any) => has(q, x.subject, x.detail, x.kind, x.assigned_to)
    && (!g('sev') || x.severity === g('sev')) && (!g('kind') || x.kind === g('kind')))
  const discovered = r.discovered.filter((d: any) => has(q, d.name, d.source))

  const common = { q, setQ: set('q'), onClear: clear }
  const active = r.people.filter((p: any) => p.active).length
  return (
    <>
      <div className="head"><div><h1>Registry</h1>
        <p className="muted" style={{ maxWidth: 760 }}>Who is accountable for what: staff, reporting lines, business units and systems, linked to their components, tasks and claims. Owners are directory identities, never email addresses.</p></div>
        <div className="card" style={{ minWidth: 200 }}><div className="faint">Estate</div>
          <div className="stat">{r.assets.length} systems · {active} staff</div></div></div>
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>{TABS.map((t) => (
        <button key={t} className={`btn ${tab === t ? 'primary' : ''}`} onClick={() => setTab(t)}>{t}{t === 'Findings' ? ` (${r.findings.length})` : ''}</button>))}</div>

      {tab === 'Systems' && <>
        <Controls {...common} shown={assets.length} total={r.assets.length} selects={[
          { label: 'Business unit', value: g('bu'), set: set('bu'), options: units },
          { label: 'Ownership', value: g('owner'), set: set('owner'), options: [['valid', 'Valid'], ['invalid', 'Invalid']] },
          { label: 'Inventory', value: g('inv'), set: set('inv'), options: [['current', 'Current'], ['stale', 'Stale'], ['none', 'None']] },
          { label: 'Exposure', value: g('exposure'), set: set('exposure'), options: [['internet', 'Internet-facing'], ['critical', 'Critical']] },
          { label: 'Open tasks', value: g('work'), set: set('work'), options: [['tasks', 'Has open tasks'], ['none', 'None']] }]} />
        <OwnershipMap assets={assets} />
        {assets.map((a: any) => <System key={a.id} a={a} />)}
        {!assets.length && <p className="muted" style={{ marginTop: 14 }}>No systems match.</p>}</>}

      {tab === 'Organisations' && <>
        <Controls {...common} shown={bus.length} total={r.business_units.length} selects={[
          { label: 'Business unit', value: g('bu'), set: set('bu'), options: units }]} />
        <div className="card" style={{ marginTop: 14 }}><h2>Business units</h2>
          <table><thead><tr><th>Unit</th><th>Head</th><th>People</th><th>Systems</th></tr></thead><tbody>
            {bus.map((b: any) => <tr key={b.id}><td><b>{b.name}</b></td><td>{b.head}</td>
              <td>{r.people.filter((p: any) => p.business_unit === b.name).length}</td>
              <td>{r.assets.filter((a: any) => a.business_unit === b.name).length}</td></tr>)}
            {!bus.length && <tr><td colSpan={4} className="muted">No business units match.</td></tr>}</tbody></table></div>
        <div className="card" style={{ marginTop: 14 }}><h2>Reporting lines</h2>
          <p className="muted">Each person reports to the one on their left; escalation follows these lines. Hover someone to trace their line up and their reports down.{q || g('bu') ? ' Showing matches and the people they report to.' : ''}</p>
          {show.size ? <OrgChart people={r.people} show={show} /> : <p className="muted">No people match.</p>}</div></>}

      {tab === 'People' && <>
        <Controls {...common} shown={people.length} total={r.people.length} selects={[
          { label: 'Business unit', value: g('bu'), set: set('bu'), options: units },
          { label: 'Role', value: g('role'), set: set('role'), options: uniq(r.people.map((p: any) => p.role)).map((x) => [x, ROLE[x] || x] as [string, string]) },
          { label: 'Status', value: g('status'), set: set('status'), options: [['active', 'Active'], ['left', 'Left'], ['undeliverable', 'Mail undeliverable']] }]} />
        <div className="card" style={{ marginTop: 14 }}>
          <table><thead><tr><th>Name</th><th>Role</th><th>Reports to</th><th>Business unit</th><th>Owns / responsible for</th><th>Status</th></tr></thead><tbody>
            {people.map((p: any) => <tr key={p.id}><td><b>{p.name}</b><div className="faint mono">{p.directory_id}</div></td>
              <td>{p.title || ROLE[p.role]}</td><td>{p.manager || 'Board of Directors'}</td><td>{p.business_unit}</td>
              <td>{p.owns.length ? <>owns {p.owns.join(', ')}</> : DUTY[p.role] || ROLE[p.role]}</td>
              <td>{p.active ? (p.deliverable ? <Badge kind="info">active</Badge> : <Badge kind="over">mail undeliverable</Badge>) : <Badge kind="over">left</Badge>}</td></tr>)}
            {!people.length && <tr><td colSpan={6} className="muted">No people match.</td></tr>}</tbody></table></div></>}

      {tab === 'Findings' && <>
        <Controls {...common} shown={findings.length} total={r.findings.length} selects={[
          { label: 'Severity', value: g('sev'), set: set('sev'), options: uniq(r.findings.map((x: any) => x.severity)) },
          { label: 'Type', value: g('kind'), set: set('kind'), options: uniq(r.findings.map((x: any) => x.kind)).map((x) => [x, x.replace(/_/g, ' ')] as [string, string]) }]} />
        <div className="card" style={{ marginTop: 14 }}>
          <table><thead><tr><th>Finding</th><th>Severity</th><th>Assigned to</th></tr></thead><tbody>
            {findings.map((x: any) => <tr key={x.id}><td><b>{x.subject}</b><div className="muted">{x.detail}</div><div className="faint">{x.kind.replace(/_/g, ' ')}</div></td>
              <td>{x.severity}</td><td>{x.assigned_to || '—'}</td></tr>)}
            {!findings.length && <tr><td colSpan={3} className="muted">No findings match.</td></tr>}</tbody></table>
          <h2 style={{ marginTop: 16 }}>Discovery reconciliation</h2>
          <table><thead><tr><th>Seen by discovery</th><th>Source</th><th>In register</th></tr></thead><tbody>
            {discovered.map((d: any) => <tr key={d.name}><td>{d.name}</td><td>{d.source}</td><td>{d.registered ? '✓' : <b className="err">✕ unregistered</b>}</td></tr>)}</tbody></table></div></>}
    </>
  )
}
