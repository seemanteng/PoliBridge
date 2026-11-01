import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../../api'
import { Badge, Loading, Modal, useAction } from '../../ui'

const STATUS: Record<string, [string, string]> = {
  pending_approval: ['cd', 'Waiting for approval'], released: ['info', 'Sent'], withheld: ['over', 'Held back'],
  rejected: ['over', 'Rejected'], superseded: ['', 'Superseded'], draft: ['', 'Draft'],
}

export function GuidanceText({ g }: { g: any }) {
  return (
    <div style={{ marginTop: 6 }}>
      {g.text && g.text.split(/\n|(?<=[.!?])\s+(?=[A-Z])/).filter(Boolean).map((l: string, i: number) => <p key={i} style={{ fontSize: 13.5, margin: '0 0 4px' }}>{l}</p>)}
      {(g.citations || []).length > 0 && <details style={{ marginTop: 4 }}><summary className="faint" style={{ cursor: 'pointer' }}>{g.citations.length} citation{g.citations.length > 1 ? 's' : ''}</summary>
        {g.citations.map((c: any, i: number) => <blockquote key={i} style={{ margin: '6px 0', padding: '6px 10px', borderLeft: '3px solid var(--blue)', background: 'var(--blue-soft)', fontSize: 12.5 }}>
          <span className="faint">{c.source}</span><div>“{c.quote}”</div></blockquote>)}</details>}
    </div>
  )
}

export function WriteGuidanceModal({ g, onClose }: { g: any; onClose: () => void }) {
  const { data: passages } = useApi(`/obligations/${g.obligation_id}/passages${g.asset_id ? `?asset_id=${g.asset_id}` : ''}`)
  const { run, error, busy } = useAction()
  const [picked, setPicked] = useState<number[]>([])
  const [text, setText] = useState('')
  const [role, setRole] = useState(g.topic === 'legal' ? 'legal' : 'compliance')
  const toggle = (i: number) => setPicked(picked.includes(i) ? picked.filter((x) => x !== i) : [...picked, i])
  const go = async () => { if (await run(async () => {
    await api.post(`/obligations/${g.obligation_id}/guidance/write`, { text, department: g.department, asset_id: g.asset_id, author_role: role,
      citations: picked.map((i) => ({ source: passages[i].id, quote: passages[i].text })) })
    refreshAll() })) onClose() }
  return (
    <Modal title={`Write guidance for ${g.department || g.asset || 'the owner'}`} sub="The AI draft was held back, so Compliance or Legal must write this guidance by hand. Quote the source word for word and stick closely to its wording. You can't write guidance addressed to yourself." onClose={onClose} wide>
      {g.reason && <div className="note fail" style={{ marginBottom: 8 }}><b>Why the draft was held back</b><p style={{ fontSize: 13 }}>{g.reason}</p></div>}
      <label>Passages to quote (pick at least one)</label>
      <div style={{ maxHeight: 200, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 6, padding: 6 }}>
        {(passages || []).map((p: any, i: number) => (
          <label key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '4px 0', color: 'var(--ink)' }}>
            <input type="checkbox" style={{ width: 'auto', marginTop: 3 }} checked={picked.includes(i)} onChange={() => toggle(i)} />
            <span><span className="faint">{p.id}</span><br />{p.text}</span></label>))}</div>
      <label>Guidance</label><textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="What the owner must do, using the source's own words" />
      <button className="btn sm" style={{ marginTop: 6 }} disabled={!picked.length} onClick={() => setText(picked.map((i) => passages[i].text).join(' '))}>Fill in from the selected quotes</button>
      <label>Written by</label><select value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="compliance">Compliance</option><option value="legal">Legal</option><option value="cco">Chief Compliance Officer</option></select>
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy || !picked.length || !text.trim()} onClick={go}>Send guidance</button></div>
    </Modal>
  )
}

function RejectModal({ g, onClose }: { g: any; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [r, setR] = useState('')
  return (
    <Modal title="Reject guidance" sub={`Recorded under ${g.approver}'s name. Rejected guidance is not sent; someone will need to write a replacement.`} onClose={onClose}>
      <label>Reason</label><textarea rows={3} value={r} onChange={(e) => setR(e.target.value)} />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger-solid" disabled={busy || !r.trim()} onClick={async () => { if (await run(async () => { await api.post(`/guidance/${g.id}/reject`, { reason: r }); refreshAll() })) onClose() }}>Reject</button></div>
    </Modal>
  )
}

const For = ({ g }: { g: any }) => (
  <><Link to={`/acknowledgements/${g.obligation_id}`}><b>{g.obligation}</b></Link>
    <div className="faint">{g.department ? `for ${g.department} · ${g.recipient || 'no head'}` : g.asset ? `for ${g.asset} · ${g.recipient || 'no owner'}`
      : 'general fix summary · shown on the alert page, not sent to anyone'}
      {g.topic_label ? ` · ${g.topic_label}` : g.asset ? ' · security alert' : ''}</div></>
)

export default function Guidance() {
  const { data: all, error } = useApi('/guidance')
  const { run, error: aerr, busy } = useAction()
  const [dlg, setDlg] = useState<null | { kind: 'write' | 'reject'; g: any }>(null)
  const [note, setNote] = useState('')
  if (!all) return <Loading error={error} />
  const pending = all.filter((g: any) => g.status === 'pending_approval')
  const held = all.filter((g: any) => g.status === 'withheld' || g.status === 'rejected')
  const released = all.filter((g: any) => g.status === 'released')
  const act = (path: string) => run(async () => { await api.post(path); refreshAll() })
  const retryAll = () => run(async () => {
    const r = await api.post('/guidance/redraft-held')
    setNote(`${r.sent} sent, ${r.waiting} waiting for approval, ${r.still_held} still held back${r.skipped.length ? `, ${r.skipped.length} skipped` : ''}.`)
    refreshAll()
  })
  return (
    <>
      <p className="muted" style={{ margin: '14px 0 0', maxWidth: 820 }}>Guidance is the short note telling an owner what to do. For a security alert it is filled in from a fixed template using the alert and the system's own record, with no AI, and goes straight to the owner. For a changed law, policy or contract clause the AI drafts it, and it waits for its approver (Legal for legal clauses, Compliance for the rest), who can never be the person receiving it. Every quote is checked against the original, and guidance never decides whether something applies, accepts risk or closes a task; people do.</p>
      <div className="grid g2">
        <div className="card" style={{ borderColor: pending.length ? 'var(--blue)' : undefined }}><h2>Waiting for approval ({pending.length})</h2>
          {pending.map((g: any) => (
            <div key={g.id} style={{ borderTop: '1px solid var(--line)', padding: '10px 0' }}>
              <For g={g} /><GuidanceText g={g} />
              <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                <button className="btn sm primary" disabled={busy} onClick={() => act(`/guidance/${g.id}/approve`)}>Approve as {g.approver}</button>
                <button className="btn sm" onClick={() => setDlg({ kind: 'reject', g })}>Reject</button></div></div>))}
          {!pending.length && <p className="muted" style={{ marginTop: 6 }}>Nothing waiting. Guidance is drafted when a regulation clause is approved on the Acknowledgements page.</p>}</div>
        <div className="card" style={{ borderColor: held.length ? 'var(--red-line)' : undefined }}>
          <div className="sec" style={{ marginBottom: 0 }}><h2>Held back ({held.length})</h2>
            {held.length > 0 && <button className="btn sm" disabled={busy} onClick={retryAll}>Try all again</button>}</div>
          <p className="muted">Drafts that misquoted a source, or whose sources disagree. They are not sent to anyone until the AI gets them right on another try, or Compliance writes them by hand.</p>
          {note && <p className="faint" style={{ marginTop: 4 }}>{note}</p>}
          {held.map((g: any) => (
            <div key={g.id} style={{ borderTop: '1px solid var(--line)', padding: '10px 0' }}>
              <For g={g} /><div style={{ fontSize: 13, marginTop: 4 }}><Badge kind="over">{STATUS[g.status][1]}</Badge> {g.reason}</div>
              <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                <button className="btn sm" disabled={busy} onClick={() => act(`/guidance/${g.id}/redraft`)}>Try again</button>
                {(g.department || g.asset) && <button className="btn sm primary" onClick={() => setDlg({ kind: 'write', g })}>Write guidance</button>}</div></div>))}
          {!held.length && <p className="muted" style={{ marginTop: 6 }}>No drafts have been held back.</p>}</div>
      </div>
      <div className="card" style={{ marginTop: 16 }}><h2>Sent ({released.length})</h2>
        <table><thead><tr><th>Guidance</th><th>Approved by</th><th>Written by</th></tr></thead><tbody>
          {released.map((g: any) => (
            <tr key={g.id}><td><For g={g} /><GuidanceText g={g} /></td>
              <td>{g.asset && !g.human_authored ? <span className="faint">sent straight away (security alert)</span> : <>{g.approver}<div className="faint">{fmt(g.approved_at)}</div></>}</td>
              <td>{g.human_authored ? <Badge kind="cd">By hand</Badge> : (g.engine || '').startsWith('template') ? <span className="faint">fixed template</span> : <span className="faint">AI ({g.engine})</span>}</td></tr>))}
          {!released.length && <tr><td colSpan={3} className="muted">Nothing sent yet.</td></tr>}</tbody></table></div>
      {aerr && <p className="err">{aerr}</p>}
      {dlg?.kind === 'write' && <WriteGuidanceModal g={dlg.g} onClose={() => setDlg(null)} />}
      {dlg?.kind === 'reject' && <RejectModal g={dlg.g} onClose={() => setDlg(null)} />}
    </>
  )
}
