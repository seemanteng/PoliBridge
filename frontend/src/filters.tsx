import type { ReactNode } from 'react'

export type Opt = [string, string] | string
export function Controls({ q, setQ, selects, shown, total, onClear, action }: {
  q: string; setQ: (v: string) => void; shown: number; total: number; onClear: () => void
  selects: { label: string; value: string; set: (v: string) => void; options: Opt[] }[]; action?: ReactNode
}) {
  const dirty = q || selects.some((x) => x.value)
  return (
    <div className="card filter-bar">
      <input className="filter-search" placeholder="Search…" aria-label="Search records" value={q} onChange={(e) => setQ(e.target.value)} />
      {selects.map((x) => (
        <select key={x.label} value={x.value} onChange={(e) => x.set(e.target.value)} aria-label={x.label}>
          <option value="">{x.label}: all</option>
          {x.options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return <option key={v} value={v}>{l}</option> })}
        </select>))}
      <span className="faint filter-count">{shown} of {total}</span>
      {dirty && <button className="btn sm quiet" onClick={onClear}>Clear filters</button>}
      {action && <div className="filter-action">{action}</div>}
    </div>
  )
}
