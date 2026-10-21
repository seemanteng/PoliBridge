import { useState } from 'react'
import { Link } from 'react-router-dom'
import { fmt, fmtDay, useApi } from '../api'
import { Badge, Loading, Modal } from '../ui'

export default function Oversight() {
  const { data: o, error } = useApi('/oversight')
  const [board, setBoard] = useState(false)
  if (!o) return <Loading error={error} />
  const k = o.kpis
  const kpi = (label: string, value: React.ReactNode, sub: React.ReactNode, cls = '') => (
    <div className={`card kpi ${cls}`}><div className="l">{label}</div><div className="v">{value}</div><div className="l">{sub}</div></div>
  )
  return (
    <>
      <div className="head">
        <div>
          <h1>Oversight</h1>
          <p className="muted">What needs a decision today, across every claim PoliBridge holds.</p>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span className="chip">SYNTHETIC · {fmt(o.now, true).toUpperCase()}</span>
          <button className="btn primary" onClick={() => setBoard(true)}>Board summary</button>
        </div>
      </div>

      {o.banner && (
        <div className="banner">
          <Badge kind="high">High impact</Badge>
          <span><b>{o.banner.title.replace(': ', ' · ')}</b> — {o.banner.text}</span>
          <Link to={`/advisories/${o.banner.obligation_id}`}>Open advisory →</Link>
        </div>
      )}

      <div className="grid g6">
        {kpi('Overdue tasks', k.overdue_tasks, <span className="red">{o.high_overdue} High impact</span>)}
        {kpi('Cannot Determine', k.cannot_determine, 'assets blocking closure', 'cdk')}
        {kpi('Active risk acceptances', k.accepted_risks,
          o.risk_expiring_soon ? `${o.risk_expiring_soon} expires in ${o.risk_expiring_days} days` : 'none expiring soon')}
        {kpi('Expired evidence', k.expired_evidence, 'claims reverted')}
        {kpi('Lapsed monitoring', <span className={k.lapsed_monitoring ? 'red' : ''}>{k.lapsed_monitoring}</span>, 'monitoring claims')}
        {kpi('Canary pass rate', <>{k.canary.passed}<span className="muted" style={{ fontSize: 18 }}>/{k.canary.runs}</span></>,
          o.canary_failure ? <span className="red">{o.canary_failure.asset.split(' ·')[0]} routing failed</span> : 'all passing')}
      </div>

      <div className="grid g21" style={{ gridTemplateColumns: '1.1fr 1fr' }}>
        <div className="card">
          <div className="sec"><h2>Overdue and escalated</h2><Link to="/tasks">All tasks</Link></div>
          <table>
            <thead><tr><th>Task</th><th>Owner</th><th>Late by</th><th>Escalated to</th></tr></thead>
            <tbody>
              {o.overdue.map((t: any) => (
                <tr key={t.id}>
                  <td><Link to={`/tasks/${t.id}`}><b>{t.title}</b></Link><div className="faint mono">{t.ref} · {t.impact}</div></td>
                  <td>{t.owner}{t.owner_note && <div className="faint">{t.owner_note}</div>}</td>
                  <td className="late">{t.late_by}</td>
                  <td>{t.escalated_to}</td>
                </tr>
              ))}
              {!o.overdue.length && <tr><td colSpan={4} className="muted">Nothing overdue.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="card">
          <div className="sec"><h2>Recently lapsed claims</h2><Link to="/claims">All claims</Link></div>
          {o.lapsed.map((l: any, i: number) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '52px 1fr', gap: 10, padding: '10px 0', borderBottom: '1px solid #efede6' }}>
              <span className="mono muted">{fmtDay(l.ts)}</span>
              <div>
                {l.claim_id ? <Link to={`/claims/${l.claim_id}`}><b>{l.statement}</b></Link> : <b>{l.statement}</b>}
                <div className="muted" style={{ fontSize: 13 }}>{l.detail}</div>
              </div>
            </div>
          ))}
          {!o.lapsed.length && <p className="muted">No recent lapses.</p>}
        </div>
      </div>

      {o.canary_failure && (
        <div className="card" style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 20 }}>
          <div style={{ flex: 1 }}>
            <div className="mono faint">SELF-TEST · {o.canary_failure.ref} · {o.canary_failure.asset.split(' ·')[0].toUpperCase()}</div>
            <h2 style={{ fontSize: 17, margin: '4px 0' }}>Routing canary failed</h2>
            <p className="muted">{o.canary_failure.failures[0]}. Reported separately from operational metrics.</p>
          </div>
          <Link className="btn" to="/canary" style={{ textDecoration: 'none' }}>View canary run</Link>
        </div>
      )}
      {board && <BoardSummary onClose={() => setBoard(false)} />}
    </>
  )
}

function BoardSummary({ onClose }: { onClose: () => void }) {
  const { data: b } = useApi('/board-summary')
  return (
    <Modal title="Board risk summary" sub="Missed remediation deadlines escalate to the risk owner and appear here." onClose={onClose}>
      {!b ? <Loading /> : (
        <>
          <h2 style={{ marginTop: 14, fontSize: 13 }}>Items for the next board risk report</h2>
          {b.board_risk_items.map((t: any) => (
            <p key={t.task} style={{ padding: '6px 0', borderBottom: '1px solid #efede6' }}>
              TSK-{String(t.task).padStart(4, '0')} · {t.asset} · {t.impact} · due {fmt(t.remediate_due)} · {t.status}
            </p>
          ))}
          {!b.board_risk_items.length && <p className="muted">None.</p>}
          <h2 style={{ marginTop: 14, fontSize: 13 }}>Accepted risks</h2>
          {b.accepted_risks.map((r: any) => <p key={r.id}>RA-{r.id} on task {r.task}, expires {fmt(r.expiry)}</p>)}
          {!b.accepted_risks.length && <p className="muted">None.</p>}
          <h2 style={{ marginTop: 14, fontSize: 13 }}>Canary failures</h2>
          {b.canary_failures.map((c: string) => <p key={c}>{c}</p>)}
          {!b.canary_failures.length && <p className="muted">None.</p>}
        </>
      )}
      <div className="actions"><button className="btn" onClick={onClose}>Close</button></div>
    </Modal>
  )
}
