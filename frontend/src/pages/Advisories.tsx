import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, ImpactBadge, Loading, Modal, StateBadge, useAction } from '../ui'
import { has } from '../filterUtils'
import { NewVersionModal } from './documents/NewVersion'
import { GuidanceText, WriteGuidanceModal } from './documents/Guidance'

const LABEL: Record<string, string> = { applies: 'Applies', does_not_apply: 'Does Not Apply', cannot_determine: 'Cannot Determine' }
const LABEL_KIND: Record<string, string> = { applies: 'lbl-applies', does_not_apply: 'lbl-dna', cannot_determine: 'lbl-cd' }
const LabelBadge = ({ label }: { label: string }) => <Badge kind={LABEL_KIND[label]}>{LABEL[label] || label}</Badge>

type Stage = 'review' | 'waiting' | 'done'

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
      {opt('advisory', 'A security advisory', 'Advisories from approved feeds arrive on their own. Use this for one that reached you another way: fetch it from NVD and every system is matched at once.')}
      {opt('document', 'A new version of a source', 'A regulation, policy or contract that changed. PoliBridge fingerprints the file and shows which clauses are new, changed, renumbered or removed.')}
    </Modal>
  )
}

export function AdvisoryList() {
  const { data: rows, error } = useApi('/acknowledgements')
  const { data: obs } = useApi('/obligations')
  const { data: sources } = useApi('/sources')
  const [adding, setAdding] = useState<null | 'choose' | 'advisory' | 'document'>(null)
  const [rev, setRev] = useState<any>(null)
  const [q, setQ] = useState('')
  const { run, error: aerr } = useAction()
  if (!rows || !obs || !sources) return <Loading error={error} />
  const items = buildItems(rows, obs, sources)
  const shown = items.filter((i) => has(q, i.title, i.kind, i.rows.map((r: any) => `${r.scope} ${r.owner}`).join(' '), (i.clauses || []).map((c: Clause) => c.ob.title).join(' ')))
  const sign = (r: any) => run(async () => {
    if (r.task_id) await api.post(`/tasks/${r.task_id}/acknowledge`)
    else await api.post(`/obligations/${r.obligation_id}/signoff`, { department: r.department_key })
    refreshAll()
  })
  const mine = rows.filter((r: any) => r.status === 'pending' || r.status === 'overdue')
  const canReview = true
  const toReview: any[] = obs.filter((o: any) => o.status === 'candidate')

  const cell = (r: any) => (
    <tr key={r.key}>
      <td>{r.scope}{r.kind === 'Advisory' && <div className="faint">{r.department}</div>}</td>
      <td>{r.owner ? <span style={r.owner_active ? {} : { color: 'var(--red)', textDecoration: 'line-through' }}>{r.owner}</span> : <span className="red">no owner</span>}</td>
      {r.kind === 'Advisory' && <td>{r.impact ? <ImpactBadge impact={r.impact} short /> : <span className="faint">—</span>}</td>}
      <td><LabelBadge label={r.label} /></td>
      <td>{r.status === 'signed' ? <><Badge kind="info">✓ Signed off</Badge><div className="faint">{r.signed_by} · {fmt(r.signed_at)}{r.entered_by && <> · entered by {r.entered_by}</>}</div></>
        : r.status === 'overdue' ? <><Badge kind="over">Overdue</Badge>{r.escalated_to && <div className="faint">escalated to {r.escalated_to}</div>}</>
        : r.status === 'pending' ? <><Badge kind="cd">Waiting</Badge>{r.due && r.kind !== 'Advisory' && <div className="faint">due {fmt(r.due)}</div>}</>
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

      <div className="card" style={{ marginTop: 14, borderColor: (mine.length || toReview.length) ? 'var(--blue)' : undefined }}>
        <h2>Needs action</h2>
        {!mine.length && !toReview.length && <p className="muted" style={{ marginTop: 6 }}>Nothing needs action right now.</p>}
        {toReview.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '8px 0', borderTop: '1px solid var(--line)' }}>
            <div><b>{toReview.length} clause{toReview.length > 1 ? 's' : ''} to review</b><div className="muted">Decide what each changed clause means for the departments involved.</div></div>
            <button className="btn sm primary" onClick={() => setRev(toReview[0])}>Review next clause</button></div>)}
        {mine.map((r: any) => (
          <div key={r.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '8px 0', borderTop: '1px solid var(--line)' }}>
            <div><b>{r.scope}</b> <LabelBadge label={r.label} />{r.status === 'overdue' && <> <Badge kind="over">Overdue</Badge></>}
              <div className="muted">{r.title} · owner {r.owner || 'none'}</div></div>
            <button className="btn sm primary" onClick={() => sign(r)}>Sign off for {r.owner ? r.owner.split(' ')[0] : 'owner'}</button></div>))}
        {aerr && <p className="err">{aerr}</p>}
      </div>

      <div className="sec" style={{ marginTop: 20 }}>
        <h2>All items</h2>
        <input style={{ width: 260 }} placeholder="Search items, systems, owners…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <p className="faint" style={{ margin: '4px 0 10px' }}>Labels: <b>Applies</b> means act. <b>Cannot Determine</b> means we lack the data, so investigate. <b>Does Not Apply</b> means confirmed unaffected.</p>

      {shown.map((i) => (
        <details key={i.key} className="card" open={i.stage === 'waiting'} style={{ marginBottom: 10 }}>
          <summary style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
            <span><Badge>{i.kind}</Badge>{i.type === 'advisory' && i.rows.some((r: any) => r.impact) && <> <ImpactBadge impact={['High', 'Medium', 'Low'].find((l) => i.rows.some((r: any) => r.impact === l)) as string} /></>} {i.type === 'advisory' ? <Link to={`/acknowledgements/${i.id}`}><b>{i.title}</b></Link> : <b>{i.title}</b>}
              {i.type === 'document' && <div className="faint" style={{ marginTop: 3 }}>{i.clauses.length} changed clause{i.clauses.length > 1 ? 's' : ''}</div>}</span>
            <span style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
              {i.candidates ? <><Badge kind="cd">Needs review</Badge><div className="faint">{i.candidates} clause{i.candidates > 1 ? 's' : ''} to review</div></>
                : <><Badge kind={i.stage === 'done' ? 'info' : i.overdue ? 'over' : 'cd'}>{i.stage === 'done' ? 'Complete' : i.overdue ? 'Overdue' : 'Waiting'}</Badge>
                  <div className="faint">{i.signed} of {i.need} signed off</div></>}</span>
          </summary>
          {i.type === 'advisory' ? (
            <table style={{ marginTop: 8 }}><thead><tr><th>System / department</th><th>Owner</th><th>Impact</th><th>Label</th><th>Sign-off</th><th></th></tr></thead>
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
      <p className="faint" style={{ marginTop: 8 }}>A sign-off is recorded for the named owner. The audit log notes that Risk & Compliance entered it on their behalf.</p>

      {adding === 'choose' && <AddNewModal onClose={() => setAdding(null)} onPick={setAdding} />}
      {adding === 'advisory' && <IngestModal onClose={() => setAdding(null)} />}
      {adding === 'document' && <NewVersionModal onClose={() => setAdding(null)} />}
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

function IngestModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate()
  const { run, error, busy } = useAction()
  const [f, setF] = useState({ cve: 'CVE-2017-5638', product: 'struts2-core', versions: '>=2.2.3 <2.3.32, >=2.5.0 <2.5.10.1', cvss: '9.8', kev: true, fixed: '2.3.32, 2.5.10.1' })
  const submit = async () => {
    const ok = await run(async () => {
      const r = await api.post('/advisories', {
        cve: f.cve, product: f.product, affected_versions: f.versions.split(',').map((s) => s.trim()).filter(Boolean),
        cvss: f.cvss ? Number(f.cvss) : null, kev: f.kev, fixed_version: f.fixed || null, description: desc || null,
        published_at: published || null,
      })
      refreshAll(); nav(`/acknowledgements/${r.id}`)
    })
    if (ok) onClose()
  }
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const [cands, setCands] = useState<any[]>([])
  const [desc, setDesc] = useState('')
  const [published, setPublished] = useState('')
  const [note, setNote] = useState('')
  const pick = (c: any) => setF((p) => ({ ...p, product: c.inventory_name || c.product, versions: c.versions.join(', '), fixed: c.fixed_version || '' }))
  const fetchNvd = async () => {
    setNote('')
    await run(async () => {
      const r = await api.get(`/advisories/lookup?cve=${encodeURIComponent(f.cve.trim())}`)
      setCands(r.candidates)
      setDesc(r.description || '')
      setPublished(r.published || '')
      const c = r.candidates.find((x: any) => x.inventory_name) || r.candidates[0]
      setF((p) => ({ ...p, cve: r.cve, cvss: r.cvss == null ? '' : String(r.cvss), kev: !!r.kev,
        ...(c ? { product: c.inventory_name || c.product, versions: c.versions.join(', '), fixed: c.fixed_version || '' } : {}) }))
      setNote(`Filled from NVD${r.kev == null ? ' (KEV feed unreachable, check the box yourself)' : ' and CISA KEV'}. ` +
        (c ? '' : 'No affected-product data found; enter it by hand.'))
    })
  }
  return (
    <Modal title="Ingest advisory" sub="Fetch real data from NVD and CISA KEV, or type it in. It is matched against every asset at once. If an approved feed already delivered this CVE, it is merged into the same obligation." onClose={onClose}>
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

// The draft's text can embed its own quote ("Source says: ..."), so peel the quotes off to leave just the summary.
function summaryOf(g: any): string {
  let t: string = g.text || ''
  for (const c of g.citations || []) t = t.split(`"${c.quote}"`).join('').split(c.quote).join('')
  return t.replace(/\s*Source says:\s*/g, ' ').replace(/^For [^:]{1,40}:\s*/, '').replace(/\s+/g, ' ').trim()
}

function Guidance({ g }: { g: any }) {
  const label = { letterSpacing: '.06em' }
  return (
    <div style={{ marginTop: 12, border: '1px solid var(--line)', borderRadius: 8, padding: '12px 14px' }}>
      {g.citations.map((c: any, i: number) => (
        <div key={i}>
          <div className="faint" style={label}>FROM {String(c.source).toUpperCase()}</div>
          <blockquote style={{ margin: '6px 0 12px', padding: '8px 12px', borderLeft: '3px solid var(--blue)', background: 'var(--blue-soft)', borderRadius: '0 6px 6px 0' }}>“{c.quote}”</blockquote>
        </div>))}
      <div className="faint" style={label}>FIX REQUIRED</div>
      <p style={{ margin: '4px 0 0', fontSize: 15 }}>{summaryOf(g)}</p>
    </div>
  )
}

export function AdvisoryDetail() {
  const { id } = useParams()
  const { data: o, error } = useApi(`/obligations/${id}`)
  const [hist, setHist] = useState(false)
  const [writing, setWriting] = useState<any>(null)
  const { run, error: aerr } = useAction()
  // advisories ingested before this was automatic have no fix drafted yet, so ask for it once
  const asked = useRef(false)
  useEffect(() => {
    if (o?.advisory && !o.guidance.length && !asked.current) {
      asked.current = true
      api.post(`/obligations/${o.id}/guidance`, { role: 'system owner' }).then(refreshAll).catch(() => {})
    }
  }, [o])
  if (!o) return <Loading error={error} />
  const deptGuidance = (dept: string) => [...o.guidance].reverse().find((g: any) => g.department === dept && g.status !== 'superseded')
  const GSTATE: Record<string, [string, string]> = { pending_approval: ['cd', 'Waiting for approval'], released: ['info', 'Released'], withheld: ['over', 'Held back'], rejected: ['over', 'Rejected'] }
  const review = o.source?.review
  if (!o.estate) return (
    <>
      <div className="crumbs"><Link to="/acknowledgements">Acknowledgements</Link> / OBL-{String(o.id).padStart(4, '0')}</div>
      <div className="badges" style={{ marginTop: 8 }}><Badge>{o.kind === 'policy' ? 'Policy clause' : 'Regulatory clause'}</Badge><Badge kind={o.status === 'candidate' ? 'cd' : o.status === 'superseded' ? 'over' : 'info'}>{o.status}</Badge></div>
      <h1>{o.title}</h1>
      {o.source && <p className="muted">From <Link to={`/documents?tab=changes&source=${o.source.id}`}>{o.source.name}</Link>
        {review?.last_checked && <> · source last checked {fmt(review.last_checked, true)}</>}</p>}
      {review?.status === 'overdue' && <div className="note fail" style={{ marginTop: 12 }}><b>The source has not been re-checked on time.</b> This obligation may rest on outdated wording until its owner confirms the current version on Documents.</div>}
      {o.superseded_by && <div className="note fail" style={{ marginTop: 12 }}><b>Superseded.</b> {o.superseded_reason}. The current wording is <Link to={`/acknowledgements/${o.superseded_by.id}`}>OBL-{String(o.superseded_by.id).padStart(4, '0')}</Link> ({o.superseded_by.status}).</div>}
      {!o.superseded_by && o.status === 'superseded' && <div className="note fail" style={{ marginTop: 12 }}><b>Superseded.</b> {o.superseded_reason}.</div>}
      {o.supersedes && <div className="note" style={{ marginTop: 12, border: '1px solid var(--line)' }}>Replaces <Link to={`/acknowledgements/${o.supersedes.id}`}>OBL-{String(o.supersedes.id).padStart(4, '0')}</Link>, which is now {o.supersedes.status}. Departments that signed the old wording have been told to sign again once this is approved.</div>}
      {o.previous_quotation && <div className="card" style={{ marginTop: 16 }}><h2>Previous wording</h2><p className="muted">{o.previous_quotation}</p></div>}
      <div className="card" style={{ marginTop: 16 }}><h2>{o.previous_quotation ? 'Current wording' : 'Clause'}</h2><p>{o.quotation}</p>
        <p className="faint" style={{ marginTop: 8 }}>Required action: {o.required_action || '—'}</p></div>
      <div className="card" style={{ marginTop: 16 }}><h2>Departments involved</h2>
        <p className="muted">Decided by Compliance or Legal. Each department head receives guidance written for them, released only after the approver for the clause's topic agrees, and signs off by the due date.</p>
        <table><thead><tr><th>Department</th><th>Decision</th><th>Guidance</th><th>Sign-off</th></tr></thead><tbody>
          {(o.departments || []).map((d: any) => {
            const g = deptGuidance(d.department)
            const late = d.late
            return (
            <tr key={d.department}><td><b>{d.department}</b><div className="faint">head {d.owner || '—'}</div><div className="faint">{d.reason}</div></td>
              <td><LabelBadge label={d.label} />{d.decided_by && <div className="faint">by {d.decided_by}</div>}{d.note && <div className="faint">{d.note}</div>}</td>
              <td style={{ maxWidth: 320 }}>{g ? <><Badge kind={GSTATE[g.status]?.[0]}>{GSTATE[g.status]?.[1] || g.status}</Badge>
                <div className="faint">{g.status === 'pending_approval' ? `approver ${g.approver}` : g.status === 'released' ? `approved by ${g.approver}${g.human_authored ? ' · written by hand' : ''}` : g.reason}</div>
                {g.status === 'released' && <GuidanceText g={g} />}
                {g.status === 'pending_approval' && <button className="btn sm primary" style={{ marginTop: 4 }} onClick={() => run(async () => { await api.post(`/guidance/${g.id}/approve`); refreshAll() })}>Approve as {g.approver}</button>}
                {(g.status === 'withheld' || g.status === 'rejected') && <button className="btn sm primary" style={{ marginTop: 4 }} onClick={() => setWriting(g)}>Write guidance</button>}</>
                : <span className="faint">{o.status === 'candidate' ? 'drafted after approval' : d.label === 'does_not_apply' ? 'none needed' : '—'}</span>}</td>
              <td>{d.signed_at ? <><Badge kind="info">✓ Signed off</Badge><div className="faint">{d.signed_by} · {fmt(d.signed_at)}{d.entered_by && <> · entered by {d.entered_by}</>}</div></>
                : o.status === 'candidate' ? <span className="muted">Awaiting Compliance / Legal</span>
                : o.status === 'superseded' ? <span className="muted">No longer needed</span>
                : <><button className="btn sm primary" onClick={() => run(async () => { await api.post(`/obligations/${o.id}/signoff`, { department: d.department }); refreshAll() })}>Sign off for {d.owner?.split(' ')[0] || 'head'}</button>
                  {d.due && <div className={late ? 'red' : 'faint'} style={{ fontSize: 12 }}>{late ? 'overdue · ' : ''}due {fmt(d.due)}{d.escalated_to ? ` · escalated to ${d.escalated_to}` : ''}</div>}</>}</td></tr>)})}
          {!(o.departments || []).length && <tr><td colSpan={4} className="muted">No departments recorded.</td></tr>}</tbody></table>
        {aerr && <p className="err">{aerr}</p>}</div>
      {writing && <WriteGuidanceModal g={writing} onClose={() => setWriting(null)} />}
    </>)
  const a = o.advisory, est = o.estate
  const t = est.timings
  const g = est.gate
  const pct = (n: number) => `${(n / Math.max(1, g.total)) * 100}%`
  return (
    <>
      <div className="head"><div>
        <div className="crumbs"><Link to="/acknowledgements">Acknowledgements</Link> / ADV-{String(o.id).padStart(4, '0')}</div>
        <div className="badges" style={{ marginTop: 8 }}>{o.impact && <ImpactBadge impact={o.impact} />}<Badge kind="info">Security advisory · fast path</Badge></div>
        <h1>{a.cve} · {a.product}</h1>
        <p className="muted" style={{ maxWidth: 760 }}>Published {fmt(a.published_at, true)}, received {fmt(a.received_at || a.published_at, true)} from {(a.sources || []).map((x: any) => x.source).join(' and ') || 'an approved source'}, and matched against every system's component record at once. Advisories bypass the Compliance/Legal gate.</p>
      </div><button className="btn" onClick={() => setHist(true)}>Source &amp; version history</button></div>

      <div className="grid g2">
        <div className="card"><h2>Structured advisory</h2>
          <dl className="kv" style={{ marginTop: 10 }}>
            <dt>Product</dt><dd className="mono">{a.product}</dd>
            <dt>Affected</dt><dd className="mono">{a.affected_versions.join(', ')}</dd>
            <dt>Fixed in</dt><dd className="mono">{a.fixed_version || '—'}</dd>
            <dt>Severity</dt><dd>{a.cvss == null ? 'Unknown (treated conservatively)' : `CVSS ${a.cvss.toFixed(1)}`}{a.kev ? ' · exploited in the wild' : ''}</dd>
            <dt>Reported by</dt><dd>{(a.sources || []).map((x: any) => <div key={x.source}>{x.source} <span className="faint">· received {fmt(x.received_at)}</span></div>)}</dd>
          </dl>
          {a.conflict && <div className="note fail" style={{ marginTop: 10 }}><b>Sources disagree on affected versions (A4)</b><p style={{ fontSize: 13 }}>{a.conflict} Compliance has a finding to decide which is right.</p></div>}
          {(a.sources || []).length > 1 && !a.conflict && <p className="faint" style={{ marginTop: 8 }}>{a.sources.length} approved sources reported this CVE. It is one obligation, so owners get one task, not {a.sources.length}.</p>}</div>
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
            <Badge kind="lbl-applies">{est.counts.applies} Applies</Badge><Badge kind="lbl-cd">{est.counts.cannot_determine} Cannot Determine</Badge><Badge kind="lbl-dna">{est.counts.does_not_apply} Does Not Apply</Badge></div></div>
        <table>
          <thead><tr><th>Asset</th><th>Owner</th><th>Impact</th><th>Component record</th><th>State</th><th>Basis</th><th>Task</th></tr></thead>
          <tbody>{est.rows.map((r: any) => (
            <tr key={r.asset_id} className={r.state === 'cannot_determine' ? 'hl' : ''}>
              <td><b>{r.asset}</b><div className="faint">{r.tags}</div></td>
              <td>{r.owner ? <span style={r.owner_active ? {} : { color: 'var(--red)', textDecoration: 'line-through' }}>{r.owner}</span> : <span className="red">no owner</span>}
                {r.owner && !r.owner_active && <div className="red" style={{ fontSize: 12 }}>left the organisation</div>}</td>
              <td>{r.impact ? <ImpactBadge impact={r.impact} short /> : <span className="faint">—</span>}</td>
              <td><span className="mono">{r.component_record}</span>
                {r.provenance && <div className="faint mono">{r.provenance}</div>}
                {r.stale && <div className="red" style={{ fontSize: 12 }}>stale · over 30 days</div>}</td>
              <td><StateBadge state={r.state} /></td>
              <td style={{ maxWidth: 260 }}>{r.basis}</td>
              <td>{r.task_id ? <><Link to={`/remediations/${r.task_id}`} className="mono"><b>{r.task_ref}</b></Link>
                <div className={r.task_overdue ? 'red' : 'muted'} style={{ fontSize: 12.5 }}>{r.task_status}</div></> : <span className="muted">No task</span>}</td>
            </tr>))}</tbody>
        </table>
        {aerr && <p className="err">{aerr}</p>}
        <p className="faint" style={{ marginTop: 10 }}>A system only becomes Does Not Apply with evidence that covers the software and an independent reviewer. Missing, stale or conflicting data always stays Cannot Determine.</p>
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
        <h2>Fixes Required</h2>
        {(() => {
          const x = [...o.guidance].reverse().find((y: any) => !y.asset)
          if (!x) return <p className="muted" style={{ marginTop: 10 }}>Working out the fix…</p>
          return x.status === 'draft' ? <Guidance g={x} /> : (
            <div className="note fail" style={{ marginTop: 8 }}><b>Withheld for human review</b><p>{x.reason}</p>
              <button className="btn sm" onClick={() => run(async () => { await api.post(`/obligations/${o.id}/guidance`, { role: 'system owner' }); refreshAll() })}>Try again</button></div>)
        })()}
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Guidance sent to each system</h2>
        <p className="muted">Written for each owner from their own component record. Released at once (fast path, C1); Information Security checks it alongside, and a held-back draft goes to Compliance (A4).</p>
        <table style={{ marginTop: 8 }}><thead><tr><th>System · owner</th><th>Guidance</th><th>Checked alongside</th></tr></thead><tbody>
          {o.guidance.filter((x: any) => x.asset && x.status !== 'superseded').map((x: any) => (
            <tr key={x.id}><td><b>{x.asset}</b><div className="faint">{x.recipient || 'no owner'}</div></td>
              <td style={{ maxWidth: 460 }}>{x.status === 'released' ? <GuidanceText g={x} /> : <><Badge kind="over">Held back</Badge> <span className="faint">{x.reason}</span>
                <div><button className="btn sm primary" style={{ marginTop: 4 }} onClick={() => setWriting(x)}>Write guidance</button></div></>}</td>
              <td>{x.consult ? (x.consulted_at ? <>{x.consult}<div className="faint">{fmt(x.consulted_at)}</div></>
                : <button className="btn sm" onClick={() => run(async () => { await api.post(`/guidance/${x.id}/consult`); refreshAll() })}>Mark checked as {x.consult}</button>) : <span className="faint">—</span>}</td></tr>))}
          {!o.guidance.some((x: any) => x.asset) && <tr><td colSpan={3} className="muted">No system needs action for this advisory.</td></tr>}</tbody></table>
      </div>
      {writing && <WriteGuidanceModal g={writing} onClose={() => setWriting(null)} />}
      {hist && <Modal title="Source & version history" onClose={() => setHist(false)}>
        <dl className="kv" style={{ marginTop: 10 }}><dt>CVE</dt><dd>{a.cve}</dd>
          <dt>Published</dt><dd>{fmt(a.published_at, true)}</dd><dt>Received</dt><dd>{fmt(a.received_at, true)}</dd>
          <dt>Matched</dt><dd>{fmt(a.ingested_at, true)}</dd><dt>Affected</dt><dd className="mono">{a.affected_versions.join(', ')}</dd></dl>
        <table style={{ marginTop: 10 }}><thead><tr><th>Approved source</th><th>Received</th><th>Its affected versions</th></tr></thead><tbody>
          {(a.sources || []).map((x: any) => <tr key={x.source}><td>{x.source}</td><td>{fmt(x.received_at, true)}</td><td className="mono">{x.affected_versions.join(', ')}</td></tr>)}</tbody></table>
        <div className="actions"><button className="btn" onClick={() => setHist(false)}>Close</button></div></Modal>}
    </>
  )
}
