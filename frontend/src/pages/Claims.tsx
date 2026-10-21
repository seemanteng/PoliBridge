import { Link, useParams } from 'react-router-dom'
import { fmt, useApi } from '../api'
import { Badge, Loading } from '../ui'

const TYPE: Record<string, string> = { applicability: 'Applicability claim', remediation: 'Remediation claim', ownership: 'Ownership claim', monitoring: 'Monitoring claim', risk_acceptance: 'Risk acceptance' }

export function ClaimList() {
  const { data: cs, error } = useApi('/claims')
  return (
    <>
      <h1>Claims</h1>
      <p className="muted">Every status PoliBridge holds is a claim with scoped evidence and an expiry. Lapsed claims revert to Cannot Determine.</p>
      <div className="card" style={{ marginTop: 16 }}>
        {!cs ? <Loading error={error} /> : (
          <table><thead><tr><th>Claim</th><th>Type</th><th>Asset</th><th>Status</th><th>Expiry</th></tr></thead>
            <tbody>{cs.map((c: any) => (
              <tr key={c.id}><td><Link to={`/claims/${c.id}`}><b>{c.statement}</b></Link></td><td>{TYPE[c.claim_type]}</td><td>{c.asset}</td>
                <td><Badge kind={c.status === 'expired' ? 'cd' : c.status === 'active' ? 'info' : ''}>{c.status === 'expired' ? '? Lapsed' : c.status}</Badge></td>
                <td className="mono">{fmt(c.expiry, true)}</td></tr>))}</tbody></table>)}
      </div>
    </>
  )
}

export function ClaimDetail() {
  const { id } = useParams()
  const { data: c, error } = useApi(`/claims/${id}`)
  if (!c) return <Loading error={error} />
  const start = new Date(c.life.start).getTime(), end = c.life.end ? new Date(c.life.end).getTime() : null, now = new Date(c.life.now).getTime()
  const span = end ? end - start : 1
  const nowPct = Math.min(100, Math.max(0, ((now - start) / span) * 100))
  const warnPct = Math.max(0, 100 - Math.min(100, (21 * 864e5 / span) * 100))
  const lapsed = c.status === 'expired'
  const scope = (c.evidence_scope as string[]).filter((s) => !s.startsWith('cert:'))
  return (
    <>
      <div className="crumbs"><Link to="/claims">Claims</Link> / {c.ref}</div>
      <div className="badges" style={{ marginTop: 8 }}><Badge>{TYPE[c.type]}</Badge>
        {lapsed ? <Badge kind="cd">? Lapsed → Cannot Determine</Badge> : <Badge kind="info">{c.status}</Badge>}</div>
      <h1>“{c.statement}”</h1>
      <p className="muted">{c.type === 'monitoring' ? 'This claim was only as good as the certificate on the inspection device. When the certificate expired, so did the claim.' : c.reason || 'Evidence life and dependants are shown below.'}</p>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><h2>Evidence life</h2><span className="faint">{c.depends_on ? `Expiry tied to ${c.depends_on.label.replace('TLS certificate · ', 'the ')} certificate` : 'Expiry tied to its evidence'}</span></div>
        <div className="life"><i style={{ width: `${Math.min(nowPct, warnPct)}%`, background: '#9fb1ee' }} />
          <i style={{ width: `${Math.max(0, Math.min(nowPct, 100) - warnPct)}%`, background: '#f2c29b' }} />
          <i style={{ width: `${100 - Math.max(nowPct, warnPct)}%`, background: lapsed ? '#a58be0' : '#e6e4dc' }} /></div>
        <div className="mono faint" style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span>{fmt(c.life.start, true)} · accepted</span><span className={lapsed ? 'purple' : ''}>{fmt(c.expiry, true)} · {lapsed ? 'lapsed' : 'expires'}</span></div></div>

      {c.dependents.length > 0 && <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><h2>{lapsed ? 'What the lapse reopened' : 'What depends on this claim'}</h2><span className="faint">Rule F2 · each flagged to its owner</span></div>
        <div className="flow">
          {c.depends_on ? <div className="node depends"><div className="lbl red">Depends on</div><b>{c.depends_on.label}</b><div className="faint">{c.depends_on.expired ? 'Expired' : 'Expires'} {fmt(c.depends_on.expiry)}</div></div> : <div />}
          <span className="muted" style={{ textAlign: 'center' }}>→</span>
          <div className="node this"><div className="lbl purple">This claim · {c.ref}</div><b>{c.statement}</b><div className="faint">{lapsed ? 'Now Cannot Determine' : 'Active'}</div></div>
          <span className="muted" style={{ textAlign: 'center' }}>→</span>
          <div><div className="mono faint" style={{ marginBottom: 6 }}>DEPENDED ON BY · {c.dependents.filter((d: any) => d.reopened).length} REOPENED</div>
            {c.dependents.map((d: any, i: number) => <div key={i} className="node" style={{ marginBottom: 6, display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <b>{d.label}</b><span className="muted">{d.owner}{d.reopened ? ' · reopened' : ''}</span></div>)}</div></div></div>}

      <div className="grid g2">
        <div className="card"><h2>Claim record</h2><dl className="kv" style={{ marginTop: 10, gridTemplateColumns: '120px 1fr' }}>
          <dt>Asset · component</dt><dd>{c.asset}{c.component ? ` · ${c.component}` : ''}</dd>
          <dt>Evidence scope</dt><dd>{scope.join(', ') || '—'}</dd><dt>Method</dt><dd>{(c.method || '—').replaceAll('_', ' ')}</dd>
          <dt>Evidence time</dt><dd className="mono">{fmt(c.evidence_ts, true)}</dd><dt>Submitter</dt><dd>{c.submitter || '—'}</dd>
          <dt>Reviewer</dt><dd>{c.reviewer || '—'}</dd><dt>Expiry</dt><dd className="mono">{fmt(c.expiry, true)}</dd></dl></div>
        <div className="card"><h2>History</h2>{c.history.map((h: any, i: number) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '96px 1fr', gap: 10, padding: '8px 0', borderBottom: '1px solid #efede6' }}>
            <span className="mono muted">{fmt(h.ts)}</span><span className={h.text.startsWith('Expired') ? 'purple' : ''}>{h.text}</span></div>))}</div>
      </div>
    </>
  )
}
