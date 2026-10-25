import { Link } from 'react-router-dom'
import { fmt, useApi } from '../../api'
import { Badge } from '../../ui'

export const KIND_LABEL: Record<string, string> = { advisory: 'Advisory feed', vendor: 'Vendor', regulator: 'Regulator', policy: 'Internal policy', contract: 'Contract' }

const CHANGE: Record<string, [string, string]> = {
  changed: ['info', 'Changed'], moved: ['info', 'Moved and reworded'], renumbered: ['', 'Moved, same wording'],
  new: ['ok', 'New'], removed: ['over', 'Removed'],
}

export function ReviewBadge({ review }: { review: any }) {
  if (!review || review.status === 'feed') return null
  const [k, l] = ({ current: ['ok', 'Current'], due_soon: ['cd', 'Due soon'], overdue: ['over', 'Review overdue'], never: ['over', 'Never reviewed'] } as Record<string, [string, string]>)[review.status] || ['', review.status]
  return <Badge kind={k}>{l}</Badge>
}

export function FeedBadge({ feed }: { feed: any }) {
  if (!feed) return null
  const [k, l] = ({ healthy: ['ok', 'Healthy'], failing: ['cd', 'Failing'], silent: ['over', 'Stopped'] } as Record<string, [string, string]>)[feed.state] || ['', feed.state]
  return <Badge kind={k}>{l}</Badge>
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge kind={status === 'approved' ? 'info' : status === 'pending' ? 'cd' : 'over'}>{status === 'pending' ? 'Awaiting CCO' : status}</Badge>
}

export function PeopleSelect({ value, onChange, placeholder = 'Choose a person…' }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const { data: people } = useApi('/people')
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}><option value="">{placeholder}</option>
      {(people || []).filter((p: any) => p.active).map((p: any) => <option key={p.id} value={p.id}>{p.name}{p.title ? ` · ${p.title}` : ''}</option>)}</select>
  )
}

const cut = (s: string | null, n = 220) => (s && s.length > n ? s.slice(0, n) + '…' : s)

// One row per clause-level change: what moved, the wording before and after, and the obligation it touched.
export function ChangeList({ changes }: { changes: any[] }) {
  if (!changes.length) return <p className="muted">Nothing changed.</p>
  return (
    <table><thead><tr><th style={{ width: 150 }}>Clause</th><th style={{ width: 150 }}>Change</th><th>Wording</th><th style={{ width: 150 }}>Obligation</th></tr></thead><tbody>
      {changes.map((c: any, i: number) => (
        <tr key={i} className={c.kind === 'removed' ? 'bad' : ''}>
          <td className="mono"><b>{c.ref}</b>{c.old_ref && c.old_ref !== c.ref && <div className="faint">was {c.old_ref}</div>}</td>
          <td><Badge kind={CHANGE[c.kind]?.[0]}>{CHANGE[c.kind]?.[1] || c.kind}</Badge></td>
          <td style={{ fontSize: 13 }}>
            {c.old && c.kind !== 'renumbered' && <div className="faint" style={{ textDecoration: c.kind === 'removed' ? 'line-through' : 'none' }} title={c.old}>Before: {cut(c.old)}</div>}
            {c.new && <div title={c.new} style={{ marginTop: c.old ? 4 : 0 }}>{c.kind === 'renumbered' ? 'Same wording: ' : c.old ? 'Now: ' : ''}{cut(c.new)}</div>}</td>
          <td>{c.obligation_id ? <Link to={`/acknowledgements/${c.obligation_id}`} className="mono">OBL-{String(c.obligation_id).padStart(4, '0')}</Link> : c.affects ? <span className="mono">{c.affects}</span> : <span className="faint">—</span>}
            {c.obligation_status && <div className="faint">{c.obligation_status}</div>}
            {c.supersedes && <div className="faint">replaces OBL-{String(c.supersedes).padStart(4, '0')}</div>}
            {c.kind === 'removed' && c.obligation_id && <div className="faint">superseded</div>}</td>
        </tr>))}
    </tbody></table>
  )
}

export function Counts({ s }: { s: any }) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      <Badge kind="info">{s.changed ?? 0} changed</Badge><Badge kind="ok">{s.new ?? 0} new</Badge>
      <Badge kind="over">{Array.isArray(s.removed) ? s.removed.length : s.removed ?? 0} removed</Badge>
      <Badge>{s.renumbered ?? 0} renumbered</Badge><Badge>{s.unchanged ?? 0} unchanged</Badge></div>
  )
}

export const when = (iso?: string | null) => (iso ? fmt(iso, true) : '—')
export const short = (sha?: string | null) => (sha ? sha.slice(0, 12) : '—')
