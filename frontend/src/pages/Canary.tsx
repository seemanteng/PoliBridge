import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, Loading, pretty, useAction } from '../ui'

export default function Canary() {
  const { data: c, error } = useApi('/canary')
  const [params] = useSearchParams()
  const assets = useApi('/assets')
  const [asset, setAsset] = useState('')
  const { run, error: aerr, busy } = useAction()
  if (!c) return <Loading error={error} />
  const focus = Number(params.get('run')) || null
  const latest = c.runs.find((r: any) => r.id === focus) || c.runs.find((r: any) => r.status === 'failed') || c.runs[0]
  const go = () => run(async () => { await api.post('/canary/run', { asset_id: Number(asset) }); refreshAll() })
  return (
    <>
      <div className="head"><div><h1>Canary self-tests</h1>
        <p className="muted" style={{ maxWidth: 700 }}>Synthetic advisories, marked as tests, travel through the CISA/NVD feed and the real matching rule, then check that routing reaches a named person and escalation fires on time. They test the process, not whether a fix worked.</p></div>
        <div className="card" style={{ minWidth: 160 }}><div className="faint">This quarter</div><div className="stat">{c.passed}/{c.total} passed</div></div></div>

      {latest && <div className={`card ${latest.status === 'failed' ? 'alert' : ''}`} style={{ marginTop: 16 }}>
        <div className="canary-run-meta">{latest.ref} · {latest.asset} · {fmt(latest.ts, true)}</div>
        <h2 style={{ fontSize: 18, margin: '4px 0 8px' }}>{latest.status === 'failed' ? ({ feed: 'Failed at the feed', match: 'Failed at matching' } as Record<string, string>)[latest.stage] || 'Failed at routing' : latest.status === 'passed' ? 'Passed' : 'Running'}</h2>
        <div className="grid g2" style={{ marginTop: 0 }}>
          <div>{latest.steps.map((s: any) => (
            <div key={s.n} className="step"><span className={`r ${s.result}`}>{s.n} · {s.result.replace('_', ' ').toUpperCase()}</span>
              <div><b>{s.title}</b><div className="muted" style={{ fontSize: 13 }}>{s.detail}</div></div></div>))}</div>
          {latest.finding && <div className="note" style={{ background: '#f7f4fd', border: '1px dashed var(--purple)', alignSelf: 'start' }}>
            <b>Finding {latest.finding.ref}</b> assigned to {latest.finding.assignee}: {latest.failures[0]}. Reported separately from operational metrics.
            <div className="faint" style={{ marginTop: 4 }}>{pretty(latest.finding.status)}</div></div>}</div></div>}

      <div className="card" style={{ marginTop: 16 }}>
        <div className="sec"><div><h2>Recent runs</h2><p className="muted">Scheduled, and after changes to owners, systems or routing rules</p></div>
          <div style={{ display: 'flex', gap: 6 }}><select style={{ width: 220 }} value={asset} onChange={(e) => setAsset(e.target.value)}>
            <option value="">Run canary on…</option>{assets.data?.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
            <button className="btn" disabled={!asset || busy} onClick={go}>Run</button></div></div>
        <table><thead><tr><th>Run</th><th>System</th><th>Named owner</th><th>Result</th></tr></thead>
          <tbody>{c.runs.map((r: any) => (
            <tr key={r.id} className={r.id === focus ? 'hl' : ''}><td><strong className="canary-run-ref">{r.ref}</strong><div className="canary-run-date">{fmt(r.ts, true)}</div></td><td>{r.asset}</td>
              <td className={r.named_owner === 'Distribution list' || r.named_owner === 'No owner' ? 'red' : ''}>{r.named_owner === 'Distribution list' || r.named_owner === 'No owner' ? '✕ ' : '✓ '}{r.named_owner}</td>
              <td><Badge kind={r.status === 'failed' ? 'over' : r.status === 'passed' ? 'info' : ''}>{pretty(r.status)}</Badge></td></tr>))}</tbody></table>
        {aerr && <p className="err">{aerr}</p>}
        <p className="faint" style={{ marginTop: 10 }}>Runs are unannounced. Internal Audit runs its own seeded-fault tests independently of these canaries.</p></div>
    </>
  )
}
