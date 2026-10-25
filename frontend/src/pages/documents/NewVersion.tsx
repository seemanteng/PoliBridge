import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, refreshAll, useApi } from '../../api'
import { Badge, Modal, useAction } from '../../ui'
import { ChangeList, Counts } from './common'

// Upload (or pick) a document, say which approved source it is a version of, preview the clause changes, then ingest.
// The file is fingerprinted, and each changed clause becomes a candidate obligation that supersedes the old wording.
export function NewVersionModal({ onClose, sourceId, document }: { onClose: () => void; sourceId?: number; document?: string }) {
  const { data: sources } = useApi('/sources')
  const { data: docs } = useApi('/documents')
  const { run, error, busy } = useAction()
  const nav = useNavigate()
  const [sid, setSid] = useState(sourceId ? String(sourceId) : '')
  const [mode, setMode] = useState<'upload' | 'existing'>(document ? 'existing' : 'upload')
  const [file, setFile] = useState<File | null>(null)
  const [doc, setDoc] = useState(document || '')
  const [f, setF] = useState({ version: '', retrieved_from: '', effective_date: '' })
  const [preview, setPreview] = useState<any>(null)
  const [result, setResult] = useState<any>(null)
  const manual = (sources || []).filter((s: any) => s.intake === 'manual' && s.status === 'approved')
  const src = manual.find((s: any) => String(s.id) === sid)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  const pickSource = (v: string) => { setSid(v); setPreview(null); const s = manual.find((x: any) => String(x.id) === v); if (s && !f.retrieved_from) set('retrieved_from', s.url || '') }

  const ensureDoc = async () => {
    if (mode === 'existing') return doc
    const r = await api.upload(`/documents/upload?filename=${encodeURIComponent(file!.name)}`, file!)
    setDoc(r.id); setMode('existing'); refreshAll()
    return r.id as string
  }
  const doPreview = () => run(async () => { const d = await ensureDoc(); setPreview(await api.post(`/sources/${sid}/preview`, { document: d })) })
  const ingest = () => run(async () => {
    const r = await api.post(`/sources/${sid}/versions`, { document: doc, version: f.version, retrieved_from: f.retrieved_from || null, effective_date: f.effective_date || null })
    setResult(r); refreshAll()
  })
  const ready = sid && (mode === 'upload' ? file : doc)

  if (result) return (
    <Modal title={result.identical ? 'No changes found' : 'New version added'} sub={`${result.source} · version ${result.version}`} onClose={onClose} wide>
      {result.identical
        ? <p className="muted" style={{ marginTop: 8 }}>This file is the same as version {result.version}, so no new version was added. It still counts as a review of this source.</p>
        : <>
          <div style={{ margin: '10px 0' }}><Counts s={result.summary} /></div>
          {result.warnings?.map((w: string) => <p key={w} className="faint">{w}</p>)}
          <div style={{ maxHeight: 340, overflow: 'auto' }}><ChangeList changes={result.changes} /></div>
          <p className="muted" style={{ marginTop: 8 }}>{result.candidates.length} new obligation{result.candidates.length === 1 ? ' is' : 's are'} waiting for Compliance or Legal to review. Obligations based on wording that changed or was removed have been replaced, and the departments affected will be asked to sign off again.</p></>}
      <div className="actions"><button className="btn" onClick={() => { onClose(); nav(`/documents?tab=changes&source=${sid}${result.version_id ? `&version=${result.version_id}` : ''}`) }}>Compare versions</button>
        {!result.identical && <button className="btn primary" onClick={() => { onClose(); nav('/acknowledgements') }}>Review new obligations</button>}</div>
    </Modal>)

  return (
    <Modal title="Upload a new version" sub="PoliBridge compares the new file with the previous version and shows you what changed before anything is saved." onClose={onClose} wide>
      <label>Which source is this a new version of?</label>
      <select value={sid} onChange={(e) => pickSource(e.target.value)}><option value="">Choose a source…</option>
        {manual.map((s: any) => <option key={s.id} value={s.id}>{s.name} · {s.jurisdiction} · currently v{s.latest_version || '—'}</option>)}</select>
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button className={`btn sm ${mode === 'upload' ? 'primary' : ''}`} onClick={() => { setMode('upload'); setPreview(null) }}>Upload a file</button>
        <button className={`btn sm ${mode === 'existing' ? 'primary' : ''}`} onClick={() => { setMode('existing'); setPreview(null) }}>Pick an already-uploaded file</button></div>
      {mode === 'upload'
        ? <><label>File (PDF, text or Markdown)</label><input type="file" accept=".pdf,.txt,.md" onChange={(e) => { setFile(e.target.files?.[0] || null); setPreview(null) }} /></>
        : <><label>Uploaded file</label><select value={doc} onChange={(e) => { setDoc(e.target.value); setPreview(null) }}><option value="">Choose a file…</option>
          {(docs || []).filter((d: any) => d.group === 'Documents').map((d: any) => <option key={d.id} value={d.id}>{d.name}.{d.ext}{d.linked?.length ? ` (already used for ${d.linked.join(', ')})` : ''}</option>)}</select></>}
      <div className="grid g3" style={{ marginTop: 0, gap: 10 }}>
        <div><label>Version name</label><input value={f.version} onChange={(e) => set('version', e.target.value)} placeholder="2022" /></div>
        <div><label>Takes effect on</label><input type="date" value={f.effective_date} onChange={(e) => set('effective_date', e.target.value)} /></div>
        <div><label>Downloaded from (link)</label><input value={f.retrieved_from} onChange={(e) => set('retrieved_from', e.target.value)} placeholder="https://…" /></div></div>
      {preview && (preview.identical
        ? <div className="note ok" style={{ marginTop: 12 }}><b>Identical to version {preview.previous}.</b> Saving will record that you reviewed it, without adding a new version.</div>
        : <div style={{ marginTop: 12 }}>
          <div className="sec"><b>Compared with {preview.previous ? `version ${preview.previous}` : 'nothing (first version)'}</b><span className="faint mono">{preview.clauses} clauses</span></div>
          <Counts s={preview} />
          {preview.warnings.map((w: string) => <p key={w} className="faint" style={{ marginTop: 6 }}><Badge kind="cd">Note</Badge> {w}</p>)}
          <div style={{ maxHeight: 280, overflow: 'auto', marginTop: 8 }}><ChangeList changes={preview.changes} /></div></div>)}
      {error && <p className="err">{error}</p>}
      <div className="actions"><button className="btn" onClick={onClose}>Cancel</button>
        {!preview ? <button className="btn primary" disabled={busy || !ready} onClick={doPreview}>Preview changes</button>
          : <button className="btn primary" disabled={busy || (!preview.identical && !f.version.trim())} onClick={ingest}>{preview.identical ? 'Save as reviewed' : 'Save new version'}</button>}</div>
      {src && <p className="faint" style={{ marginTop: 8 }}>{src.name} is owned by {src.owner || 'nobody'} and reviewed every {src.review_days} days. Uploading a new version counts as a review.</p>}
    </Modal>
  )
}
