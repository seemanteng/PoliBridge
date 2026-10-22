import { Fragment } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useApi } from '../api'
import { Loading } from '../ui'

const pdfUrl = (id: string) => '/api/documents/' + id.split('/').map(encodeURIComponent).join('/')

function Record({ id }: { id: string }) {
  const { data: r, error } = useApi(`/records/${id}`)
  if (!r) return <Loading error={error} />
  return (
    <div style={{ padding: '18px 22px' }}>
      <div className="mono faint">{r.subtitle}</div>
      <h2 style={{ fontSize: 20, margin: '4px 0 12px' }}>{r.title}</h2>
      <dl className="kv">{r.fields.map(([k, v]: [string, string]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
      {r.checks && <table style={{ marginTop: 14 }}><thead><tr><th>Check</th><th>Claim requires</th><th>Evidence recorded</th><th>Result</th></tr></thead>
        <tbody>{r.checks.map((c: any) => (
          <tr key={c.check} className={c.pass ? '' : 'bad'}><td><b>{c.check}</b></td><td>{c.requires}</td><td className="mono">{c.recorded}</td>
            <td className={c.pass ? 'pass' : 'fail'}>{c.pass ? '✓ Pass' : '✕ Fail'}</td></tr>))}</tbody></table>}
      {r.text && <pre className="mono" style={{ whiteSpace: 'pre-wrap', background: 'var(--bg, #f6f6f3)', padding: 14, borderRadius: 8, marginTop: 14 }}>{r.text}</pre>}
    </div>
  )
}

export default function Documents() {
  const { data: pdfs, error } = useApi('/documents')
  const { data: recs } = useApi('/records')
  const [params, setParams] = useSearchParams()
  if (!pdfs) return <Loading error={error} />
  const items = [...pdfs.map((d: any) => ({ ...d, pdf: true })), ...(recs || [])]
  const cur = items.find((d: any) => d.id === params.get('doc')) || items[0]
  const groups = [...new Set(items.map((d: any) => d.group))] as string[]
  return (
    <>
      <div className="head"><div><h1>Documents</h1>
        <p className="muted">PDFs, plus the in-app records: source texts, advisories and submitted evidence. Drop a PDF into <span className="mono">docs/</span> and it appears here.</p></div></div>
      {!cur ? <div className="card">Nothing to show.</div> : (
        <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, marginTop: 16, alignItems: 'start' }}>
          <div className="card">
            {groups.map((g) => (
              <div key={g} style={{ marginBottom: 12 }}>
                <div className="faint" style={{ marginBottom: 6 }}>{g}</div>
                {items.filter((d: any) => d.group === g).map((d: any) => (
                  <button key={d.id} className={`btn block ${d.id === cur.id ? 'primary' : ''}`} style={{ marginBottom: 6, textAlign: 'left' }}
                    onClick={() => setParams({ doc: d.id })}>{d.name}</button>))}
              </div>))}
          </div>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            {cur.pdf ? <>
              <div className="sec" style={{ padding: '10px 14px' }}><b>{cur.name}</b>
                <a href={pdfUrl(cur.id)} target="_blank" rel="noreferrer">Open in new tab</a></div>
              <iframe key={cur.id} title={cur.name} src={pdfUrl(cur.id)} style={{ width: '100%', height: 'calc(100vh - 220px)', border: 0 }} />
            </> : <Record key={cur.id} id={cur.id} />}
          </div>
        </div>)}
    </>
  )
}
