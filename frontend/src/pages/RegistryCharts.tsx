import { useMemo, useState, type ReactNode } from 'react'

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s)
const UNIT_COLOURS = ['#315cbd', '#7352ae', '#a45d1a', '#25805a', '#1f7485', '#a84575', '#837319']

function Box({ x, y, w, h, bar, dim, danger, title, children, onEnter, onLeave }: {
  x: number; y: number; w: number; h: number; bar: string; dim?: boolean; danger?: boolean; title?: string
  children: ReactNode; onEnter?: () => void; onLeave?: () => void
}) {
  return (
    <g transform={`translate(${x},${y})`} opacity={dim ? 0.25 : 1} onMouseEnter={onEnter} onMouseLeave={onLeave} style={{ transition: 'opacity .12s' }}>
      {title && <title>{title}</title>}
      <rect width={w} height={h} rx={7} style={{ fill: danger ? 'var(--red-soft)' : '#fff', stroke: danger ? 'var(--red-line)' : 'var(--line)' }} />
      <rect width={5} height={h} rx={2.5} fill={bar} />
      {children}
    </g>
  )
}

/** Left-to-right reporting chart. Hover a person to light up the line above them and the people below. */
export function OrgChart({ people, show }: { people: any[]; show: Set<number> }) {
  const [hover, setHover] = useState<number | null>(null)
  const NW = 214, NH = 48, GX = 60, GY = 12
  const units = useMemo(() => [...new Set(people.map((p) => p.business_unit))].sort(), [people])
  const colour = (u: string) => UNIT_COLOURS[units.indexOf(u) % UNIT_COLOURS.length]

  const { pos, rows } = useMemo(() => {
    const pos = new Map<number, { d: number; y: number }>()
    let row = 0
    const kids = (id: number | null) => people.filter((p) => p.manager_id === id && show.has(p.id))
    const walk = (p: any, d: number): number => {
      const ks = kids(p.id)
      const y = ks.length ? (() => { const ys = ks.map((k) => walk(k, d + 1)); return (Math.min(...ys) + Math.max(...ys)) / 2 })() : row++
      pos.set(p.id, { d, y })
      return y
    }
    kids(null).forEach((r) => walk(r, 0))
    return { pos, rows: row }
  }, [people, show])

  const byId = new Map<number, any>(people.map((p) => [p.id, p]))
  const lit = useMemo(() => {
    if (hover == null) return null
    const s = new Set<number>()
    for (let p = byId.get(hover); p; p = byId.get(p.manager_id)) s.add(p.id)
    const down = (id: number) => people.filter((p) => p.manager_id === id).forEach((p) => { s.add(p.id); down(p.id) })
    down(hover)
    return s
  }, [hover, people])  // eslint-disable-line react-hooks/exhaustive-deps

  const shown = people.filter((p) => pos.has(p.id))
  const depth = Math.max(0, ...[...pos.values()].map((v) => v.d))
  const W = (depth + 1) * (NW + GX) - GX, H = Math.max(1, rows) * (NH + GY) - GY
  const X = (id: number) => pos.get(id)!.d * (NW + GX), Y = (id: number) => pos.get(id)!.y * (NH + GY)

  return (
    <div>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', margin: '4px 0 12px' }}>
        {units.map((u) => <span key={u} className="faint"><i style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: colour(u), marginRight: 5 }} />{u}</span>)}
        <span className="faint">▣ n = systems owned</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <svg width={W} height={H} style={{ display: 'block' }} role="img" aria-label="Reporting lines">
          {shown.filter((p) => p.manager_id && pos.has(p.manager_id)).map((p) => {
            const x1 = X(p.manager_id) + NW, y1 = Y(p.manager_id) + NH / 2, x2 = X(p.id), y2 = Y(p.id) + NH / 2
            const on = !lit || (lit.has(p.id) && lit.has(p.manager_id))
            return <path key={p.id} d={`M${x1},${y1} C${x1 + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`} fill="none"
              style={{ stroke: on && lit ? 'var(--blue)' : 'var(--faint)' }} strokeWidth={on && lit ? 2 : 1.2} opacity={on ? 1 : 0.15} />
          })}
          {shown.map((p) => (
            <Box key={p.id} x={X(p.id)} y={Y(p.id)} w={NW} h={NH} bar={p.active ? colour(p.business_unit) : 'var(--red)'} danger={!p.active} dim={!!lit && !lit.has(p.id)}
              title={`${p.name} (${p.directory_id})\nReports to: ${p.manager || 'Board of Directors'}\n${p.business_unit}${p.owns.length ? '\nOwns: ' + p.owns.join(', ') : ''}`}
              onEnter={() => setHover(p.id)} onLeave={() => setHover(null)}>
              <text x={14} y={20} fontSize={13} fontWeight={600} style={{ fill: 'var(--ink)' }} textDecoration={p.active ? undefined : 'line-through'}>{trunc(p.name, 21)}</text>
              <text x={14} y={36} fontSize={11} fontWeight={p.active ? 400 : 600} style={{ fill: p.active ? 'var(--muted)' : 'var(--red)' }}>{trunc(p.active ? (p.title || p.role) : p.owns.length ? `Left · still owns ${p.owns.length} system${p.owns.length > 1 ? 's' : ''}` : 'Left the organisation', 32)}</text>
              {p.owns.length > 0 && <text x={NW - 10} y={20} fontSize={11} textAnchor="end" style={{ fill: p.active ? 'var(--blue)' : 'var(--red)', fontFamily: 'var(--mono)' }}>▣ {p.owns.length}</text>}
            </Box>))}
        </svg>
      </div>
    </div>
  )
}

type N = { key: string; col: number; label: string; sub?: string; y: number; bad?: boolean; bar: string; tip: string; sysId?: number }
type L = { from: string; to: string; bad?: boolean }

/** System → owner → manager → business-unit head, as a four-column flow. Hover any box to trace its line. */
export function OwnershipMap({ assets }: { assets: any[] }) {
  const [hover, setHover] = useState<string | null>(null)
  const COLW = [290, 196, 176, 196], NH = 50, GAP = 56, ROW = 62
  const heads = ['System', 'Owner', 'Manager · escalation at 24h', 'Business-unit head']

  const { nodes, links, H } = useMemo(() => {
    const nodes = new Map<string, N>(), links: L[] = [], seen = new Set<string>()
    const link = (from: string, to: string, bad?: boolean) => { const k = from + '>' + to; if (!seen.has(k)) { seen.add(k); links.push({ from, to, bad }) } }
    assets.forEach((a, i) => {
      const sk = 's' + a.id
      const flags = [a.internet_facing && 'internet-facing', a.open_tasks.length && `${a.open_tasks.length} open task${a.open_tasks.length > 1 ? 's' : ''}`,
        a.inventory.state !== 'current' && (a.inventory.state === 'none' ? 'no inventory' : 'inventory stale')].filter(Boolean).join(' · ')
      nodes.set(sk, { key: sk, col: 0, label: a.name, sub: flags || 'no open work', y: i * ROW, bad: !a.owner_valid, sysId: a.id,
        bar: a.owner_valid ? 'var(--green)' : 'var(--red)', tip: `${a.name}\n${a.owner_issue || 'ownership valid'}` })
      const ok = 'o:' + (a.owner || 'none' + a.id)
      if (!nodes.has(ok)) nodes.set(ok, { key: ok, col: 1, label: a.owner || 'No named owner', sub: a.owner_active ? 'directory identity' : a.owner ? 'left the organisation' : a.legacy_contact ? `legacy: ${a.legacy_contact}` : 'unowned',
        y: 0, bad: !a.owner_active, bar: a.owner_active ? 'var(--blue)' : 'var(--red)', tip: a.owner_issue || a.owner || '' })
      link(sk, ok, !a.owner_valid)
      if (a.manager) {
        const mk = 'm:' + a.manager
        if (!nodes.has(mk)) nodes.set(mk, { key: mk, col: 2, label: a.manager, y: 0, bar: 'var(--purple)', tip: a.manager })
        link(ok, mk)
        if (a.bu_head) {
          const hk = 'h:' + a.bu_head
          if (!nodes.has(hk)) nodes.set(hk, { key: hk, col: 3, label: a.bu_head, sub: a.business_unit, y: 0, bar: 'var(--orange)', tip: a.bu_head })
          link(mk, hk)
        }
      }
    })
    // place each later column at the average height of what feeds it, then push apart so boxes don't overlap
    for (let col = 1; col <= 3; col++) {
      const ns = [...nodes.values()].filter((n) => n.col === col)
      ns.forEach((n) => { const ys = links.filter((l) => l.to === n.key).map((l) => nodes.get(l.from)!.y); n.y = ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0 })
      ns.sort((a, b) => a.y - b.y)
      ns.forEach((n, i) => { if (i && n.y < ns[i - 1].y + ROW) n.y = ns[i - 1].y + ROW })
    }
    return { nodes, links, H: Math.max(...[...nodes.values()].map((n) => n.y)) + NH }
  }, [assets])

  const lit = useMemo(() => {
    if (!hover) return null
    const s = new Set<string>([hover])
    const walk = (k: string, dir: 'up' | 'down') => links.forEach((l) => {
      const [a, b] = dir === 'down' ? [l.from, l.to] : [l.to, l.from]
      if (a === k && !s.has(b)) { s.add(b); walk(b, dir) }
    })
    walk(hover, 'down'); walk(hover, 'up')
    return s
  }, [hover, links])

  if (!assets.length) return null
  const W = COLW.reduce((a, b) => a + b, 0) + 3 * GAP
  const X = (col: number) => COLW.slice(0, col).reduce((a, b) => a + b, 0) + col * GAP
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="sec"><div><h2>Ownership map</h2>
        <p className="muted">Follow a system to who is accountable and where an unacknowledged task escalates. Red means ownership is invalid; hover a box to trace its line.</p></div></div>
      <div style={{ overflowX: 'auto', marginTop: 8 }}>
        <svg width={W} height={H + 28} style={{ display: 'block' }} role="img" aria-label="Ownership map">
          {heads.map((h, i) => <text key={h} x={X(i)} y={12} fontSize={11} style={{ fill: 'var(--faint)', fontFamily: 'var(--mono)' }}>{h.toUpperCase()}</text>)}
          <g transform="translate(0,28)">
            {links.map((l) => {
              const a = nodes.get(l.from)!, b = nodes.get(l.to)!
              const x1 = X(a.col) + COLW[a.col], y1 = a.y + NH / 2, x2 = X(b.col), y2 = b.y + NH / 2
              const on = !lit || (lit.has(l.from) && lit.has(l.to))
              return <path key={l.from + l.to} d={`M${x1},${y1} C${x1 + GAP / 2},${y1} ${x2 - GAP / 2},${y2} ${x2},${y2}`} fill="none"
                style={{ stroke: l.bad ? 'var(--red)' : lit && on ? 'var(--blue)' : 'var(--faint)' }} strokeWidth={lit && on ? 2.2 : 1.4}
                strokeDasharray={l.bad ? '5 4' : undefined} opacity={on ? 1 : 0.12} />
            })}
            {[...nodes.values()].map((n) => (
              <Box key={n.key} x={X(n.col)} y={n.y} w={COLW[n.col]} h={NH} bar={n.bar} danger={n.bad && n.col === 1} dim={!!lit && !lit.has(n.key)} title={n.tip}
                onEnter={() => setHover(n.key)} onLeave={() => setHover(null)}>
                <text x={14} y={21} fontSize={13} fontWeight={600} style={{ fill: 'var(--ink)' }} textDecoration={n.bad && n.col === 1 && n.sub === 'left the organisation' ? 'line-through' : undefined}>{trunc(n.label, n.col === 0 ? 34 : 22)}</text>
                {n.sub && <text x={14} y={38} fontSize={11} style={{ fill: n.bad && n.col === 0 ? 'var(--red)' : 'var(--muted)' }}>{trunc(n.sub, n.col === 0 ? 52 : 26)}</text>}
              </Box>))}
          </g>
        </svg>
      </div>
    </div>
  )
}
