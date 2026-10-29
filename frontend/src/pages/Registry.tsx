import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, Loading, Modal, useAction } from '../ui'
import { OrgChart, OwnershipMap } from './RegistryCharts'
import { Controls } from '../filters'
import { has, uniq } from '../filterUtils'

const TABS = ['People', 'Organisations', 'Systems'] as const
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

// Owners are picked by role first; when several people share the role, a second list names them,
// with the system's department head marked and listed first.
function OwnerPicker({ people, exclude, headId, value, onChange, none }: {
  people: any[]; exclude?: number | null; headId?: number | null; value: string; onChange: (id: string) => void; none: string }) {
  const [role, setRole] = useState(() => people.find((p) => String(p.id) === value)?.role || '')
  const candidates = people.filter((p) => p.active && p.id !== exclude)
  const roles = ROLE_OPTIONS.filter((r) => candidates.some((p) => p.role === r))
  const inRole = candidates.filter((p) => p.role === role)
    .sort((x, y) => Number(y.id === headId) - Number(x.id === headId) || x.name.localeCompare(y.name))
  const label = (p: any) => `${p.name}${p.id === headId ? ' (head)' : ''}`
  const choose = (r: string) => {
    setRole(r)
    const match = candidates.filter((p) => p.role === r)
    onChange(match.length === 1 ? String(match[0].id) : '')
  }
  return (
    <>
      <select value={role} onChange={(e) => choose(e.target.value)}><option value="">{none}</option>
        {roles.map((r) => <option key={r} value={r}>{ROLE[r]}</option>)}</select>
      {inRole.length > 1 && <select value={value} onChange={(e) => onChange(e.target.value)}><option value="">Choose one of {inRole.length}…</option>
        {inRole.map((p) => <option key={p.id} value={p.id}>{label(p)}</option>)}</select>}
      {inRole.length === 1 && <span className="faint">→ {label(inRole[0])}</span>}
    </>
  )
}

function PersonModal({ person, people, units, onClose }: { person?: any; people: any[]; units: any[]; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ name: person?.name || '', email: person?.email || '', directory_id: '', title: person?.title || '', role: person?.role || 'owner',
    manager_id: person?.manager_id ? String(person.manager_id) : '', business_unit_id: person ? String(units.find((u) => u.name === person.business_unit)?.id || '') : '',
    deliverable: person ? person.deliverable : true })
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const go = async () => {
    const body: any = { name: f.name, email: f.email, title: f.title || null, role: f.role, business_unit_id: f.business_unit_id ? Number(f.business_unit_id) : null,
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
      <label>Email</label><input type="email" value={f.email} onChange={(e) => set('email', e.target.value)} placeholder="jane.doe@company.com" />
      <p className="faint">How they are contacted. Ownership and sign-offs follow their directory id, never their email.</p>
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
        <button className="btn primary" disabled={busy || !f.name.trim() || !f.email.trim() || !f.business_unit_id} onClick={go}>{person ? 'Save' : 'Add person'}</button></div>
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
        <button className="btn danger-solid" disabled={busy} onClick={go}>Remove</button></div>
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

function SystemModal({ system, name, people, units, onClose }: { system?: any; name?: string; people: any[]; units: any[]; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ name: system?.name || name || '', unit: system?.business_unit_id ? String(system.business_unit_id) : '', owner: '',
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
      <div className="inline-actions" style={{ marginTop: 0 }}>
        <OwnerPicker people={people} exclude={system?.owner_id} headId={units.find((u) => String(u.id) === f.unit)?.head_id}
          value={f.owner} onChange={(v) => set('owner', v)} none={system ? `Keep ${system.owner || 'no owner'}` : 'Choose a role…'} /></div>
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
  const [reset, setReset] = useState(0)  // remounts the picker so its role clears after a reassignment
  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
      <div className="faint">Ownership</div>
      <div className="inline-actions">
        <OwnerPicker key={reset} people={people} exclude={a.owner_id} headId={a.bu_head_id} value={pick} onChange={setPick} none="Reassign owner to role…" />
        <button className="btn sm" disabled={busy || !pick} onClick={() => run(async () => { await api.post(`/registry/assets/${a.id}/owner`, { owner_id: Number(pick) }); setPick(''); setReset((n) => n + 1); refreshAll() })}>Reassign</button>
        {(!a.owner_valid || a.attestation.state === 'due_soon') && a.owner_active && <button className="btn sm primary" disabled={busy} onClick={() => run(async () => { await api.post(`/assets/${a.id}/attest`); refreshAll() })}>{a.owner_valid ? 'Re-attest ownership' : 'Attest ownership'}</button>}
      </div>
      {!a.owner_valid && <p className="faint" style={{ marginTop: 4 }}>Record the new owner's confirmation with Attest ownership.</p>}
      {error && <p className="err">{error}</p>}
    </div>
  )
}

const SOURCE: Record<string, string> = {
  baseline: 'On record', manual: 'Entered by hand', sbom: 'SBOM import', sca: 'SCA scan', authenticated_scan: 'Authenticated scan',
  remediation_evidence: 'Accepted remediation evidence',
}

function VersionControls({ a }: { a: any }) {
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ name: '', version: '', note: '' })
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  const go = () => run(async () => {
    await api.post(`/registry/assets/${a.id}/components`, f)
    setF({ name: '', version: '', note: '' })
    refreshAll()
  })
  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
      <div className="faint">Record a version change</div>
      <div className="inline-actions">
        <input list={`comps-${a.id}`} placeholder="Component" value={f.name} onChange={(e) => set('name', e.target.value)} />
        <datalist id={`comps-${a.id}`}>{a.components.map((c: any) => <option key={c.name} value={c.name} />)}</datalist>
        <input className="version-input" placeholder="New version" value={f.version} onChange={(e) => set('version', e.target.value)} />
        <input placeholder="Note (optional)" value={f.note} onChange={(e) => set('note', e.target.value)} />
        <button className="btn sm" disabled={busy || !f.name.trim() || !f.version.trim()} onClick={go}>Record</button>
      </div>
      <p className="faint" style={{ marginTop: 4 }}>Fixes for a flagged advisory are recorded through remediation evidence on the task, so they are independently reviewed.</p>
      {error && <p className="err">{error}</p>}
    </div>
  )
}

const LIST_STATUS: Record<string, [string, string]> = {
  active: ['ok', 'active'], undeliverable: ['over', 'mail undeliverable'], left: ['over', 'left: not sent to'],
}

function MailingList({ a, people, restricted = false }: { a: any; people: any[]; restricted?: boolean }) {
  const { run, error, busy } = useAction()
  const [add, setAdd] = useState({ person: '', stake: '' })
  const [editing, setEditing] = useState<{ id: number; stake: string } | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [result, setResult] = useState<any>(null)
  const listed = new Set(a.mailing_list.map((m: any) => m.person_id))
  const reachable = a.mailing_list.filter((m: any) => m.status === 'active').length
  return (
    <div style={{ marginTop: 14 }}>
      <div className="faint">{restricted ? 'Stakeholders to contact about this system' : 'Stakeholder mailing list (summaries only; tasks and escalations always go to the owner)'}</div>
      <table style={{ marginTop: 4 }}><thead><tr><th>Person</th><th>Stake in the system</th><th>Directory check</th>{!restricted && <th></th>}</tr></thead><tbody>
        {a.mailing_list.map((m: any) => <tr key={m.id}>
          <td><b>{m.name}</b><div className="faint mono">{m.directory_id}{m.title ? ` · ${m.title}` : ''}</div>
            {restricted && m.email && <div className="person-email">{m.email}</div>}</td>
          <td>{editing?.id === m.id
            ? <input value={editing?.stake || ''} onChange={(e) => setEditing({ id: m.id, stake: e.target.value })} />
            : m.stake || <span className="faint">—</span>}</td>
          <td><Badge kind={LIST_STATUS[m.status][0]}>{LIST_STATUS[m.status][1]}</Badge></td>
          {!restricted && <td style={{ whiteSpace: 'nowrap' }}>{editing?.id === m.id
            ? <><button className="btn sm primary" disabled={busy} onClick={() => run(async () => { await api.patch(`/registry/mailing-list/${m.id}`, { stake: editing?.stake || '' }); setEditing(null); refreshAll() })}>Save</button>{' '}
                <button className="btn sm" onClick={() => setEditing(null)}>Cancel</button></>
            : <><button className="btn sm" onClick={() => setEditing({ id: m.id, stake: m.stake || '' })}>Edit</button>{' '}
                <button className="btn sm danger" disabled={busy} onClick={() => run(async () => { await api.del(`/registry/mailing-list/${m.id}`); refreshAll() })}>Remove</button></>}</td>}</tr>)}
        {!a.mailing_list.length && <tr><td colSpan={restricted ? 3 : 4} className="muted">Nobody is on this system's mailing list yet.</td></tr>}</tbody></table>
      {!restricted && <>
      <div className="inline-actions">
        <select value={add.person} onChange={(e) => setAdd({ ...add, person: e.target.value })}><option value="">Add a stakeholder…</option>
          {people.filter((p) => p.active && !listed.has(p.id)).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.title || ROLE[p.role]}</option>)}</select>
        <input placeholder="Stake, e.g. Business user" value={add.stake} onChange={(e) => setAdd({ ...add, stake: e.target.value })} />
        <button className="btn sm" disabled={busy || !add.person} onClick={() => run(async () => { await api.post(`/registry/assets/${a.id}/mailing-list`, { person_id: Number(add.person), stake: add.stake }); setAdd({ person: '', stake: '' }); refreshAll() })}>Add</button>
      </div>
      <div className="inline-actions">
        <button className="btn sm" disabled={busy} onClick={() => run(async () => { setPreview((await api.get(`/registry/assets/${a.id}/summary`)).summary) })}>Preview summary</button>
        <button className="btn sm primary" disabled={busy || !reachable} onClick={() => run(async () => { setResult(await api.post(`/registry/assets/${a.id}/mailing-list/broadcast`)); setPreview(null); refreshAll() })}>Send summary to {reachable} active member{reachable === 1 ? '' : 's'}</button>
        <span className="faint">{a.last_summary_at ? `last sent ${fmt(a.last_summary_at)}` : 'no summary sent yet'}</span>
      </div>
      {preview && <pre className="note" style={{ whiteSpace: 'pre-wrap', marginTop: 8, background: 'var(--bg)' }}>{preview}</pre>}
      {result && <div className="note ok" style={{ marginTop: 8 }}><b>Summary sent to {result.sent.length}</b>{result.sent.length ? `: ${result.sent.map((x: any) => x.name).join(', ')}` : ''}
        {result.skipped.length > 0 && <p style={{ margin: '4px 0 0' }}>Not sent: {result.skipped.map((x: any) => `${x.name} (${x.reason})`).join(', ')}</p>}</div>}
      {error && <p className="err">{error}</p>}</>}
    </div>
  )
}

function System({ a, people, onEdit, restricted = false, focused = false }: { a: any; people: any[]; onEdit: () => void; restricted?: boolean; focused?: boolean }) {
  const inv = a.inventory
  const details = useRef<HTMLDetailsElement>(null)
  useEffect(() => { if (focused && details.current) details.current.open = true }, [focused])
  return (
    <details ref={details} id={`asset-${a.id}`} className={`card system-card ${focused ? 'focus-record' : ''}`}>
      <summary className="system-summary"><div className="system-summary-main"><h2><span className="chev">▸</span>{a.name}</h2>
        <div className="faint">{restricted ? <>{a.business_unit || 'no department'} · {a.owner || 'no named owner'}</> : <>
          {[a.internet_facing && 'Internet-facing', a.critical && 'Critical', a.customer_facing && 'Customer-facing', a.data_sensitivity].filter(Boolean).join(' · ')}
          {' · '}{a.owner || 'no named owner'} · {a.open_tasks.length} open task{a.open_tasks.length === 1 ? '' : 's'} · {a.claims.length} claim{a.claims.length === 1 ? '' : 's'}</>}</div></div>
        <div className="system-summary-actions">
          {a.attestation.state === 'due_soon' && <Badge kind="cd">attestation due soon</Badge>}
          {a.updates_needed > 0 && <Badge kind="over">{a.updates_needed} update{a.updates_needed > 1 ? 's' : ''} needed</Badge>}
          <Badge kind={a.owner_valid ? 'info' : 'over'}>{a.owner_valid ? 'ownership valid' : 'ownership invalid'}</Badge>
          {/* keep the click from toggling the section */}
          {!restricted && <button className="btn sm" onClick={(e) => { e.preventDefault(); onEdit() }}>Edit</button>}</div></summary>
      <div className="system-overview" style={restricted ? { gridTemplateColumns: '1fr' } : undefined}>
        <div className="system-panel">
          <div className="faint">Accountability chain (who gets routed and escalated to)</div>
          <p style={{ margin: '4px 0' }}>
            <b style={a.owner_active ? {} : { textDecoration: 'line-through' }}>{a.owner || 'no named owner'}</b> → {a.manager || '—'} → {a.bu_head ? `${a.bu_head} (head)` : '—'}
          </p>
          <Link className="btn sm" to={a.owner_id ? `/registry?tab=People&person=${a.owner_id}` : '/registry?tab=People'}>
            {a.owner_id ? `Check ${a.owner}'s people record` : 'Open people directory'}
          </Link>
          {a.owner_issue && <p className="err" style={{ margin: 0 }}>{a.owner_issue}{a.legacy_contact ? ` · legacy contact: ${a.legacy_contact}` : ''}</p>}
          <div className="faint" style={{ marginTop: 8 }}>Business unit</div><div>{a.business_unit || '—'}</div>
          <div className="faint" style={{ marginTop: 8 }}>Ownership attested (quarterly)</div>
          <div>{a.ownership_attested_at ? fmt(a.ownership_attested_at) : 'never'}
            {a.attestation.due && <> · {a.attestation.state === 'lapsed' ? 'lapsed' : 'next due'} {fmt(a.attestation.due)}</>}
            {a.attestation.state === 'due_soon' && <> <Badge kind="cd">due soon</Badge></>}</div>
          {a.confirmation && ['awaiting', 'overdue', 'unscheduled'].includes(a.confirmation.state) &&
            <p className={a.confirmation.state === 'overdue' ? 'err' : 'muted'} style={{ margin: '6px 0 0' }}>
              {a.confirmation.state === 'overdue' ? `Owner confirmation overdue since ${fmt(a.confirmation.due, true)}`
                : a.confirmation.due ? `New owner must confirm by ${fmt(a.confirmation.due, true)}`
                  : 'Owner confirmation needs a due date'}
            </p>}
          {!restricted && !a.in_scanner_targets && <p className="err">Not in scanner targets (coverage gap)</p>}
          {!restricted && <OwnerControls a={a} people={people} />}
        </div>
        {!restricted && <div className="system-panel">
          <div className="faint">Component inventory <Badge kind={inv.state === 'current' ? 'info' : 'over'}>inventory {inv.state}</Badge></div>
          <div>{inv.verified_at ? <>verified {fmt(inv.verified_at)} · {inv.method?.replace(/_/g, ' ')}</> : 'never verified'}</div>
          {inv.verified_at && <div className="faint">{inv.scope.join(', ')}</div>}
          <VersionControls a={a} />
        </div>}
      </div>
      {restricted ? <MailingList a={a} people={people} restricted /> : <>
      <div className="system-data-section"><h3>Components</h3>
        <table style={{ marginTop: 4 }}><thead><tr><th>Component</th><th>Version on record</th><th>Install path</th><th>Update status</th></tr></thead><tbody>
          {a.components.map((c: any) => <tr key={c.name}><td>{c.name}</td><td className="mono">{c.version}</td><td className="mono faint">{c.path || '—'}</td>
            <td>{c.advisories.length
              ? <><Badge kind="over">update to {c.update_to || 'a fixed release'}</Badge>
                  <div className="faint" style={{ marginTop: 3 }}>{c.advisories.map((x: any, i: number) => <span key={x.cve}>{i > 0 && ', '}
                    <Link to={`/acknowledgements/${x.advisory_id}`}>{x.cve}</Link>{x.kev ? ' (known exploited)' : ''}</span>)}</div></>
              : <Badge kind="ok">no known update needed</Badge>}</td></tr>)}
          {!a.components.length && <tr><td colSpan={4} className="muted">No component data on record, so PoliBridge cannot tell whether an update is needed.</td></tr>}</tbody></table></div>
      <div className="system-data-section"><h3>Version audit log</h3>
        <table style={{ marginTop: 4 }}><thead><tr><th>When</th><th>Component</th><th>Change</th><th>Recorded by</th><th>Source</th><th>Note</th></tr></thead><tbody>
          {a.version_history.map((h: any, i: number) => <tr key={h.id ?? `base-${i}`}><td style={{ whiteSpace: 'nowrap' }}>{h.ts ? fmt(h.ts) : 'before log began'}</td>
            <td>{h.component}</td>
            <td className="mono">{h.from === null ? <>added at {h.to}</> : h.to === null ? <>removed ({h.from})</> : <>{h.from} → {h.to}</>}</td>
            <td>{h.actor_name ? <>{h.actor_name}<div className="faint mono">{h.actor}</div></> : h.actor}</td><td>{SOURCE[h.source] || h.source.replace(/_/g, ' ')}</td><td className="faint">{h.note || '—'}</td></tr>)}
          {!a.version_history.length && <tr><td colSpan={6} className="muted">No versions recorded yet.</td></tr>}</tbody></table></div>
      <div className="system-data-section"><h3>Open tasks</h3>
        <table style={{ marginTop: 4 }}><thead><tr><th>Task</th><th>Type</th><th>Impact</th><th>Status</th><th>Assigned to</th></tr></thead><tbody>
          {a.open_tasks.map((t: any) => <tr key={t.id}><td><Link to={`/remediations/${t.id}`}>{t.ref}</Link></td><td>{t.kind}</td><td>{t.impact || '—'}</td>
            <td>{t.status.replace(/_/g, ' ')}</td><td>{t.owner || '—'}</td></tr>)}
          {!a.open_tasks.length && <tr><td colSpan={5} className="muted">No open tasks.</td></tr>}</tbody></table></div>
      <div className="system-data-section"><h3>Claims</h3>
        <table style={{ marginTop: 4 }}><thead><tr><th>Claim</th><th>Value</th><th>Status</th></tr></thead><tbody>
          {a.claims.map((c: any) => <tr key={c.id}><td><Link to={`/claims/${c.id}`}>{c.type}</Link></td><td>{c.value?.replace(/_/g, ' ') || '—'}</td>
            <td><Badge kind={c.status === 'active' ? 'info' : 'over'}>{c.status}</Badge></td></tr>)}
          {!a.claims.length && <tr><td colSpan={3} className="muted">No claims.</td></tr>}</tbody></table></div>
      <MailingList a={a} people={people} /></>}
    </details>
  )
}

export default function Registry() {
  const { data: r, error } = useApi('/registry')
  // the tab lives in the URL, so a refresh or a shared link comes back to the same place
  const [params, setParams] = useSearchParams()
  const tab = TABS.find((t) => t === params.get('tab')) || 'People'
  const focusAsset = Number(params.get('asset')) || null
  const focusPerson = Number(params.get('person')) || null
  useEffect(() => {
    const id = tab === 'Systems' && focusAsset ? `asset-${focusAsset}` : tab === 'People' && focusPerson ? `person-${focusPerson}` : null
    if (id && r) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }, [tab, focusAsset, focusPerson, r])
  const setTab = (t: (typeof TABS)[number]) => setParams(t === 'People' ? {} : { tab: t })
  // search text and filters are kept per tab, so switching tabs does not lose them
  const [f, setF] = useState<Record<string, string>>({})
  const [editing, setEditing] = useState<any>(null)  // person being edited, or {} when adding
  const [removing, setRemoving] = useState<any>(null)
  const [editDept, setEditDept] = useState<any>(null)
  const [editSys, setEditSys] = useState<any>(null)
  const { run, busy, error: runError } = useAction()
  const g = (k: string) => f[`${tab}.${k}`] || ''
  const set = (k: string) => (v: string) => setF((x) => ({ ...x, [`${tab}.${k}`]: v }))
  const clear = () => setF((x) => Object.fromEntries(Object.entries(x).filter(([k]) => !k.startsWith(tab + '.'))))
  if (!r) return <Loading error={error} />
  const q = g('q')
  const units = uniq(r.business_units.map((b: any) => b.name))

  const people = r.people.filter((p: any) => p.id === focusPerson || (has(q, p.name, p.email, p.directory_id, p.title, ROLE[p.role], p.manager, p.business_unit, p.owns.join(' '))
    && (!g('bu') || p.business_unit === g('bu')) && (!g('role') || p.role === g('role'))
    && (!g('status') || (g('status') === 'active' ? p.active && p.deliverable : g('status') === 'left' ? !p.active : p.active && !p.deliverable))))

  // roles without security clearance get ownership only: the server leaves out components, tasks, exposure and discovery
  const restricted = !!r.restricted
  const assets = r.assets.filter((a: any) => a.id === focusAsset || (has(q, a.name, a.owner, a.manager, a.bu_head, a.business_unit, a.legacy_contact,
      (a.components || []).map((c: any) => `${c.name} ${c.version}`).join(' '), (a.open_tasks || []).map((t: any) => t.ref).join(' '))
    && (!g('bu') || a.business_unit === g('bu'))
    && (!g('owner') || (g('owner') === 'valid') === a.owner_valid)
    && (!g('inv') || a.inventory.state === g('inv'))
    && (!g('exposure') || (g('exposure') === 'internet' ? a.internet_facing : a.critical))
    && (!g('work') || (g('work') === 'tasks' ? a.open_tasks.length > 0 : a.open_tasks.length === 0))
    && (!g('updates') || (g('updates') === 'needed') === a.updates_needed > 0)))
  const outdated = r.assets.filter((a: any) => a.updates_needed > 0)

  const bus = r.business_units.filter((b: any) => has(q, b.name, b.head) && (!g('bu') || b.name === g('bu')))
  // keep matching people and everyone above them so the reporting line stays readable
  const byId = new Map<number, any>(r.people.map((p: any) => [p.id, p]))
  const show = new Set<number>()
  r.people.filter((p: any) => has(q, p.name, p.title, ROLE[p.role], p.owns.join(' ')) && (!g('bu') || p.business_unit === g('bu')))
    .forEach((p: any) => { for (let x = p; x && !show.has(x.id); x = byId.get(x.manager_id)) show.add(x.id) })

  const discovered = r.discovered.filter((d: any) => has(q, d.name, d.source))

  const common = { q, setQ: set('q'), onClear: clear }
  const active = r.people.filter((p: any) => p.active).length
  return (
    <>
      <div className="head"><div><h1>Registry</h1>
        <p className="muted" style={{ maxWidth: 760 }}>Who is accountable for what: staff, reporting lines, departments and systems, linked to their components, tasks and claims. Owners are directory identities, never email addresses.</p></div>
        <div className="card registry-estate"><div className="faint">Estate</div>
          <div className="stat">{r.assets.length} systems · {active} staff</div></div></div>
      <div className="registry-topbar">
        <div className="tabs" role="group" aria-label="Registry sections">{TABS.map((t) => (
          <button key={t} className={`btn ${tab === t ? 'primary' : ''}`} aria-pressed={tab === t} onClick={() => setTab(t)}>{t}</button>))}</div>
      </div>

      {tab === 'Systems' && <>
        <Controls {...common} shown={assets.length} total={r.assets.length} action={!restricted && <button className="btn primary" onClick={() => setEditSys({})}>Add system</button>} selects={[
          { label: 'Department', value: g('bu'), set: set('bu'), options: units },
          { label: 'Ownership', value: g('owner'), set: set('owner'), options: [['valid', 'Valid'], ['invalid', 'Invalid']] },
          ...(restricted ? [] : [
            { label: 'Inventory', value: g('inv'), set: set('inv'), options: [['current', 'Current'], ['stale', 'Stale'], ['none', 'None']] as [string, string][] },
            { label: 'Exposure', value: g('exposure'), set: set('exposure'), options: [['internet', 'Internet-facing'], ['critical', 'Critical']] as [string, string][] },
            { label: 'Open remediation', value: g('work'), set: set('work'), options: [['tasks', 'Has open remediation'], ['none', 'None']] as [string, string][] },
            { label: 'Versions', value: g('updates'), set: set('updates'), options: [['needed', 'Update needed'], ['current', 'No known update needed']] as [string, string][] }])]} />
        {!restricted && outdated.length > 0 && <div className="note fail" style={{ marginTop: 14 }}>
          <b>{outdated.length} system{outdated.length > 1 ? 's' : ''} running a version with a known advisory</b>
          <p style={{ margin: '4px 0 0' }}>{outdated.map((a: any) => `${a.name} (${a.components.filter((c: any) => c.advisories.length)
            .map((c: any) => `${c.name} ${c.version} → ${c.update_to || 'fixed release'}`).join(', ')})`).join(' · ')}</p>
          {g('updates') !== 'needed' && <button className="btn sm" style={{ marginTop: 8 }} onClick={() => set('updates')('needed')}>Show only these</button>}</div>}
        <OwnershipMap assets={assets} restricted={restricted} />
        {assets.map((a: any) => <System key={`${a.id}-${a.id === focusAsset}`} a={a} people={r.people} restricted={restricted}
          focused={a.id === focusAsset} onEdit={() => setEditSys(a)} />)}
        {!assets.length && <p className="muted" style={{ marginTop: 14 }}>No systems match.</p>}
        {!restricted && <div className="card" style={{ marginTop: 14 }}>
          <div className="sec"><h2>Discovery reconciliation</h2>
            <button className="btn sm" disabled={busy} onClick={() => run(async () => { await api.post('/sweep'); refreshAll() })}>Run check now</button></div>
          <p className="muted" style={{ marginTop: 0 }}>The register is checked against discovery every minute. An unregistered internet-facing system raises a High finding for the CISO; registering it resolves the finding.</p>
          <table><thead><tr><th>Seen by discovery</th><th>Source</th><th>Exposure</th><th>In register</th><th></th></tr></thead><tbody>
            {discovered.map((d: any) => <tr key={d.name}><td>{d.name}</td><td>{d.source}</td><td>{d.internet_facing ? 'Internet-facing' : 'Internal'}</td>
              <td>{d.registered ? '✓' : <b className="err">✕ unregistered</b>}</td>
              <td>{!d.registered && <button className="btn sm primary" onClick={() => setEditSys({ name: d.name })}>Register</button>}</td></tr>)}</tbody></table>
          {runError && <p className="err">{runError}</p>}</div>}</>}

      {tab === 'Organisations' && <>
        <Controls {...common} shown={bus.length} total={r.business_units.length} action={<button className="btn primary" onClick={() => setEditDept({})}>Add department</button>} selects={[
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
        <Controls {...common} shown={people.length} total={r.people.length} action={<button className="btn primary" onClick={() => setEditing({})}>Add person</button>} selects={[
          { label: 'Department', value: g('bu'), set: set('bu'), options: units },
          { label: 'Role', value: g('role'), set: set('role'), options: uniq(r.people.map((p: any) => p.role)).map((x) => [x, ROLE[x] || x] as [string, string]) },
          { label: 'Status', value: g('status'), set: set('status'), options: [['active', 'Active'], ['left', 'Left'], ['undeliverable', 'Mail undeliverable']] }]} />
        <div className="card registry-table-card" style={{ marginTop: 14 }}>
          <div className="table-scroll"><table className="people-table"><thead><tr><th>Person</th><th>Position</th><th>Reports to</th><th>Owns / responsible for</th><th>Status</th><th>Actions</th></tr></thead><tbody>
            {people.map((p: any) => <tr key={p.id} id={`person-${p.id}`} className={p.id === focusPerson ? 'hl' : ''}><td><b>{p.name}</b><div className="faint mono">{p.directory_id}</div>
              <div className="person-email">{p.email || <span className="red">missing email</span>}</div></td>
              <td>{p.title || ROLE[p.role]}<div className="faint">{p.business_unit}</div></td>
              <td>{p.manager || 'Board of Directors'}</td>
              <td>{p.owns.length ? <>owns {p.owns.join(', ')}</> : DUTY[p.role] || ROLE[p.role]}</td>
              <td>{p.active ? (p.deliverable ? <Badge kind="ok">active</Badge> : <Badge kind="over">mail undeliverable</Badge>) : <Badge kind="over">left</Badge>}</td>
              <td>{p.active && <div className="row-actions"><button className="btn sm" onClick={() => setEditing(p)}>Edit</button>
                <button className="btn sm danger" onClick={() => setRemoving(p)}>Remove</button></div>}</td></tr>)}
            {!people.length && <tr><td colSpan={6} className="muted">No people match.</td></tr>}</tbody></table></div></div></>}

      {editing && <PersonModal person={editing.id ? editing : undefined} people={r.people} units={r.business_units} onClose={() => setEditing(null)} />}
      {removing && <RemoveModal person={removing} onClose={() => setRemoving(null)} />}
      {editDept && <DeptModal dept={editDept.id ? editDept : undefined} people={r.people} onClose={() => setEditDept(null)} />}
      {editSys && <SystemModal system={editSys.id ? editSys : undefined} name={editSys.name} people={r.people} units={r.business_units} onClose={() => setEditSys(null)} />}
    </>
  )
}
