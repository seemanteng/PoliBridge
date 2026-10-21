import { type ReactNode, useState } from 'react'
import { ApiError } from './api'

export const Badge = ({ kind = '', children }: { kind?: string; children: ReactNode }) => (
  <span className={`badge ${kind}`}>{children}</span>
)

export const ImpactBadge = ({ impact }: { impact: string }) => (
  <Badge kind={impact === 'High' ? 'high' : ''}>{impact} impact</Badge>
)

export function StateBadge({ state }: { state: string }) {
  if (state === 'cannot_determine') return <Badge kind="cd">? Cannot Determine</Badge>
  if (state === 'applies') return <Badge kind="applies">● Applies</Badge>
  return <Badge kind="dna">– Does Not Apply</Badge>
}

export const Loading = ({ error }: { error?: string | null }) => (
  <p className={error ? 'err' : 'muted'}>{error ? `Could not load: ${error}` : 'Loading…'}</p>
)

export function Modal({ title, sub, onClose, children }: { title: string; sub?: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
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
