// Component 5: the two sign-offs a remediation or "Does Not Apply" claim needs before it closes (report §6.5),
// and what happens when they disagree.
import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, refreshAll } from '../api'
import { Badge, Modal, useAction } from '../ui'

type Signoff = { key: string; label: string; dual: boolean; confirms: string; signer: string | null; signed_by: string | null;
  signed_at: string | null; record_version: string | null; record_source: string | null; note: string | null }

const first = (name: string | null) => (name || '').split(' ')[0]

export function SignoffCard({ t }: { t: any }) {
  const [dlg, setDlg] = useState<null | { kind: 'record' | 'dispute'; s: Signoff }>(null)
  const { run, error, busy } = useAction()
  const open = ['open', 'reopened', 'fixed_pending_review'].includes(t.status)
  const ev = t.current_evidence
  const d = t.dispute
  const disputed = d && !d.resolved_at
  const signs: Signoff[] = t.signoffs
  const extra = signs.filter((s) => !s.dual)
  const sign = (s: Signoff) => run(async () => { await api.post(`/tasks/${t.id}/review-signoff`, { role: s.key }); refreshAll() })
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <h2>Remediation sign-offs</h2>
      <p className="muted" style={{ margin: '6px 0' }}>Two functions must agree before this closes: the <b>asset owner (IT)</b> confirms the
        system's component record, and <b>Information Security</b> confirms the check covered the component.
        {extra.length > 0 && <> This system's impact also needs {extra.map((s) => s.label).join(' and ')}.</>}</p>

      {disputed && <div className="note fail signoff-dispute" style={{ margin: '8px 0' }}>
        <b>Sign-offs disagree{d.rounds > 1 ? ` (round ${d.rounds})` : ''}</b>
        <p>{d.label} · {d.by} · {fmt(d.at)}{d.auto ? ' · detected automatically' : ''}: {d.reason}.</p>
        <p>The claim is back to <b>Cannot Determine</b> and the evidence was set aside. Both sides have a task on the Alerts page.
          Escalated to {(d.escalated_to || []).join(', ') || 'the owner’s manager'}.{' '}
          {d.escalated_at ? 'Unresolved past the deadline, so it is also in the board risk report.'
            : 'If it is still unresolved after 48 hours it goes to the risk owner, senior management and the board report.'}</p>
        <p className="muted">To resolve: submit new evidence, have it reviewed, and both functions sign off again.</p></div>}
      {d && d.resolved_at && <p className="faint" style={{ margin: '6px 0' }}>An earlier disagreement was resolved {fmt(d.resolved_at)}: {d.resolved_how}.</p>}
      {!ev && open && <p className="faint" style={{ margin: '6px 0' }}>{disputed ? 'Sign-offs reopen when new evidence is submitted.' : 'Sign-offs open once evidence has been submitted.'}</p>}

      {signs.map((s) => (
        <div key={s.key} className="signoff-row">
          <div>
            <div className="signoff-head"><b>{s.label}</b>{s.signed_by ? <Badge kind="info">✓ Signed</Badge> : <span className="faint">{s.signer || 'nobody holds this role'}</span>}</div>
            <div className="muted">{s.confirms}</div>
            {s.signed_by && <div className="faint">{s.signed_by} · {fmt(s.signed_at)}
              {s.record_source && <> · record shows {s.record_version || 'not present'} ({s.record_source})</>}</div>}</div>
          {!s.signed_by && open && ev && s.signer && <div className="signoff-actions">
            <button className="btn sm" disabled={busy} onClick={() => s.key === 'system_owner' ? setDlg({ kind: 'record', s }) : sign(s)}>
              {s.key === 'system_owner' ? `Confirm record as ${first(s.signer)}` : `Sign as ${first(s.signer)}`}</button>
            <button className="btn sm danger" disabled={busy} onClick={() => setDlg({ kind: 'dispute', s })}>Disagree</button></div>}
        </div>))}
      {error && <p className="err">{error}</p>}
      {dlg?.kind === 'record' && <RecordModal t={t} s={dlg.s} onClose={() => setDlg(null)} />}
      {dlg?.kind === 'dispute' && <DisputeModal t={t} s={dlg.s} onClose={() => setDlg(null)} />}
    </div>
  )
}

// The asset owner states what their own record shows. If it contradicts the evidence, PoliBridge records a disagreement.
function RecordModal({ t, s, onClose }: { t: any; s: Signoff; onClose: () => void }) {
  const absence = t.current_evidence?.purpose === 'absence'
  const [present, setPresent] = useState(!absence)
  const [version, setVersion] = useState('')
  const [source, setSource] = useState('deployment_record')
  const { run, error, busy } = useAction()
  const go = async () => { if (await run(async () => {
    await api.post(`/tasks/${t.id}/review-signoff`, { role: s.key, record_source: source,
      record_version: present && source !== 'none' ? version : null }); refreshAll() })) onClose() }
  return <Modal title={`Confirm the component record · ${s.signer}`} onClose={onClose}
    sub={`State what your own record for ${t.asset} shows. PoliBridge compares it with the evidence; if they differ, the sign-offs disagree and the claim goes back to Cannot Determine.`}>
    <label>Where your record comes from</label>
    <select value={source} onChange={(e) => setSource(e.target.value)}>
      {t.record_sources.map((r: any) => <option key={r.key} value={r.key}>{r.label}</option>)}</select>
    {source !== 'none' && <>
      {absence && <><label>{t.component} in your record</label>
        <select value={present ? 'yes' : 'no'} onChange={(e) => setPresent(e.target.value === 'yes')}>
          <option value="no">Not present</option><option value="yes">Present</option></select></>}
      {present && <><label>{t.component} version your record shows</label>
        <input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="e.g. 2.3.32" /></>}</>}
    <p className="faint">{absence ? 'The evidence claims the component is not present.' : `The evidence observed ${t.current_evidence?.observed_version || 'no version'}.`}
      {source === 'none' && ' With no record, this is treated as uncertainty, not agreement.'}</p>
    {error && <p className="err">{error}</p>}
    <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy || (present && source !== 'none' && !version.trim())} onClick={go}>Confirm and sign</button></div>
  </Modal>
}

function DisputeModal({ t, s, onClose }: { t: any; s: Signoff; onClose: () => void }) {
  const [reason, setReason] = useState('')
  const { run, error, busy } = useAction()
  const go = async () => { if (await run(async () => { await api.post(`/tasks/${t.id}/signoff-dispute`, { role: s.key, reason }); refreshAll() })) onClose() }
  return <Modal title={`Disagree · ${s.label}`} onClose={onClose}
    sub="The claim goes back to Cannot Determine, the evidence is set aside, both functions get a task, and the owner's manager is told now. Unresolved after 48 hours, it goes to the risk owner, senior management and the board report.">
    <label>Why you disagree (recorded for {s.signer})</label>
    <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
      placeholder={s.key === 'security' ? 'e.g. the scan credentials could not read the webapps folder' : 'e.g. our deployment record still shows the old version'} />
    {error && <p className="err">{error}</p>}
    <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn danger-solid" disabled={busy || !reason.trim()} onClick={go}>Record disagreement</button></div>
  </Modal>
}

// On a claim: how it was closed, by whom, and any disagreement on the way.
export function ClaimClosure({ c }: { c: any }) {
  if (!c) return null
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="sec"><h2>How this claim was closed</h2>
        <span className="faint"><Link to={`/remediations/${c.task_id}`}>{c.task_ref}</Link> · <Link to={`/evidence/${c.evidence_id}`}>EVD-{String(c.evidence_id).padStart(4, '0')}</Link></span></div>
      <dl className="kv" style={{ marginTop: 10, gridTemplateColumns: '180px 1fr' }}>
        <dt>Independent reviewer</dt><dd>{c.reviewer || '—'}</dd>
        {c.signoffs.map((s: Signoff) => <Fragment key={s.key}><dt>{s.label}</dt>
          <dd>{s.signed_by ? <>{s.signed_by} <span className="faint">· {fmt(s.signed_at)}</span></> : <span className="muted">not signed</span>}
            {s.record_source && <div className="faint">Component record: {s.record_version || 'not present'} ({s.record_source})</div>}</dd></Fragment>)}
      </dl>
      {c.disputes.length > 0 && <><h3 style={{ marginTop: 12 }}>Disagreements before closure</h3>
        {c.disputes.map((x: any, i: number) => <div key={i} className="note fail signoff-dispute" style={{ marginTop: 6 }}>
          <b>{x.role} · {x.by}</b> <span className="faint">{fmt(x.ts)}{x.auto ? ' · detected automatically' : ''}</span><p>{x.reason}</p></div>)}</>}
    </div>
  )
}
