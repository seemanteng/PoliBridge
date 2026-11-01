// Component 5: how an owner follows the vendor's instruction inside PoliBridge, and which response windows apply
// to a task and why. The numbers and their reasons come from backend/timings.yaml.
import { fmt } from '../api'
import { Badge } from '../ui'

export function VendorInstruction({ t }: { t: any }) {
  const v = t.vendor
  if (!v) return null
  const high = t.impact === 'High'
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
        {high ? ' (due within 48 hours)' : ''}. It reduces exposure but does not close the task: only the fixed release, verified, or a
        time-limited risk acceptance does.</p>
    </div>
  )
}

const STATE: Record<string, [string, string]> = { met: ['info', 'Met'], running: ['', 'Running'], missed: ['over', 'Missed'], late: ['over', 'Late'] }

export function ResponseRules({ t }: { t: any }) {
  const r = t.response_rules
  if (!r) return null
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="sec"><h2>Response rules for this task</h2><span className="faint">Rules D1–D3</span></div>
      <p className="muted" style={{ margin: '4px 0 6px' }}>{r.impact} impact. The clock started {fmt(r.started, true)}, when this task was created
        {r.vendor_published && <> (the vendor published {fmt(r.vendor_published, true)})</>}. {r.start_note}</p>
      {r.rows.map((x: any) => (
        <div key={x.key} className="rule-row">
          <div className="signoff-head"><b>{x.step} · <span className="mono">{x.window}</span></b>
            <span><Badge kind={STATE[x.state]?.[0] || ''}>{STATE[x.state]?.[1] || x.state}</Badge>
              <span className="faint"> {x.status.split(' · ').slice(1).join(' · ')}</span></span></div>
          {x.why && <div className="muted"><b>Why:</b> {x.why}</div>}
          {x.if_missed && <div className="faint">If missed: {x.if_missed}.</div>}
        </div>))}
      <p className="faint" style={{ margin: '8px 0 0' }}>The windows and their reasons are set in backend/timings.yaml.</p>
    </div>
  )
}
