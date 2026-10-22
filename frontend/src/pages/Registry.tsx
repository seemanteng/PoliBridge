import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, Loading, Modal, useAction } from '../ui'
import { OrgChart, OwnershipMap } from './RegistryCharts'
import { Controls } from '../filters'
import { has, uniq } from '../filterUtils'

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

const ROLE_OPTIONS = Object.keys(ROLE)

function PersonModal({ person, people, units, onClose }: { person?: any; people: any[]; units: any[]; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ name: person?.name || '', directory_id: '', title: person?.title || '', role: person?.role || 'owner',
    manager_id: person?.manager_id ? String(person.manager_id) : '', business_unit_id: person ? String(units.find((u) => u.name === person.business_unit)?.id || '') : '',
    deliverable: person ? person.deliverable : true })
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const go = async () => {
    const body: any = { name: f.name, title: f.title || null, role: f.role, business_unit_id: f.business_unit_id ? Number(f.business_unit_id) : null,
      manager_id: f.manager_id ? Number(f.manager_id) : null, deliverable: f.deliverable }
    const ok = await run(async () => {
      if (person) await api.patch(`/registry/people/${person.id}`, { ...body, clear_manager: !f.manager_id })
      else await api.post('/registry/people', { ...body, directory_id: f.directory_id || null })
      refreshAll()
    })
    if (ok) onClose()
  }
  return (
    <Modal title={person ? `Edit ${person.name}` : 'Add person'} sub={person ? 'Changing department or manager counts as a mover event: systems they own need ownership re-attested.' : 'A new joiner. Owners are identified by directory id, never by email address.'} onClose={onClose}>
      <label>Name</label><input value={f.name} onChange={(e) => set('name', e.target.value)} />
      {!person && <><label>Directory id (optional, generated from the name)</label><input value={f.directory_id} onChange={(e) => set('directory_id', e.target.value)} placeholder="e.g. jdoe" /></>}
      <label>Job title</label><input value={f.title} onChange={(e) => set('title', e.target.value)} />
      <label>Role in PoliBridge</label>
      <select value={f.role} onChange={(e) => set('role', e.target.value)}>{ROLE_OPTIONS.map((r) => <option key={r} value={r}>{ROLE[r]}</option>)}</select>
      <label>Reports to</label>
      <select value={f.manager_id} onChange={(e) => set('manager_id', e.target.value)}><option value="">Board of Directors (nobody)</option>
        {people.filter((p) => p.active && p.id !== person?.id).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.title || ROLE[p.role]}</option>)}</select>
      <label>Department</label>
      <select value={f.business_unit_id} onChange={(e) => set('business_unit_id', e.target.value)}><option value="">Choose…</option>
        {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
      {person && <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10 }}><input type="checkbox" style={{ width: 'auto' }} checked={f.deliverable} onChange={(e) => set('deliverable', e.target.checked)} />Mail is deliverable</label>}
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !f.name.trim() || !f.business_unit_id} onClick={go}>{person ? 'Save' : 'Add person'}</button></div>
    </Modal>
  )
}

function RemoveModal({ person, onClose }: { person: any; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const go = async () => { if (await run(async () => { await api.post(`/people/${person.id}/jml`, { event: 'leaver' }); refreshAll() })) onClose() }
  return (
    <Modal title={`Remove ${person.name}?`} sub="They are recorded as having left the organisation. The record stays for the audit trail." onClose={onClose}>
      {person.owns.length > 0
        ? <div className="note fail"><b>They still own {person.owns.length} system{person.owns.length > 1 ? 's' : ''}: {person.owns.join(', ')}</b>
            <p>Ownership of each becomes invalid, open remediation is rerouted to the department head, and a "reassign owner" finding is raised. Reassign the systems on the Systems tab.</p></div>
        : <p className="muted">They own no systems, so nothing else is affected.</p>}
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" style={{ background: 'var(--red)', borderColor: 'var(--red)' }} disabled={busy} onClick={go}>Remove</button></div>
    </Modal>
  )
}

function DeptModal({ dept, people, onClose }: { dept?: any; people: any[]; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [name, setName] = useState(dept?.name || '')
  const [head, setHead] = useState(dept?.head_id ? String(dept.head_id) : '')
  const go = async () => {
    const body = { name, head_id: head ? Number(head) : null }
    const ok = await run(async () => {
      if (dept) await api.patch(`/registry/business-units/${dept.id}`, body)
      else await api.post('/registry/business-units', body)
      refreshAll()
    })
    if (ok) onClose()
  }
  return (
    <Modal title={dept ? `Edit ${dept.name}` : 'Add department'} sub="The head is the accountable person for the department, and the first stop when one of its owners is unavailable." onClose={onClose}>
      <label>Name</label><input value={name} onChange={(e) => setName(e.target.value)} />
      <label>Department head</label>
      <select value={head} onChange={(e) => setHead(e.target.value)}><option value="">Choose a person…</option>
        {people.filter((p) => p.active).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.title || ROLE[p.role]}</option>)}</select>
      {dept && <p className="faint">Renaming updates the department on clauses already decided. Sign-offs still waiting move to the new head.</p>}
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !name.trim() || !head} onClick={go}>{dept ? 'Save' : 'Add department'}</button></div>
    </Modal>
  )
}

function SystemModal({ system, people, units, onClose }: { system?: any; people: any[]; units: any[]; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ name: system?.name || '', unit: system?.business_unit_id ? String(system.business_unit_id) : '', owner: '',
    internet: !!system?.internet_facing, critical: !!system?.critical, customer: !!system?.customer_facing,
    sensitivity: system?.data_sensitivity || 'internal', roots: (system?.app_roots || []).join(', '), scanner: system ? system.in_scanner_targets : true })
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const roots = String(f.roots).split(',').map((s: string) => s.trim()).filter(Boolean)
  const go = async () => {
    const ok = await run(async () => {
      const body = { name: f.name, business_unit_id: Number(f.unit), internet_facing: f.internet, critical: f.critical, customer_facing: f.customer,
        data_sensitivity: f.sensitivity, app_roots: roots, in_scanner_targets: f.scanner }
      if (system) {
        await api.patch(`/registry/assets/${system.id}/details`, body)
        if (f.owner) await api.post(`/registry/assets/${system.id}/owner`, { owner_id: Number(f.owner) })
      } else await api.post('/registry/assets', { ...body, owner_id: f.owner ? Number(f.owner) : null })
      refreshAll()
    })
    if (ok) onClose()
  }
  const check = (k: string, label: string) => (
    <label style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '6px 0' }}>
      <input type="checkbox" style={{ width: 'auto' }} checked={(f as any)[k]} onChange={(e) => set(k, e.target.checked)} />{label}</label>)
  return (
    <Modal title={system ? `Edit ${system.name}` : 'Add system'} sub={system ? 'Changing how exposed a system is re-matches open advisories, because impact depends on it.' : 'A new system has no component data yet, so open advisories treat it as Cannot Determine until its software is recorded.'} onClose={onClose}>
      <label>Name</label><input value={f.name} onChange={(e) => set('name', e.target.value)} />
      <label>Department</label>
      <select value={f.unit} onChange={(e) => set('unit', e.target.value)}><option value="">Choose…</option>
        {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
      <label>{system ? 'Owner (leave to keep the current owner)' : 'Owner'}</label>
      <select value={f.owner} onChange={(e) => set('owner', e.target.value)}><option value="">{system ? `Keep ${system.owner || 'no owner'}` : 'Choose a person…'}</option>
        {people.filter((p) => p.active && p.id !== system?.owner_id).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.title || ROLE[p.role]}</option>)}</select>
      {system && f.owner && <p className="faint">Changing the owner voids the current attestation. The new owner must attest, and open remediation moves to them.</p>}
      <label>Data sensitivity</label>
      <select value={f.sensitivity} onChange={(e) => set('sensitivity', e.target.value)}>
        <option value="internal">Internal</option><option value="confidential">Confidential</option><option value="restricted">Restricted</option></select>
      <div style={{ marginTop: 6 }}>{check('internet', 'Internet-facing')}{check('critical', 'Critical')}{check('customer', 'Customer-facing')}{check('scanner', 'Included in vulnerability scanner targets')}</div>
      <label>Application folders (comma separated)</label>
      <input value={f.roots} onChange={(e) => set('roots', e.target.value)} placeholder="/opt/payments, /srv/payments" />
      <p className="faint">Evidence must cover these folders to prove a fix or that software is absent.</p>
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !f.name.trim() || !f.unit || (!system && !f.owner)} onClick={go}>{system ? 'Save' : 'Add system'}</button></div>
    </Modal>
  )
}

function OwnerControls({ a, people }: { a: any; people: any[] }) {
  const { run, error, busy } = useAction()
  const [pick, setPick] = useState('')
  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
      <div className="faint">Ownership</div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
        <select style={{ width: 'auto', minWidth: 200 }} value={pick} onChange={(e) => setPick(e.target.value)}><option value="">Reassign owner to…</option>
          {people.filter((p) => p.active && p.name !== a.owner).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <button className="btn sm" disabled={busy || !pick} onClick={() => run(async () => { await api.post(`/registry/assets/${a.id}/owner`, { owner_id: Number(pick) }); setPick(''); refreshAll() })}>Reassign</button>
        {!a.owner_valid && a.owner_active && <button className="btn sm primary" disabled={busy} onClick={() => run(async () => { await api.post(`/assets/${a.id}/attest`); refreshAll() })}>Attest ownership</button>}
      </div>
      {!a.owner_valid && <p className="faint" style={{ marginTop: 4 }}>The new owner must attest. Switch to them in the sidebar, then click Attest ownership.</p>}
      {error && <p className="err">{error}</p>}
    </div>
  )
}

function System({ a, people, onEdit }: { a: any; people: any[]; onEdit: () => void }) {
  const inv = a.inventory
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="sec"><div><h2>{a.name}</h2>
        <div className="faint">{[a.internet_facing && 'Internet-facing', a.critical && 'Critical', a.customer_facing && 'Customer-facing', a.data_sensitivity].filter(Boolean).join(' · ')}</div></div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><Badge kind={a.owner_valid ? 'info' : 'over'}>{a.owner_valid ? 'ownership valid' : 'ownership invalid'}</Badge>
          <button className="btn sm" onClick={onEdit}>Edit</button></div></div>
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
          <OwnerControls a={a} people={people} />
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
          {a.open_tasks.length ? a.open_tasks.map((t: any) => <div key={t.id}><Link to={`/remediations/${t.id}`}>{t.ref}</Link> {t.kind} · {t.impact} · {t.status.replace(/_/g, ' ')} · {t.owner}</div>) : <div className="muted">none</div>}</div>
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
  const [editing, setEditing] = useState<any>(null)  // person being edited, or {} when adding
  const [removing, setRemoving] = useState<any>(null)
  const [editDept, setEditDept] = useState<any>(null)
  const [editSys, setEditSys] = useState<any>(null)
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
        <p className="muted" style={{ maxWidth: 760 }}>Who is accountable for what: staff, reporting lines, departments and systems, linked to their components, tasks and claims. Owners are directory identities, never email addresses.</p></div>
        <div className="card" style={{ minWidth: 200 }}><div className="faint">Estate</div>
          <div className="stat">{r.assets.length} systems · {active} staff</div></div></div>
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>{TABS.map((t) => (
        <button key={t} className={`btn ${tab === t ? 'primary' : ''}`} onClick={() => setTab(t)}>{t}{t === 'Findings' ? ` (${r.findings.length})` : ''}</button>))}</div>

      {tab === 'Systems' && <>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}><button className="btn primary" onClick={() => setEditSys({})}>Add system</button></div>
        <Controls {...common} shown={assets.length} total={r.assets.length} selects={[
          { label: 'Department', value: g('bu'), set: set('bu'), options: units },
          { label: 'Ownership', value: g('owner'), set: set('owner'), options: [['valid', 'Valid'], ['invalid', 'Invalid']] },
          { label: 'Inventory', value: g('inv'), set: set('inv'), options: [['current', 'Current'], ['stale', 'Stale'], ['none', 'None']] },
          { label: 'Exposure', value: g('exposure'), set: set('exposure'), options: [['internet', 'Internet-facing'], ['critical', 'Critical']] },
          { label: 'Open remediation', value: g('work'), set: set('work'), options: [['tasks', 'Has open remediation'], ['none', 'None']] }]} />
        <OwnershipMap assets={assets} />
        {assets.map((a: any) => <System key={a.id} a={a} people={r.people} onEdit={() => setEditSys(a)} />)}
        {!assets.length && <p className="muted" style={{ marginTop: 14 }}>No systems match.</p>}</>}

      {tab === 'Organisations' && <>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}><button className="btn primary" onClick={() => setEditDept({})}>Add department</button></div>
        <Controls {...common} shown={bus.length} total={r.business_units.length} selects={[
          { label: 'Department', value: g('bu'), set: set('bu'), options: units }]} />
        <div className="card" style={{ marginTop: 14 }}><h2>Departments</h2>
          <table><thead><tr><th>Department</th><th>Head</th><th>People</th><th>Systems</th><th></th></tr></thead><tbody>
            {bus.map((b: any) => <tr key={b.id}><td><b>{b.name}</b></td><td>{b.head}</td>
              <td>{r.people.filter((p: any) => p.business_unit === b.name).length}</td>
              <td>{r.assets.filter((a: any) => a.business_unit === b.name).length}</td>
              <td><button className="btn sm" onClick={() => setEditDept(b)}>Edit</button></td></tr>)}
            {!bus.length && <tr><td colSpan={5} className="muted">No departments match.</td></tr>}</tbody></table></div>
        <div className="card" style={{ marginTop: 14 }}><h2>Reporting lines</h2>
          <p className="muted">Each person reports to the one on their left; escalation follows these lines. Hover someone to trace their line up and their reports down.{q || g('bu') ? ' Showing matches and the people they report to.' : ''}</p>
          {show.size ? <OrgChart people={r.people} show={show} /> : <p className="muted">No people match.</p>}</div></>}

      {tab === 'People' && <>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}><button className="btn primary" onClick={() => setEditing({})}>Add person</button></div>
        <Controls {...common} shown={people.length} total={r.people.length} selects={[
          { label: 'Department', value: g('bu'), set: set('bu'), options: units },
          { label: 'Role', value: g('role'), set: set('role'), options: uniq(r.people.map((p: any) => p.role)).map((x) => [x, ROLE[x] || x] as [string, string]) },
          { label: 'Status', value: g('status'), set: set('status'), options: [['active', 'Active'], ['left', 'Left'], ['undeliverable', 'Mail undeliverable']] }]} />
        <div className="card" style={{ marginTop: 14 }}>
          <table><thead><tr><th>Name</th><th>Role</th><th>Reports to</th><th>Department</th><th>Owns / responsible for</th><th>Status</th><th></th></tr></thead><tbody>
            {people.map((p: any) => <tr key={p.id}><td><b>{p.name}</b><div className="faint mono">{p.directory_id}</div></td>
              <td>{p.title || ROLE[p.role]}</td><td>{p.manager || 'Board of Directors'}</td><td>{p.business_unit}</td>
              <td>{p.owns.length ? <>owns {p.owns.join(', ')}</> : DUTY[p.role] || ROLE[p.role]}</td>
              <td>{p.active ? (p.deliverable ? <Badge kind="info">active</Badge> : <Badge kind="over">mail undeliverable</Badge>) : <Badge kind="over">left</Badge>}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{p.active && <><button className="btn sm" onClick={() => setEditing(p)}>Edit</button>{' '}
                <button className="btn sm danger" onClick={() => setRemoving(p)}>Remove</button></>}</td></tr>)}
            {!people.length && <tr><td colSpan={7} className="muted">No people match.</td></tr>}</tbody></table></div></>}

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
      {editing && <PersonModal person={editing.id ? editing : undefined} people={r.people} units={r.business_units} onClose={() => setEditing(null)} />}
      {removing && <RemoveModal person={removing} onClose={() => setRemoving(null)} />}
      {editDept && <DeptModal dept={editDept.id ? editDept : undefined} people={r.people} onClose={() => setEditDept(null)} />}
      {editSys && <SystemModal system={editSys.id ? editSys : undefined} people={r.people} units={r.business_units} onClose={() => setEditSys(null)} />}
    </>
  )
}
