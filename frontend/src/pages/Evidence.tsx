import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, Loading, useAction } from '../ui'

export function EvidenceList() {
  const { data: ev, error } = useApi('/evidence')
  return (
    <>
      <h1>Evidence review</h1>
      <p className="muted">Every claim is held to the same standard as a fix: the evidence must have examined the component.</p>
      <div className="card" style={{ marginTop: 16 }}>
        {!ev ? <Loading error={error} /> : (
          <table><thead><tr><th>Evidence</th><th>Task</th><th>Submitted by</th><th>Status</th></tr></thead>
            <tbody>{ev.map((e: any) => (
              <tr key={e.id}><td><Link to={`/evidence/${e.id}`}><b>EVD-{String(e.id).padStart(4, '0')}</b></Link>
                <div className="faint">{e.purpose === 'absence' ? 'Does Not Apply claim' : 'Remediation'} · {fmt(e.created_at)}</div></td>
                <td>{e.asset_task}</td><td>{e.submitter}</td>
                <td><Badge kind={e.status === 'rejected' ? 'over' : e.status === 'accepted' ? 'info' : ''}>{e.status}</Badge></td></tr>))}
              {!ev.length && <tr><td colSpan={4} className="muted">No evidence submitted yet.</td></tr>}</tbody></table>)}
      </div>
    </>
  )
}

export function EvidenceReview() {
  const { id } = useParams()
  const { data: e, error } = useApi(`/evidence/${id}`)
  const [comment, setComment] = useState('')
  const { run, error: aerr, busy } = useAction()
  if (!e) return <Loading error={error} />
  const decide = (action: string) => run(async () => { await api.post(`/evidence/${e.id}/decision`, { action, comment }); refreshAll() })
  const pending = e.status === 'pending'
  const examined = e.examined as string[]
  return (
    <>
      <div className="crumbs"><Link to={`/tasks/${e.task_id}`}>{e.task_ref}</Link> / EVD-{String(e.id).padStart(4, '0')}</div>
      <div className="mono faint" style={{ marginTop: 8 }}>CLAIM · {e.purpose === 'absence' ? 'APPLICABILITY' : 'REMEDIATION'}</div>
      <h1>{e.claim}</h1>
      <p className="muted" style={{ maxWidth: 780 }}>Submitted by {e.submitter}, {fmt(e.submitted_at)}. A negative claim is held to the same standard as a fix: the evidence must have examined the component.</p>

      {e.status === 'rejected' && <div className="note fail" style={{ marginTop: 16 }}><b>{e.all_pass ? 'Evidence rejected by reviewer' : 'Scope check failed — evidence cannot support this claim'}</b>
        <p>{e.purpose === 'absence' ? `${e.asset} stays at Cannot Determine and the task stays open. ` : 'The task stays open and is escalated to the owner’s manager. '}
          PoliBridge flags what the evidence lacks; it never infers safety from an uploaded report.</p></div>}
      {pending && <div className="note ok" style={{ marginTop: 16 }}><b>All automated checks passed</b><p>An independent reviewer must still decide.</p></div>}
      {e.status === 'accepted' && <div className="note ok" style={{ marginTop: 16 }}><b>Evidence accepted</b></div>}

      <div className="grid g21" style={{ gridTemplateColumns: '1.6fr 1fr' }}>
        <div className="card"><div className="sec"><h2>Claim versus evidence</h2><span className="faint">Rules E1–E3</span></div>
          <table><thead><tr><th>Check</th><th>Claim requires</th><th>Evidence recorded</th><th>Result</th></tr></thead>
            <tbody>{e.checks.map((c: any) => (
              <tr key={c.check} className={c.pass ? '' : 'bad'}><td><b>{c.check}</b></td><td>{c.requires}</td><td className="mono">{c.recorded}</td>
                <td className={c.pass ? 'pass' : 'fail'}>{c.pass ? '✓ Pass' : '✕ Fail'}</td></tr>))}</tbody></table>
          {e.reasons.length > 0 && <ul className="muted" style={{ marginBottom: 0 }}>{e.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>}</div>
        <div>
          <div className="card"><h2>What the scan actually examined</h2>
            <div className="tree" style={{ marginTop: 10 }}>
              {examined.length ? examined.map((s) => <div key={s}><div className="top"><span>{s}</span><span className="blue">{s.endsWith('/**') ? 'scanned · recursive' : 'scanned · depth 1'}</span></div></div>)
                : <div className="top"><span>nothing recorded</span></div>}
              <div className="body">
                {e.needed_paths.map((p: string) => <div key={p}>└ <span className={e.gaps.includes(p) ? 'red' : 'blue'}>{p}/…/{e.component}</span></div>)}
                {e.gaps.length > 0 && <div className="faint" style={{ fontFamily: 'inherit' }}>Not examined — where the component lives</div>}</div></div>
            <p className="muted" style={{ marginTop: 10 }}>A clean result here says nothing about {e.component}. Running a scan is not the same as checking the component.</p></div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}><h2>Reviewer decision</h2>
        <label>Reason recorded with the decision</label>
        <textarea rows={3} value={comment} onChange={(ev) => setComment(ev.target.value)} placeholder="Say what is missing, e.g. an authenticated scan scoped to /opt/acis/**." />
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          {pending && <button className="btn primary" disabled={busy || !e.all_pass} onClick={() => decide('approve')}>Approve</button>}
          <button className={`btn ${pending ? '' : 'primary'}`} disabled={busy || e.status === 'accepted'} onClick={() => decide('reject')}>{pending ? 'Reject' : 'Confirm rejection'}</button>
          <button className="btn" disabled={busy} onClick={() => decide('request')}>Request specific evidence</button></div>
        {!e.all_pass && <p className="faint" style={{ marginTop: 8 }}>Approve is unavailable while any check fails.</p>}
        {aerr && <p className="err">{aerr}</p>}</div>
    </>
  )
}
