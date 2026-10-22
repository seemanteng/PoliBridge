import { NavLink, Route, Routes } from 'react-router-dom'
import { api, fmt, getUser, refreshAll, setUser, useApi } from './api'
import Oversight from './pages/Oversight'
import { AdvisoryList, AdvisoryDetail } from './pages/Advisories'
import { TaskList, TaskDetail } from './pages/Tasks'
import { EvidenceList, EvidenceReview } from './pages/Evidence'
import { ClaimList, ClaimDetail } from './pages/Claims'
import Canary from './pages/Canary'

const NAV = [
  ['/', 'Oversight', '▦'], ['/advisories', 'Advisories & obligations', '▤'], ['/tasks', 'Tasks', '☑'],
  ['/evidence', 'Evidence review', '▣'], ['/claims', 'Claims', '◷'], ['/canary', 'Canary tests', '∿'],
] as const

function Shell({ children }: { children: React.ReactNode }) {
  const people = useApi('/people')
  const clock = useApi('/clock')
  const me = people.data?.find((p: { directory_id: string }) => p.directory_id === getUser())

  const advance = async (body: object) => { await api.post('/clock/advance', body); refreshAll() }
  const reset = async (mode: string) => {
    if (!confirm(`Reset all data and load the ${mode === 'replay' ? 'March 2017 replay' : 'baseline (8 Mar, before the advisory)'}?`)) return
    await api.post(`/demo/reset?mode=${mode}`); refreshAll()
  }

  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand"><img src="/logo.svg" alt="" />PoliBridge</div>
        {NAV.map(([to, label, icon]) => (
          <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `item${isActive ? ' active' : ''}`}>
            <span>{icon}</span>{label}
          </NavLink>
        ))}
        <div className="spacer" />
        <div className="demo">
          <div>Simulated clock</div>
          <div className="mono" style={{ color: '#fff', marginTop: 2 }}>{fmt(clock.data?.now, true)}</div>
          <div className="row">
            <button onClick={() => advance({ hours: 1 })}>+1h</button>
            <button onClick={() => advance({ hours: 24 })}>+1d</button>
            <button onClick={() => advance({ days: 7 })}>+7d</button>
            <button onClick={() => advance({ days: 30 })}>+30d</button>
          </div>
          <div className="row">
            <button onClick={() => reset('replay')}>Replay</button>
            <button onClick={() => reset('baseline')}>Baseline</button>
          </div>
        </div>
        <div className="who">
          <select value={getUser()} onChange={(e) => { setUser(e.target.value); refreshAll(); location.reload() }}>
            {people.data?.filter((p: { active: boolean }) => p.active).map((p: { directory_id: string; name: string }) => (
              <option key={p.directory_id} value={p.directory_id}>{p.name}</option>
            ))}
          </select>
          <div className="t">{me?.title || me?.role}</div>
        </div>
      </nav>
      <main>{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Oversight />} />
        <Route path="/advisories" element={<AdvisoryList />} />
        <Route path="/advisories/:id" element={<AdvisoryDetail />} />
        <Route path="/tasks" element={<TaskList />} />
        <Route path="/tasks/:id" element={<TaskDetail />} />
        <Route path="/evidence" element={<EvidenceList />} />
        <Route path="/evidence/:id" element={<EvidenceReview />} />
        <Route path="/claims" element={<ClaimList />} />
        <Route path="/claims/:id" element={<ClaimDetail />} />
        <Route path="/canary" element={<Canary />} />
      </Routes>
    </Shell>
  )
}
