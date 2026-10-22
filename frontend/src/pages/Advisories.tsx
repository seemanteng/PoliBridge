import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmt, getUser, refreshAll, useApi } from '../api'
import { Badge, ImpactBadge, Loading, Modal, StateBadge, useAction } from '../ui'
import { has } from '../filterUtils'

const LABEL: Record<string, string> = { applies: 'Applies', does_not_apply: 'Does Not Apply', cannot_determine: 'Cannot Determine' }
const LABEL_KIND: Record<string, string> = { applies: 'applies', does_not_apply: 'dna', cannot_determine: 'cd' }
const LabelBadge = ({ label }: { label: string }) => <Badge kind={LABEL_KIND[label]}>{LABEL[label] || label}</Badge>

type Stage = 'review' | 'waiting' | 'done'
const STAGES: { key: Stage; n: string; title: string; hint: string }[] = [
  { key: 'review', n: '1', title: 'Needs review', hint: 'Compliance or Legal decide what a changed clause means for each department' },
  { key: 'waiting', n: '2', title: 'Waiting for owners', hint: 'Each owner confirms they have seen their label' },
  { key: 'done', n: '3', title: 'Complete', hint: 'Everyone has signed off' },
]

// One item per advisory, and one per document (its clauses sit inside it), each with a stage.
type Clause = { ob: any; rows: any[] }
function buildItems(rows: any[], obs: any[], sources: any[]) {
  const items: any[] = []
  const done = (rs: any[]) => { const need = rs.filter((r) => r.status !== 'not_required'); return { need: need.length, signed: need.filter((r) => r.status === 'signed').length } }
  const adv = new Map<number, any[]>()
  const docs = new Map<number, Clause[]>()
  for (const o of obs.filter((x) => x.status === 'candidate')) docs.set(o.source_id, [...(docs.get(o.source_id) || []), { ob: o, rows: [] }])
  for (const r of rows) {
    const ob = obs.find((o) => o.id === r.obligation_id)
    if (ob?.source_id && ob.kind !== 'advisory') {
      const cl = docs.get(ob.source_id) || []
      const hit = cl.find((c) => c.ob.id === ob.id)
      if (hit) hit.rows.push(r); else cl.push({ ob, rows: [r] })
      docs.set(ob.source_id, cl)
    } else adv.set(r.obligation_id, [...(adv.get(r.obligation_id) || []), r])
  }
  for (const [id, rs] of adv) items.push({ key: `a${id}`, id, type: 'advisory', kind: 'Security advisory', title: rs[0].title, rows: rs, candidates: 0, ...done(rs) })
  for (const [sid, clauses] of docs) {
    const rs = clauses.flatMap((c) => c.rows)
    items.push({ key: `d${sid}`, id: clauses[0].ob.id, type: 'document', kind: 'Document', title: sources.find((x) => x.id === sid)?.name || 'Document',
      clauses, rows: rs, candidates: clauses.filter((c) => c.ob.status === 'candidate').length, ...done(rs) })
  }
  return items.map((it) => {
    const stage: Stage = it.candidates ? 'review' : it.need > it.signed ? 'waiting' : 'done'
    return { ...it, stage, overdue: it.rows.some((r: any) => r.status === 'overdue') }
  }).sort((a, b) => ['waiting', 'review', 'done'].indexOf(a.stage) - ['waiting', 'review', 'done'].indexOf(b.stage) || b.id - a.id)
}

function AddNewModal({ onClose, onPick }: { onClose: () => void; onPick: (k: 'advisory' | 'document') => void }) {
  const opt = (k: 'advisory' | 'document', title: string, text: string) => (
    <button className="btn block" style={{ textAlign: 'left', marginBottom: 10, padding: '12px 14px' }} onClick={() => onPick(k)}>
      <b>{title}</b><div className="muted" style={{ marginTop: 2 }}>{text}</div></button>)
  return (
    <Modal title="What is new?" sub="Either one creates items below for the affected systems or departments." onClose={onClose}>
      {opt('advisory', 'A security advisory', 'A vulnerability notice such as a CVE. Fetch the details from NVD, and every system is matched and labelled at once.')}
      {opt('document', 'A document', 'A regulation or policy that changed. Pick an uploaded file and PoliBridge shows which clauses are new or different.')}
    </Modal>
  )
}

export function AdvisoryList() {
  const { data: rows, error } = useApi('/acknowledgements')
  const { data: obs } = useApi('/obligations')
  const { data: people } = useApi('/people')
  const { data: sources } = useApi('/sources')
  const [adding, setAdding] = useState<null | 'choose' | 'advisory' | 'document'>(null)
  const [rev, setRev] = useState<any>(null)
  const [stage, setStage] = useState<Stage | null>(null)
  const [q, setQ] = useState('')
  const { run, error: aerr } = useAction()
  if (!rows || !obs || !people || !sources) return <Loading error={error} />
  const me = people.find((p: any) => p.directory_id === getUser())
  const items = buildItems(rows, obs, sources)
  // step 1 counts clauses awaiting a decision; steps 2 and 3 count individual sign-offs
  const count = (s: Stage) => s === 'review' ? items.reduce((n, i) => n + i.candidates, 0)
    : rows.filter((r: any) => (s === 'done' ? r.status === 'signed' : r.status === 'pending' || r.status === 'overdue')).length
  const shown = items.filter((i) => (!stage || i.stage === stage) && has(q, i.title, i.kind, i.rows.map((r: any) => `${r.scope} ${r.owner}`).join(' '), (i.clauses || []).map((c: Clause) => c.ob.title).join(' ')))
  const sign = (r: any) => run(async () => {
    if (r.task_id) await api.post(`/tasks/${r.task_id}/acknowledge`)
    else await api.post(`/obligations/${r.obligation_id}/signoff`, { department: r.department_key })
    refreshAll()
  })
  const mine = rows.filter((r: any) => r.owner === me?.name && (r.status === 'pending' || r.status === 'overdue'))
  const canReview = me && (me.role === 'compliance' || me.role === 'legal')
  const toReview: any[] = canReview ? obs.filter((o: any) => o.status === 'candidate') : []

  const cell = (r: any) => (
    <tr key={r.key}>
      <td>{r.scope}{r.kind === 'Advisory' && <div className="faint">{r.department}</div>}</td>
      <td>{r.owner ? <span style={r.owner_active ? {} : { color: 'var(--red)', textDecoration: 'line-through' }}>{r.owner}</span> : <span className="red">no owner</span>}</td>
      <td><LabelBadge label={r.label} /></td>
      <td>{r.status === 'signed' ? <><Badge kind="info">✓ Signed off</Badge><div className="faint">{r.signed_by} · {fmt(r.signed_at)}</div></>
        : r.status === 'overdue' ? <Badge kind="over">Overdue</Badge>
        : r.status === 'pending' ? <Badge kind="cd">Waiting</Badge>
        : <span className="muted">Nothing to do</span>}</td>
      <td style={{ whiteSpace: 'nowrap' }}>{(r.status === 'pending' || r.status === 'overdue') && <button className="btn sm primary" onClick={() => sign(r)}>Sign off</button>}</td>
    </tr>)

  return (
    <>
      <div className="head">
        <div><h1>Acknowledgements</h1>
          <p className="muted" style={{ maxWidth: 720 }}>When something new comes in, every affected system or department gets a label, and its owner confirms they have seen it. Anything left unconfirmed escalates on the Alerts page.</p></div>
        <button className="btn primary" onClick={() => setAdding('choose')}>Add something new</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginTop: 16 }}>
        {STAGES.map((s) => (
          <button key={s.key} className="card" onClick={() => setStage(stage === s.key ? null : s.key)} aria-pressed={stage === s.key}
            style={{ textAlign: 'left', cursor: 'pointer', borderColor: stage === s.key ? 'var(--blue)' : undefined, boxShadow: stage === s.key ? '0 0 0 1px var(--blue)' : undefined }}>
            <div className="faint">STEP {s.n}</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}><span style={{ fontSize: 28, fontWeight: 500 }}>{count(s.key)}</span><b>{s.title}</b><span className="faint">{s.key === 'review' ? 'clauses' : 'sign-offs'}</span></div>
            <div className="muted" style={{ fontSize: 12.5 }}>{s.hint}</div>
          </button>))}
      </div>

      <div className="card" style={{ marginTop: 14, borderColor: (mine.length || toReview.length) ? 'var(--blue)' : undefined }}>
        <h2>Waiting for you{me ? ` (${me.name})` : ''}</h2>
        {!mine.length && !toReview.length && <p className="muted" style={{ marginTop: 6 }}>Nothing needs you right now. To act as an owner, pick them in the sidebar user switcher.</p>}
        {toReview.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '8px 0', borderTop: '1px solid var(--line)' }}>
            <div><b>{toReview.length} clause{toReview.length > 1 ? 's' : ''} to review</b><div className="muted">Decide what each changed clause means for the departments involved.</div></div>
            <button className="btn sm primary" onClick={() => setRev(toReview[0])}>Review next clause</button></div>)}
        {mine.map((r: any) => (
          <div key={r.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '8px 0', borderTop: '1px solid var(--line)' }}>
            <div><b>{r.scope}</b> <LabelBadge label={r.label} />{r.status === 'overdue' && <> <Badge kind="over">Overdue</Badge></>}
              <div className="muted">{r.title}</div></div>
            <button className="btn sm primary" onClick={() => sign(r)}>Sign off</button></div>))}
        {aerr && <p className="err">{aerr}</p>}
      </div>

      <div className="sec" style={{ marginTop: 20 }}>
        <h2>All items{stage ? ` · ${STAGES.find((s) => s.key === stage)!.title}` : ''}</h2>
        <input style={{ width: 260 }} placeholder="Search items, systems, owners…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <p className="faint" style={{ margin: '4px 0 10px' }}>Labels: <b>Applies</b> means act. <b>Cannot Determine</b> means we lack the data, so investigate. <b>Does Not Apply</b> means confirmed unaffected.</p>

      {shown.map((i) => (
        <details key={i.key} className="card" open={i.stage === 'waiting'} style={{ marginBottom: 10 }}>
          <summary style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
            <span><Badge>{i.kind}</Badge> {i.type === 'advisory' ? <Link to={`/acknowledgements/${i.id}`}><b>{i.title}</b></Link> : <b>{i.title}</b>}
              {i.type === 'document' && <div className="faint" style={{ marginTop: 3 }}>{i.clauses.length} changed clause{i.clauses.length > 1 ? 's' : ''}</div>}</span>
            <span style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
              {i.candidates ? <><Badge kind="cd">Needs review</Badge><div className="faint">{i.candidates} clause{i.candidates > 1 ? 's' : ''} to review</div></>
                : <><Badge kind={i.stage === 'done' ? 'info' : i.overdue ? 'over' : 'cd'}>{i.stage === 'done' ? 'Complete' : i.overdue ? 'Overdue' : 'Waiting'}</Badge>
                  <div className="faint">{i.signed} of {i.need} signed off</div></>}</span>
          </summary>
          {i.type === 'advisory' ? (
            <table style={{ marginTop: 8 }}><thead><tr><th>System / department</th><th>Owner</th><th>Label</th><th>Sign-off</th><th></th></tr></thead>
              <tbody>{i.rows.map(cell)}</tbody></table>
          ) : i.clauses.map((c: Clause) => (
            <div key={c.ob.id} style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
              <Link to={`/acknowledgements/${c.ob.id}`}><b>{c.ob.source_ref || c.ob.title}</b></Link>
              <div className="faint" style={{ margin: '2px 0 6px' }}>“{(c.ob.quotation || '').slice(0, 170)}{(c.ob.quotation || '').length > 170 ? '…' : ''}”</div>
              {c.ob.status === 'candidate' ? (
                <div>{(c.ob.departments || []).map((d: any) => <div key={d.department}><b>{d.department}</b> <span className="faint">owner {d.owner || '—'} · {d.reason}</span></div>)}
                  {canReview ? <p style={{ marginTop: 6 }}><button className="btn sm primary" onClick={() => setRev(c.ob)}>Review</button></p>
                    : <p className="faint" style={{ marginTop: 6 }}>Waiting for Compliance or Legal to review.</p>}</div>
              ) : (
                <table><thead><tr><th>Department</th><th>Owner</th><th>Label</th><th>Sign-off</th><th></th></tr></thead><tbody>{c.rows.map(cell)}</tbody></table>)}
            </div>))}
        </details>))}
      {!shown.length && <div className="card"><p className="muted">{items.length ? 'Nothing matches.' : 'Nothing here yet. Click “Add something new” to bring in a security advisory or a changed document.'}</p></div>}
      <p className="faint" style={{ marginTop: 8 }}>Only the named owner can sign off, so switch user in the sidebar to sign off as them.</p>

      {adding === 'choose' && <AddNewModal onClose={() => setAdding(null)} onPick={setAdding} />}
      {adding === 'advisory' && <IngestModal onClose={() => setAdding(null)} />}
      {adding === 'document' && <IngestDocModal onClose={() => setAdding(null)} />}
      {rev && <ReviewModal o={rev} onClose={() => setRev(null)} />}
    </>
  )
}

function ReviewModal({ o, onClose }: { o: any; onClose: () => void }) {
  const { run, error, busy } = useAction()
  const [dec, setDec] = useState<Record<string, { label: string; note: string }>>(
    Object.fromEntries((o.departments || []).map((d: any) => [d.department, { label: d.label, note: d.note || '' }])))
  const [action, setAction] = useState(o.required_action && !o.required_action.startsWith('To be confirmed') ? o.required_action : '')
  const set = (d: string, k: 'label' | 'note', v: string) => setDec({ ...dec, [d]: { ...dec[d], [k]: v } })
  const go = (approve: boolean) => run(async () => {
    await api.post(`/obligations/${o.id}/decide`, { approve, required_action: action || null,
      departments: approve ? Object.entries(dec).map(([department, v]) => ({ department, label: v.label, note: v.note || null })) : null })
    refreshAll(); onClose()
  }).then((ok) => ok)
  return (
    <Modal title={o.title} sub="Applicability of a regulatory clause is a decision for Compliance or Legal, not the system." onClose={onClose}>
      {o.previous_quotation && <><label>Previous wording</label><div className="note" style={{ background: '#f6f6f3' }}>{o.previous_quotation}</div></>}
      <label>{o.previous_quotation ? 'New wording' : 'New clause'}</label><div className="note ok">{o.quotation}</div>
      <label style={{ marginTop: 12 }}>Departments involved</label>
      <table><thead><tr><th>Department · owner</th><th>Why involved</th><th>Decision</th></tr></thead><tbody>
        {(o.departments || []).map((d: any) => (
          <tr key={d.department}><td><b>{d.department}</b><div className="faint">owner {d.owner || '—'}</div></td>
            <td className="muted" style={{ maxWidth: 180 }}>{d.reason}</td>
            <td><select value={dec[d.department].label} onChange={(e) => set(d.department, 'label', e.target.value)}>
              {Object.entries(LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              {dec[d.department].label === 'does_not_apply' &&
                <input style={{ marginTop: 6 }} placeholder="Reason (required)" value={dec[d.department].note} onChange={(e) => set(d.department, 'note', e.target.value)} />}</td></tr>))}
      </tbody></table>
      <p className="faint">Anything left as Cannot Determine stays open for investigation. It never defaults to Does Not Apply.</p>
      <label>Required action (optional)</label><input value={action} onChange={(e) => setAction(e.target.value)} placeholder="What each owner must do" />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger" disabled={busy} onClick={() => go(false)}>Reject</button>
        <button className="btn primary" disabled={busy} onClick={() => go(true)}>Approve obligation</button></div>
    </Modal>
  )
}

function IngestDocModal({ onClose }: { onClose: () => void }) {
  const { data: sources } = useApi('/sources')
  const { data: docs } = useApi('/documents')
  const { run, error, busy } = useAction()
  const [sid, setSid] = useState('')
  const [doc, setDoc] = useState('')
  const [version, setVersion] = useState('')
  const [res, setRes] = useState<any>(null)
  const regs = (sources || []).filter((s: any) => s.kind !== 'advisory')
  const go = () => run(async () => {
    const name = doc.split('/').slice(1).join('/')
    const t = await api.get(`/documents/docs/${encodeURIComponent(name)}/text`)
    setRes(await api.post(`/sources/${sid}/versions`, { version: version || t.name, text: t.text }))
    refreshAll()
  })
  if (res) return (
    <Modal title="Summary of changes" sub={`${res.source} · version ${res.version}`} onClose={onClose}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '8px 0' }}>
        <Badge kind="info">{res.changes.filter((c: any) => c.kind === 'changed').length} changed</Badge>
        <Badge>{res.changes.filter((c: any) => c.kind === 'new').length} new</Badge>
        <Badge kind="over">{res.removed.length} removed</Badge><Badge kind="dna">{res.unchanged} unchanged</Badge></div>
      <div style={{ maxHeight: 340, overflow: 'auto' }}>
        <table><thead><tr><th>Clause</th><th>Change</th><th>Departments involved</th></tr></thead><tbody>
          {res.changes.map((c: any) => (
            <tr key={c.obligation_id}><td><b className="mono">{c.ref}</b><div className="faint">{(c.new || '').slice(0, 120)}{(c.new || '').length > 120 ? '…' : ''}</div></td>
              <td><Badge kind={c.kind === 'changed' ? 'info' : ''}>{c.kind}</Badge></td><td>{c.departments.join(', ')}</td></tr>))}
          {res.removed.map((r: string) => <tr key={r}><td className="mono"><b>{r}</b></td><td><Badge kind="over">removed</Badge></td><td className="muted">no longer in the source</td></tr>)}
        </tbody></table></div>
      <p className="muted" style={{ marginTop: 8 }}>Each changed clause is now a candidate obligation. Review it to see the departments involved, who owns each, and record Applies, Does Not Apply or Cannot Determine.</p>
      <div className="actions"><button className="btn primary" onClick={onClose}>Review candidates</button></div>
    </Modal>)
  return (
    <Modal title="Ingest document" sub="Pick a source and one of your uploaded documents. PoliBridge compares it with the previous version and turns each changed clause into a candidate obligation." onClose={onClose}>
      <label>Source it updates</label>
      <select value={sid} onChange={(e) => setSid(e.target.value)}><option value="">Choose a source…</option>
        {regs.map((s: any) => <option key={s.id} value={s.id}>{s.name} ({s.kind})</option>)}</select>
      <label>Document</label>
      <select value={doc} onChange={(e) => { setDoc(e.target.value); setVersion('') }}><option value="">Choose an uploaded document…</option>
        {(docs || []).map((d: any) => <option key={d.id} value={d.id}>{d.name}.{d.ext}</option>)}</select>
      {docs && !docs.length && <p className="muted">No documents yet. Upload one on the Documents page first.</p>}
      <label>Version label (optional)</label><input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="e.g. 2022" />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !sid || !doc} onClick={go}>Compare and ingest</button></div>
    </Modal>)
}

function IngestModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate()
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ cve: 'CVE-2017-5638', product: 'struts2-core', versions: '>=2.2.3 <2.3.32, >=2.5.0 <2.5.10.1', cvss: '9.8', kev: true, fixed: '2.3.32, 2.5.10.1' })
  const submit = async () => {
    const ok = await run(async () => {
      const r = await api.post('/advisories', {
        cve: f.cve, product: f.product, affected_versions: f.versions.split(',').map((s) => s.trim()).filter(Boolean),
        cvss: f.cvss ? Number(f.cvss) : null, kev: f.kev, fixed_version: f.fixed || null, description: desc || null,
      })
      refreshAll(); nav(`/acknowledgements/${r.id}`)
    })
    if (ok) onClose()
  }
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const [cands, setCands] = useState<any[]>([])
  const [desc, setDesc] = useState('')
  const [note, setNote] = useState('')
  const pick = (c: any) => setF((p) => ({ ...p, product: c.inventory_name || c.product, versions: c.versions.join(', '), fixed: c.fixed_version || '' }))
  const fetchNvd = async () => {
    setNote('')
    await run(async () => {
      const r = await api.get(`/advisories/lookup?cve=${encodeURIComponent(f.cve.trim())}`)
      setCands(r.candidates)
      setDesc(r.description || '')
      const c = r.candidates.find((x: any) => x.inventory_name) || r.candidates[0]
      setF((p) => ({ ...p, cve: r.cve, cvss: r.cvss == null ? '' : String(r.cvss), kev: !!r.kev,
        ...(c ? { product: c.inventory_name || c.product, versions: c.versions.join(', '), fixed: c.fixed_version || '' } : {}) }))
      setNote(`Filled from NVD${r.kev == null ? ' (KEV feed unreachable, check the box yourself)' : ' and CISA KEV'}. ` +
        (c ? '' : 'No affected-product data found; enter it by hand.'))
    })
  }
  return (
    <Modal title="Ingest advisory" sub="Fetch real data from NVD and CISA KEV, or type it in. It is matched against every asset at once." onClose={onClose}>
      <label>CVE</label>
      <div style={{ display: 'flex', gap: 8 }}><input value={f.cve} onChange={(e) => set('cve', e.target.value)} />
        <button className="btn" disabled={busy} onClick={fetchNvd}>Fetch from NVD</button></div>
      {note && <p className="muted">{note}</p>}
      {cands.length > 1 && <><label>NVD lists {cands.length} affected products — pick the one in your inventories</label>
        <select onChange={(e) => pick(cands[Number(e.target.value)])} value={cands.findIndex((c) => (c.inventory_name || c.product) === f.product)}>
          {cands.map((c, i) => <option key={c.product} value={i}>{c.product}{c.inventory_name ? ` → ${c.inventory_name}` : ''}</option>)}
        </select></>}
      <label>Affected product (as recorded in component inventories)</label><input value={f.product} onChange={(e) => set('product', e.target.value)} />
      <label>Affected versions (comma separated ranges)</label><input value={f.versions} onChange={(e) => set('versions', e.target.value)} />
      <label>CVSS (leave empty if unknown — never lowers impact)</label><input value={f.cvss} onChange={(e) => set('cvss', e.target.value)} />
      <label><input type="checkbox" style={{ width: 'auto' }} checked={f.kev} onChange={(e) => set('kev', e.target.checked)} /> Listed as exploited in the wild</label>
      <label>Fixed version</label><input value={f.fixed} onChange={(e) => set('fixed', e.target.value)} />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={submit}>Ingest and match</button></div>
    </Modal>
  )
}

export function AdvisoryDetail() {
  const { id } = useParams()
  const { data: o, error } = useApi(`/obligations/${id}`)
  const [hist, setHist] = useState(false)
  const [guid, setGuid] = useState<any>(null)
  const { run, error: aerr } = useAction()
  if (!o) return <Loading error={error} />
  if (!o.estate) return (
    <>
      <div className="crumbs"><Link to="/acknowledgements">Acknowledgements</Link> / OBL-{String(o.id).padStart(4, '0')}</div>
      <div className="badges" style={{ marginTop: 8 }}><Badge>{o.kind === 'policy' ? 'Policy clause' : 'Regulatory clause'}</Badge><Badge kind={o.status === 'candidate' ? 'cd' : 'info'}>{o.status}</Badge></div>
      <h1>{o.title}</h1>
      {o.previous_quotation && <div className="card" style={{ marginTop: 16 }}><h2>Previous wording</h2><p className="muted">{o.previous_quotation}</p></div>}
      <div className="card" style={{ marginTop: 16 }}><h2>{o.previous_quotation ? 'Current wording' : 'Clause'}</h2><p>{o.quotation}</p>
        <p className="faint" style={{ marginTop: 8 }}>Required action: {o.required_action || '—'}</p></div>
      <div className="card" style={{ marginTop: 16 }}><h2>Departments involved</h2>
        <p className="muted">Decided by Compliance or Legal. Cannot Determine stays open until someone decides.</p>
        <table><thead><tr><th>Department</th><th>Owner</th><th>Why involved</th><th>Decision</th><th>Sign-off</th></tr></thead><tbody>
          {(o.departments || []).map((d: any) => (
            <tr key={d.department}><td><b>{d.department}</b></td><td>{d.owner || '—'}</td><td className="muted">{d.reason}</td>
              <td><LabelBadge label={d.label} />{d.decided_by && <div className="faint">by {d.decided_by}</div>}{d.note && <div className="faint">{d.note}</div>}</td>
              <td>{d.signed_at ? <><Badge kind="info">✓ Signed off</Badge><div className="faint">{d.signed_by} · {fmt(d.signed_at)}</div></>
                : o.status === 'candidate' ? <span className="muted">Awaiting Compliance / Legal</span>
                : <button className="btn sm primary" onClick={() => run(async () => { await api.post(`/obligations/${o.id}/signoff`, { department: d.department }); refreshAll() })}>Sign off</button>}</td></tr>))}
          {!(o.departments || []).length && <tr><td colSpan={5} className="muted">No departments recorded.</td></tr>}</tbody></table>
        {aerr && <p className="err">{aerr}</p>}</div>
    </>)
  const a = o.advisory, est = o.estate
  const t = est.timings
  const g = est.gate
  const pct = (n: number) => `${(n / Math.max(1, g.total)) * 100}%`
  const override = (row: any) => {
    const reason = prompt(`Move ${row.asset} to Cannot Determine. Reason (recorded with the change):`)
    if (reason) run(async () => { await api.post(`/obligations/${o.id}/override`, { asset_id: row.asset_id, new_label: 'cannot_determine', reason }); refreshAll() })
  }
  return (
    <>
      <div className="head"><div>
        <div className="crumbs"><Link to="/acknowledgements">Acknowledgements</Link> / ADV-{String(o.id).padStart(4, '0')}</div>
        <div className="badges" style={{ marginTop: 8 }}>{o.impact && <ImpactBadge impact={o.impact} />}<Badge kind="info">Security advisory · fast path</Badge></div>
        <h1>{a.cve} · {a.product}</h1>
        <p className="muted" style={{ maxWidth: 760 }}>Ingested automatically on {fmt(a.published_at, true)} and matched against every asset's component records. Advisories bypass the Compliance/Legal gate.</p>
      </div><button className="btn" onClick={() => setHist(true)}>Source &amp; version history</button></div>

      <div className="grid g2">
        <div className="card"><h2>Structured advisory</h2>
          <dl className="kv" style={{ marginTop: 10 }}>
            <dt>Product</dt><dd className="mono">{a.product}</dd>
            <dt>Affected</dt><dd className="mono">{a.affected_versions.join(', ')}</dd>
            <dt>Fixed in</dt><dd className="mono">{a.fixed_version || '—'}</dd>
            <dt>Severity</dt><dd>{a.cvss == null ? 'Unknown (treated conservatively)' : `CVSS ${a.cvss.toFixed(1)}`}{a.kev ? ' · exploited in the wild' : ''}</dd>
          </dl></div>
        <div className="card"><div className="sec"><h2>Why High impact</h2><span className="faint">Appendix B</span></div>
          {est.why_high.length ? est.why_high.map((w: string) => <p key={w} style={{ padding: '3px 0' }}><span className="blue">✓</span> {w}</p>) : <p className="muted">No decisive trigger.</p>}
          <div className="grid g3" style={{ marginTop: 10, borderTop: '1px solid var(--line)', paddingTop: 10, gap: 8 }}>
            <div><div className="faint">Acknowledge</div><b>{t.ack_hours ? `${t.ack_hours}h` : `${t.ack_working_days} working days`}</b></div>
            <div><div className="faint">Interim control</div><b>{t.interim_hours ? `${t.interim_hours}h` : 'Not required'}</b></div>
            <div><div className="faint">Remediate</div><b>{t.remediate_hours / 24} days</b></div>
          </div></div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><div><h2>Applicability across the estate</h2>
          <p className="muted">Missing or stale data becomes Cannot Determine — never Does Not Apply.</p></div>
          <div style={{ display: 'flex', gap: 6 }}>
            <Badge kind="applies">{est.counts.applies} Applies</Badge><Badge kind="cd">{est.counts.cannot_determine} Cannot Determine</Badge><Badge kind="dna">{est.counts.does_not_apply} Does Not Apply</Badge></div></div>
        <table>
          <thead><tr><th>Asset</th><th>Owner</th><th>Component record</th><th>State</th><th>Basis</th><th>Task</th></tr></thead>
          <tbody>{est.rows.map((r: any) => (
            <tr key={r.asset_id} className={r.state === 'cannot_determine' ? 'hl' : ''}>
              <td><b>{r.asset}</b><div className="faint">{r.tags}</div></td>
              <td>{r.owner ? <span style={r.owner_active ? {} : { color: 'var(--red)', textDecoration: 'line-through' }}>{r.owner}</span> : <span className="red">no owner</span>}
                {r.owner && !r.owner_active && <div className="red" style={{ fontSize: 12 }}>left the organisation</div>}</td>
              <td><span className="mono">{r.component_record}</span>
                {r.provenance && <div className="faint mono">{r.provenance}</div>}
                {r.stale && <div className="red" style={{ fontSize: 12 }}>stale · over 30 days</div>}</td>
              <td><StateBadge state={r.state} />{r.state !== 'cannot_determine' &&
                <div><button className="btn sm danger" onClick={() => override(r)}>Make more cautious</button></div>}</td>
              <td style={{ maxWidth: 260 }}>{r.basis}</td>
              <td>{r.task_id ? <><Link to={`/remediations/${r.task_id}`} className="mono"><b>{r.task_ref}</b></Link>
                <div className={r.task_overdue ? 'red' : 'muted'} style={{ fontSize: 12.5 }}>{r.task_status}</div></> : <span className="muted">No task</span>}</td>
            </tr>))}</tbody>
        </table>
        {aerr && <p className="err">{aerr}</p>}
        <p className="faint" style={{ marginTop: 10 }}>Anyone may move an asset to a more cautious state with a reason. Moving it to Does Not Apply needs component-scoped evidence and an independent reviewer.</p>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Departments involved</h2>
        <p className="muted">Who is accountable in each department and where each system stands for this advisory.</p>
        <table><thead><tr><th>Department</th><th>Department head</th><th>Systems · owner · state</th></tr></thead><tbody>
          {(o.advisory_departments || []).map((d: any) => (
            <tr key={d.department}><td><b>{d.department}</b></td><td>{d.head || '—'}</td>
              <td>{d.systems.map((s: any) => <div key={s.name} style={{ margin: '2px 0' }}><StateBadge state={s.state} /> {s.name} <span className="faint">· owner </span>
                <span style={s.owner_active ? {} : { color: 'var(--red)', textDecoration: 'line-through' }}>{s.owner || 'none'}</span></div>)}</td></tr>))}</tbody></table>
      </div>

      <div className="gate">
        <div><div className="mono" style={{ color: '#98a0ae', fontSize: 11 }}>OBLIGATION OBL-{String(o.id).padStart(4, '0')} · CLOSURE GATE</div>
          <h2 style={{ fontSize: 19, margin: '4px 0' }}>{o.status === 'closed' ? 'Obligation closed' : `${g.approved} of ${g.total} assets have an approved disposition`}</h2>
          <p style={{ color: '#c9ced8' }}>Closes only when every affected asset is remediated or under approved risk acceptance, and none remains Cannot Determine.</p>
          {o.closure_blockers?.length > 0 && <p style={{ color: '#98a0ae', fontSize: 12, marginTop: 6 }}>Blocking: {o.closure_blockers.join(' · ')}</p>}</div>
        <div><div className="seg"><i style={{ width: pct(g.approved), background: '#8ea4f0' }} /><i style={{ width: pct(g.in_review), background: '#f0b48a' }} /><i style={{ width: pct(g.cannot_determine), background: '#a58be0' }} /></div>
          <div className="seglabels"><span>{g.approved} approved</span><span>{g.in_review} in review</span><span>{g.cannot_determine} Cannot Determine</span></div></div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><div><h2>Role-specific guidance</h2>
          <p className="muted">Drafted from the source and policies with citations. RAG cannot confirm applicability, accept risk or authorise closure.</p></div>
          <button className="btn" onClick={async () => { setGuid(await api.post(`/obligations/${o.id}/guidance`, { role: 'system owner' })); refreshAll() }}>Draft guidance</button></div>
        {[...(guid ? [guid] : []), ...o.guidance.filter((x: any) => x.id !== guid?.id)].slice(0, 3).map((x: any) => (
          <div key={x.id} className={`note ${x.status === 'draft' ? 'ok' : 'fail'}`} style={{ marginTop: 8 }}>
            {x.status === 'draft' ? <><b>Grounded draft · {x.role}</b><p>{x.text}</p>
              {x.citations.map((c: any, i: number) => <p key={i} className="faint">cites {c.source}: “{c.quote}”</p>)}</>
              : <><b>Withheld for human review</b><p>{x.reason}</p></>}
          </div>))}
      </div>
      {hist && <Modal title="Source & version history" onClose={() => setHist(false)}>
        <dl className="kv" style={{ marginTop: 10 }}><dt>Source</dt><dd>CISA/NVD (approved source)</dd><dt>CVE</dt><dd>{a.cve}</dd>
          <dt>Published</dt><dd>{fmt(a.published_at, true)}</dd><dt>Affected</dt><dd className="mono">{a.affected_versions.join(', ')}</dd></dl>
        <div className="actions"><Link className="btn" to={`/documents?doc=advisory/${a.id}`}>Preview as document</Link> <button className="btn" onClick={() => setHist(false)}>Close</button></div></Modal>}
    </>
  )
}
