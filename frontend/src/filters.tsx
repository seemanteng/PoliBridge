import type { ReactNode } from 'react'

export type Opt = [string, string] | string
export function Controls({ q, setQ, selects, shown, total, onClear, action, searchLabel = 'Search records',
  searchPlaceholder = 'Search…', countLabel = 'records' }: {
  q: string; setQ: (v: string) => void; shown: number; total: number; onClear: () => void
  selects: { label: string; value: string; set: (v: string) => void; options: Opt[] }[]; action?: ReactNode
  searchLabel?: string; searchPlaceholder?: string; countLabel?: string
}) {
  const dirty = q || selects.some((x) => x.value)
  return (
    <div className="card filter-bar">
      <input className="filter-search" placeholder={searchPlaceholder} aria-label={searchLabel} value={q} onChange={(e) => setQ(e.target.value)} />
      {selects.map((x) => (
        <select key={x.label} value={x.value} onChange={(e) => x.set(e.target.value)} aria-label={x.label}>
          <option value="">{x.label}: all</option>
          {x.options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return <option key={v} value={v}>{l}</option> })}
        </select>))}
      <span className="faint filter-count" aria-live="polite">Showing {shown} of {total} {countLabel}</span>
      {dirty && <button className="btn sm quiet" onClick={onClear}>Clear filters</button>}
      {action && <div className="filter-action">{action}</div>}
    </div>
  )
}
