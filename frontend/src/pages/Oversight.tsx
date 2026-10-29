import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, fmt, fmtDay, useApi } from '../api'
import { Badge, Loading, Modal, pretty } from '../ui'
import { Controls } from '../filters'
import { has, uniq } from '../filterUtils'

const TABS = [['dashboard', 'Dashboard'], ['log', 'Audit log'], ['notifications', 'Notifications']] as const
type IssueKey = 'overdue_tasks' | 'cannot_determine' | 'accepted_risks' | 'expired_evidence' | 'failed_deliveries' | 'pending_confirmations' | 'overdue_confirmations' | 'lapsed_monitoring' | 'canary'
type IssueItem = { label: string; detail: string; href: string }
const confirmationStatus = (a: any) => a.state === 'overdue' || a.state === 'lapsed'
  ? `Overdue since ${fmtDay(a.due, true)}`
  : a.due ? `Confirm by ${fmtDay(a.due, true)}` : 'Due date unavailable'
const FALLBACK: Record<IssueKey, string> = {
  overdue_tasks: '/remediations', cannot_determine: '/claims', accepted_risks: '/remediations',
  expired_evidence: '/claims', failed_deliveries: '/oversight?tab=notifications',
  pending_confirmations: '/registry?tab=Systems', overdue_confirmations: '/registry?tab=Systems',
  lapsed_monitoring: '/claims', canary: '/alerts?tab=selftests',
}

async function relatedRecords(key: IssueKey, oversight: any): Promise<IssueItem[]> {
  if (Array.isArray(oversight.drilldowns?.[key])) return oversight.drilldowns[key]
  const item = (label: string, detail: string, href: string): IssueItem => ({ label, detail, href })
  if (key === 'overdue_tasks') return (oversight.overdue || []).map((t: any) =>
    item(t.title, `${t.owner || 'No owner'} · ${t.late_by} late`, `/remediations/${t.id}`))
  if (key === 'pending_confirmations' || key === 'overdue_confirmations') {
    const all = oversight.confirmations || oversight.overdue_confirmations || []
    return all.filter((a: any) => key === 'overdue_confirmations'
      ? a.state === 'overdue' || a.state === 'lapsed' : a.state !== 'overdue' && a.state !== 'lapsed')
      .map((a: any) => item(a.asset, `${a.owner || 'No owner'} · ${confirmationStatus(a)}`,
        `/registry?tab=Systems&asset=${a.asset_id}`))
  }
  if (key === 'failed_deliveries') return (oversight.failed_deliveries || []).map((n: any) =>
    item(`${n.kind.replace(/_/g, ' ')} to ${n.recipient || 'no recipient'}`, n.message,
      `/oversight?tab=notifications&notification=${n.id}`))
  if (key === 'cannot_determine' || key === 'expired_evidence' || key === 'lapsed_monitoring') {
    const claims = await api.get('/claims')
    return claims.filter((c: any) => key === 'cannot_determine'
      ? c.claim_type === 'applicability' && c.status === 'active' && c.value === 'cannot_determine'
      : c.status === 'expired' && (key !== 'lapsed_monitoring' || c.claim_type === 'monitoring'))
      .map((c: any) => item(c.asset || c.statement, c.statement, `/claims/${c.id}`))
  }
  if (key === 'accepted_risks') {
    const risks = await api.get('/risk-acceptances')
    return risks.filter((r: any) => r.status === 'approved').map((r: any) =>
      item(`Risk acceptance RA-${r.id}`, `Task ${r.task_id} · expires ${fmt(r.expiry, true)}`, `/remediations/${r.task_id}`))
  }
  const canary = await api.get('/canary')
  return canary.runs.filter((r: any) => r.status !== 'running').map((r: any) =>
    item(r.asset, `${r.ref} · ${pretty(r.status)}`, `/alerts?tab=selftests&run=${r.id}`))
}

function TabBar({ tab }: { tab: string }) {
  const [, setParams] = useSearchParams()
  return (
    <div className="tabs" role="group" aria-label="Oversight sections">{TABS.map(([k, l]) => (
      <button key={k} className={`btn ${tab === k ? 'primary' : ''}`} aria-pressed={tab === k} onClick={() => setParams(k === 'dashboard' ? {} : { tab: k })}>{l}</button>))}</div>
  )
}

export default function Oversight() {
  const [params] = useSearchParams()
  const tab = params.get('tab') === 'log' ? 'log' : params.get('tab') === 'notifications' ? 'notifications' : 'dashboard'
  if (tab === 'dashboard') return <Dashboard tabs={<TabBar tab={tab} />} />
  return (
    <>
      <div className="head"><div><h1>Oversight</h1><p className="muted">{tab === 'log' ? 'Every action taken in PoliBridge, newest first. Nothing here can be edited.' : 'Every notification PoliBridge has sent, and whether it was delivered.'}</p></div></div>
      <TabBar tab={tab} />
      {tab === 'log' ? <AuditLog /> : <Notifications />}
    </>
  )
}

function Dashboard({ tabs }: { tabs: ReactNode }) {
  const { data: o, error } = useApi('/oversight')
  const [board, setBoard] = useState(false)
  const [issue, setIssue] = useState<{ title: string; items: IssueItem[] } | null>(null)
  const [loadingKey, setLoadingKey] = useState<IssueKey | null>(null)
  const navigate = useNavigate()
  if (!o) return <Loading error={error} />
  const k = o.kpis
  const confirmations = o.confirmations || o.overdue_confirmations || []
  const overdueConfirmations = k.pending_confirmations === undefined
    ? confirmations.filter((a: any) => a.state === 'overdue' || a.state === 'lapsed').length
    : k.overdue_confirmations
  const pendingConfirmations = k.pending_confirmations === undefined
    ? confirmations.length - overdueConfirmations : k.pending_confirmations
  const openIssue = async (key: IssueKey, title: string) => {
    setLoadingKey(key)
    try {
      const items = await relatedRecords(key, o)
      if (items.length === 1) navigate(items[0].href)
      else if (items.length === 0) navigate(FALLBACK[key])
      else setIssue({ title, items })
    } catch {
      navigate(FALLBACK[key])
    } finally {
      setLoadingKey(null)
    }
  }
  const tone: Partial<Record<IssueKey, string>> = {
    overdue_tasks: 'tone-danger', failed_deliveries: 'tone-danger', overdue_confirmations: 'tone-danger',
    cannot_determine: 'tone-warning', pending_confirmations: 'tone-warning',
    expired_evidence: 'tone-purple', canary: 'tone-purple', accepted_risks: 'tone-good',
  }
  const kpi = (key: IssueKey, label: string, value: React.ReactNode, sub: React.ReactNode, cls = '') => (
    <button type="button" className={`card kpi kpi-link ${tone[key] || ''} ${cls}`} onClick={() => openIssue(key, label)}
      aria-label={`${label}: ${typeof value === 'number' || typeof value === 'string' ? value : ''}. Open related records`}>
      <div className="l">{label}</div><div className="v">{value}</div><div className="l">{sub}</div>
      <div className="kpi-open">{loadingKey === key ? 'Opening…' : 'View records →'}</div>
    </button>
  )
  return (
    <>
      <div className="head">
        <div>
          <h1>Oversight</h1>
          <p className="muted">Monitor overdue actions, ownership confirmations, evidence validity, and notification delivery.</p>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span className="chip">SYNTHETIC DATA · {fmt(o.now, true).toUpperCase()}</span>
          <button className="btn primary" onClick={() => setBoard(true)}>Management summary</button>
        </div>
      </div>

      {tabs}
      <h2 className="oversight-group-title">Items requiring attention</h2>
      <div className="grid oversight-cards">
        {kpi('overdue_tasks', 'Overdue actions', k.overdue_tasks,
          `${o.high_overdue} high impact · remediation or investigation deadlines passed`)}
        {kpi('cannot_determine', 'Applicability unresolved', k.cannot_determine,
          'Further assessment is needed to determine impact', 'cdk')}
        {kpi('expired_evidence', 'Lapsed evidence', k.expired_evidence,
          k.lapsed_monitoring ? `Includes ${k.lapsed_monitoring} monitoring control${k.lapsed_monitoring === 1 ? '' : 's'} requiring re-verification`
            : 'Supporting evidence requires renewal')}
        {kpi('failed_deliveries', 'Failed notifications', k.failed_deliveries,
          'Notifications that did not reach their recipients')}
      </div>

      <h2 className="oversight-group-title">Ownership confirmations</h2>
      <div className="grid oversight-cards oversight-cards-two">
        {kpi('pending_confirmations', 'Pending confirmations', pendingConfirmations,
          'Awaiting owner confirmation before the due date')}
        {kpi('overdue_confirmations', 'Overdue confirmations', overdueConfirmations,
          'Owner confirmation was not completed by the due date')}
      </div>
      {!o.drilldowns && <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>Due dates are unavailable from the currently running backend.</p>}

      <h2 className="oversight-group-title">Assurance checks</h2>
      <div className="grid oversight-cards oversight-cards-two">
        {kpi('accepted_risks', 'Active risk acceptances', k.accepted_risks,
          o.risk_expiring_soon ? `${o.risk_expiring_soon} expire within ${o.risk_expiring_days} days` : 'Approved exceptions under review')}
        {kpi('canary', 'Notification test results', k.canary.runs ? `${k.canary.passed}/${k.canary.runs}` : '—',
          k.canary.runs ? `${k.canary.runs - k.canary.passed} failed · verifies delivery and escalation` : 'No completed tests yet')}
      </div>

      <div className="grid g21" style={{ gridTemplateColumns: '1fr 1fr', marginTop: 16 }}>
        <div className="card">
          <div className="sec"><h2>Messages that did not arrive</h2><Link to="/oversight?tab=notifications">All notifications</Link></div>
          {o.failed_deliveries.slice(0, 6).map((n: any) => <p key={n.id} style={{ margin: '8px 0' }}>
            <Link to={`/oversight?tab=notifications&notification=${n.id}`}><b>{n.kind.replace(/_/g, ' ')}</b> to {n.recipient || 'no recipient'}</Link> · {fmt(n.ts, true)}
            {n.task_id && <> · <Link to={`/remediations/${n.task_id}`}>Task {n.task_id}</Link></>}
          </p>)}
          {!o.failed_deliveries.length && <p className="muted">No failed deliveries.</p>}
        </div>
        <div className="card">
          <div className="sec"><h2>Owners who need to confirm</h2><Link to="/registry">Registry</Link></div>
          {confirmations.slice(0, 6).map((a: any) => <p key={a.asset_id} style={{ margin: '8px 0' }}>
            <Link to={`/registry?tab=Systems&asset=${a.asset_id}`}><b>{a.asset}</b></Link> · {a.owner || 'No owner'} · {confirmationStatus(a)}
          </p>)}
          {!confirmations.length && <p className="muted">All ownership confirmations are current.</p>}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><h2>Who changed the mailing list</h2><Link to="/oversight?tab=log">Full audit log</Link></div>
        {o.mailing_list_changes.slice(0, 6).map((e: any) => <p key={e.id} style={{ margin: '8px 0' }}>
          <span className="mono muted">{fmt(e.ts, true)}</span> · <Link to={`/registry?tab=Systems&asset=${e.asset_id}`}><b>{e.asset}</b></Link> · {e.action.replace(/_/g, ' ')} · {e.actor}
        </p>)}
        {!o.mailing_list_changes.length && <p className="muted">No mailing-list changes recorded.</p>}
      </div>

      <div className="grid g21" style={{ gridTemplateColumns: '1.1fr 1fr' }}>
        <div className="card">
          <div className="sec"><h2>Late tasks</h2><Link to="/remediations">All work</Link></div>
          <table>
            <thead><tr><th>Remediation</th><th>Owner</th><th>Late by</th><th>Escalated to</th></tr></thead>
            <tbody>
              {o.overdue.map((t: any) => (
                <tr key={t.id}>
                  <td><Link to={`/remediations/${t.id}`}><b>{t.title}</b></Link><div className="faint mono">{t.ref} · {t.impact}</div></td>
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
          <div className="sec"><h2>Recently invalidated checks</h2><Link to="/claims">All checks</Link></div>
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
            <h2 style={{ fontSize: 17, margin: '4px 0' }}>A notification self-test failed</h2>
            <p className="muted">{o.canary_failure.failures[0]}. Reported separately from operational metrics.</p>
          </div>
          <Link className="btn" to="/alerts?tab=selftests" style={{ textDecoration: 'none' }}>View canary run</Link>
        </div>
      )}
      {board && <BoardSummary onClose={() => setBoard(false)} />}
      {issue && <Modal title={issue.title} sub="Choose a record to inspect." onClose={() => setIssue(null)} wide>
        {issue.items.map((item: IssueItem, i: number) => <Link key={`${item.href}-${i}`} className="issue-row" to={item.href} onClick={() => setIssue(null)}>
          <span><b>{item.label}</b><span className="muted">{item.detail}</span></span><span aria-hidden="true">→</span>
        </Link>)}
        <div className="actions"><button className="btn" onClick={() => setIssue(null)}>Close</button></div>
      </Modal>}
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
              TSK-{String(t.task).padStart(4, '0')} · {t.asset} · {t.impact} · due {fmt(t.remediate_due)} · {pretty(t.status)}
            </p>
          ))}
          {!b.board_risk_items.length && <p className="muted">None.</p>}
          <h2 style={{ marginTop: 14, fontSize: 13 }}>Accepted risks</h2>
          {b.accepted_risks.map((r: any) => <p key={r.id}>RA-{r.id} on task {r.task}, expires {fmt(r.expiry)}</p>)}
          {!b.accepted_risks.length && <p className="muted">None.</p>}
          <h2 style={{ marginTop: 14, fontSize: 13 }}>Canary failures</h2>
          {b.canary_failures.map((c: string) => <p key={c}>{c}</p>)}
          {!b.canary_failures.length && <p className="muted">None.</p>}
          <p className="muted">{b.failed_deliveries} failed deliveries · {b.pending_confirmations === undefined
            ? `${b.overdue_confirmations} ownership confirmations needing action`
            : `${b.pending_confirmations} pending confirmations · ${b.overdue_confirmations} overdue confirmations`} · {b.open_findings} open operational findings</p>
        </>
      )}
      <div className="actions"><button className="btn" onClick={onClose}>Close</button></div>
    </Modal>
  )
}

const LINK: Record<string, string> = { task: '/remediations', obligation: '/acknowledgements', claim: '/claims', evidence: '/evidence' }
const detailText = (d: Record<string, unknown> | null) => Object.entries(d || {}).filter(([, v]) => v !== null && v !== '' && v !== undefined)
  .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' · ')

function AuditLog() {
  const { data: ev, error } = useApi('/timeline')
  const [f, setF] = useState<Record<string, string>>({})
  const [rows, setRows] = useState(100)
  if (!ev) return <Loading error={error} />
  const set = (k: string) => (v: string) => setF((x) => ({ ...x, [k]: v }))
  const shown = ev.filter((e: any) => has(f.q || '', e.actor, e.entity, e.action, detailText(e.detail), e.entity_id)
    && (!f.entity || e.entity === f.entity) && (!f.actor || e.actor === f.actor) && (!f.action || e.action === f.action))
  return (
    <>
      <Controls q={f.q || ''} setQ={set('q')} shown={shown.length} total={ev.length} onClear={() => setF({})} selects={[
        { label: 'Record type', value: f.entity || '', set: set('entity'), options: uniq(ev.map((e: any) => e.entity)) },
        { label: 'Who', value: f.actor || '', set: set('actor'), options: uniq(ev.map((e: any) => e.actor)) },
        { label: 'Action', value: f.action || '', set: set('action'), options: uniq(ev.map((e: any) => e.action)) }]} />
      <div className="card" style={{ marginTop: 14 }}>
        <table><thead><tr><th>When</th><th>Who</th><th>Record</th><th>Action</th><th>Details</th></tr></thead><tbody>
          {shown.slice(0, rows).map((e: any) => (
            <tr key={e.id}><td className="mono" style={{ whiteSpace: 'nowrap' }}>{fmt(e.ts, true)}</td><td>{e.actor}</td>
              <td>{LINK[e.entity] && e.entity_id ? <Link to={`${LINK[e.entity]}/${e.entity_id}`}>{e.entity} #{e.entity_id}</Link> : <>{e.entity}{e.entity_id ? ` #${e.entity_id}` : ''}</>}</td>
              <td><b>{e.action.replace(/_/g, ' ')}</b></td><td className="muted" style={{ maxWidth: 420 }}>{detailText(e.detail)}</td></tr>))}
          {!shown.length && <tr><td colSpan={5} className="muted">No events match.</td></tr>}</tbody></table>
        {shown.length > rows && <p style={{ marginTop: 10 }}><button className="btn" onClick={() => setRows(rows + 100)}>Show 100 more ({shown.length - rows} left)</button></p>}
      </div>
    </>
  )
}

function Notifications() {
  const { data: ns, error } = useApi('/notifications')
  const { data: people } = useApi('/people')
  const [params] = useSearchParams()
  const focus = Number(params.get('notification')) || null
  const [f, setF] = useState<Record<string, string>>({})
  useEffect(() => {
    if (ns && focus) requestAnimationFrame(() => document.getElementById(`notification-${focus}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }, [ns, focus])
  if (!ns || !people) return <Loading error={error} />
  const name = (id: number | null) => people.find((p: any) => p.id === id)?.name || '—'
  const set = (k: string) => (v: string) => setF((x) => ({ ...x, [k]: v }))
  const shown = ns.filter((n: any) => n.id === focus || (has(f.q || '', n.message, name(n.person_id), n.kind) && (!f.kind || n.kind === f.kind)
    && (!f.delivered || (f.delivered === 'yes') === n.delivered)))
  return (
    <>
      <Controls q={f.q || ''} setQ={set('q')} shown={shown.length} total={ns.length} onClear={() => setF({})} selects={[
        { label: 'Type', value: f.kind || '', set: set('kind'), options: uniq(ns.map((n: any) => n.kind)).map((k) => [k, k.replace(/_/g, ' ')] as [string, string]) },
        { label: 'Delivered', value: f.delivered || '', set: set('delivered'), options: [['yes', 'Delivered'], ['no', 'Not delivered']] }]} />
      <div className="card" style={{ marginTop: 14 }}>
        <table><thead><tr><th>When</th><th>To</th><th>Type</th><th>Message</th><th>Delivered</th></tr></thead><tbody>
          {shown.map((n: any) => (
            <tr key={n.id} id={`notification-${n.id}`} className={n.id === focus ? 'hl' : ''}><td className="mono" style={{ whiteSpace: 'nowrap' }}>{fmt(n.created_at, true)}</td><td>{name(n.person_id)}</td>
              <td>{n.kind.replace(/_/g, ' ')}</td><td className="muted">{n.message}</td>
              <td>{n.delivered ? <Badge kind="info">✓</Badge> : <Badge kind="over">✕ failed</Badge>}</td></tr>))}
          {!shown.length && <tr><td colSpan={5} className="muted">No notifications match.</td></tr>}</tbody></table>
      </div>
    </>
  )
}
