import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, fmt, refreshAll, useApi } from '../api'
import { Badge, ImpactBadge, Loading, Modal, pretty, TaskStatusPills, useAction } from '../ui'
import { EvidenceList } from './Evidence'
import { SignoffCard } from './Signoffs'
import { GuidanceText } from './documents/Guidance'

function FixSteps({ t }: { t: any }) {
  const adv = t.advisory
  const g = t.guidance[0]
  if (!adv || t.renewal) return g ? <div className="card" style={{ marginTop: 14 }}><h2>Guidance for this system</h2>
    {g.status === 'released' ? <GuidanceText g={g} /> : <p className="muted" style={{ marginTop: 6 }}>Held back for Compliance: {g.reason}</p>}</div> : null
  const paths = (t.scope_paths || []).join(', ') || 'every application folder'
  const affected = adv.affected_versions.join(' or ')
  const fix = adv.fixed_version
  const rem = t.kind === 'investigation'
  const step = (_n: number, title: string, body: any) => (
    <li style={{ margin: '10px 0' }}><b>{title}</b><div className="muted" style={{ marginTop: 2 }}>{body}</div></li>)
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <h2>What to do</h2>
      <p className="muted" style={{ margin: '6px 0' }}>{adv.cve} affects <b>{adv.product}</b> {affected}. {fix ? <>Fixed in <b>{fix}</b>.</> : 'No fixed version is published yet.'}
        {adv.kev && ' It is being exploited in the wild.'}</p>
      <ol style={{ margin: '4px 0 0', paddingLeft: 20 }}>
        {step(1, `Find out whether ${t.asset} runs ${adv.product}`,
          <>Look in <code>{paths}</code>. For example: <code>find {(t.scope_paths?.[0] || '/').replace('/**', '')} -iname "{adv.product}*"</code>.
            {rem && ' Today there is no current component record for this system, which is why it is marked Cannot Determine.'}</>)}
        {step(2, 'Compare what you find with the fixed version', <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          <li><b>Not installed, or already {fix ? `at ${fix}` : 'fixed'}:</b> nothing to patch. Go to step 3 and submit evidence that shows it.</li>
          <li><b>In the affected range:</b> {fix ? <>upgrade to {fix}</> : 'apply the vendor mitigation'}, redeploy, then click <b>Mark fixed</b> on this page.</li>
          <li><b>Cannot be patched in time:</b> request a time-limited risk acceptance and record an interim control{t.interim.due_h ? ` within ${t.interim.due_h} hours` : ''}.</li></ul>)}
        {step(3, 'Submit evidence on this page', <>Use an authenticated scan or a direct version check. It must cover <code>{paths}</code> and be dated within the validity window{t.kind === 'remediation' ? ', after you marked it fixed' : ''}.</>)}
        {step(4, 'It is then reviewed and signed off', <>An independent reviewer ({t.people.reviewer || 'unassigned'}) approves the evidence. The system owner (IT) and Information Security then each sign off.</>)}
      </ol>
      <p className="faint" style={{ marginTop: 10 }}>Deadlines: acknowledge {t.ack.note} · {t.interim.due_h ? `interim control ${t.interim.note} · ` : ''}fix by {t.disposition.due}.</p>
      {g && g.status === 'released' && <details style={{ marginTop: 8 }}><summary className="faint" style={{ cursor: 'pointer' }}>Where this guidance comes from</summary><GuidanceText g={g} /></details>}
      {g && g.status !== 'released' && <p className="muted" style={{ marginTop: 6 }}>Written guidance held back for Compliance: {g.reason}</p>}
      {g?.consult && <p className="faint" style={{ marginTop: 6 }}>{g.consulted_at ? `Checked by ${g.consult}` : `${g.consult} is checking it alongside`}</p>}
    </div>
  )
}

export function TaskList() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'evidence' ? 'evidence' : 'updates'
  return (
    <>
      <div className="head"><div><h1>Remediations</h1>
        <p className="muted" style={{ maxWidth: 720 }}>The work each owner must do to fix or investigate a finding, and the proof that it was done. Escalations for work that has stalled are on the Alerts page.</p></div></div>
      <div className="tabs" role="group" aria-label="Remediation sections">
        <button className={`btn ${tab === 'updates' ? 'primary' : ''}`} aria-pressed={tab === 'updates'} onClick={() => setParams({})}>Remediation updates</button>
        <button className={`btn ${tab === 'evidence' ? 'primary' : ''}`} aria-pressed={tab === 'evidence'} onClick={() => setParams({ tab: 'evidence' })}>Evidence review</button></div>
      {tab === 'updates' ? <Updates /> : <EvidenceList embedded />}
    </>
  )
}

function Updates() {
  const [scope, setScope] = useState<'open' | 'all'>('open')
  const { data: allTasks, error } = useApi('/tasks')
  const tasks = allTasks ? (scope === 'open'
    ? allTasks.filter((t: any) => ['open', 'reopened', 'fixed_pending_review'].includes(String(t.status).toLowerCase()))
    : allTasks) : null
  return (
    <>
      <div className="head" style={{ marginTop: 14 }}><p className="muted">Separate acknowledgement and remediation countdowns for every item. Canary tests are excluded.</p>
        <div className="task-list-controls"><span className="muted">{tasks ? `${tasks.length} ${scope === 'open' ? 'open' : 'total'} task${tasks.length === 1 ? '' : 's'}` : 'Loading tasks'}</span>
          <div className="tabs sm" role="group" aria-label="Filter remediations">{(['open', 'all'] as const).map((s) =>
            <button type="button" key={s} className={`btn ${scope === s ? 'primary' : ''}`} aria-pressed={scope === s} onClick={() => setScope(s)}>{s[0].toUpperCase() + s.slice(1)}</button>)}</div></div></div>
      <div className="card" style={{ marginTop: 16 }}>
        {!tasks ? <Loading error={error} /> : (
          <table><thead><tr><th>Remediation</th><th>Impact</th><th>Owner</th><th>Status</th><th>Latest update</th><th>Acknowledge</th><th>Remediate</th></tr></thead>
            <tbody>{tasks.map((t: any) => (
              <tr key={t.id} className={t.overdue ? 'bad' : ''}>
                <td><Link to={`/remediations/${t.id}`}><b>{t.kind === 'investigation' ? 'Investigate' : 'Remediate'} {t.component} on {t.asset}</b></Link>
                  <div className="faint mono">TSK-{String(t.id).padStart(4, '0')}</div></td>
                <td><ImpactBadge impact={t.impact} short /></td>
                <td>{t.owner}</td><td className="task-status-cell"><TaskStatusPills status={t.status} overdue={t.overdue} /></td>
                <td style={{ maxWidth: 240 }}>{t.latest_update ? <><b style={{ fontWeight: 500 }}>{t.latest_update.title}</b><div className="faint">{fmt(t.latest_update.ts, true)}</div></> : <span className="muted">—</span>}</td>
                <td className="mono">{t.acknowledged_at ? 'acknowledged' : `${t.countdown.ack_remaining_h}h`}</td>
                <td className={`mono ${t.countdown.remediate_remaining_h < 0 ? 'red' : ''}`}>{t.countdown.remediate_remaining_h}h</td>
              </tr>))}
              {!tasks.length && <tr><td colSpan={7} className="muted">{scope === 'open' ? 'No open remediations.' : 'No remediations found.'}</td></tr>}</tbody></table>)}
      </div>
    </>
  )
}

type Dlg = null | 'evidence' | 'risk' | 'reject' | 'interim'

export function TaskDetail() {
  const { id } = useParams()
  const { data: t, error } = useApi(`/tasks/${id}`)
  const risks = useApi('/risk-acceptances')
  const [dlg, setDlg] = useState<Dlg>(null)
  const { run, error: aerr } = useAction()
  if (!t) return <Loading error={error} />
  const act = (path: string) => run(async () => { await api.post(path); refreshAll() })
  const myRisks = (risks.data || []).filter((r: any) => r.task_id === t.id)
  const open = ['open', 'reopened', 'fixed_pending_review'].includes(t.status)
  return (
    <>
      <div className="crumbs"><Link to={`/acknowledgements/${t.obligation_id}`}>ADV-{String(t.obligation_id).padStart(4, '0')}</Link> / {t.ref}</div>
      <div className="badges" style={{ marginTop: 8 }}><ImpactBadge impact={t.impact} />
        {t.kind === 'investigation' && <Badge kind="lbl-cd">? Investigation · {t.asset.split(' ·')[0]} is Cannot Determine</Badge>}
        <TaskStatusPills status={t.status} overdue={t.overdue} /></div>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: 780 }}>{t.summary}</p>

      <div className="grid g3">
        <div className="card"><div className="faint">Acknowledgement · {t.ack.due_h ? `${t.ack.due_h}h` : 'working days'}</div>
          <div className="stat blue">{t.ack.acknowledged ? 'Acknowledged' : 'Awaiting acknowledgement'}</div>
          <div className="faint mono">{t.ack.acknowledged ? `${fmt(t.ack.at)} · ${t.ack.note}` : t.ack.note}</div></div>
        <div className={`card ${t.interim.state === 'reopened' ? 'dash' : ''}`}><div className="faint">Interim control · {t.interim.due_h ? `${t.interim.due_h}h` : 'not required'}</div>
          <div className={`stat ${t.interim.state === 'reopened' ? 'purple' : t.interim.state === 'pending' ? 'red' : ''}`}>
            {{ reopened: 'Reopened', recorded: 'In place', pending: 'Required', not_required: 'Not required' }[t.interim.state as string]}</div>
          <div className="faint mono">{t.interim.note}</div></div>
        <div className={`card ${t.disposition.overdue ? 'alert' : ''}`}><div className="faint">Disposition · {t.disposition.days} days</div>
          <div className={`stat ${t.disposition.overdue ? 'red' : ''}`}>{t.disposition.remaining}</div>
          <div className="faint mono">due {t.disposition.due} · not reset</div></div>
      </div>

      <div className="grid g21">
        <div className="card"><h2>Routing and escalation</h2>
          <ul className="tl" style={{ marginTop: 6 }}>{t.timeline.map((e: any, i: number) => (
            <li key={i} className={e.tone}><span className="ts">{fmt(e.ts)}</span><div><div className="tt">{e.title}</div>
              {e.detail && <div className="muted" style={{ fontSize: 13 }}>{e.detail}</div>}</div></li>))}</ul>
          {myRisks.length > 0 && <><h2 style={{ marginTop: 14 }}>Risk acceptance</h2>{myRisks.map((r: any) => (
            <div key={r.id} className="note" style={{ border: '1px solid var(--line)', marginTop: 8 }}>
              <b>RA-{r.id}</b> · {pretty(r.status)} · expires {fmt(r.expiry)}<p className="muted">{r.scenario} — controls: {r.controls}</p>
              {r.status === 'pending' && <button className="btn sm primary" onClick={() => act(`/risk-acceptances/${r.id}/approve`)}>Approve as authorised risk owner</button>}</div>))}</>}
        </div>
        <div>
          <div className="card"><h2>People</h2>
            <dl className="kv" style={{ marginTop: 10 }}>
              <dt>Owner</dt><dd><b>{t.people.owner?.name}</b><div className="faint">{t.people.owner?.title}</div></dd>
              <dt>Manager</dt><dd>{t.people.manager || '—'}</dd><dt>Risk owner</dt><dd>{t.people.risk_owner}</dd>
              <dt>Reviewer</dt><dd>{t.people.reviewer}<div className="faint">must differ from submitter</div></dd></dl></div>
          {t.signoffs.length > 0 && <SignoffCard t={t} />}
          <FixSteps t={t} />
          <div className="card" style={{ marginTop: 14 }}><h2>To close this task</h2>
            <p className="muted" style={{ margin: '8px 0' }}>{t.close_requirement}</p>
            {t.renewal && <div className="note" style={{ marginBottom: 10 }}>
              <b>Renewal steps</b><ol style={{ margin: '6px 0 0', paddingLeft: 20 }}>
                <li>Renew or recheck the control for {t.component} on {t.asset}.</li>
                <li>{t.renewal.marked_fixed ? 'Already marked fixed. You can continue with evidence.' : <>Click <b>Mark fixed</b> when that work is done.</>}</li>
                <li>Submit evidence using <b>{t.renewal.method.replaceAll('_', ' ')}</b> and the exact scope shown below. The evidence time must be after you marked it fixed.</li>
                <li>Have an independent reviewer approve the evidence in <Link to="/remediations?tab=evidence">Evidence review</Link>.</li>
              </ol><div className="faint" style={{ marginTop: 6 }}>Required scope: <code>{t.renewal.scope.join(', ')}</code></div>
              {t.evidence.some((e: any) => e.status === 'rejected' && e.reasons.some((r: string) => r.includes('missing scope'))) &&
                <p className="muted" style={{ margin: '8px 0 0' }}>Your earlier submission had an empty scope. The evidence form now fills in the required scope for you.</p>}
            </div>}
            {open && <>
              {!t.ack.acknowledged && <button className="btn block" onClick={() => act(`/tasks/${t.id}/acknowledge`)}>Acknowledge</button>}
              {t.kind === 'remediation' && t.status !== 'fixed_pending_review' && <button className="btn block" onClick={() => act(`/tasks/${t.id}/fixed`)}>Mark fixed</button>}
              <button className="btn primary block" onClick={() => setDlg('evidence')}>Submit evidence</button>
              <button className="btn block" onClick={() => setDlg('risk')}>Request risk acceptance</button>
              {t.impact === 'High' && !t.interim.note?.startsWith('Web') && t.interim.state !== 'recorded' &&
                <button className="btn block" onClick={() => setDlg('interim')}>Record interim control</button>}
              <button className="btn danger block" onClick={() => setDlg('reject')}>Reject — not my system</button>
              <p className="faint" style={{ marginTop: 8 }}>Rejecting reroutes the task immediately and raises an ownership finding. The deadline keeps running.</p></>}
            {aerr && <p className="err">{aerr}</p>}</div>
        </div>
      </div>
      {dlg === 'evidence' && <EvidenceModal t={t} onClose={() => setDlg(null)} />}
      {dlg === 'risk' && <RiskModal t={t} onClose={() => setDlg(null)} />}
      {dlg === 'reject' && <ReasonModal t={t} kind="reject" onClose={() => setDlg(null)} />}
      {dlg === 'interim' && <ReasonModal t={t} kind="interim" onClose={() => setDlg(null)} />}
    </>
  )
}

function ReasonModal({ t, kind, onClose }: { t: any; kind: 'reject' | 'interim'; onClose: () => void }) {
  const [text, setText] = useState('')
  const { run, error, busy } = useAction()
  const isRej = kind === 'reject'
  const go = async () => { if (await run(async () => {
    await api.post(`/tasks/${t.id}/${isRej ? 'reject' : 'interim-control'}`, isRej ? { reason: text } : { description: text }); refreshAll() })) onClose() }
  return <Modal title={isRej ? 'Reject — not my system' : 'Record interim control'}
    sub={isRej ? 'The task is rerouted to the business-unit head now. The original deadline keeps running.' : 'A compensating control that reduces exposure until the fix lands.'} onClose={onClose}>
    <label>{isRej ? 'Reason' : 'Control description'}</label><textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
    {error && <p className="err">{error}</p>}
    <div className="actions"><button className="btn" onClick={onClose}>Cancel</button><button className={`btn ${isRej ? 'danger-solid' : 'primary'}`} disabled={busy || !text} onClick={go}>{isRej ? 'Reject and reroute' : 'Record'}</button></div></Modal>
}

function RiskModal({ t, onClose }: { t: any; onClose: () => void }) {
  const [f, setF] = useState({ scenario: '', controls: '', residual_risk: 'Medium', days: 14 })
  const { run, error, busy } = useAction()
  const go = async () => { if (await run(async () => { await api.post(`/tasks/${t.id}/risk-acceptance`, f); refreshAll() })) onClose() }
  return <Modal title="Request risk acceptance" sub="Time-limited. An authorised risk owner other than you must approve; it reopens on expiry." onClose={onClose}>
    <label>Risk scenario</label><textarea rows={2} value={f.scenario} onChange={(e) => setF({ ...f, scenario: e.target.value })} />
    <label>Compensating controls</label><textarea rows={2} value={f.controls} onChange={(e) => setF({ ...f, controls: e.target.value })} />
    <label>Residual risk</label><select value={f.residual_risk} onChange={(e) => setF({ ...f, residual_risk: e.target.value })}><option>Low</option><option>Medium</option><option>High</option></select>
    <label>Expires in (days, max 90)</label><input type="number" value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })} />
    {error && <p className="err">{error}</p>}
    <div className="actions"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy || !f.scenario || !f.controls} onClick={go}>Submit request</button></div></Modal>
}

function EvidenceModal({ t, onClose }: { t: any; onClose: () => void }) {
  const clock = useApi('/clock')
  const [f, setF] = useState({ purpose: t.kind === 'investigation' ? 'absence' : 'remediation', asset_name: t.asset, component: t.component || '',
    scope: t.renewal?.scope?.join(', ') || '', method: t.renewal?.method || 'authenticated_scan', observed_version: '', timestamp: '' })
  const { run, error, busy } = useAction()
  const go = async () => { if (await run(async () => {
    const r = await api.post(`/tasks/${t.id}/evidence`, { ...f, scope: f.scope.split(',').map((s: string) => s.trim()).filter(Boolean),
      observed_version: f.observed_version || null, timestamp: f.timestamp || clock.data?.now })
    refreshAll(); if (r.status === 'rejected') alert('Evidence rejected: ' + r.reasons.join('; ')) })) onClose() }
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  return <Modal title="Submit evidence" sub={t.renewal ? 'Verify the renewed monitoring control after marking it fixed. Scope and method are filled from the expired claim.' : 'Structured evidence is checked against the claim: asset, component scope, method, timestamp.'} onClose={onClose}>
    <label>Purpose</label><select value={f.purpose} onChange={(e) => set('purpose', e.target.value)}>
      <option value="remediation">{t.renewal ? 'Monitoring control renewed' : 'Remediation (it is patched)'}</option>{!t.renewal && <option value="absence">Absence (component not present)</option>}</select>
    <label>Asset the evidence is for</label><input value={f.asset_name} onChange={(e) => set('asset_name', e.target.value)} />
    <label>Component examined</label><input value={f.component} onChange={(e) => set('component', e.target.value)} />
    <label>{t.renewal ? 'Scope examined (comma separated; keep the control identifiers)' : <>Scope examined (comma separated; <code>/opt/app/**</code> means everything under)</>}</label><input value={f.scope} onChange={(e) => set('scope', e.target.value)} placeholder={t.renewal ? t.renewal.scope.join(', ') : '/opt/acis/**'} />
    {t.renewal && <p className="faint">Required: {t.renewal.scope.join(', ')}</p>}
    <label>Method</label><select value={f.method} onChange={(e) => set('method', e.target.value)}>
      <option value="authenticated_scan">Authenticated scan</option><option value="direct_version_check">Direct version check</option>
      <option value="certificate_check">Certificate check</option><option value="configuration_review">Configuration review</option>
      <option value="sbom">SBOM</option><option value="unauthenticated_external_scan">Unauthenticated external scan</option></select>
    {f.purpose === 'remediation' && !t.renewal && <><label>Observed version</label><input value={f.observed_version} onChange={(e) => set('observed_version', e.target.value)} placeholder="2.3.32" /></>}
    <label>Evidence generated at (blank = now)</label><input value={f.timestamp} onChange={(e) => set('timestamp', e.target.value)} placeholder="2017-03-15T10:48:00" />
    {error && <p className="err">{error}</p>}
    <div className="actions"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy} onClick={go}>Submit for review</button></div></Modal>
}
