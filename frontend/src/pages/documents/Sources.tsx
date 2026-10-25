import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmt, fmtDay, refreshAll, useApi } from '../../api'
import { Loading, Modal, useAction } from '../../ui'
import { FeedBadge, KIND_LABEL, PeopleSelect, ReviewBadge, StatusBadge } from './common'
import { NewVersionModal } from './NewVersion'

const JURISDICTIONS = ['US federal', 'US state', 'UK', 'EU', 'Internal', 'Global']

function ProposeModal({ onClose }: { onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ name: '', kind: 'regulator', authority: '', jurisdiction: 'US federal', url: '', intake: 'manual', owner_id: '', review_days: '90', version: '' })
  const [file, setFile] = useState<File | null>(null)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  const feedable = f.kind === 'advisory' || f.kind === 'vendor'
  const manual = !feedable || f.intake === 'manual'
  const go = async () => { if (await run(async () => {
    const doc = manual && file ? (await api.upload(`/documents/upload?filename=${encodeURIComponent(file.name)}`, file)).id : null
    await api.post('/sources', { ...f, owner_id: f.owner_id ? Number(f.owner_id) : null, review_days: manual ? Number(f.review_days) : null,
      intake: feedable ? f.intake : 'manual', url: f.url || null, authority: f.authority || null, document: doc, version: doc ? f.version || null : null })
    refreshAll() })) onClose() }
  return (
    <Modal title="Suggest a new source" sub="The Chief Compliance Officer has to approve it before it can be used. You can't approve a source you suggested." onClose={onClose}>
      <label>Name</label><input value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="NYDFS Cybersecurity Regulation" />
      <div className="grid g2" style={{ marginTop: 0, gap: 10 }}>
        <div><label>Type</label><select value={f.kind} onChange={(e) => set('kind', e.target.value)}>
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div><label>Jurisdiction</label><select value={f.jurisdiction} onChange={(e) => set('jurisdiction', e.target.value)}>
          {JURISDICTIONS.map((j) => <option key={j}>{j}</option>)}</select></div></div>
      <label>Issued by</label><input value={f.authority} onChange={(e) => set('authority', e.target.value)} placeholder="New York State Department of Financial Services" />
      <label>Where to find it</label><input value={f.url} onChange={(e) => set('url', e.target.value)} placeholder="https://…" />
      {feedable && <><label>How updates arrive</label><select value={f.intake} onChange={(e) => set('intake', e.target.value)}>
        <option value="feed">Automatic feed</option><option value="manual">Someone checks it by hand</option></select></>}
      {manual && <><label>Review every (days)</label><input type="number" min={1} max={730} value={f.review_days} onChange={(e) => set('review_days', e.target.value)} />
        <div className="grid g2" style={{ marginTop: 0, gap: 10 }}>
          <div><label>Its current text (optional: PDF, text or Markdown)</label><input type="file" accept=".pdf,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] || null)} /></div>
          <div><label>Version label</label><input value={f.version} onChange={(e) => set('version', e.target.value)} placeholder="1" disabled={!file} /></div></div>
        {file && <p className="faint" style={{ marginTop: 4 }}>Once the source is approved, this file becomes its first version and each clause goes to Compliance or Legal to review.</p>}</>}
      <label>Owner (the person who keeps an eye on it)</label>
      <PeopleSelect value={f.owner_id} onChange={(v) => set('owner_id', v)} placeholder="Use the default owner" />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy || !f.name.trim()} onClick={go}>Submit</button></div>
    </Modal>
  )
}

function EditModal({ s, onClose }: { s: any; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [owner, setOwner] = useState(s.owner_id ? String(s.owner_id) : '')
  const [days, setDays] = useState(String(s.review_days || ''))
  const [url, setUrl] = useState(s.url || '')
  const go = async () => { if (await run(async () => {
    await api.patch(`/sources/${s.id}`, { owner_id: owner ? Number(owner) : null, url: url || null, review_days: s.intake === 'manual' && days ? Number(days) : null })
    refreshAll() })) onClose() }
  return (
    <Modal title={`Edit ${s.name}`} sub="The owner gets a reminder when a review is due, or when the feed stops working." onClose={onClose}>
      <label>Owner</label><PeopleSelect value={owner} onChange={setOwner} />
      {s.intake === 'manual' && <><label>Review every (days)</label><input type="number" min={1} max={730} value={days} onChange={(e) => setDays(e.target.value)} /></>}
      <label>Where to find it</label><input value={url} onChange={(e) => setUrl(e.target.value)} />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy} onClick={go}>Save</button></div>
    </Modal>
  )
}

function ReasonModal({ title, sub, onSubmit, onClose }: { title: string; sub: string; onSubmit: (r: string) => Promise<unknown>; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [r, setR] = useState('')
  return (
    <Modal title={title} sub={sub} onClose={onClose}>
      <label>Reason</label><textarea rows={3} value={r} onChange={(e) => setR(e.target.value)} />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !r.trim()} onClick={async () => { if (await run(() => onSubmit(r))) onClose() }}>Confirm</button></div>
    </Modal>
  )
}

const FEED_KINDS = ['advisory', 'vendor']

export default function Sources() {
  const { data: sources, error } = useApi('/sources')
  const { run, error: aerr, busy } = useAction()
  const nav = useNavigate()
  const [dlg, setDlg] = useState<null | { kind: 'propose' | 'edit' | 'suspend' | 'version'; s?: any }>(null)
  const [note, setNote] = useState('')
  if (!sources) return <Loading error={error} />
  const feeds = sources.filter((s: any) => FEED_KINDS.includes(s.kind))
  const texts = sources.filter((s: any) => !FEED_KINDS.includes(s.kind))
  const act = (path: string, body?: unknown) => run(async () => { await api.post(path, body); refreshAll() })
  const say = (r: any) => {
    const list = Array.isArray(r) ? r : [r]
    const got = list.flatMap((x: any) => x.received.map((t: string) => `${x.source}: ${t}`))
    setNote([got.length ? got.join(' · ') : 'Nothing new.', ...list.filter((x: any) => !x.ok).map((x: any) => `${x.source}: ${x.error}`)].join(' '))
  }
  const approveCell = (s: any) => s.status !== 'approved' && (
    <button className="btn sm primary" disabled={busy} onClick={() => act(`/sources/${s.id}/approve`)}>{s.status === 'suspended' ? 'Restore (as CCO)' : 'Approve (as CCO)'}</button>)
  const editCells = (s: any) => <>
    <button className="btn sm" onClick={() => setDlg({ kind: 'edit', s })}>Edit</button>
    {s.status === 'approved' && <button className="btn sm danger" onClick={() => setDlg({ kind: 'suspend', s })}>Suspend</button>}</>
  const name = (s: any) => <>
    <b>{s.name}</b> {s.status !== 'approved' && <StatusBadge status={s.status} />}
    <div className="faint">{KIND_LABEL[s.kind] || s.kind} · {s.jurisdiction || '—'} · {s.authority || '—'}</div>
    {s.status_reason && <div className="faint red">{s.status_reason}</div>}
    {s.pending_file && <div className="faint">File attached: {s.pending_file} · becomes v{s.pending_version} when approved</div>}</>
  const open = (path: string) => (e: React.MouseEvent) => { if (!(e.target as HTMLElement).closest('button, a')) nav(path) }
  return (
    <>
      <div className="sec" style={{ marginTop: 16 }}>
        <p className="muted" style={{ maxWidth: 760 }}>Only sources on this list can create obligations. Security feeds send alerts automatically; laws, policies and contracts have an owner who reviews them regularly. Click a source to open it.</p>
        <button className="btn" onClick={() => setDlg({ kind: 'propose' })}>Suggest a source</button></div>

      <div className="card" style={{ marginTop: 10 }}>
        <div className="sec"><div><h2>Security advisories</h2>
          <p className="muted" style={{ maxWidth: 720 }}>Feeds that send security alerts about software. PoliBridge checks them every minute and matches each alert against every system's software list. Open a feed to see which systems its alerts affect.</p></div>
          <div style={{ display: 'flex', gap: 8, whiteSpace: 'nowrap' }}>
            <button className="btn" disabled={busy} onClick={() => run(async () => { say(await api.post('/feeds/poll')); refreshAll() })}>Check feeds now</button>
            <button className="btn primary" disabled={busy} onClick={() => run(async () => { say(await api.post('/feeds/release-next')); refreshAll() })}>Send next demo alert</button></div></div>
        <p className="faint">Demo shortcut: the feeds are simulated, so Send next demo alert delivers the next queued alert now instead of waiting.</p>
        {note && <p className="muted" style={{ marginTop: 4 }}>{note}</p>}
        <table style={{ marginTop: 6 }}><thead><tr><th>Feed</th><th>Status</th><th>Owner</th><th>Alerts</th><th style={{ width: 200 }}></th></tr></thead><tbody>
          {feeds.map((s: any) => (
            <tr key={s.id} className={s.feed?.state === 'silent' ? 'bad' : ''} style={{ cursor: 'pointer' }} onClick={open(`/documents?tab=feed&source=${s.id}`)}>
              <td>{name(s)}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{s.intake === 'feed' ? <><FeedBadge feed={s.feed} /><div className="faint">last worked {fmt(s.feed?.last_success_at)}</div>
                {s.feed?.consecutive_failures > 0 && <div className="faint red">{s.feed.consecutive_failures} failed checks</div>}</> : <span className="faint">entered by hand</span>}</td>
              <td>{s.owner || <span className="red">nobody</span>}</td>
              <td>{s.alerts && <><b>{s.alerts.alerts}</b> alert{s.alerts.alerts === 1 ? '' : 's'}{s.alerts.new > 0 && <> · <span className="blue">{s.alerts.new} new</span></>}
                <div className="faint">{s.alerts.systems_affected} system{s.alerts.systems_affected === 1 ? '' : 's'} affected{s.alerts.awaiting_ack ? ` · ${s.alerts.awaiting_ack} waiting for owners` : ''}</div>
                {s.alerts.queued > 0 && <div className="faint">{s.alerts.queued} demo alert{s.alerts.queued === 1 ? '' : 's'} queued</div>}</>}</td>
              <td style={{ textAlign: 'right' }}>{approveCell(s)}
                <Link className="btn sm primary" style={{ textDecoration: 'none' }} to={`/documents?tab=feed&source=${s.id}`}>Open</Link>{' '}{editCells(s)}</td>
            </tr>))}
          {!feeds.length && <tr><td colSpan={5} className="muted">No security feeds yet.</td></tr>}</tbody></table>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><div><h2>Source texts</h2>
          <p className="muted" style={{ maxWidth: 720 }}>Laws, policies and contracts. Each is one document that gets amended over time, so it has versions. Open one to compare its versions clause by clause.</p></div></div>
        <table><thead><tr><th>Source</th><th>Reviewed</th><th>Owner</th><th>Status</th><th>Obligations</th><th style={{ width: 230 }}></th></tr></thead><tbody>
          {texts.map((s: any) => (
            <tr key={s.id} className={s.review.status === 'overdue' ? 'bad' : ''} style={{ cursor: 'pointer' }} onClick={open(`/documents?tab=changes&source=${s.id}`)}>
              <td>{name(s)}</td>
              <td style={{ whiteSpace: 'nowrap' }}>Every {s.review_days} days<div className="faint">{s.latest_version ? `now v${s.latest_version}` : 'no version yet'}</div></td>
              <td>{s.owner || <span className="red">nobody</span>}</td>
              <td style={{ whiteSpace: 'nowrap' }}><ReviewBadge review={s.review} />
                <div className="faint">{s.review.last_checked ? `reviewed ${fmtDay(s.review.last_checked, true)}` : 'never reviewed'}</div>
                {s.review.next_due && <div className="faint">due {fmtDay(s.review.next_due, true)}</div>}</td>
              <td>{s.obligations.approved} approved<div className="faint">{s.obligations.candidate} to review · {s.obligations.superseded} superseded</div></td>
              <td style={{ textAlign: 'right' }}>{approveCell(s)}
                {s.status === 'approved' && <div style={{ marginBottom: 4, whiteSpace: 'nowrap' }}><button className="btn sm primary" disabled={busy} onClick={() => act(`/sources/${s.id}/check`, {})} title="Confirm you checked this source and nothing has changed">Mark as reviewed</button>{' '}
                  <button className="btn sm" onClick={() => setDlg({ kind: 'version', s })}>Upload new version</button></div>}
                <div style={{ whiteSpace: 'nowrap' }}><Link className="btn sm" style={{ textDecoration: 'none' }} to={`/documents?tab=changes&source=${s.id}`}>Open</Link>{' '}{editCells(s)}</div></td>
            </tr>))}
          {!texts.length && <tr><td colSpan={6} className="muted">No source texts yet.</td></tr>}</tbody></table>
        {aerr && <p className="err">{aerr}</p>}
        <p className="faint" style={{ marginTop: 10 }}>Approvals are recorded under the CCO's name and reviews under the owner's name. The audit log notes that Risk & Compliance clicked the button.</p>
      </div>
      {dlg?.kind === 'propose' && <ProposeModal onClose={() => setDlg(null)} />}
      {dlg?.kind === 'edit' && <EditModal s={dlg.s} onClose={() => setDlg(null)} />}
      {dlg?.kind === 'version' && <NewVersionModal sourceId={dlg.s?.id} onClose={() => setDlg(null)} />}
      {dlg?.kind === 'suspend' && <ReasonModal title={`Suspend ${dlg.s.name}`} sub="While suspended, this source can't create obligations or send alerts. Approve it again to restore it."
        onSubmit={async (r) => { await api.post(`/sources/${dlg.s.id}/suspend`, { reason: r }); refreshAll() }} onClose={() => setDlg(null)} />}
    </>
  )
}
