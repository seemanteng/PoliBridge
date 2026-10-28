import { useEffect, useState } from 'react'
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { getRole, setRole, useApi } from './api'
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

// the page (and so the permission area) a URL belongs to; evidence and canary live inside other pages
const areaOf = (path: string) => ({ evidence: 'remediations', canary: 'alerts' } as Record<string, string>)[path.split('/')[1]] || path.split('/')[1]
const PAGE = Object.fromEntries(NAV.map(([to, label]) => [to.slice(1), label])) as Record<string, string>

function Shell({ children }: { children: React.ReactNode }) {
  const { data: roles, error: rolesError } = useApi('/roles')
  const [role, pick] = useState(getRole())
  const loc = useLocation()
  const navigate = useNavigate()
  const r = roles?.find((x: any) => x.id === role) || roles?.find((x: any) => x.id === 'super_admin')
  const area = areaOf(loc.pathname)
  const nav = NAV.filter(([to]) => !r || r.view.includes(to.slice(1)))
  const canView = !r || !PAGE[area] || r.view.includes(area)
  const canEdit = !r || !PAGE[area] || r.edit.includes(area)
  // a role saved by an older build may no longer exist; fall back so requests are not refused as unknown
  useEffect(() => { if (r && r.id !== role) { setRole(r.id); pick(r.id) } }, [r, role])
  const change = (id: string) => {
    const next = roles.find((x: any) => x.id === id)
    setRole(id)
    pick(id)
    // leaving a page the new role cannot see, go to the first one it can
    if (!next.view.includes(area)) navigate(`/${next.view.find((v: string) => PAGE[v]) || ''}`)
  }

  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand"><img src="/logo.svg" alt="" />PoliBridge</div>
        {nav.map(([to, label, icon]) => (
          <NavLink key={to} to={to} className={({ isActive }) => `item${isActive ? ' active' : ''}`}>
            <span>{icon}</span>{label}
          </NavLink>
        ))}
        <div className="spacer" />
        <div className="who"><div className="t" style={{ marginBottom: 6 }}>Viewing as (demo)</div>
          <select aria-label="Role" value={r?.id || role} disabled={!roles} onChange={(e) => change(e.target.value)}>
            {(roles || []).map((x: any) => <option key={x.id} value={x.id}>{x.label}</option>)}</select>
          <div className="t" style={{ opacity: 0.7 }}>{rolesError ? 'Roles could not be loaded. Restart the backend so it serves /api/roles.' : r?.description}</div></div>
      </nav>
      <main>
        {/* wait for the roles so a page the role cannot see never flashes; if they fail, pages show their own errors */}
        {!r && !rolesError ? null : loc.pathname === '/' ? <Navigate to={nav[0]?.[0] || '/registry'} replace /> : !canView
          ? <div className="card"><h2>No access</h2>
              <p className="muted">{r.label} cannot see {PAGE[area] || 'this page'}.</p>
              {nav.length > 0 && <Link className="btn primary" to={nav[0][0]} style={{ marginTop: 10 }}>Go to {nav[0][1]}</Link>}</div>
          : <>{!canEdit && <div className="note view-only">View only: {r.label} can see {PAGE[area]} but not change it. Switch role to make changes.</div>}
              {children}</>}
      </main>
    </div>
  )
}

export default function App() {
  return (
    <Shell>
      <Routes>
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
