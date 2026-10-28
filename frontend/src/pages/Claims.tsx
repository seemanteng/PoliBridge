import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, Loading, Modal, useAction } from '../ui'
import { ClaimClosure } from './Signoffs'

const TYPE: Record<string, string> = { applicability: 'Applicability claim', remediation: 'Remediation claim', ownership: 'Ownership claim', monitoring: 'Monitoring claim', risk_acceptance: 'Risk acceptance' }

const METHODS: [string, string][] = [['authenticated_scan', 'Authenticated scan'], ['direct_version_check', 'Direct version check'], ['sbom', 'SBOM'],
  ['sca', 'Software-composition analysis'], ['attestation', 'Attestation'], ['certificate_check', 'Certificate check'],
  ['configuration_review', 'Configuration review']]
const TYPES: [string, string, string][] = [
  ['monitoring', 'Monitoring', 'A control you rely on is in effect. It lapses when its certificate expires, or when its configuration is next due for review.'],
  ['ownership', 'Ownership', 'A named person owns a system. Lapses when the attestation does.'],
  ['remediation', 'Remediation', 'A fix was applied. Normally created through Remediation and Evidence review.'],
  ['applicability', 'Applicability', 'A system is affected, or cannot yet be determined. Does Not Apply needs reviewed evidence, so it is blocked here.']]
const iso = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16)

// Dates default from the server's clock, so the form waits for it
function NewClaim({ onClose }: { onClose: () => void }) {
  const clock = useApi('/clock')
  if (!clock.data) return <Modal title="New claim" onClose={onClose}><Loading error={clock.error} /></Modal>
  return <NewClaimForm now={new Date(clock.data.now)} onClose={onClose} />
}

function NewClaimForm({ now, onClose }: { now: Date; onClose: () => void }) {
  const assets = useApi('/assets'), people = useApi('/people')
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ type: 'monitoring', asset: '', statement: '', value: '', component: '', method: 'certificate_check', scope: '',
    evidence: iso(now), expiry: iso(new Date(now.getTime() + 90 * 864e5)), owner: '' })
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  const t = TYPES.find((x) => x[0] === f.type)!
  const go = async () => {
    const ok = await run(async () => {
      await api.post('/claims', { claim_type: f.type, asset_id: Number(f.asset), statement: f.statement, value: f.value || null,
        component: f.component || null, verification_method: f.method, evidence_scope: f.scope.split(',').map((s) => s.trim()).filter(Boolean),
        evidence_ts: f.evidence, expiry: f.expiry, owner_id: f.owner ? Number(f.owner) : null })
      refreshAll()
    })
    if (ok) onClose()
  }
  return (
    <Modal title="New claim" sub="A claim is a statement someone stands behind, with evidence that says what it examined and a date it lapses." onClose={onClose}>
      <label>Type</label>
      <select value={f.type} onChange={(e) => set('type', e.target.value)}>{TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      <p className="faint">{t[2]}</p>
      <label>System</label>
      <select value={f.asset} onChange={(e) => set('asset', e.target.value)}><option value="">Choose a system…</option>
        {(assets.data || []).map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
      <label>Statement</label><input value={f.statement} onChange={(e) => set('statement', e.target.value)} placeholder="e.g. ACIS outbound traffic is inspected" />
      {f.type === 'applicability' && <><label>Value</label>
        <select value={f.value} onChange={(e) => set('value', e.target.value)}><option value="">Choose…</option><option value="applies">Applies</option>
          <option value="cannot_determine">Cannot Determine</option><option value="does_not_apply">Does Not Apply</option></select></>}
      <label>Component (optional)</label><input value={f.component} onChange={(e) => set('component', e.target.value)} placeholder="e.g. struts2-core" />
      <label>How it was verified</label>
      <select value={f.method} onChange={(e) => set('method', e.target.value)}>{METHODS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      <label>What the evidence examined (comma separated)</label><input value={f.scope} onChange={(e) => set('scope', e.target.value)} placeholder="e.g. /opt/acis/**, cert:SSLV-02" />
      <div className="grid g2" style={{ marginTop: 0 }}>
        <div><label>Evidence date</label><input type="datetime-local" value={f.evidence} onChange={(e) => set('evidence', e.target.value)} /></div>
        <div><label>{f.method === 'certificate_check' ? 'Lapses on (certificate expiry)' : f.method === 'configuration_review' ? 'Lapses on (next configuration review)' : 'Lapses on'}</label><input type="datetime-local" value={f.expiry} onChange={(e) => set('expiry', e.target.value)} /></div></div>
      <label>Told when it lapses</label>
      <select value={f.owner} onChange={(e) => set('owner', e.target.value)}><option value="">Risk &amp; Compliance team</option>
        {(people.data || []).filter((p: any) => p.active).map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !f.asset || !f.statement} onClick={go}>Add claim</button></div>
    </Modal>
  )
}

export function ClaimList() {
  const { data: cs, error } = useApi('/claims')
  const [adding, setAdding] = useState(false)
  return (
    <>
      <div className="head"><div><h1>Claims</h1>
        <p className="muted">Every status PoliBridge holds is a claim with scoped evidence and an expiry. Lapsed claims revert to Cannot Determine.</p></div>
        <button className="btn primary" onClick={() => setAdding(true)}>New claim</button></div>
      {adding && <NewClaim onClose={() => setAdding(false)} />}
      <div className="card" style={{ marginTop: 16 }}>
        {!cs ? <Loading error={error} /> : (
          <table><thead><tr><th>Claim</th><th>Type</th><th>Asset</th><th>Sign-offs</th><th>Status</th><th>Expiry</th></tr></thead>
            <tbody>{cs.map((c: any) => (
              <tr key={c.id}><td><Link to={`/claims/${c.id}`}><b>{c.statement}</b></Link></td><td>{TYPE[c.claim_type]}</td><td>{c.asset}</td>
                <td>{c.signoffs ? <span title="Asset owner (IT), Information Security and any impact sign-offs">
                  {c.signoffs.signed}/{c.signoffs.needed} signed{c.signoffs.disputes > 0 && <span className="faint"> · {c.signoffs.disputes} disagreement{c.signoffs.disputes > 1 ? 's' : ''}</span>}</span>
                  : <span className="muted">—</span>}</td>
                <td><Badge kind={c.status === 'expired' ? 'cd' : c.status === 'active' ? 'info' : ''}>{c.status === 'expired' ? '? Lapsed' : c.status}</Badge></td>
                <td className="mono">{fmt(c.expiry, true)}</td></tr>))}</tbody></table>)}
      </div>
    </>
  )
}

export function ClaimDetail() {
  const { id } = useParams()
  const { data: c, error } = useApi(`/claims/${id}`)
  if (!c) return <Loading error={error} />
  const start = new Date(c.life.start).getTime(), end = c.life.end ? new Date(c.life.end).getTime() : null, now = new Date(c.life.now).getTime()
  const span = end ? end - start : 1
  const nowPct = Math.min(100, Math.max(0, ((now - start) / span) * 100))
  const warnPct = Math.max(0, 100 - Math.min(100, (21 * 864e5 / span) * 100))
  const lapsed = c.status === 'expired'
  const scope = (c.evidence_scope as string[]).filter((s) => !s.startsWith('cert:'))
  return (
    <>
      <div className="crumbs"><Link to="/claims">Claims</Link> / {c.ref}</div>
      <div className="badges" style={{ marginTop: 8 }}><Badge>{TYPE[c.type]}</Badge>
        {lapsed ? <Badge kind="lbl-cd">? Lapsed → Cannot Determine</Badge> : <Badge kind="info">{c.status}</Badge>}</div>
      <h1>“{c.statement}”</h1>
      <p className="muted">{c.type === 'monitoring' ? 'This claim was only as good as the certificate on the inspection device. When the certificate expired, so did the claim.' : c.reason || 'Evidence life and dependants are shown below.'}</p>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><h2>Evidence life</h2><span className="faint">{c.depends_on ? `Expiry tied to ${c.depends_on.label.replace('TLS certificate · ', 'the ')} certificate` : 'Expiry tied to its evidence'}</span></div>
        <div className="life"><i style={{ width: `${Math.min(nowPct, warnPct)}%`, background: '#9fb1ee' }} />
          <i style={{ width: `${Math.max(0, Math.min(nowPct, 100) - warnPct)}%`, background: '#f2c29b' }} />
          <i style={{ width: `${100 - Math.max(nowPct, warnPct)}%`, background: lapsed ? '#a58be0' : '#e6e4dc' }} /></div>
        <div className="mono faint" style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span>{fmt(c.life.start, true)} · accepted</span><span className={lapsed ? 'purple' : ''}>{fmt(c.expiry, true)} · {lapsed ? 'lapsed' : 'expires'}</span></div></div>

      <ClaimClosure c={c.closure} />

      {c.dependents.length > 0 && <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><h2>{lapsed ? 'What the lapse reopened' : 'What depends on this claim'}</h2><span className="faint">Rule F2 · each flagged to its owner</span></div>
        <div className="flow">
          {c.depends_on ? <div className="node depends"><div className="lbl red">Depends on</div><b>{c.depends_on.label}</b><div className="faint">{c.depends_on.expired ? 'Expired' : 'Expires'} {fmt(c.depends_on.expiry)}</div></div> : <div />}
          <span className="muted" style={{ textAlign: 'center' }}>→</span>
          <div className="node this"><div className="lbl purple">This claim · {c.ref}</div><b>{c.statement}</b><div className="faint">{lapsed ? 'Now Cannot Determine' : 'Active'}</div></div>
          <span className="muted" style={{ textAlign: 'center' }}>→</span>
          <div><div className="mono faint" style={{ marginBottom: 6 }}>DEPENDED ON BY · {c.dependents.filter((d: any) => d.reopened).length} REOPENED</div>
            {c.dependents.map((d: any, i: number) => <div key={i} className="node" style={{ marginBottom: 6, display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <b>{d.label}</b><span className="muted">{d.owner}{d.reopened ? ' · reopened' : ''}</span></div>)}</div></div></div>}

      <div className="grid g2">
        <div className="card"><h2>Claim record</h2><dl className="kv" style={{ marginTop: 10, gridTemplateColumns: '120px 1fr' }}>
          <dt>Asset · component</dt><dd>{c.asset}{c.component ? ` · ${c.component}` : ''}</dd>
          <dt>Evidence scope</dt><dd>{scope.join(', ') || '—'}</dd><dt>Method</dt><dd>{(c.method || '—').replaceAll('_', ' ')}</dd>
          <dt>Evidence time</dt><dd className="mono">{fmt(c.evidence_ts, true)}</dd><dt>Submitter</dt><dd>{c.submitter || '—'}</dd>
          <dt>Reviewer</dt><dd>{c.reviewer || '—'}</dd><dt>Expiry</dt><dd className="mono">{fmt(c.expiry, true)}</dd></dl></div>
        <div className="card"><h2>History</h2>{c.history.map((h: any, i: number) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '96px 1fr', gap: 10, padding: '8px 0', borderBottom: '1px solid #efede6' }}>
            <span className="mono muted">{fmt(h.ts)}</span><span className={h.text.startsWith('Expired') ? 'purple' : ''}>{h.text}</span></div>))}</div>
      </div>
    </>
  )
}
