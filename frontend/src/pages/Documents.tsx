import { useSearchParams } from 'react-router-dom'
import Changes from './documents/Changes'
import FeedDetail from './documents/FeedDetail'
import Guidance from './documents/Guidance'
import Quarterly from './documents/Quarterly'
import Sources from './documents/Sources'

// Where requirements come from (report §6.4, Section 2 of the build plan): the approved-source registry (security
// feeds open a dashboard of affected systems; source texts open a clause-by-clause version comparison), the guidance
// sent to owners, and the quarterly summary.
const TABS = [['sources', 'Sources', Sources], ['guidance', 'Guidance', Guidance], ['quarterly', 'Quarterly report', Quarterly]] as const
// Opened from a row on Sources: a feed's dashboard, or a source text's version comparison. Sources stays highlighted.
const PAGES: Record<string, () => React.JSX.Element> = { feed: FeedDetail, changes: Changes }

export default function Documents() {
  const [params, setParams] = useSearchParams()
  const page = PAGES[params.get('tab') || '']
  const tab = (!page && TABS.find(([k]) => k === params.get('tab'))) || TABS[0]
  const View = page || tab[2]
  return (
    <>
      <div className="head"><div><h1>Documents</h1>
        <p className="muted" style={{ maxWidth: 760 }}>The laws, policies, contracts and security feeds the company has to follow. Only approved sources can create obligations.</p></div></div>
      <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>{TABS.map(([k, l]) => (
        <button key={k} className={`btn ${tab[0] === k ? 'primary' : ''}`} onClick={() => setParams(k === 'sources' ? {} : { tab: k })}>{l}</button>))}</div>
      <View />
    </>
  )
}
