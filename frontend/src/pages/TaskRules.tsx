// Component 5: how an owner follows the vendor's instruction inside PoliBridge, and which response windows apply
// to a task, what each one counts from, and why. The numbers and their reasons come from backend/timings.yaml.
import { useState } from 'react'
import { api, fmt, refreshAll } from '../api'
import { Badge, ImpactBadge, Modal, useAction } from '../ui'

export function VendorInstruction({ t }: { t: any }) {
  const v = t.vendor
  if (!v) return null
  const interimH = t.interim?.due_h
  return (
    <div className="note" style={{ marginBottom: 10 }}>
      <b>Follow the vendor's instruction</b>
      <ol style={{ margin: '6px 0 0', paddingLeft: 20 }}>
        <li>The vendor fix for {v.cve} is <b>{v.product} {v.required}</b> or later{v.current && <> (this system runs {v.current})</>}.
          <div className="faint">Published fixed releases: {v.published}{v.issuers?.length > 0 && <> · reported by {v.issuers.join(', ')}</>}</div></li>
        <li>Apply that release, then click <b>Mark fixed</b>.</li>
        <li>Submit evidence: a scan or version check across the component's folder, showing {v.required} or later (rule E6).</li>
        <li>An independent reviewer accepts it, then the system owner (IT) and Information Security both sign off.</li>
      </ol>
      <p className="faint" style={{ margin: '8px 0 0' }}>Can't apply the vendor fix yet? Record the vendor's workaround as an interim control
        {interimH ? ` (due within ${interimH} hours)` : t.emergency ? ' straight away: the fix itself is due within 48 hours' : ''}.
        It reduces exposure but does not close the task: only the fixed release, verified, or a time-limited risk acceptance does.</p>
    </div>
  )
}

const STATE: Record<string, [string, string]> = {
  met: ['info', 'Met'], running: ['', 'Running'], missed: ['over', 'Missed'], late: ['over', 'Late'], sent: ['', 'Requested'] }

export function ResponseRules({ t }: { t: any }) {
  const r = t.response_rules
  const [overriding, setOverriding] = useState(false)
  if (!r) return null
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="sec"><h2>Response rules for this task</h2><span className="faint">Rules C1, D1–D3</span></div>
      <div className="badges" style={{ margin: '6px 0' }}><ImpactBadge impact={r.impact} emergency={r.emergency} />
        {r.overdue_at_discovery && <Badge kind="over">Found after its deadline</Badge>}
        {r.override && <Badge kind="cd">Task-specific deadline</Badge>}</div>
      <p className="muted" style={{ margin: '4px 0 6px' }}>
        {r.emergency ? 'Emergency timings: a critical or exploited flaw on an internet-facing system. ' : `${r.impact} impact timings. `}
        The fix deadline counts from <b>{fmt(r.trigger_at, true)}</b>, when {r.trigger_label}. Acknowledgement and the
        progress check count from when the task was assigned ({fmt(r.assigned, true)}).</p>
      {r.start_note && <p className="faint" style={{ margin: '0 0 6px' }}>{r.start_note}</p>}
      {r.override && <div className="note" style={{ margin: '6px 0' }}><b>Deadline shortened by {r.override.by}</b>
        <p className="muted" style={{ margin: '4px 0 0' }}>{r.override.reason} · was {fmt(r.override.previous, true)}, now {fmt(r.override.due, true)}</p></div>}
      {r.rows.map((x: any) => (
        <div key={x.key} className="rule-row">
          <div className="signoff-head"><b>{x.step} · <span className="mono">{x.window}</span></b>
            <span><Badge kind={STATE[x.state]?.[0] || ''}>{STATE[x.state]?.[1] || x.state}</Badge>
              <span className="faint"> {x.status.split(' · ').slice(1).join(' · ')}</span></span></div>
          <div className="faint">Counts from {x.counts_from === 'trigger' ? 'the vendor release / exploit date' : 'assignment'}</div>
          {x.why && <div className="muted"><b>Why:</b> {x.why}</div>}
          {x.if_missed && <div className="faint">If missed: {x.if_missed}.</div>}
        </div>))}
      {r.can_override && <button className="btn sm" style={{ marginTop: 10 }} onClick={() => setOverriding(true)}>Shorten deadline…</button>}
      <p className="faint" style={{ margin: '8px 0 0' }}>The windows and their reasons are set in backend/timings.yaml. A deadline can only be
        shortened here; more time needs a risk acceptance (D4).</p>
      {overriding && <OverrideModal t={t} onClose={() => setOverriding(false)} />}
    </div>
  )
}

const local = (iso: string) => iso.slice(0, 16)

function OverrideModal({ t, onClose }: { t: any; onClose: () => void }) {
  const current = t.response_rules.rows.find((x: any) => x.key === 'remediate')?.due
  const [due, setDue] = useState(current ? local(current) : '')
  const [reason, setReason] = useState('')
  const { run, error, busy } = useAction()
  const go = async () => {
    if (await run(async () => { await api.post(`/tasks/${t.id}/deadline-override`, { due, reason }); refreshAll() })) onClose()
  }
  return (
    <Modal title="Shorten the fix deadline" sub="For an emergency, a specific obligation or a vulnerability-specific policy. Recorded by the CISO, risk owner, Compliance or Legal." onClose={onClose}>
      <label>New fix deadline (must be earlier than {current ? fmt(current, true) : 'the current one'})</label>
      <input type="datetime-local" value={due} max={current ? local(current) : undefined} onChange={(e) => setDue(e.target.value)} />
      <label>Reason</label>
      <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Regulator directive: patch within 24 hours" />
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !due || !reason.trim()} onClick={go}>Shorten deadline</button></div>
    </Modal>
  )
}
