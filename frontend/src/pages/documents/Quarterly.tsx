import { useSearchParams } from 'react-router-dom'
import { useApi } from '../../api'
import { Badge, Loading } from '../../ui'
import { KIND_LABEL, ReviewBadge, when } from './common'

function quarters(now: string): string[] {
  const d = new Date(now)
  let y = d.getFullYear(), q = Math.floor(d.getMonth() / 3) + 1
  const out = []
  for (let i = 0; i < 4; i++) {
    out.push(`${y}-Q${q}`)
    q -= 1
    if (!q) { q = 4; y -= 1 }
  }
  return out
}

const List = ({ label, items, kind }: { label: string; items: string[]; kind: string }) => (
  <div className="card kpi"><div className="l">{label}</div><div className="v">{items.length}</div>
    <div className="l">{items.length ? items.join(', ') : 'none'}</div>{items.length > 0 && <Badge kind={kind}>{kind === 'over' ? 'needs action' : 'recorded'}</Badge>}</div>
)

export default function Quarterly() {
  const { data: clock } = useApi('/clock')
  const [params, setParams] = useSearchParams()
  const q = params.get('quarter') || ''
  const { data: s, error } = useApi(`/quarterly-summary${q ? `?quarter=${q}` : ''}`)
  if (!s || !clock) return <Loading error={error} />
  const changed = s.sources.filter((r: any) => r.has_changes)
  return (
    <>
      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><div><h2>Quarterly report · {s.quarter}</h2>
          <p className="muted" style={{ maxWidth: 760 }}>What changed in each source this quarter, what Compliance and Legal decided, and which sources were reviewed with no changes. Unchanged sources are listed too, so the board can see they were actually checked.</p></div>
          <div style={{ display: 'flex', gap: 8, whiteSpace: 'nowrap' }}>
            <select style={{ width: 130 }} value={s.quarter} onChange={(e) => setParams({ tab: 'quarterly', quarter: e.target.value })}>
              {quarters(clock.now).map((x) => <option key={x}>{x}</option>)}</select>
            <a className="btn" href={`/api/quarterly-summary.csv?quarter=${s.quarter}`}>Download CSV</a>
            <button className="btn" onClick={() => window.print()}>Print</button></div></div>
        <p className="faint">{when(s.start)} to {when(s.end)} · generated {when(s.generated_at)}</p>
      </div>
      <div className="grid g6" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        <List label="Changed this quarter" items={changed.map((r: any) => r.source)} kind="info" />
        <List label="Reviewed, no change" items={s.checked_no_change} kind="ok" />
        <List label="Not reviewed this quarter" items={s.not_checked} kind="over" />
        <List label="Review overdue now" items={s.overdue_reviews} kind="over" />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <table><thead><tr><th>Source</th><th>New versions</th><th>Clauses</th><th>Obligations from them</th><th>Advisories</th><th>Reviews</th><th>Review status</th></tr></thead><tbody>
          {s.sources.map((r: any) => (
            <tr key={r.source} className={r.review_status === 'overdue' ? 'bad' : ''}>
              <td><b>{r.source}</b><div className="faint">{KIND_LABEL[r.kind] || r.kind} · {r.jurisdiction || '—'}{r.status !== 'approved' ? ` · ${r.status}` : ''}</div></td>
              <td>{r.versions_seen ? r.versions.map((v: string) => `v${v}`).join(', ') : <span className="faint">none</span>}</td>
              <td>{r.versions_seen ? <span style={{ fontSize: 13 }}>{r.changed} changed · {r.new} new · {r.removed} removed · {r.renumbered} renumbered</span> : <span className="faint">—</span>}</td>
              <td>{r.intake === 'feed' ? <span className="faint">—</span> : <span style={{ fontSize: 13 }}>{r.candidate_obligations} to review · {r.approved_obligations} approved · {r.rejected_obligations} rejected · {r.superseded_obligations} superseded</span>}</td>
              <td>{r.intake === 'feed' ? <span style={{ fontSize: 13 }}>{r.advisories_ingested} added · {r.advisories_merged} combined · {r.advisories_skipped} ignored</span> : <span className="faint">—</span>}</td>
              <td>{r.intake === 'feed' ? <span className="faint">automatic</span> : <>{r.checks}<div className="faint">{r.checked_no_change} with no change</div></>}</td>
              <td>{r.intake === 'feed' ? <span className="faint">feed</span> : <ReviewBadge review={{ status: r.review_status }} />}<div className="faint">{r.last_checked ? `reviewed ${when(r.last_checked)}` : ''}</div></td></tr>))}</tbody></table>
      </div>
    </>
  )
}
