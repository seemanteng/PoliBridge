import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, ImpactBadge, Loading, Modal, StateBadge, useAction } from '../ui'

export function AdvisoryList() {
  const { data: obs, error } = useApi('/obligations')
  const [open, setOpen] = useState(false)
  if (!obs) return <Loading error={error} />
  const advisories = obs.filter((o: any) => o.kind === 'advisory')
  const candidates = obs.filter((o: any) => o.status === 'candidate')
  const others = obs.filter((o: any) => o.kind !== 'advisory' && o.status !== 'candidate')
  return (
    <>
      <div className="head">
        <div><h1>Advisories &amp; obligations</h1>
          <p className="muted">Advisories take the fast path; regulatory and policy changes wait for Compliance or Legal.</p></div>
        <button className="btn primary" onClick={() => setOpen(true)}>Ingest advisory</button>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <table>
          <thead><tr><th>Obligation</th><th>Kind</th><th>Impact</th><th>Status</th><th>Tasks closed</th></tr></thead>
          <tbody>
            {advisories.map((o: any) => (
              <tr key={o.id}>
                <td><Link to={`/advisories/${o.id}`}><b>{o.title}</b></Link></td><td>Security advisory</td>
                <td>{o.impact ? <ImpactBadge impact={o.impact} /> : '—'}</td><td>{o.status}</td>
                <td>{o.task_counts.closed} / {o.task_counts.total}</td>
              </tr>
            ))}
            {!advisories.length && <tr><td colSpan={5} className="muted">No advisories yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {candidates.length > 0 && <Candidates items={candidates} />}
      {others.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Approved regulatory and policy obligations</h2>
          <table><tbody>{others.map((o: any) => (
            <tr key={o.id}><td><b>{o.title}</b><div className="faint">“{o.quotation}”</div></td><td>{o.status}</td><td>{o.applicability_decision || ''}</td></tr>
          ))}</tbody></table>
        </div>
      )}
      {open && <IngestModal onClose={() => setOpen(false)} />}
    </>
  )
}

function Candidates({ items }: { items: any[] }) {
  const { run, error } = useAction()
  const decide = (id: number, approve: boolean) =>
    run(async () => { await api.post(`/obligations/${id}/decide`, { approve, applicability: approve ? 'applies' : null }); refreshAll() })
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>Candidate obligations awaiting Compliance / Legal</h2>
      <p className="muted" style={{ marginBottom: 6 }}>Quoted exactly from the changed clause. Materiality and applicability are human decisions.</p>
      <table><tbody>{items.map((o) => (
        <tr key={o.id}>
          <td><b>{o.title}</b><div className="faint">“{o.quotation}”</div></td>
          <td style={{ whiteSpace: 'nowrap' }}>
            <button className="btn sm primary" onClick={() => decide(o.id, true)}>Approve</button>{' '}
            <button className="btn sm" onClick={() => decide(o.id, false)}>Reject</button>
          </td>
        </tr>
      ))}</tbody></table>
      {error && <p className="err">{error}</p>}
    </div>
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
      })
      refreshAll(); nav(`/advisories/${r.id}`)
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
  if (!o.estate) return <><div className="crumbs"><Link to="/advisories">Advisories</Link></div><h1>{o.title}</h1><p className="muted">This obligation has no per-asset applicability; it is tracked through its tasks.</p></>
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
        <div className="crumbs"><Link to="/advisories">Advisories</Link> / ADV-{String(o.id).padStart(4, '0')}</div>
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
          <thead><tr><th>Asset</th><th>Component record</th><th>State</th><th>Basis</th><th>Task</th></tr></thead>
          <tbody>{est.rows.map((r: any) => (
            <tr key={r.asset_id} className={r.state === 'cannot_determine' ? 'hl' : ''}>
              <td><b>{r.asset}</b><div className="faint">{r.tags}</div></td>
              <td><span className="mono">{r.component_record}</span>
                {r.provenance && <div className="faint mono">{r.provenance}</div>}
                {r.stale && <div className="red" style={{ fontSize: 12 }}>stale · over 30 days</div>}</td>
              <td><StateBadge state={r.state} />{r.state !== 'cannot_determine' &&
                <div><button className="btn sm danger" onClick={() => override(r)}>Make more cautious</button></div>}</td>
              <td style={{ maxWidth: 260 }}>{r.basis}</td>
              <td>{r.task_id ? <><Link to={`/tasks/${r.task_id}`} className="mono"><b>{r.task_ref}</b></Link>
                <div className={r.task_overdue ? 'red' : 'muted'} style={{ fontSize: 12.5 }}>{r.task_status}</div></> : <span className="muted">No task</span>}</td>
            </tr>))}</tbody>
        </table>
        {aerr && <p className="err">{aerr}</p>}
        <p className="faint" style={{ marginTop: 10 }}>Anyone may move an asset to a more cautious state with a reason. Moving it to Does Not Apply needs component-scoped evidence and an independent reviewer.</p>
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
