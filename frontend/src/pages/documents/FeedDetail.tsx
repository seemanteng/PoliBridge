import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../../api'
import { Badge, ImpactBadge, Loading, StateBadge, useAction } from '../../ui'
import { FeedBadge } from './common'

const OUTCOME: Record<string, [string, string]> = {
  ingested: ['info', 'Added'], merged: ['ok', 'Combined with existing'], conflict: ['cd', 'Combined · sources disagree'],
  skipped: ['', 'Ignored'], rejected: ['over', 'Rejected'],
}
const hrs = (h: number | null) => (h == null ? '—' : h >= 48 ? `${Math.round(h / 24)}d` : h >= 1 ? `${h.toFixed(1)}h` : `${Math.round(h * 60)}m`)

// One system against one alert: whether it is affected, and whether its owner has picked it up.
function Cell({ c }: { c: any }) {
  if (!c) return <span className="faint">—</span>
  return (
    <div>
      <StateBadge state={c.state} />
      {c.task_id && <div style={{ marginTop: 4, fontSize: 12 }}>
        <Link to={`/remediations/${c.task_id}`} className="mono">{c.task_ref}</Link>{' '}
        {c.open ? (c.acknowledged ? <span className="faint">· acknowledged</span> : <span className="red">· waiting for owner</span>)
          : <span className="faint">· {c.task_status}</span>}</div>}
    </div>
  )
}

export default function FeedDetail() {
  const [params] = useSearchParams()
  const sid = params.get('source')
  const { data: d, error } = useApi(`/feeds/${sid}/dashboard`)
  const { run, error: aerr, busy } = useAction()
  const [note, setNote] = useState('')
  if (!d) return <Loading error={error} />
  const s = d.source
  const fresh = d.alerts.filter((a: any) => a.new || a.awaiting_ack)
  const say = (r: any) => setNote(r.ok ? (r.received.length ? r.received.join(' · ') : 'Nothing new.') : r.error)
  const hit = (sys: any) => d.alerts.filter((a: any) => ['applies', 'cannot_determine'].includes(a.cells[sys.id]?.state) && a.cells[sys.id]?.open).length
  return (
    <>
      <div className="crumbs" style={{ marginTop: 16 }}><Link to="/documents">Sources</Link> / {s.name}</div>
      <div className="card" style={{ marginTop: 10 }}>
        <div className="sec"><div><h2 style={{ fontSize: 18 }}>{s.name} <FeedBadge feed={s.feed} /></h2>
          <p className="muted">{s.authority} · {s.jurisdiction} · owner {s.owner || 'nobody'}{s.feed && <> · last worked {fmt(s.feed.last_success_at)}</>}</p></div>
          <div style={{ display: 'flex', gap: 8, whiteSpace: 'nowrap' }}>
            <button className="btn" disabled={busy} onClick={() => run(async () => { say((await api.post('/feeds/poll')).find((x: any) => x.source === s.name) || { ok: true, received: [] }); refreshAll() })}>Check now</button>
            <button className="btn primary" disabled={busy || !d.upcoming.length} onClick={() => run(async () => { say(await api.post(`/feeds/${s.id}/release-next`)); refreshAll() })}>Send next demo alert</button></div></div>
        {note && <p className="muted">{note}</p>}
        {s.intake === 'feed' && <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
          <input type="checkbox" style={{ width: 'auto' }} checked={s.simulate_outage} disabled={busy}
            onChange={(e) => run(async () => { await api.patch(`/sources/${s.id}`, { simulate_outage: e.target.checked }); refreshAll() })} />
          Simulate an outage</label>}
        {aerr && <p className="err">{aerr}</p>}
        <p className="faint" style={{ marginTop: 6 }}>If this feed fails {d.rule.max_failed_polls} times in a row, or goes {d.rule.max_silence_h} hours without working, its owner gets a High-priority finding: a broken feed fails silently, like the expired certificate at Equifax.</p>
      </div>

      <div className="grid g6" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        {[['Alerts from this feed', d.alerts.length, `${d.alerts.filter((a: any) => a.new).length} new in the last ${d.rule.new_hours} hours`, false],
          ['Systems affected', d.systems.filter((x: any) => d.alerts.some((a: any) => a.cells[x.id]?.state === 'applies' && a.cells[x.id]?.open)).length, `of ${d.systems.length} systems`, true],
          ["Can't tell yet", d.systems.filter((x: any) => d.alerts.some((a: any) => a.cells[x.id]?.state === 'cannot_determine' && a.cells[x.id]?.open)).length, 'systems with a missing or old software list', true],
          ['Waiting for owners', d.alerts.reduce((n: number, a: any) => n + a.awaiting_ack, 0), 'tasks not yet acknowledged', true]].map(([l, v, sub, bad]) => (
          <div key={l as string} className="card kpi"><div className="l">{l}</div><div className="v"><span className={bad && v ? 'red' : ''}>{v as number}</span></div><div className="l">{sub}</div></div>))}
      </div>

      <div className="card" style={{ marginTop: 16, borderColor: fresh.length ? 'var(--blue)' : undefined }}>
        <h2>New alerts</h2>
        <p className="muted">Received in the last {d.rule.new_hours} hours, or still waiting for an owner to acknowledge.</p>
        {fresh.map((a: any) => (
          <div key={a.obligation_id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', padding: '10px 0', borderTop: '1px solid var(--line)' }}>
            <div><Link to={`/acknowledgements/${a.obligation_id}`}><b className="mono">{a.cve}</b></Link> · {a.product}
              {a.new && <> <Badge kind="info">New</Badge></>} {a.impact && <ImpactBadge impact={a.impact} short />}
              <div className="faint">{a.cvss == null ? 'no severity score yet' : `CVSS ${a.cvss}`}{a.kev ? ' · actively exploited' : ''} · received {fmt(a.received_at)}{a.also_from.length > 0 && ` · also reported by ${a.also_from.join(', ')}`}</div></div>
            <div style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
              <div><Badge kind="lbl-applies">{a.counts.applies} affected</Badge> <Badge kind="lbl-cd">{a.counts.cannot_determine} can't tell</Badge> <Badge kind="lbl-dna">{a.counts.does_not_apply} not affected</Badge></div>
              {a.awaiting_ack > 0 && <div className="faint red" style={{ marginTop: 4 }}>{a.awaiting_ack} owner{a.awaiting_ack > 1 ? 's' : ''} still to acknowledge</div>}</div>
          </div>))}
        {!fresh.length && <p className="muted" style={{ marginTop: 6 }}>No new alerts. {d.upcoming.length ? `The next demo alert from this feed arrives ${fmt(d.upcoming[0].available_at)}, or press Send next demo alert.` : ''}</p>}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Systems and the alerts that affect them</h2>
        <p className="muted">Every Equifax system (mock data) against every alert this feed has sent. A system with no software list, or an out-of-date one, shows as Cannot Determine: missing information is never treated as safe.</p>
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table><thead><tr><th style={{ minWidth: 240 }}>System</th>
            {d.alerts.map((a: any) => <th key={a.obligation_id} style={{ minWidth: 150 }}><Link to={`/acknowledgements/${a.obligation_id}`} className="mono">{a.cve}</Link>
              <div className="faint">{a.product}{a.new ? ' · new' : ''}</div></th>)}
            <th>Open alerts</th></tr></thead>
            <tbody>{d.systems.map((sys: any) => (
              <tr key={sys.id} className={hit(sys) ? 'hl' : ''}>
                <td><b>{sys.name}</b><div className="faint">{sys.owner ? `owner ${sys.owner}` : <span className="red">no valid owner</span>}{sys.tags.length ? ` · ${sys.tags.join(', ')}` : ''}</div>
                  <div className="mono" style={{ fontSize: 11.5, marginTop: 3 }}>{sys.software.length ? sys.software.join(', ') : 'no software list on record'}
                    {sys.inventory === 'stale' && <span className="red"> · out of date</span>}</div></td>
                {d.alerts.map((a: any) => <td key={a.obligation_id}><Cell c={a.cells[sys.id]} /></td>)}
                <td><b className={hit(sys) ? 'red' : ''}>{hit(sys)}</b></td></tr>))}</tbody></table></div>
        {!d.alerts.length && <p className="muted" style={{ marginTop: 8 }}>This feed has not sent any alerts yet.</p>}
      </div>

      <div className="grid g2">
        <div className="card"><h2>Alert log</h2>
          <p className="muted">Everything this feed delivered, and what PoliBridge did with it. An alert is added if it affects software we use, is being actively exploited, has a CVSS score of {d.rule.min_cvss} or higher, or has no score yet.</p>
          <table style={{ marginTop: 6 }}><tbody>{d.log.map((i: any) => (
            <tr key={i.id}><td><b className="mono">{i.cve}</b><div className="faint">{i.product}</div></td>
              <td><Badge kind={OUTCOME[i.outcome]?.[0]}>{OUTCOME[i.outcome]?.[1] || i.outcome}</Badge><div className="faint" style={{ maxWidth: 240 }}>{i.reason}</div></td>
              <td className="faint" style={{ whiteSpace: 'nowrap' }}>received {fmt(i.received_at)}<div>{hrs(i.latency.publish_to_receive_h)} after publication</div></td></tr>))}
            {!d.log.length && <tr><td className="muted">Nothing received yet.</td></tr>}</tbody></table></div>
        <div className="card"><h2>Coming up (demo)</h2>
          <p className="muted">This feed is simulated: these alerts are queued and arrive on their own.</p>
          <table style={{ marginTop: 6 }}><tbody>{d.upcoming.map((i: any) => (
            <tr key={i.id}><td><b className="mono">{i.cve}</b><div className="faint">{i.product}</div></td><td className="faint">arrives {fmt(i.available_at)}</td></tr>))}
            {!d.upcoming.length && <tr><td className="muted">No more demo alerts queued for this feed.</td></tr>}</tbody></table></div>
      </div>
    </>
  )
}
