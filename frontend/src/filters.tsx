export type Opt = [string, string] | string
export function Controls({ q, setQ, selects, shown, total, onClear }: {
  q: string; setQ: (v: string) => void; shown: number; total: number; onClear: () => void
  selects: { label: string; value: string; set: (v: string) => void; options: Opt[] }[]
}) {
  const dirty = q || selects.some((x) => x.value)
  return (
    <div className="card" style={{ marginTop: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
      <input style={{ flex: '1 1 240px', width: 'auto' }} placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      {selects.map((x) => (
        <select key={x.label} style={{ width: 'auto' }} value={x.value} onChange={(e) => x.set(e.target.value)} aria-label={x.label}>
          <option value="">{x.label}: all</option>
          {x.options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return <option key={v} value={v}>{l}</option> })}
        </select>))}
      <span className="faint">{shown} of {total}</span>
      {dirty && <button className="btn sm" onClick={onClear}>Clear</button>}
    </div>
  )
}
