import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, refreshAll, useApi } from '../../api'
import { Badge, Loading, useAction } from '../../ui'
import { Counts, ReviewBadge, short, when } from './common'
import { NewVersionModal } from './NewVersion'

const CHANGE: Record<string, [string, string]> = {
  changed: ['info', 'Changed'], moved: ['info', 'Moved and reworded'], renumbered: ['', 'Moved, same wording'],
  new: ['ok', 'New'], removed: ['over', 'Removed'],
}

// Word-level comparison of two quotations (longest common subsequence), so the reader sees exactly which words changed.
function wordDiff(a: string, b: string): [[string, boolean][], [string, boolean][]] {
  const x = a.split(/(\s+)/), y = b.split(/(\s+)/)
  const L = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0))
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
  const before: [string, boolean][] = [], after: [string, boolean][] = []
  let i = 0, j = 0
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) { before.push([x[i], false]); after.push([y[j], false]); i++; j++ }
    else if (L[i + 1][j] >= L[i][j + 1]) before.push([x[i++], true])
    else after.push([y[j++], true])
  }
  while (i < x.length) before.push([x[i++], true])
  while (j < y.length) after.push([y[j++], true])
  return [before, after]
}

function Quote({ label, parts, tone }: { label: string; parts: [string, boolean][]; tone: 'old' | 'new' }) {
  const mark = tone === 'old' ? { background: 'var(--red-soft)', color: 'var(--red)', textDecoration: 'line-through' } : { background: '#dcf1e4', color: 'var(--green)' }
  return (
    <div style={{ marginTop: 6 }}>
      <div className="faint">{label}</div>
      <blockquote style={{ margin: '3px 0 0', padding: '8px 12px', borderLeft: `3px solid ${tone === 'old' ? 'var(--red-line)' : '#a9d8bc'}`, background: '#fafaf7', fontSize: 13.5, lineHeight: 1.55 }}>
        “{parts.map(([t, changed], k) => (changed && t.trim() ? <span key={k} style={{ ...mark, borderRadius: 3, padding: '0 2px' }}>{t}</span> : <span key={k}>{t}</span>))}”</blockquote>
    </div>
  )
}

function Change({ c, from, to }: { c: any; from: string; to: string }) {
  const [before, after]: [[string, boolean][], [string, boolean][]] = c.old && c.new ? wordDiff(c.old, c.new) : [[[c.old || '', false]], [[c.new || '', false]]]
  return (
    <div style={{ borderTop: '1px solid var(--line)', padding: '12px 0' }}>
      <div className="sec" style={{ marginBottom: 0 }}>
        <div><b className="mono">{c.old_ref && c.old_ref !== c.ref ? `${c.old_ref} → ${c.ref}` : c.ref}</b> <Badge kind={CHANGE[c.kind]?.[0]}>{CHANGE[c.kind]?.[1] || c.kind}</Badge></div>
        <div className="faint" style={{ textAlign: 'right' }}>{c.obligation_id ? <>obligation <Link to={`/acknowledgements/${c.obligation_id}`} className="mono">OBL-{String(c.obligation_id).padStart(4, '0')}</Link> · {c.obligation_status}</> : c.kind === 'renumbered' ? 'no new obligation: same wording' : ''}
          {c.supersedes && <div>replaces OBL-{String(c.supersedes).padStart(4, '0')}</div>}</div></div>
      {c.kind === 'renumbered' ? <Quote label={`Same wording in v${from} (${c.old_ref}) and v${to} (${c.ref})`} parts={[[c.new, false]]} tone="new" />
        : <>{c.old && <Quote label={`v${from} · ${c.old_ref}`} parts={before} tone="old" />}
          {c.new && <Quote label={`v${to} · ${c.ref}`} parts={after} tone="new" />}</>}
    </div>
  )
}

export default function Changes() {
  const [params, setParams] = useSearchParams()
  const sid = params.get('source')
  const to = params.get('version'), from = params.get('from')
  const q = [to && `to=${to}`, from && `from=${from}`].filter(Boolean).join('&')
  const { data: d, error } = useApi(`/sources/${sid}/compare${q ? `?${q}` : ''}`)
  const { run, error: aerr, busy } = useAction()
  const [adding, setAdding] = useState(false)
  if (!d) return <Loading error={error} />
  const s = d.source
  const pick = (k: 'version' | 'from', v: string) => {
    const next: Record<string, string> = { tab: 'changes', source: String(s.id) }
    if (k === 'version') next.version = v
    else { next.version = String(d.to.id); next.from = v }
    setParams(next)
  }
  const older = d.to ? d.versions.filter((v: any) => v.id < d.to.id) : []
  return (
    <>
      <div className="crumbs" style={{ marginTop: 16 }}><Link to="/documents">Sources</Link> / {s.name}</div>
      <div className="card" style={{ marginTop: 10 }}>
        <div className="sec"><div><h2 style={{ fontSize: 18 }}>{s.name}</h2>
          <p className="muted">{s.authority} · {s.jurisdiction} · owner {s.owner || 'nobody'} · <ReviewBadge review={s.review} /></p></div>
          {s.status === 'approved' && <div style={{ display: 'flex', gap: 8, whiteSpace: 'nowrap' }}>
            <button className="btn" disabled={busy} onClick={() => run(async () => { await api.post(`/sources/${s.id}/check`, {}); refreshAll() })}>Mark as reviewed</button>
            <button className="btn primary" onClick={() => setAdding(true)}>Upload new version</button></div>}</div>
        <p className="muted" style={{ maxWidth: 780 }}>Compare regulatory and policy sources with previous versions and extract changed clauses with exact quotations and references. Words that were taken out are struck through in red; words that were added are highlighted in green.</p>
        {aerr && <p className="err">{aerr}</p>}
        {d.to && <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          <span className="muted">Compare</span>
          <select style={{ width: 'auto' }} value={d.from?.id || ''} disabled={!older.length} onChange={(e) => pick('from', e.target.value)}>
            {!older.length && <option value="">no earlier version</option>}
            {older.map((v: any) => <option key={v.id} value={v.id}>v{v.version} · {when(v.ingested_at)}</option>)}</select>
          <span className="muted">with</span>
          <select style={{ width: 'auto' }} value={d.to.id} onChange={(e) => pick('version', e.target.value)}>
            {d.versions.map((v: any) => <option key={v.id} value={v.id}>v{v.version} · {when(v.ingested_at)}</option>)}</select>
          {d.summary && <Counts s={d.summary} />}</div>}
      </div>

      {d.from ? <div className="card" style={{ marginTop: 16 }}>
        <h2>Changed clauses · v{d.from.version} → v{d.to.version}</h2>
        {(d.warnings || []).map((w: string) => <p key={w} className="faint" style={{ marginTop: 6 }}><Badge kind="cd">Note</Badge> {w}</p>)}
        <div style={{ marginTop: 6 }}>{d.changes.map((c: any, i: number) => <Change key={i} c={c} from={d.from.version} to={d.to.version} />)}</div>
        {!d.changes.length && <p className="muted" style={{ marginTop: 6 }}>No clause changed between these versions.</p>}
        {d.unchanged.length > 0 && <details style={{ marginTop: 10 }}><summary className="muted" style={{ cursor: 'pointer' }}>{d.unchanged.length} clause{d.unchanged.length > 1 ? 's' : ''} unchanged</summary>
          {d.unchanged.map((c: any) => <p key={c.ref} style={{ fontSize: 13, margin: '6px 0' }}><b className="mono">{c.ref}</b> {c.text}</p>)}</details>}
      </div> : d.to && <div className="card" style={{ marginTop: 16 }}>
        <h2>Clauses on record · v{d.to.version}</h2>
        <p className="muted">There is no earlier version to compare with yet. When a new version is uploaded, its clauses are compared against these.</p>
        {d.clauses.map((c: any) => <p key={c.ref} style={{ fontSize: 13, margin: '8px 0' }}><b className="mono">{c.ref}</b> {c.text}</p>)}
      </div>}
      {!d.to && <div className="card" style={{ marginTop: 16 }}><p className="muted">No version on record yet. Upload one to start.</p></div>}

      {d.versions.length > 0 && <div className="card" style={{ marginTop: 16 }}>
        <h2>Versions on record</h2>
        <table style={{ marginTop: 6 }}><thead><tr><th>Version</th><th>Added</th><th>By</th><th>Takes effect</th><th>File</th><th>Fingerprint</th></tr></thead><tbody>
          {d.versions.map((v: any) => (
            <tr key={v.id} className={v.id === d.to?.id || v.id === d.from?.id ? 'hl' : ''}><td><b>v{v.version}</b></td><td>{when(v.ingested_at)}</td><td>{v.uploaded_by || '—'}</td>
              <td>{v.effective_date || '—'}</td><td className="mono" style={{ fontSize: 12 }}>{v.file_name || '—'}</td><td className="mono faint" title={v.sha256 || ''}>{short(v.sha256)}</td></tr>))}</tbody></table>
        <p className="faint" style={{ marginTop: 8 }}>The fingerprint (SHA-256) lets an assessor check that an obligation came from exactly this text.</p>
      </div>}
      {adding && <NewVersionModal sourceId={s.id} onClose={() => setAdding(false)} />}
    </>
  )
}
