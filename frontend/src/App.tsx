import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { getUser, refreshAll, setUser, useApi } from './api'
import Oversight from './pages/Oversight'
import { AdvisoryList, AdvisoryDetail } from './pages/Advisories'
import { TaskList, TaskDetail } from './pages/Tasks'
import { EvidenceReview } from './pages/Evidence'
import { ClaimList, ClaimDetail } from './pages/Claims'
import Documents from './pages/Documents'
import Registry from './pages/Registry'
import Alerts from './pages/Alerts'

const NAV = [
  ['/registry', 'Registry', '☰'], ['/documents', 'Documents', '▭'], ['/acknowledgements', 'Acknowledgements', '✓'],
  ['/remediations', 'Remediations', '☑'], ['/alerts', 'Alerts', '△'], ['/claims', 'Claims', '◷'], ['/oversight', 'Oversight', '▦'],
] as const

function Shell({ children }: { children: React.ReactNode }) {
  const people = useApi('/people')
  const me = people.data?.find((p: { directory_id: string }) => p.directory_id === getUser())

  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand"><img src="/logo.svg" alt="" />PoliBridge</div>
        {NAV.map(([to, label, icon]) => (
          <NavLink key={to} to={to} className={({ isActive }) => `item${isActive ? ' active' : ''}`}>
            <span>{icon}</span>{label}
          </NavLink>
        ))}
        <div className="spacer" />
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
        <Route path="/" element={<Navigate to="/registry" replace />} />
        <Route path="/registry" element={<Registry />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/acknowledgements" element={<AdvisoryList />} />
        <Route path="/acknowledgements/:id" element={<AdvisoryDetail />} />
        <Route path="/remediations" element={<TaskList />} />
        <Route path="/remediations/:id" element={<TaskDetail />} />
        <Route path="/evidence" element={<Navigate to="/remediations?tab=evidence" replace />} />
        <Route path="/evidence/:id" element={<EvidenceReview />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/canary" element={<Navigate to="/alerts?tab=selftests" replace />} />
        <Route path="/claims" element={<ClaimList />} />
        <Route path="/claims/:id" element={<ClaimDetail />} />
        <Route path="/oversight" element={<Oversight />} />
      </Routes>
    </Shell>
  )
}
