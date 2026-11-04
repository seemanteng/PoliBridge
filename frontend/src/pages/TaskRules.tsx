// Dialog for shortening a task's fix deadline.
import { useState } from 'react'
import { api, fmt, refreshAll } from '../api'
import { Modal, useAction } from '../ui'

const local = (iso: string) => iso.slice(0, 16)

export function OverrideModal({ t, onClose }: { t: any; onClose: () => void }) {
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
