import { Fragment, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, Loading, Modal, useAction } from '../ui'

const fileUrl = (id: string) => '/api/documents/' + id.split('/').map(encodeURIComponent).join('/')
const STATUS: Record<string, [string, string]> = { overdue: ['over', 'Overdue'], due_soon: ['cd', 'Due soon'], ok: ['info', 'On track'] }

function Record({ id }: { id: string }) {
  const { data: r, error } = useApi(`/records/${id}`)
  if (!r) return <Loading error={error} />
  return (
    <div style={{ padding: '18px 22px' }}>
      <div className="mono faint">{r.subtitle}</div>
      <h2 style={{ fontSize: 20, margin: '4px 0 12px' }}>{r.title}</h2>
      <dl className="kv">{r.fields.map(([k, v]: [string, string]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
      {r.checks && <table style={{ marginTop: 14 }}><thead><tr><th>Check</th><th>Claim requires</th><th>Evidence recorded</th><th>Result</th></tr></thead>
        <tbody>{r.checks.map((c: any) => (
          <tr key={c.check} className={c.pass ? '' : 'bad'}><td><b>{c.check}</b></td><td>{c.requires}</td><td className="mono">{c.recorded}</td>
            <td className={c.pass ? 'pass' : 'fail'}>{c.pass ? '✓ Pass' : '✕ Fail'}</td></tr>))}</tbody></table>}
      {r.text && <pre className="mono" style={{ whiteSpace: 'pre-wrap', background: 'var(--bg, #f6f6f3)', padding: 14, borderRadius: 8, marginTop: 14 }}>{r.text}</pre>}
    </div>
  )
}

function PeopleSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data: people } = useApi('/people')
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}><option value="">Choose a person…</option>
      {(people || []).filter((p: any) => p.active).map((p: any) => <option key={p.id} value={p.id}>{p.name}{p.title ? ` · ${p.title}` : ''}</option>)}</select>
  )
}

function UploadModal({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const { run, error, busy } = useAction()
  const [file, setFile] = useState<File | null>(null)
  const [remind, setRemind] = useState(false)
  const [owner, setOwner] = useState('')
  const [days, setDays] = useState('90')
  const [note, setNote] = useState('')
  const go = async () => {
    const ok = await run(async () => {
      const r = await api.upload(`/documents/upload?filename=${encodeURIComponent(file!.name)}`, file!)
      if (remind) await api.post('/reminders', { document: r.name, owner_id: Number(owner), every_days: Number(days) })
      refreshAll(); onDone(r.id)
      setNote(r.replaced ? 'Replaced the earlier version (archived).' : 'Uploaded.')
    })
    if (ok) onClose()
  }
  return (
    <Modal title="Upload document" sub="PDF, text or Markdown, up to 20 MB. Uploading a file with the same name replaces it and keeps the old one in docs/archive." onClose={onClose}>
      <label>File</label><input type="file" accept=".pdf,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12 }}>
        <input type="checkbox" style={{ width: 'auto' }} checked={remind} onChange={(e) => setRemind(e.target.checked)} />
        Remind someone to upload an updated version</label>
      {remind && <>
        <label>Who is reminded</label><PeopleSelect value={owner} onChange={setOwner} />
        <label>Every (days)</label><input type="number" min={1} value={days} onChange={(e) => setDays(e.target.value)} list="cycles" />
        <datalist id="cycles"><option value="30" /><option value="90" /><option value="180" /><option value="365" /></datalist></>}
      {error && <p className="err">{error}</p>}{note && <p className="muted">{note}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !file || (remind && !owner)} onClick={go}>Upload</button></div>
    </Modal>
  )
}

function ReminderModal({ docs, onClose }: { docs: any[]; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [doc, setDoc] = useState('')
  const [owner, setOwner] = useState('')
  const [days, setDays] = useState('90')
  const [first, setFirst] = useState('')
  const go = async () => { if (await run(async () => { await api.post('/reminders', { document: doc, owner_id: Number(owner), every_days: Number(days), first_due: first ? new Date(first).toISOString() : null }); refreshAll() })) onClose() }
  return (
    <Modal title="Set a reminder" sub="The named person is notified, and a finding is raised, when an updated version has not been uploaded by the due date. Uploading the document resets the clock." onClose={onClose}>
      <label>Document</label>
      <input list="doc-titles" value={doc} onChange={(e) => setDoc(e.target.value)} placeholder="Choose or type a document title" />
      <datalist id="doc-titles">{docs.filter((d) => d.group === 'Documents').map((d) => <option key={d.id} value={d.name} />)}</datalist>
      <label>Who is reminded</label><PeopleSelect value={owner} onChange={setOwner} />
      <label>Then every (days)</label><input type="number" min={1} value={days} onChange={(e) => setDays(e.target.value)} />
      <label>First update due (optional, defaults to one cycle from now)</label>
      <input type="datetime-local" value={first} onChange={(e) => setFirst(e.target.value)} />
      <p className="faint">Pick a time a minute or two ahead to see the reminder fire during a demo. The check runs every minute, or press Check for updates.</p>
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !doc.trim() || !owner} onClick={go}>Set reminder</button></div>
    </Modal>
  )
}

function Reminders({ docs }: { docs: any[] }) {
  const { data: rs, error } = useApi('/reminders')
  const [adding, setAdding] = useState(false)
  const { run, error: derr, busy } = useAction()
  const [checked, setChecked] = useState<string | null>(null)
  const check = () => run(async () => {
    const r = await api.post('/sweep')
    refreshAll()
    setChecked(r.documents_due?.length ? `Reminders sent for: ${r.documents_due.join(', ')}.` : 'Checked. Nothing newly due.')
  })
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="sec"><div><h2>Update reminders</h2>
        <p className="muted">The named person is reminded when a document is overdue for an update. Set a first due date a few minutes ahead to watch one come due.</p></div>
        <div style={{ display: 'flex', gap: 8 }}><button className="btn" disabled={busy} onClick={check}>Check for updates</button>
          <button className="btn" onClick={() => setAdding(true)}>Set reminder</button></div></div>
      {checked && <p className="muted">{checked}</p>}
      {!rs ? <Loading error={error} /> : (
        <table><thead><tr><th>Document</th><th>Reminds</th><th>Cycle</th><th>Last updated</th><th>Next due</th><th>Status</th><th></th></tr></thead><tbody>
          {rs.map((r: any) => (
            <tr key={r.id}><td><b>{r.document}</b>{!r.has_document && <div className="faint">not uploaded yet</div>}</td><td>{r.owner}</td><td>every {r.every_days} days</td>
              <td>{fmt(r.last_updated_at)}</td><td>{fmt(r.due_at)}<div className="faint">{r.days_left < 0 ? `${Math.abs(r.days_left).toFixed(0)} days late` : `in ${r.days_left.toFixed(0)} days`}</div></td>
              <td><Badge kind={STATUS[r.status][0]}>{STATUS[r.status][1]}</Badge></td>
              <td><button className="btn sm danger" onClick={() => run(async () => { await api.del(`/reminders/${r.id}`); refreshAll() })}>Remove</button></td></tr>))}
          {!rs.length && <tr><td colSpan={7} className="muted">No reminders yet.</td></tr>}</tbody></table>)}
      {derr && <p className="err">{derr}</p>}
      {adding && <ReminderModal docs={docs} onClose={() => setAdding(false)} />}
    </div>
  )
}

export default function Documents() {
  const { data: files, error } = useApi('/documents')
  const { data: recs } = useApi('/records')
  const [params, setParams] = useSearchParams()
  const [up, setUp] = useState(false)
  if (!files) return <Loading error={error} />
  const items = [...files.map((d: any) => ({ ...d, file: true })), ...(recs || [])]
  const cur = items.find((d: any) => d.id === params.get('doc')) || items[0]
  const groups = [...new Set(items.map((d: any) => d.group))] as string[]
  return (
    <>
      <div className="head"><div><h1>Documents</h1>
        <p className="muted">Uploaded documents, plus the in-app records: source texts, advisories and submitted evidence.</p></div>
        <button className="btn primary" onClick={() => setUp(true)}>Upload document</button></div>
      <Reminders docs={items} />
      {!cur ? <div className="card" style={{ marginTop: 16 }}>Nothing to show.</div> : (
        <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, marginTop: 16, alignItems: 'start' }}>
          <div className="card">
            {groups.map((g) => (
              <div key={g} style={{ marginBottom: 12 }}>
                <div className="faint" style={{ marginBottom: 6 }}>{g}</div>
                {items.filter((d: any) => d.group === g).map((d: any) => (
                  <button key={d.id} className={`btn block ${d.id === cur.id ? 'primary' : ''}`} style={{ marginBottom: 6, textAlign: 'left', whiteSpace: 'normal', overflowWrap: 'anywhere' }}
                    onClick={() => setParams({ doc: d.id })}>{d.name}{d.ext ? <span style={{ opacity: 0.6 }}> .{d.ext}</span> : ''}</button>))}
              </div>))}
          </div>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            {cur.file ? <>
              <div className="sec" style={{ padding: '10px 14px' }}><b>{cur.name}</b>
                <a href={fileUrl(cur.id)} target="_blank" rel="noreferrer">Open in new tab</a></div>
              <iframe key={cur.id} title={cur.name} src={fileUrl(cur.id)} style={{ width: '100%', height: 'calc(100vh - 220px)', border: 0 }} />
            </> : <Record key={cur.id} id={cur.id} />}
          </div>
        </div>)}
      {up && <UploadModal onClose={() => setUp(false)} onDone={(id) => setParams({ doc: id })} />}
    </>
  )
}
