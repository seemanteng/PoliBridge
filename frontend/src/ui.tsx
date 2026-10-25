import { type ReactNode, useState } from 'react'
import { ApiError } from './api'

export const Badge = ({ kind = '', children }: { kind?: string; children: ReactNode }) => (
  <span className={`badge ${kind}`}>{children}</span>
)

const IMPACT: Record<string, [string, string]> = { High: ['imp-high', '▲▲▲'], Medium: ['imp-med', '▲▲'], Low: ['imp-low', '▲'] }

// Impact is how much it matters (High, Medium or Low). It drives the deadlines, so it is shown on every list.
export const ImpactBadge = ({ impact, short = false }: { impact: string; short?: boolean }) => (
  <Badge kind={IMPACT[impact]?.[0] || ''}>{IMPACT[impact]?.[1]} {impact}{short ? '' : ' impact'}</Badge>
)

export function StateBadge({ state }: { state: string }) {
  if (state === 'cannot_determine') return <Badge kind="lbl-cd">? Cannot Determine</Badge>
  if (state === 'applies') return <Badge kind="lbl-applies">● Applies</Badge>
  return <Badge kind="lbl-dna">✓ Does Not Apply</Badge>
}

export const Loading = ({ error }: { error?: string | null }) => (
  <p className={error ? 'err' : 'muted'}>{error ? `Could not load: ${error}` : 'Loading…'}</p>
)

export function Modal({ title, sub, onClose, children, wide = false }: { title: string; sub?: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={wide ? { width: 880 } : undefined}>
        <h2>{title}</h2>
        {sub && <p className="muted" style={{ marginBottom: 6 }}>{sub}</p>}
        {children}
      </div>
    </div>
  )
}

// Runs an API action, surfacing the server's rule message (e.g. "E3: reviewer is the evidence submitter").
export function useAction() {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try { await fn(); return true }
    catch (e) { setError(e instanceof ApiError ? e.message : String(e)); return false }
    finally { setBusy(false) }
  }
  return { run, error, busy, setError }
}
