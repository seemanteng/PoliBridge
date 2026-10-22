import { Link, useSearchParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, ImpactBadge, Loading, useAction } from '../ui'
import Canary from './Canary'

const hours = (h: number) => (h >= 48 ? `${Math.floor(h / 24)}d ${Math.round(h % 24)}h` : `${Math.round(h)}h`)
const TYPE: Record<string, [string, string]> = { unsigned: ['cd', 'Unsigned'], overdue: ['over', 'Overdue'], interim: ['high', 'No interim control'] }

export default function Alerts() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'selftests' ? 'selftests' : 'alerts'
  return (
    <>
      <div className="head"><div><h1>Alerts</h1>
        <p className="muted" style={{ maxWidth: 720 }}>Acknowledgements and remediations that nobody has dealt with. Timers escalate them automatically; you can also remind the owner or escalate right now.</p></div></div>
      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <button className={`btn ${tab === 'alerts' ? 'primary' : ''}`} onClick={() => setParams({})}>Escalations</button>
        <button className={`btn ${tab === 'selftests' ? 'primary' : ''}`} onClick={() => setParams({ tab: 'selftests' })}>Self-tests</button></div>
      {tab === 'alerts' ? <Escalations /> : <Canary />}
    </>
  )
}

function Escalations() {
  const { data: a, error } = useApi('/alerts')
  const { run, error: aerr, busy } = useAction()
  if (!a) return <Loading error={error} />
  const n = (t: string) => a.items.filter((i: any) => i.type === t).length
  const act = (id: number, what: 'escalate' | 'remind') => run(async () => { await api.post(`/tasks/${id}/${what}`); refreshAll() })
  return (
    <>
      <div className="grid g3">
        <div className="card kpi"><div className="l">Unsigned acknowledgements</div><div className="v"><span className={n('unsigned') ? 'red' : ''}>{n('unsigned')}</span></div><div className="l">owner has not signed off in time</div></div>
        <div className="card kpi"><div className="l">Overdue remediations</div><div className="v"><span className={n('overdue') ? 'red' : ''}>{n('overdue')}</span></div><div className="l">past their deadline</div></div>
        <div className="card kpi"><div className="l">Other alerts</div><div className="v">{a.findings.length + n('interim')}</div><div className="l">findings and missing interim controls</div></div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><div><h2>Needs escalating</h2>
          <p className="muted">Escalation climbs one step at a time: the owner's manager, then the CISO, then the risk owner and senior management, who also see it in the board report.</p></div></div>
        <table><thead><tr><th>Item</th><th>Owner</th><th>Late by</th><th>Escalated to so far</th><th></th></tr></thead><tbody>
          {a.items.map((i: any) => (
            <tr key={i.type + i.task_id} className={i.type === 'overdue' ? 'bad' : ''}>
              <td><Badge kind={TYPE[i.type][0]}>{TYPE[i.type][1]}</Badge> <ImpactBadge impact={i.impact} />
                <div><Link to={`/remediations/${i.task_id}`}><b>{i.title}</b></Link></div><div className="faint mono">{i.ref} · {i.what}</div></td>
              <td>{i.owner || '—'}</td><td className="late">{hours(i.late_by_h)}</td>
              <td>{i.escalated_to || <span className="muted">nobody yet</span>}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button className="btn sm" disabled={busy} onClick={() => act(i.task_id, 'remind')}>Remind owner</button>{' '}
                {i.next ? <button className="btn sm primary" disabled={busy} onClick={() => act(i.task_id, 'escalate')}>Escalate to {i.next}</button>
                  : <span className="faint">top of the ladder</span>}</td></tr>))}
          {!a.items.length && <tr><td colSpan={5} className="muted">Nothing is waiting. Items appear here when an owner has not signed off or updated work by its deadline, for example after you ingest an advisory on the Acknowledgements page.</td></tr>}
        </tbody></table>
        {aerr && <p className="err">{aerr}</p>}
      </div>

      <div className="grid g2">
        <div className="card"><h2>Open findings</h2>
          <table><tbody>{a.findings.map((f: any) => (
            <tr key={f.id}><td><b>{f.subject}</b><div className="muted">{f.detail}</div><div className="faint">{f.kind.replace(/_/g, ' ')} · {f.severity} · assigned to {f.assigned_to || 'nobody'}</div></td></tr>))}
            {!a.findings.length && <tr><td className="muted">No open findings.</td></tr>}</tbody></table></div>
        <div className="card"><h2>Recent escalations and reminders</h2>
          <table><tbody>{a.log.map((l: any, i: number) => (
            <tr key={i}><td><div className="faint mono">{fmt(l.ts, true)}</div><div>{l.message}</div>
              <div className="faint">{l.kind.replace(/_/g, ' ')} → {l.to || '—'} {!l.delivered && <span className="red">· not delivered</span>}</div></td></tr>))}
            {!a.log.length && <tr><td className="muted">Nothing sent yet.</td></tr>}</tbody></table></div>
      </div>
    </>
  )
}
