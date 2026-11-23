import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import {
  Activity, ArrowUpRight, BadgeCheck, Bell, Bike, Check, ChevronDown, CircleAlert,
  ClipboardList, Clock3, Filter, Flag, Gauge, HeartHandshake, ImagePlus, LogOut,
  MapPin, Menu, MessageSquareText, Plus, Search, ShieldCheck, Siren,
  Wrench, X,
} from 'lucide-react'
import {
  Area, AreaChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, formatDate, shortStatus, type Complaint, type DashboardSummary, type NotificationItem, type Role, type User } from './types'
import './App.css'

const MapView = lazy(() => import('./MapView'))

type Page = 'overview' | 'reports' | 'map' | 'repairs' | 'impact' | 'manage'
type NearbyReport = { id: string; road_name: string; status: string; severity: string; distance_meters: number }
type Intervention = { id: number; complaint_id: string; proposal: string; status: string; created_at: string }
type AdminUser = User & { is_active: boolean }
type Organization = { id: number; name: string; registration_number: string; verification_status: string; verification_notes: string | null; user_id: number }

const navItems: { page: Page; label: string; icon: typeof Activity; roles?: Role[] }[] = [
  { page: 'overview', label: 'Overview', icon: Activity },
  { page: 'reports', label: 'Reports', icon: ClipboardList },
  { page: 'map', label: 'Live map', icon: MapPin },
  { page: 'repairs', label: 'Repair progress', icon: Wrench, roles: ['authority', 'admin', 'ngo'] },
  { page: 'impact', label: 'Community impact', icon: HeartHandshake },
  { page: 'manage', label: 'Administration', icon: ShieldCheck, roles: ['authority', 'admin', 'ngo'] },
]

const nextStatuses: Record<string, string[]> = {
  SUBMITTED: ['PENDING_VERIFICATION', 'REJECTED'],
  PENDING_VERIFICATION: ['VERIFIED', 'REJECTED'],
  VERIFIED: ['ASSIGNED', 'REPAIR_IN_PROGRESS', 'REJECTED'],
  ASSIGNED: ['REPAIR_IN_PROGRESS', 'REJECTED'],
  REPAIR_IN_PROGRESS: ['RESOLVED', 'REOPENED'],
  RESOLVED: ['REOPENED'],
  REJECTED: ['REOPENED'],
  REOPENED: ['PENDING_VERIFICATION', 'VERIFIED', 'REJECTED'],
}
const chartColors = ['#e7aa4f', '#4b856c', '#839c52', '#de775f', '#6786a8', '#b0726b', '#a6a6a1']

function App() {
  const [token, setToken] = useState(() => localStorage.getItem('civicfix_token') || '')
  const [user, setUser] = useState<User | null>(null)
  const [authLoading, setAuthLoading] = useState(Boolean(token))
  const [page, setPage] = useState<Page>('overview')
  const [complaints, setComplaints] = useState<Complaint[]>([])
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [selected, setSelected] = useState<Complaint | null>(null)
  const [showReportForm, setShowReportForm] = useState(false)
  const [showNotifications, setShowNotifications] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [loading, setLoading] = useState(false)

  const signOut = useCallback(() => {
    localStorage.removeItem('civicfix_token')
    setToken('')
    setUser(null)
    setComplaints([])
    setSummary(null)
    setNotifications([])
    setSelected(null)
  }, [])

  useEffect(() => {
    if (!token) {
      setAuthLoading(false)
      return
    }
    let active = true
    api<User>('/api/auth/me', token)
      .then((me) => { if (active) setUser(me) })
      .catch(() => { if (active) signOut() })
      .finally(() => { if (active) setAuthLoading(false) })
    return () => { active = false }
  }, [token, signOut])

  const refresh = useCallback(async () => {
    if (!token || !user) return
    setLoading(true)
    setError('')
    try {
      const [reports, totals, alerts] = await Promise.all([
        api<{ items: Complaint[] }>('/api/complaints?page_size=100', token),
        api<DashboardSummary>('/api/analytics/summary', token),
        api<{ items: NotificationItem[] }>('/api/notifications', token),
      ])
      setComplaints(reports.items)
      setSummary(totals)
      setNotifications(alerts.items)
      setSelected((previous) => previous ? reports.items.find((item) => item.id === previous.id) || previous : null)
    } catch (requestError) {
      setError(messageOf(requestError))
    } finally {
      setLoading(false)
    }
  }, [token, user])

  useEffect(() => { void refresh() }, [refresh])

  const analysisPending = complaints.some((complaint) => complaint.analyses.some((analysis) => analysis.status === 'QUEUED' || analysis.status === 'PROCESSING'))
  useEffect(() => {
    if (!analysisPending || !token) return
    const timer = window.setInterval(() => {
      api<{ items: Complaint[] }>('/api/complaints?page_size=100', token)
        .then((response) => {
          setComplaints(response.items)
          setSelected((previous) => previous ? response.items.find((item) => item.id === previous.id) || previous : null)
        })
        .catch((requestError: unknown) => setError(messageOf(requestError)))
    }, 4000)
    return () => window.clearInterval(timer)
  }, [analysisPending, token])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3500)
    return () => window.clearTimeout(timer)
  }, [toast])

  const visibleNav = navItems.filter((item) => !item.roles || (user && item.roles.includes(user.role)))
  const filteredComplaints = useMemo(() => complaints.filter((item) => {
    const matchesStatus = statusFilter === 'ALL' || item.status === statusFilter
    const term = search.trim().toLowerCase()
    const matchesSearch = !term || `${item.id} ${item.road_name} ${item.description}`.toLowerCase().includes(term)
    return matchesStatus && matchesSearch
  }), [complaints, statusFilter, search])

  function authenticate(nextToken: string, nextUser: User) {
    localStorage.setItem('civicfix_token', nextToken)
    setToken(nextToken)
    setUser(nextUser)
    setPage('overview')
    setError('')
  }

  if (authLoading) return <div className="app-loading"><div className="brand-mark"><Bike size={21} /></div><span>Opening your civic workspace…</span></div>
  if (!user || !token) {
    return <AuthScreen onAuthenticated={authenticate} />
  }

  const unreadCount = notifications.filter((item) => !item.read).length
  const title = pageTitle(page, user.role)

  async function markRead(item: NotificationItem) {
    try {
      await api(`/api/notifications/${item.id}/read`, token, { method: 'PATCH' })
      setNotifications((items) => items.map((notice) => notice.id === item.id ? { ...notice, read: true } : notice))
      if (item.complaint_id) setSelected(complaints.find((complaint) => complaint.id === item.complaint_id) || null)
    } catch (requestError) { setError(messageOf(requestError)) }
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNavOpen ? 'sidebar-open' : ''}`}>
        <a className="brand" href="#" onClick={(event) => { event.preventDefault(); setPage('overview'); setSelected(null) }}>
          <span className="brand-mark"><Bike size={21} strokeWidth={2.2} /></span>
          <span className="brand-name">civic<span>fix</span><small>COMMUNITY WORKS</small></span>
        </a>
        <div className="workspace-label">WORKSPACE</div>
        <nav className="primary-nav">
          {visibleNav.map(({ page: target, label, icon: Icon }) => (
            <button key={target} className={`nav-link ${page === target ? 'active' : ''}`} onClick={() => { setPage(target); setSelected(null); setMobileNavOpen(false) }}>
              <Icon size={18} strokeWidth={1.8} /><span>{label}</span>
              {target === 'reports' && complaints.length > 0 && <span className="nav-count">{complaints.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="city-card">
            <div className="city-icon"><MapPin size={16} /></div>
            <div><strong>Community network</strong><span>Local road watch</span></div>
            <span className="online-dot" />
          </div>
          <div className="profile-row">
            <div className="avatar">{initials(user.full_name)}</div>
            <div className="profile-meta"><strong>{user.full_name}</strong><span>{shortStatus(user.role)}</span></div>
            <button className="icon-button quiet" aria-label="Sign out" title="Sign out" onClick={signOut}><LogOut size={17} /></button>
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="Open menu" onClick={() => setMobileNavOpen((open) => !open)}><Menu size={19} /></button>
          <div className="breadcrumbs"><span>CivicFix</span><span className="crumb-slash">/</span><strong>{selected ? selected.id : title}</strong></div>
          <div className="topbar-actions">
            <span className="role-pill"><span />{shortStatus(user.role)} workspace</span>
            <button className={`icon-button notification-button ${showNotifications ? 'pressed' : ''}`} aria-label="Notifications" onClick={() => setShowNotifications((show) => !show)}>
              <Bell size={18} />{unreadCount > 0 && <span className="notification-badge">{unreadCount > 9 ? '9+' : unreadCount}</span>}
            </button>
            <div className="avatar avatar-small">{initials(user.full_name)}</div>
          </div>
        </header>

        <div className="page-content">
          {error && <div className="alert alert-error"><CircleAlert size={18} /><span>{error}</span><button className="alert-dismiss" onClick={() => setError('')} aria-label="Dismiss error"><X size={15} /></button></div>}
          {showNotifications && <NotificationPanel items={notifications} onClose={() => setShowNotifications(false)} onRead={markRead} />}
          {selected
            ? <ComplaintDetail complaint={selected} user={user} token={token} onClose={() => setSelected(null)} onError={setError} onChanged={async (message) => { setToast(message); await refresh() }} />
            : <PageContent
              page={page}
              user={user}
              token={token}
              complaints={complaints}
              filteredComplaints={filteredComplaints}
              summary={summary}
              loading={loading}
              search={search}
              setSearch={setSearch}
              statusFilter={statusFilter}
              setStatusFilter={setStatusFilter}
              onRefresh={refresh}
              onOpen={setSelected}
              onToast={setToast}
              onError={setError}
              onNewReport={() => setShowReportForm(true)}
            />}
        </div>
      </main>

      {showReportForm && <ReportModal token={token} complaints={complaints} onClose={() => setShowReportForm(false)} onCreated={async (message) => { setShowReportForm(false); setToast(message); setPage('reports'); await refresh() }} />}
      {toast && <div className="toast"><span className="toast-check"><Check size={14} /></span>{toast}</div>}
    </div>
  )
}

function AuthScreen({ onAuthenticated }: { onAuthenticated: (token: string, user: User) => void }) {
  const [registering, setRegistering] = useState(false)
  const [ngo, setNgo] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setBusy(true)
    const form = new FormData(event.currentTarget)
    const payload: Record<string, string> = {
      email: String(form.get('email') || ''),
      password: String(form.get('password') || ''),
    }
    let path = '/api/auth/login'
    if (registering) {
      path = '/api/auth/register'
      payload.full_name = String(form.get('full_name') || '')
      payload.role = ngo ? 'ngo' : 'citizen'
      if (ngo) {
        payload.organization_name = String(form.get('organization_name') || '')
        payload.registration_number = String(form.get('registration_number') || '')
      }
    }
    try {
      const response = await api<{ access_token: string; user: User }>(path, '', { method: 'POST', body: JSON.stringify(payload) })
      onAuthenticated(response.access_token, response.user)
    } catch (requestError) {
      setError(messageOf(requestError))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-left">
        <a className="brand auth-brand" href="#"><span className="brand-mark"><Bike size={21} /></span><span className="brand-name">civic<span>fix</span><small>COMMUNITY WORKS</small></span></a>
        <div className="auth-story">
          <span className="eyebrow"><span className="eyebrow-line" /> A better street starts here</span>
          <h1>Small reports.<br /><em>Real change.</em></h1>
          <p>One place for neighbors and local teams to spot road damage, follow repairs, and make every street safer.</p>
          <div className="auth-stats"><div><strong>01</strong><span>See an issue</span></div><i /><div><strong>02</strong><span>Share the location</span></div><i /><div><strong>03</strong><span>Follow the fix</span></div></div>
        </div>
        <div className="auth-art" aria-hidden="true"><div className="sun-disc" /><div className="building building-one" /><div className="building building-two" /><div className="building building-three" /><div className="road-art"><span className="road-dash" /></div><div className="tree tree-one" /><div className="tree tree-two" /><div className="art-pin"><MapPin size={21} fill="currentColor" /></div></div>
        <p className="auth-footnote">Built with neighbors, for the places we call home.</p>
      </div>
      <div className="auth-right">
        <div className="auth-form-wrap">
          <div className="form-eyebrow">{registering ? 'JOIN YOUR COMMUNITY' : 'WELCOME BACK'}</div>
          <h2>{registering ? 'Create your account' : 'Good to see you.'}</h2>
          <p className="auth-subtitle">{registering ? 'Get involved in keeping your neighborhood moving.' : 'Sign in to pick up where you left off.'}</p>
          <form onSubmit={submit} className="auth-form">
            {registering && <label>Full name<input name="full_name" autoComplete="name" placeholder="How should we address you?" minLength={2} maxLength={120} required /></label>}
            <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required /></label>
            <label>Password<input name="password" type="password" autoComplete={registering ? 'new-password' : 'current-password'} placeholder={registering ? 'At least 10 characters' : 'Your password'} minLength={registering ? 10 : 1} maxLength={128} required /></label>
            {registering && <label className="check-label"><input type="checkbox" checked={ngo} onChange={(event) => setNgo(event.target.checked)} /> Register as a community organization</label>}
            {ngo && <><label>Organization name<input name="organization_name" required maxLength={180} placeholder="Registered organization name" /></label><label>Registration number<input name="registration_number" required maxLength={120} placeholder="Official registration number" /></label><p className="form-note">Your organization needs administrator verification before joining active cases.</p></>}
            {error && <div className="form-error"><CircleAlert size={16} />{error}</div>}
            <button className="button button-primary auth-submit" disabled={busy}>{busy ? 'Please wait…' : registering ? 'Create account' : 'Sign in'}<span>→</span></button>
          </form>
          <div className="auth-switch">{registering ? 'Already have an account?' : 'New to CivicFix?'} <button onClick={() => { setRegistering((open) => !open); setError('') }}>{registering ? 'Sign in' : 'Create an account'}</button></div>
          <div className="auth-safe"><ShieldCheck size={15} /> Your reports are private and access-controlled.</div>
        </div>
        <span className="auth-corner-note">A little care goes a long way.</span>
      </div>
    </div>
  )
}

function PageContent(props: {
  page: Page; user: User; token: string; complaints: Complaint[]; filteredComplaints: Complaint[]; summary: DashboardSummary | null
  loading: boolean; search: string; setSearch: (value: string) => void; statusFilter: string; setStatusFilter: (value: string) => void
  onRefresh: () => void; onOpen: (complaint: Complaint) => void; onToast: (message: string) => void
  onError: (message: string) => void; onNewReport: () => void
}) {
  if (props.page === 'overview') return <Overview {...props} />
  if (props.page === 'reports') return <ReportsPage {...props} />
  if (props.page === 'map') return <MapPage complaints={props.complaints} onOpen={props.onOpen} />
  if (props.page === 'repairs') return <RepairsPage {...props} />
  if (props.page === 'impact') return <ImpactPage {...props} />
  return <ManagePage {...props} />
}

function Overview(props: Parameters<typeof PageContent>[0]) {
  const { user, summary, complaints } = props
  const pending = complaints.filter((item) => ['SUBMITTED', 'PENDING_VERIFICATION', 'REOPENED'].includes(item.status)).length
  const inProgress = complaints.filter((item) => item.status === 'REPAIR_IN_PROGRESS' || item.status === 'ASSIGNED').length
  const resolved = complaints.filter((item) => item.status === 'RESOLVED').length
  const high = complaints.filter((item) => item.severity === 'HIGH').length
  const displayName = user.full_name.split(' ')[0]
  return (
    <>
      <section className="welcome-row hero-panel">
        <div><div className="eyebrow"><span className="eyebrow-line" /> YOUR COMMUNITY, IN MOTION</div><h1>{greeting()}, {displayName}<span className="greeting-dot">.</span></h1><p>Here’s the latest on the streets you care about.</p></div>
        {user.role === 'citizen' && <button className="button button-primary" onClick={props.onNewReport}><Plus size={17} />Report an issue</button>}
      </section>
      <section className="metric-grid">
        <MetricCard label={user.role === 'citizen' ? 'Your reports' : 'Reports received'} value={summary?.total ?? '—'} icon={ClipboardList} accent="sage" helper="Across the community" />
        <MetricCard label="Awaiting review" value={pending} icon={Clock3} accent="amber" helper="Needs a first look" trend={pending ? 'Action needed' : 'All caught up'} />
        <MetricCard label="In progress" value={inProgress} icon={Wrench} accent="blue" helper="Work underway" />
        <MetricCard label="Resolved" value={resolved} icon={BadgeCheck} accent="green" helper="Back on the road" />
      </section>
      <section className="overview-grid">
        <article className="panel trend-panel">
          <div className="panel-heading"><div><span className="section-kicker">COMMUNITY PULSE</span><h2>Reports over time</h2></div><span className="panel-tag"><span className="tag-dot" /> Live data</span></div>
          <div className="chart-summary"><strong>{summary?.total ?? 0}</strong><span>tracked reports</span>{high > 0 && <span className="summary-flag"><Flag size={13} />{high} high priority</span>}</div>
          <div className="trend-chart">
            {summary?.over_time.length ? <ResponsiveContainer width="100%" height="100%"><AreaChart data={summary.over_time} margin={{ top: 10, right: 12, left: -20, bottom: 0 }}><defs><linearGradient id="report-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#63806d" stopOpacity={0.25} /><stop offset="100%" stopColor="#63806d" stopOpacity={0.02} /></linearGradient></defs><CartesianGrid stroke="#eeeae2" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="month" tickFormatter={shortMonth} tickLine={false} axisLine={false} tick={{ fill: '#92948b', fontSize: 11 }} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: '#92948b', fontSize: 11 }} /><Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #e8e5de', boxShadow: '0 8px 24px #172a1812' }} /><Area type="monotone" dataKey="count" name="Reports" stroke="#63806d" strokeWidth={2.5} fill="url(#report-fill)" activeDot={{ r: 5, fill: '#63806d' }} /></AreaChart></ResponsiveContainer> : <EmptyChart text={props.loading ? 'Loading your community data…' : 'Your report history will appear here.'} />}
          </div>
          <div className="chart-footer"><span><span className="legend-dot" /> Reports submitted</span><span>AI assists. People decide.</span></div>
        </article>
        <article className="panel status-panel">
          <div className="panel-heading"><div><span className="section-kicker">WHERE THINGS STAND</span><h2>Report status</h2></div><button className="small-link" onClick={() => props.onOpen(complaints[0])} disabled={!complaints.length}>View latest <ArrowUpRight size={14} /></button></div>
          <div className="status-chart-wrap">
            {summary?.by_status.length ? <ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={summary.by_status} dataKey="value" nameKey="name" innerRadius={66} outerRadius={88} paddingAngle={3} stroke="none">{summary.by_status.map((_, index) => <Cell key={index} fill={chartColors[index % chartColors.length]} />)}</Pie><Tooltip formatter={(value, name) => [value, shortStatus(String(name))]} /></PieChart></ResponsiveContainer> : <EmptyChart text="No reports to summarize yet." />}
            <div className="donut-center"><strong>{summary?.total ?? 0}</strong><span>total</span></div>
          </div>
          <div className="status-legend">{(summary?.by_status || []).slice(0, 4).map((item, index) => <div key={item.name}><span style={{ background: chartColors[index % chartColors.length] }} /><span>{shortStatus(item.name)}</span><strong>{item.value}</strong></div>)}</div>
        </article>
      </section>
      <section className="panel recent-panel">
        <div className="panel-heading"><div><span className="section-kicker">THE LATEST</span><h2>Recent reports</h2></div><button className="small-link" onClick={() => props.onRefresh()}><Activity size={14} />Refresh</button></div>
        {complaints.length ? <ReportTable complaints={complaints.slice(0, 5)} onOpen={props.onOpen} /> : <EmptyState title="A clear road starts with a report." text="When a road issue is shared with the community, it’ll show up here." action={user.role === 'citizen' ? <button className="button button-secondary" onClick={props.onNewReport}><Plus size={16} /> Create first report</button> : undefined} />}
      </section>
      <footer className="page-foot"><span>Every report makes a difference.</span><span><span className="online-dot" />Data refreshes when you open this page</span></footer>
    </>
  )
}

function MetricCard({ label, value, icon: Icon, accent, helper, trend }: { label: string; value: number | string; icon: typeof Activity; accent: string; helper: string; trend?: string }) {
  return <article className="metric-card"><div className="metric-top"><span>{label}</span><span className={`metric-icon ${accent}`}><Icon size={17} /></span></div><div className="metric-value">{value}</div><div className="metric-foot"><span>{trend ? <><ArrowUpRight size={14} />{trend}</> : helper}</span>{trend && <span>{helper}</span>}</div></article>
}

function ReportsPage(props: Parameters<typeof PageContent>[0]) {
  const allowedStatuses = props.user.role === 'authority' || props.user.role === 'admin'
    ? ['PENDING_VERIFICATION', 'VERIFIED', 'ASSIGNED', 'REPAIR_IN_PROGRESS', 'RESOLVED', 'REJECTED', 'REOPENED']
    : ['SUBMITTED', 'PENDING_VERIFICATION', 'VERIFIED', 'ASSIGNED', 'REPAIR_IN_PROGRESS', 'RESOLVED', 'REJECTED', 'REOPENED']
  return (
    <section>
      <div className="page-title-row hero-panel compact"><div><div className="eyebrow"><span className="eyebrow-line" /> REPORT REGISTER</div><h1>{props.user.role === 'citizen' ? 'Your reports' : 'Community reports'}</h1><p>A clear, shared view of issues and the steps being taken.</p></div>{props.user.role === 'citizen' && <button className="button button-primary" onClick={props.onNewReport}><Plus size={17} />New report</button>}</div>
      <div className="panel reports-panel">
        <div className="table-toolbar"><div className="search-field"><Search size={16} /><input value={props.search} onChange={(event) => props.setSearch(event.target.value)} placeholder="Search reports, roads, or details" /></div><label className="filter-field"><Filter size={15} /><select value={props.statusFilter} onChange={(event) => props.setStatusFilter(event.target.value)}><option value="ALL">All statuses</option>{allowedStatuses.map((status) => <option key={status} value={status}>{shortStatus(status)}</option>)}</select><ChevronDown size={14} /></label><button className="icon-button" title="Refresh reports" onClick={props.onRefresh}><Activity size={17} /></button></div>
        {props.filteredComplaints.length ? <ReportTable complaints={props.filteredComplaints} onOpen={props.onOpen} /> : <EmptyState title="Nothing matches this view." text="Try changing the status filter or search term." />}
      </div>
      <footer className="page-foot"><span>{props.filteredComplaints.length} report{props.filteredComplaints.length === 1 ? '' : 's'} shown</span><span>Priorities are estimates; every report is reviewed by people.</span></footer>
    </section>
  )
}

function ReportTable({ complaints, onOpen }: { complaints: Complaint[]; onOpen: (complaint: Complaint) => void }) {
  return <div className="table-scroll"><table className="report-table"><thead><tr><th>Report</th><th>Location</th><th>Date reported</th><th>AI priority</th><th>Status</th><th /></tr></thead><tbody>
    {complaints.map((item) => <tr key={item.id} onClick={() => onOpen(item)} tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter') onOpen(item) }}>
      <td><div className="report-id-cell"><span className="report-symbol"><MapPin size={15} /></span><div><strong>{item.id}</strong><span>{item.description.length > 49 ? `${item.description.slice(0, 49)}…` : item.description}</span></div></div></td>
      <td><div className="location-cell">{item.road_name || 'Location pin'}<small>{item.latitude != null ? `${item.latitude.toFixed(4)}, ${item.longitude?.toFixed(4)}` : 'Location not set'}</small></div></td>
      <td>{formatDate(item.created_at)}</td><td><Severity severity={item.severity} /><span className="priority-number">{item.priority_score || '—'}</span></td>
      <td><StatusBadge status={item.status} /></td><td><button className="row-action" aria-label={`Open ${item.id}`} onClick={(event) => { event.stopPropagation(); onOpen(item) }}><ArrowUpRight size={16} /></button></td>
    </tr>)}
  </tbody></table></div>
}

function Severity({ severity }: { severity: string }) {
  const className = severity === 'HIGH' ? 'severity-high' : severity === 'MEDIUM' ? 'severity-medium' : severity === 'LOW' ? 'severity-low' : 'severity-review'
  return <span className={`severity ${className}`}><span />{shortStatus(severity)}</span>
}

function StatusBadge({ status }: { status: string }) {
  const stateClass = status === 'RESOLVED' ? 'status-resolved' : status === 'REJECTED' ? 'status-rejected' : status === 'REPAIR_IN_PROGRESS' || status === 'ASSIGNED' ? 'status-progress' : status === 'VERIFIED' ? 'status-verified' : 'status-pending'
  return <span className={`status-badge ${stateClass}`}><span />{shortStatus(status)}</span>
}

function MapPage({ complaints, onOpen }: { complaints: Complaint[]; onOpen: (complaint: Complaint) => void }) {
  return <section><div className="page-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> LOCAL VIEW</div><h1>Road watch map</h1><p>See reported issues where they happened. Select a marker to open its details.</p></div><span className="map-total"><MapPin size={15} />{complaints.filter((item) => item.latitude != null).length} mapped reports</span></div><div className="map-page-layout"><div className="panel large-map-panel"><Suspense fallback={<div className="empty-chart">Loading map…</div>}><MapView complaints={complaints} className="large-map" /></Suspense></div><div className="panel map-list-panel"><div className="panel-heading"><div><span className="section-kicker">RECENTLY REPORTED</span><h2>Nearby in the register</h2></div></div><div className="map-report-list">{complaints.filter((item) => item.latitude != null).slice(0, 8).map((item) => <button key={item.id} className="map-report-item" onClick={() => onOpen(item)}><span className="report-symbol"><MapPin size={15} /></span><span><strong>{item.road_name || item.id}</strong><small>{item.id} · {formatDate(item.created_at)}</small></span><Severity severity={item.severity} /></button>)}{complaints.filter((item) => item.latitude != null).length === 0 && <p className="empty-note">Reports with a shared location will appear here.</p>}</div></div></div><p className="map-attribution-note">Map tiles © OpenStreetMap contributors. Base map requires an internet connection; reports stay on your configured server.</p></section>
}

function RepairsPage(props: Parameters<typeof PageContent>[0]) {
  const rows = props.complaints.filter((item) => ['ASSIGNED', 'REPAIR_IN_PROGRESS', 'RESOLVED', 'REOPENED'].includes(item.status))
  return <section><div className="page-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> FROM PLAN TO PAVEMENT</div><h1>Repair progress</h1><p>Keep visible repair updates together with the original community report.</p></div></div><div className="repair-grid">{rows.map((item) => <button key={item.id} className="repair-card" onClick={() => props.onOpen(item)}><div className="repair-card-top"><span className="report-id">{item.id}</span><StatusBadge status={item.status} /></div><h3>{item.road_name || 'Road repair'}</h3><p>{item.description}</p><div className="repair-progress"><span className={`progress-step ${['ASSIGNED', 'REPAIR_IN_PROGRESS', 'RESOLVED'].includes(item.status) ? 'complete' : ''}`} /><i /><span className={`progress-step ${['REPAIR_IN_PROGRESS', 'RESOLVED'].includes(item.status) ? 'complete' : ''}`} /><i /><span className={`progress-step ${item.status === 'RESOLVED' ? 'complete' : ''}`} /></div><div className="repair-steps"><span>Assigned</span><span>In repair</span><span>Resolved</span></div><div className="repair-card-foot"><span><MessageSquareText size={14} />{item.updates.length} updates</span><span>Updated {formatDate(item.updated_at)}</span></div></button>)}{rows.length === 0 && <div className="panel empty-repairs"><EmptyState title="No repair activity yet." text="When a report is assigned, its progress and evidence will appear here." /></div>}</div></section>
}

function ImpactPage(props: Parameters<typeof PageContent>[0]) {
  const resolved = props.complaints.filter((item) => item.status === 'RESOLVED').length
  const updates = props.complaints.reduce((total, item) => total + item.updates.length, 0)
  const verified = props.complaints.filter((item) => !['SUBMITTED', 'PENDING_VERIFICATION', 'REJECTED'].includes(item.status)).length
  return <section><div className="page-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> THE GOOD WORK, TOGETHER</div><h1>Community impact</h1><p>Progress is a team effort. Here’s the difference being made, one street at a time.</p></div></div><div className="impact-banner"><div className="impact-copy"><span className="impact-mark"><HeartHandshake size={20} /></span><span className="section-kicker">A SHARED COMMITMENT</span><h2>Good streets happen<br /><em>when we show up.</em></h2><p>Every clear report helps local teams see what needs attention. Every honest update helps neighbors know what comes next.</p><button className="button button-light" onClick={props.onNewReport} hidden={props.user.role !== 'citizen'}><Plus size={16} />Share what you see</button></div><div className="impact-illustration" aria-hidden="true"><div className="impact-sun" /><div className="impact-house house-a" /><div className="impact-house house-b" /><div className="impact-road" /><div className="impact-person person-a" /><div className="impact-person person-b" /><div className="impact-spark">✳</div></div></div><div className="impact-stats"><div className="panel impact-stat"><span className="impact-stat-icon green"><BadgeCheck size={18} /></span><strong>{resolved}</strong><span>Reports resolved</span><small>Each one a safer route</small></div><div className="panel impact-stat"><span className="impact-stat-icon amber"><ShieldCheck size={18} /></span><strong>{verified}</strong><span>Reviewed reports</span><small>Checked by local people</small></div><div className="panel impact-stat"><span className="impact-stat-icon blue"><MessageSquareText size={18} /></span><strong>{updates}</strong><span>Progress updates</span><small>Keeping neighbors informed</small></div><div className="panel impact-stat"><span className="impact-stat-icon red"><Siren size={18} /></span><strong>{props.summary?.total ?? 0}</strong><span>Neighbors spoke up</span><small>And their reports matter</small></div></div><div className="impact-note"><div className="impact-note-icon"><CircleAlert size={19} /></div><div><strong>Good data comes from people.</strong><p>AI can help organize a photo, but it can’t confirm a repair. Status changes and outcomes are recorded by the people doing the work.</p></div></div></section>
}

function ManagePage(props: Parameters<typeof PageContent>[0]) {
  const [tab, setTab] = useState<'queue' | 'people' | 'organizations' | 'audit'>(
    props.user.role === 'ngo' ? 'queue' : props.user.role === 'authority' ? 'queue' : 'people',
  )
  const tabs = props.user.role === 'admin'
    ? [{ id: 'people', label: 'People & access' }, { id: 'organizations', label: 'NGO verification' }, { id: 'audit', label: 'Audit history' }]
    : props.user.role === 'authority'
      ? [{ id: 'queue', label: 'Review queue' }, { id: 'organizations', label: 'NGO proposals' }]
      : [{ id: 'queue', label: 'Community opportunities' }]
  return (
    <section>
      <div className="page-title-row">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> {props.user.role === 'ngo' ? 'VERIFIED PARTNERS' : 'KEEPING THINGS ACCOUNTABLE'}</div>
          <h1>{props.user.role === 'ngo' ? 'Community work' : 'Workspace administration'}</h1>
          <p>{props.user.role === 'ngo' ? 'Browse verified opportunities and keep community support transparent.' : 'Review reports, support access, and maintain an accountable record.'}</p>
        </div>
      </div>
      <div className="manage-tabs">
        {tabs.map((item) => <button key={item.id} className={tab === item.id ? 'selected' : ''} onClick={() => setTab(item.id as typeof tab)}>{item.label}</button>)}
      </div>
      {tab === 'queue' && <OperationsQueue {...props} />}
      {tab === 'people' && <AdminPeople {...props} />}
      {tab === 'organizations' && (props.user.role === 'ngo' ? <NGOWork {...props} /> : props.user.role === 'authority' ? <AuthorityInterventions token={props.token} onError={props.onError} onToast={props.onToast} /> : <NGOVerification token={props.token} onError={props.onError} onToast={props.onToast} />)}
      {tab === 'audit' && <AuditHistory token={props.token} onError={props.onError} />}
    </section>
  )
}

function OperationsQueue(props: Parameters<typeof PageContent>[0]) {
  const [opportunities, setOpportunities] = useState<{ id: string; road_name: string; status: string; severity: string; latitude: number | null; longitude: number | null; priority_score: number }[]>([])
  const isNGO = props.user.role === 'ngo'
  useEffect(() => {
    if (!isNGO) return
    api<{ items: typeof opportunities; message?: string }>('/api/ngo/opportunities', props.token).then((result) => setOpportunities(result.items)).catch((error: unknown) => props.onError(messageOf(error)))
  }, [isNGO, props.token, props.onError])
  const rows = isNGO ? opportunities.map((opportunity) => props.complaints.find((item) => item.id === opportunity.id)).filter((item): item is Complaint => Boolean(item)) : props.complaints.filter((item) => ['SUBMITTED', 'PENDING_VERIFICATION', 'REOPENED'].includes(item.status))
  return <div className="panel reports-panel"><div className="panel-heading"><div><span className="section-kicker">{isNGO ? 'VERIFIED PUBLIC CASES' : 'FIRST LOOK'}</span><h2>{isNGO ? 'Open opportunities' : 'Reports needing review'}</h2></div><button className="small-link" onClick={() => props.onRefresh()}><Activity size={14} />Refresh</button></div>{rows.length ? <ReportTable complaints={rows} onOpen={props.onOpen} /> : <EmptyState title={isNGO ? 'No open opportunities yet.' : 'Your review queue is clear.'} text={isNGO ? 'Verified community opportunities appear here when available.' : 'New community reports will appear here.'} />}</div>
}

function AuthorityInterventions({ token, onError, onToast }: { token: string; onError: (message: string) => void; onToast: (message: string) => void }) {
  const [items, setItems] = useState<Intervention[]>([])
  const [busy, setBusy] = useState<number | null>(null)
  const load = useCallback(() => api<{ items: Intervention[] }>('/api/authority/interventions', token).then((result) => setItems(result.items)).catch((error: unknown) => onError(messageOf(error))), [token, onError])
  useEffect(() => { void load() }, [load])
  async function decide(item: Intervention, status: 'ACCEPTED' | 'DECLINED') {
    setBusy(item.id)
    try {
      await api(`/api/authority/interventions/${item.id}`, token, { method: 'PATCH', body: JSON.stringify({ status, note: '' }) })
      onToast(`Community proposal ${status.toLowerCase()}.`)
      await load()
    } catch (error) { onError(messageOf(error)) } finally { setBusy(null) }
  }
  return <div className="panel reports-panel"><div className="panel-heading"><div><span className="section-kicker">COMMUNITY SUPPORT PROPOSALS</span><h2>NGO intervention review</h2></div><span className="panel-tag">{items.length} awaiting review</span></div>{items.length ? <div className="organization-list">{items.map((item) => <div className="organization-row" key={item.id}><span className="org-icon"><HeartHandshake size={18} /></span><div className="organization-name"><strong>{item.complaint_id}</strong><span>{item.proposal}</span><small>{formatDate(item.created_at)}</small></div><div className="organization-actions"><button className="button button-secondary button-small" disabled={busy === item.id} onClick={() => void decide(item, 'DECLINED')}>Decline</button><button className="button button-primary button-small" disabled={busy === item.id} onClick={() => void decide(item, 'ACCEPTED')}><Check size={13} />Accept</button></div></div>)}</div> : <EmptyState title="No proposals need review." text="Verified organizations’ proposals will be listed here." />}</div>
}

function NGOWork(props: Parameters<typeof PageContent>[0]) {
  const [organization, setOrganization] = useState<Organization | null>(null)
  const [interventions, setInterventions] = useState<Intervention[]>([])
  const [proposalId, setProposalId] = useState('')
  const [proposal, setProposal] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try {
      const [org, response] = await Promise.all([api<Organization | null>('/api/ngo/organization', props.token), api<{ items: Intervention[] }>('/api/ngo/interventions', props.token)])
      setOrganization(org)
      setInterventions(response.items)
    } catch (error) { props.onError(messageOf(error)) }
  }, [props.token, props.onError])
  useEffect(() => { void load() }, [load])
  async function submitProposal(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await api('/api/ngo/interventions?complaint_id=' + encodeURIComponent(proposalId), props.token, { method: 'POST', body: JSON.stringify({ proposal }) })
      setProposal('')
      setProposalId('')
      props.onToast('Community proposal recorded. The official report status was not changed.')
      await load()
    } catch (error) { props.onError(messageOf(error)) } finally { setBusy(false) }
  }
  async function updateIntervention(item: Intervention, status: string) {
    try {
      await api(`/api/ngo/interventions/${item.id}`, props.token, { method: 'PATCH', body: JSON.stringify({ status, note: '' }) })
      props.onToast('Community intervention updated.')
      await load()
    } catch (error) { props.onError(messageOf(error)) }
  }
  return <div className="ngo-work-layout"><div className="panel ngo-organization"><span className="org-icon"><HeartHandshake size={19} /></span><div><span className="section-kicker">ORGANIZATION</span><h2>{organization?.name || 'Organization profile'}</h2><p>{organization?.registration_number || 'No registration details on file'}</p></div><span className={`org-status ${organization?.verification_status?.toLowerCase() || 'pending'}`}>{organization?.verification_status || 'NOT SET UP'}</span>{organization?.verification_notes && <p className="org-notes">{organization.verification_notes}</p>}</div><div className="panel ngo-form-panel"><div className="panel-heading"><div><span className="section-kicker">PROPOSE COMMUNITY SUPPORT</span><h2>Offer help on an open report</h2></div></div>{organization?.verification_status === 'VERIFIED' ? <form className="ngo-proposal-form" onSubmit={submitProposal}><label>Open report<select value={proposalId} onChange={(event) => setProposalId(event.target.value)} required><option value="">Choose a verified public report</option>{props.complaints.filter((item) => ['VERIFIED', 'ASSIGNED', 'REPAIR_IN_PROGRESS'].includes(item.status)).map((item) => <option key={item.id} value={item.id}>{item.id} · {item.road_name || 'Location pin'}</option>)}</select></label><label>What support can your organization offer?<textarea value={proposal} onChange={(event) => setProposal(event.target.value)} minLength={10} maxLength={4000} rows={4} required placeholder="Describe a practical, safe and transparent contribution…" /></label><p className="form-note">Community proposals are tracked separately and never change the official repair status.</p><button className="button button-primary" disabled={busy || !proposalId}>{busy ? 'Submitting…' : 'Submit proposal'}<ArrowUpRight size={15} /></button></form> : <div className="empty-note">An administrator must verify your organization before it can propose support or view public cases.</div>}</div><div className="panel ngo-interventions"><div className="panel-heading"><div><span className="section-kicker">YOUR CONTRIBUTIONS</span><h2>Intervention ledger</h2></div></div>{interventions.length ? interventions.map((item) => <div className="intervention-row" key={item.id}><div><strong>{item.complaint_id}</strong><p>{item.proposal}</p><small>{formatDate(item.created_at)}</small></div><StatusBadge status={item.status} />{item.status === 'ACCEPTED' && <button className="button button-secondary button-small" onClick={() => updateIntervention(item, 'IN_PROGRESS')}>Start</button>}{item.status === 'IN_PROGRESS' && <button className="button button-secondary button-small" onClick={() => updateIntervention(item, 'COMPLETED')}>Mark complete</button>}</div>) : <EmptyState title="No proposals yet." text="Verified organizations can submit a proposal on an open report." />}</div></div>
}

function AdminPeople(props: Parameters<typeof PageContent>[0]) {
  const [people, setPeople] = useState<AdminUser[]>([])
  const [busyId, setBusyId] = useState<number | null>(null)
  const load = useCallback(() => api<{ items: AdminUser[] }>('/api/admin/users', props.token).then((result) => setPeople(result.items)).catch((error: unknown) => props.onError(messageOf(error))), [props.token, props.onError])
  useEffect(() => { void load() }, [load])
  async function update(person: AdminUser, role: Role, is_active: boolean) {
    setBusyId(person.id)
    try {
      await api(`/api/admin/users/${person.id}`, props.token, { method: 'PATCH', body: JSON.stringify({ role, is_active }) })
      props.onToast('Account access updated.')
      await load()
    } catch (error) { props.onError(messageOf(error)) } finally { setBusyId(null) }
  }
  return <div className="panel reports-panel"><div className="panel-heading"><div><span className="section-kicker">ACCOUNT CONTROL</span><h2>People & access</h2></div><span className="panel-tag">{people.length} recent accounts</span></div><div className="table-scroll"><table className="report-table"><thead><tr><th>Person</th><th>Role</th><th>Joined</th><th>Account</th><th>Change role</th></tr></thead><tbody>{people.map((person) => <tr key={person.id}><td><div className="report-id-cell"><span className="avatar avatar-small">{initials(person.full_name)}</span><div><strong>{person.full_name}</strong><span>{person.email}</span></div></div></td><td>{shortStatus(person.role)}</td><td>{formatDate(person.created_at)}</td><td><span className={`org-status ${person.is_active ? 'verified' : 'rejected'}`}>{person.is_active ? 'ACTIVE' : 'DISABLED'}</span></td><td><div className="admin-role-actions"><select aria-label={`Role for ${person.full_name}`} value={person.role} onChange={(event) => void update(person, event.target.value as Role, person.is_active)} disabled={busyId === person.id}><option value="citizen">Citizen</option><option value="authority">Authority</option><option value="ngo">NGO</option><option value="admin">Admin</option></select><button className="button button-secondary button-small" disabled={busyId === person.id} onClick={() => void update(person, person.role, !person.is_active)}>{person.is_active ? 'Disable' : 'Enable'}</button></div></td></tr>)}</tbody></table></div><p className="table-footnote">Only administrators can change roles. You cannot remove your own administrator access.</p></div>
}

function NGOVerification({ token, onError, onToast }: { token: string; onError: (message: string) => void; onToast: (message: string) => void }) {
  const [organizations, setOrganizations] = useState<Organization[]>([])
  const [busy, setBusy] = useState<number | null>(null)
  const load = useCallback(() => api<{ items: Organization[] }>('/api/admin/ngos', token).then((result) => setOrganizations(result.items)).catch((error: unknown) => onError(messageOf(error))), [token, onError])
  useEffect(() => { void load() }, [load])
  async function verify(item: Organization, verification_status: string) {
    setBusy(item.id)
    try {
      await api(`/api/admin/ngos/${item.id}?verification_status=${verification_status}`, token, { method: 'PATCH' })
      onToast(`Organization ${verification_status.toLowerCase()}.`)
      await load()
    } catch (error) { onError(messageOf(error)) } finally { setBusy(null) }
  }
  return <div className="panel reports-panel"><div className="panel-heading"><div><span className="section-kicker">COMMUNITY PARTNERS</span><h2>NGO verification</h2></div><span className="panel-tag">{organizations.filter((item) => item.verification_status === 'PENDING').length} awaiting review</span></div>{organizations.length ? <div className="organization-list">{organizations.map((item) => <div className="organization-row" key={item.id}><span className="org-icon"><HeartHandshake size={18} /></span><div className="organization-name"><strong>{item.name}</strong><span>Registration {item.registration_number} · Account #{item.user_id}</span>{item.verification_notes && <small>{item.verification_notes}</small>}</div><span className={`org-status ${item.verification_status.toLowerCase()}`}>{item.verification_status}</span><div className="organization-actions"><button className="button button-secondary button-small" onClick={() => void verify(item, 'REJECTED')} disabled={busy === item.id}>Decline</button><button className="button button-primary button-small" onClick={() => void verify(item, 'VERIFIED')} disabled={busy === item.id}><Check size={13} />Verify</button></div></div>)}</div> : <EmptyState title="No organizations to review." text="Community organization applications will appear here." />}</div>
}

function AuditHistory({ token, onError }: { token: string; onError: (message: string) => void }) {
  const [events, setEvents] = useState<{ id: number; complaint_id: string | null; actor_id: number | null; action: string; previous_status: string | null; new_status: string | null; details: string; created_at: string }[]>([])
  useEffect(() => { api<{ items: typeof events }>('/api/admin/audit', token).then((result) => setEvents(result.items)).catch((error: unknown) => onError(messageOf(error))) }, [token, onError])
  return <div className="panel audit-panel"><div className="panel-heading"><div><span className="section-kicker">APPEND-ONLY ACTIVITY RECORD</span><h2>Recent audit events</h2></div><span className="panel-tag">Latest 50</span></div>{events.length ? <div className="audit-list">{events.map((event) => <div className="audit-row" key={event.id}><span className="audit-dot" /><div><strong>{shortStatus(event.action)}</strong><p>{event.details || event.complaint_id || 'Account activity'}</p>{event.previous_status && <small>{shortStatus(event.previous_status)} → {shortStatus(event.new_status || '')}</small>}</div><span className="audit-time">{formatDate(event.created_at)}</span></div>)}</div> : <EmptyState title="No audit events yet." text="Administrative and report activity will be recorded here." />}</div>
}

function ComplaintDetail({ complaint, user, token, onClose, onChanged, onError }: { complaint: Complaint; user: User; token: string; onClose: () => void; onChanged: (message: string) => Promise<void>; onError: (message: string) => void }) {
  const [status, setStatus] = useState(complaint.status)
  const [remarks, setRemarks] = useState('')
  const [rejectionReason, setRejectionReason] = useState('')
  const [note, setNote] = useState('')
  const [evidence, setEvidence] = useState<File | null>(null)
  const [assignee, setAssignee] = useState('')
  const [team, setTeam] = useState<User[]>([])
  const [busy, setBusy] = useState(false)
  const latest = complaint.analyses.at(-1)
  const ai = latest?.result || complaint.ai_report
  const isAuthority = user.role === 'authority' || user.role === 'admin'
  const canNgo = user.role === 'ngo' && ['VERIFIED', 'ASSIGNED', 'REPAIR_IN_PROGRESS'].includes(complaint.status)

  useEffect(() => {
    if (!isAuthority) return
    api<{ items: User[] }>('/api/authority/team', token).then((result) => setTeam(result.items)).catch(() => setTeam([]))
  }, [isAuthority, token])

  useEffect(() => {
    setStatus(complaint.status)
    setRemarks('')
    setRejectionReason('')
  }, [complaint.id, complaint.status])

  async function changeStatus(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await api(`/api/complaints/${complaint.id}/status`, token, { method: 'PATCH', body: JSON.stringify({ status, remarks, rejection_reason: status === 'REJECTED' ? rejectionReason : null, assignee_id: assignee ? Number(assignee) : null }) })
      await onChanged('Report status updated.')
    } catch (error) { onError(messageOf(error)) } finally { setBusy(false) }
  }

  async function addUpdate(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    const form = new FormData()
    form.set('note', note)
    if (evidence) form.set('upload', evidence)
    try {
      await api(`/api/complaints/${complaint.id}/updates`, token, { method: 'POST', body: form })
      setNote('')
      setEvidence(null)
      await onChanged('Repair update saved.')
    } catch (error) { onError(messageOf(error)) } finally { setBusy(false) }
  }

  const latestUpdate = complaint.updates.at(-1)
  return <section className="detail-page"><button className="back-link" onClick={onClose}>← Back to {pageTitle('reports', user.role).toLowerCase()}</button><div className="detail-top"><div><div className="eyebrow"><span className="eyebrow-line" /> REPORT DETAILS</div><h1>{complaint.road_name || 'Road damage report'}</h1><p>{complaint.id} <span>·</span> Reported {formatDate(complaint.created_at)} {user.role !== 'citizen' && complaint.reporter_name && <span>· By {complaint.reporter_name}</span>}</p></div><StatusBadge status={complaint.status} /></div>
    <div className="detail-layout"><div className="detail-main">
      <article className="panel detail-overview"><div className="detail-section-head"><h2>What was reported</h2><Severity severity={complaint.severity} /></div><p className="detail-description">{complaint.description}</p><div className="detail-location"><MapPin size={16} /><span>{complaint.road_name || 'Reported location'}</span>{complaint.latitude != null && <small>{complaint.latitude.toFixed(5)}, {complaint.longitude?.toFixed(5)}</small>}</div>{complaint.latitude != null && <Suspense fallback={<div className="media-loading">Loading map…</div>}><MapView complaints={[complaint]} className="detail-map" /></Suspense>}</article>
      <article className="panel evidence-panel"><div className="panel-heading"><div><span className="section-kicker">ORIGINAL EVIDENCE</span><h2>Photos & video</h2></div></div>{complaint.media.length ? <div className="evidence-grid">{complaint.media.map((media) => <div className="evidence-item" key={media.id}><AuthenticatedMedia token={token} url={media.url} type={media.type} name={media.name} /></div>)}</div> : <p className="empty-note">No photo or video was attached to this report.</p>}
        {ai && <AIReview report={ai} analysis={latest} token={token} isVideo={complaint.media.some((media) => media.type === 'video')} />}
      </article>
      <article className="panel timeline-panel"><div className="panel-heading"><div><span className="section-kicker">A TRACEABLE RECORD</span><h2>Report timeline</h2></div></div><div className="timeline">{[...complaint.timeline].reverse().map((event, index) => <div className="timeline-item" key={`${event.timestamp}-${index}`}><span className="timeline-marker" /><div><strong>{shortStatus(event.action)}</strong>{event.to && <span className="timeline-transition">{event.from ? `${shortStatus(event.from)} → ` : ''}{shortStatus(event.to)}</span>}{event.remarks && <p>{event.remarks}</p>}<small>{formatDate(event.timestamp)}</small></div></div>)}{latestUpdate && <div className="timeline-item"><span className="timeline-marker" /><div><strong>Repair update shared</strong><p>{latestUpdate.note}</p>{latestUpdate.evidence_url && <AuthenticatedMedia token={token} url={latestUpdate.evidence_url} type="image" name="Repair evidence" />}<small>{formatDate(latestUpdate.created_at)}</small></div></div>}{complaint.timeline.length === 0 && !latestUpdate && <p className="empty-note">The report has just been submitted. Updates will be recorded here.</p>}</div></article>
    </div>
    <aside className="detail-side">{isAuthority && <article className="panel detail-action-panel"><span className="section-kicker">AUTHORITY WORKFLOW</span><h2>Update report</h2><form onSubmit={changeStatus} className="workflow-form"><label>New status<select value={status} onChange={(event) => setStatus(event.target.value)}>{(nextStatuses[complaint.status] || []).map((option) => <option value={option} key={option}>{shortStatus(option)}</option>)}</select></label>{status === 'REJECTED' && <label>Reason for rejection<textarea value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} minLength={5} maxLength={2000} required placeholder="Explain what needs to change or why this report cannot proceed." /></label>}{status === 'ASSIGNED' && <label>Assign authority team member<select value={assignee} onChange={(event) => setAssignee(event.target.value)} required><option value="">Choose an authority team member</option>{team.map((member) => <option key={member.id} value={member.id}>{member.full_name}</option>)}</select></label>}<label>Public update<textarea value={remarks} onChange={(event) => setRemarks(event.target.value)} maxLength={4000} rows={3} placeholder="Optional context to keep neighbors informed." /></label><button className="button button-primary button-full" disabled={busy}>{busy ? 'Saving…' : 'Save status update'}<ArrowUpRight size={15} /></button></form><p className="form-note">Status changes are recorded and the reporter is notified.</p></article>}
      {canNgo && <NGOProposalBox complaint={complaint} token={token} onChanged={onChanged} onError={onError} />}
      {(user.role === 'authority' || user.role === 'admin') && ['ASSIGNED', 'REPAIR_IN_PROGRESS'].includes(complaint.status) && <article className="panel detail-action-panel"><span className="section-kicker">FIELD NOTES</span><h2>Add repair update</h2><form onSubmit={addUpdate} className="workflow-form"><label>What changed?<textarea value={note} onChange={(event) => setNote(event.target.value)} minLength={2} maxLength={4000} required rows={4} placeholder="Share a meaningful progress update…" /></label><label>Photo evidence<input className="file-input" type="file" accept="image/jpeg,image/png,image/webp,image/bmp" onChange={(event) => setEvidence(event.target.files?.[0] || null)} /></label><button className="button button-secondary button-full" disabled={busy || !note.trim()}><ImagePlus size={15} />Add update</button></form></article>}
      {complaint.rejection_reason && <article className="panel rejection-note"><span className="section-kicker">AUTHORITY RESPONSE</span><h3>Why this report was declined</h3><p>{complaint.rejection_reason}</p></article>}
      <article className="panel priority-card"><div className="priority-card-icon"><Gauge size={17} /></div><div><span className="section-kicker">AI-ASSISTED PRIORITY</span><strong>{complaint.priority_score || 'Awaiting review'}<small>/ 100</small></strong><p>Helpful for triage, never a substitute for human judgment.</p></div></article>
      </aside></div>
  </section>
}

function NGOProposalBox({ complaint, token, onChanged, onError }: { complaint: Complaint; token: string; onChanged: (message: string) => Promise<void>; onError: (message: string) => void }) {
  const [proposal, setProposal] = useState('')
  const [busy, setBusy] = useState(false)
  const submitted = complaint.interventions.some((item) => item.proposal)
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await api(`/api/ngo/interventions?complaint_id=${encodeURIComponent(complaint.id)}`, token, { method: 'POST', body: JSON.stringify({ proposal }) })
      await onChanged('Community proposal recorded without changing the report status.')
    } catch (error) { onError(messageOf(error)) } finally { setBusy(false) }
  }
  return <article className="panel detail-action-panel"><span className="section-kicker">COMMUNITY SUPPORT</span><h2>Offer your help</h2>{submitted ? <p className="form-note">A community proposal has already been submitted on this report.</p> : <form onSubmit={submit} className="workflow-form"><label>Proposed contribution<textarea value={proposal} onChange={(event) => setProposal(event.target.value)} minLength={10} maxLength={4000} rows={4} required placeholder="Describe the support your organization can safely offer…" /></label><button className="button button-secondary button-full" disabled={busy}>{busy ? 'Submitting…' : 'Submit proposal'}<ArrowUpRight size={15} /></button></form>}<p className="form-note">Only verified organizations can participate. Official status remains with the authority.</p></article>
}

function ReportModal({ token, complaints, onClose, onCreated }: { token: string; complaints: Complaint[]; onClose: () => void; onCreated: (message: string) => Promise<void> }) {
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')
  const [road, setRoad] = useState('')
  const [description, setDescription] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [nearby, setNearby] = useState<NearbyReport[]>([])
  const [duplicate, setDuplicate] = useState('')
  const [checking, setChecking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState('')
  const [locating, setLocating] = useState(false)
  const [showMap, setShowMap] = useState(false)

  const pickLocation = useCallback((lat: number, lng: number) => { setLatitude(lat.toFixed(6)); setLongitude(lng.toFixed(6)); setNearby([]); setDuplicate('') }, [])
  async function locateMe() {
    if (!navigator.geolocation) { setLocalError('Location services are not available in this browser. You can enter coordinates manually.'); return }
    setLocating(true)
    navigator.geolocation.getCurrentPosition((position) => {
      pickLocation(position.coords.latitude, position.coords.longitude)
      setShowMap(true)
      setLocating(false)
    }, () => {
      setLocalError('We couldn’t get your location. Check browser permission or enter coordinates manually.')
      setLocating(false)
    }, { enableHighAccuracy: true, timeout: 10000 })
  }
  async function findNearby() {
    if (!latitude || !longitude) { setLocalError('Set a location before checking nearby reports.'); return }
    setChecking(true)
    setLocalError('')
    try {
      const result = await api<{ items: NearbyReport[] }>('/api/complaints/nearby?latitude=' + encodeURIComponent(latitude) + '&longitude=' + encodeURIComponent(longitude) + '&radius_meters=25', token)
      setNearby(result.items)
    } catch (error) { setLocalError(messageOf(error)) } finally { setChecking(false) }
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setLocalError('')
    const body = new FormData()
    body.set('description', description)
    body.set('road_name', road)
    if (latitude) body.set('latitude', latitude)
    if (longitude) body.set('longitude', longitude)
    if (duplicate) body.set('duplicate_of', duplicate)
    if (file) body.set('upload', file)
    try {
      await api<Complaint>('/api/complaints', token, { method: 'POST', body })
      await onCreated('Your report has been submitted. Any attached evidence will be analyzed in the background.')
    } catch (error) { setLocalError(messageOf(error)) } finally { setBusy(false) }
  }

  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><section className="modal report-modal"><div className="modal-header"><div><span className="section-kicker">HELP YOUR COMMUNITY SEE IT</span><h2>Report a road issue</h2><p>Clear details make it easier for the right people to respond.</p></div><button className="icon-button" onClick={onClose} aria-label="Close form"><X size={19} /></button></div><form className="report-form" onSubmit={submit}>
      <label>What did you notice?<textarea value={description} onChange={(event) => setDescription(event.target.value)} minLength={10} maxLength={8000} rows={4} required placeholder="Describe the issue, its size, and anything that might help a repair team find it." /></label>
      <label>Street or nearby landmark <span className="optional">OPTIONAL</span><input value={road} onChange={(event) => setRoad(event.target.value)} maxLength={180} placeholder="e.g. Cedar Avenue, near the library" /></label>
      <div className="location-picker-head"><div><strong>Pin the location</strong><span>Helps local teams find the issue. Coordinates are optional.</span></div><button className="button button-secondary button-small" type="button" onClick={() => void locateMe()} disabled={locating}><MapPin size={14} />{locating ? 'Finding you…' : 'Use my location'}</button></div>
      <div className="coordinate-row"><label>Latitude<input type="number" step="any" min="-90" max="90" value={latitude} onChange={(event) => { setLatitude(event.target.value); setNearby([]) }} placeholder="e.g. 12.9716" /></label><label>Longitude<input type="number" step="any" min="-180" max="180" value={longitude} onChange={(event) => { setLongitude(event.target.value); setNearby([]) }} placeholder="e.g. 77.5946" /></label><button className="button button-secondary button-small locate-map-button" type="button" onClick={() => setShowMap((show) => !show)}><MapPin size={14} />{showMap ? 'Hide map' : 'Choose on map'}</button></div>
      {showMap && <Suspense fallback={<div className="media-loading">Loading map…</div>}><MapView complaints={complaints} onPick={pickLocation} className="report-map" /></Suspense>}
      <button className="nearby-button" type="button" onClick={() => void findNearby()} disabled={checking || !latitude || !longitude}><Search size={14} />{checking ? 'Checking nearby…' : 'Check for reports within 25 m'}</button>
      {nearby.length > 0 && <div className="duplicate-options"><p>Similar open reports are nearby. Linking is optional; your report and evidence will still be saved.</p>{nearby.map((item) => <label key={item.id} className={`duplicate-option ${duplicate === item.id ? 'chosen' : ''}`}><input type="radio" name="duplicate" checked={duplicate === item.id} onChange={() => setDuplicate(item.id)} /><span><strong>{item.id} · {item.road_name || 'Location pin'}</strong><small>{item.distance_meters} m away · {shortStatus(item.status)}</small></span><Severity severity={item.severity} /></label>)}{duplicate && <button className="text-button" type="button" onClick={() => setDuplicate('')}>Remove link and submit as an independent report</button>}</div>}
      <label className="upload-field"><span className="upload-icon"><ImagePlus size={18} /></span><span><strong>{file ? file.name : 'Add a photo or short video'}</strong><small>JPEG, PNG, WebP, MP4, MOV or WebM · max 50 MB</small></span><span className="upload-choose">Browse<input type="file" accept="image/jpeg,image/png,image/webp,image/bmp,video/mp4,video/quicktime,video/webm,video/x-msvideo,video/x-matroska" onChange={(event) => { const selectedFile = event.target.files?.[0]; setFile(selectedFile || null); if (selectedFile && selectedFile.size > 50 * 1024 * 1024) setLocalError('This file exceeds the 50 MB upload limit.') }} /></span></label>
      <p className="form-note">AI reviews supported pothole evidence only. Automated results are estimates and are never a substitute for authority review.</p>
      {localError && <div className="form-error"><CircleAlert size={16} />{localError}</div>}
      <div className="modal-actions"><button className="button button-quiet" type="button" onClick={onClose}>Cancel</button><button className="button button-primary" disabled={busy || (file != null && file.size > 50 * 1024 * 1024)}>{busy ? 'Submitting report…' : 'Submit report'}<ArrowUpRight size={15} /></button></div>
    </form></section></div>
}

function NotificationPanel({ items, onClose, onRead }: { items: NotificationItem[]; onClose: () => void; onRead: (item: NotificationItem) => void }) {
  return <div className="notification-panel"><div className="panel-heading"><div><span className="section-kicker">STAY IN THE LOOP</span><h2>Notifications</h2></div><button className="icon-button" aria-label="Close notifications" onClick={onClose}><X size={17} /></button></div>{items.length ? items.slice(0, 12).map((item) => <button key={item.id} className={`notification-item ${item.read ? '' : 'unread'}`} onClick={() => onRead(item)}><span className="notification-icon"><Bell size={14} /></span><span><strong>{item.message}</strong><small>{formatDate(item.created_at)}</small></span><ArrowUpRight size={14} /></button>) : <p className="empty-note">You’re all caught up.</p>}</div>
}

function EmptyState({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-state-icon"><MapPin size={20} /></span><strong>{title}</strong><p>{text}</p>{action}</div>
}

function EmptyChart({ text }: { text: string }) { return <div className="empty-chart"><span>{text}</span></div> }

function statCell(label: string, value: string) { return <div className="ai-stat" key={label}><span>{label}</span><strong>{value}</strong></div> }

function AIReview({ report, analysis, token, isVideo }: { report: Record<string, unknown>; analysis: Complaint['analyses'][number] | undefined; token: string; isVideo: boolean }) {
  const patterns = Array.isArray(report.abnormal_patterns) ? report.abnormal_patterns : []
  return <div className="ai-review"><div className="ai-review-title"><span className="ai-spark">✳</span><div><span className="section-kicker">MODEL-ASSISTED REVIEW</span><h3>Analysis summary</h3></div><span className="ai-disclaimer">Not a final decision</span></div><div className="ai-stats">{statCell('Candidates', String(report.unique_damage_count ?? '—'))}{statCell('Model confidence', typeof report.average_confidence === 'number' ? `${Math.round(report.average_confidence * 100)}%` : '—')}{statCell('Estimated severity', String(report.severity ?? 'Review'))}</div><p>{String(report.explanation ?? 'Analysis completed; please review the original evidence.')}</p>{patterns.length > 0 && <ul className="ai-patterns">{patterns.map((pattern, index) => <li key={index}>{String(pattern)}</li>)}</ul>}{analysis?.status === 'FAILED' && <p className="ai-error">Analysis could not be completed: {analysis.error_message}</p>}{analysis?.status === 'QUEUED' || analysis?.status === 'PROCESSING' ? <p className="analysis-pending"><span className="pulse-dot" />AI analysis is {analysis.status.toLowerCase()}…</p> : analysis?.annotated_url && <AuthenticatedMedia token={token} url={analysis.annotated_url} type={isVideo ? 'video' : 'image'} name="Annotated model result" annotated />}</div>
}

function AuthenticatedMedia({ token, url, type, name, annotated = false }: { token: string; url: string; type: string; name: string; annotated?: boolean }) {
  const [objectUrl, setObjectUrl] = useState('')
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let active = true
    let currentUrl = ''
    fetch(`${import.meta.env.VITE_API_BASE ?? ''}${url}`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) throw new Error('Media could not be loaded.')
        return response.blob()
      })
      .then((blob) => {
        if (!active) return
        currentUrl = URL.createObjectURL(blob)
        setObjectUrl(currentUrl)
      })
      .catch(() => { if (active) setFailed(true) })
    return () => {
      active = false
      if (currentUrl) URL.revokeObjectURL(currentUrl)
    }
  }, [token, url])
  if (failed) return <div className="media-unavailable">Media could not be loaded.</div>
  if (!objectUrl) return <div className="media-loading">Loading protected media…</div>
  if (type === 'video') return <div className="authenticated-media"><video src={objectUrl} controls preload="metadata" />{annotated && <a className="annotated-link" href={objectUrl} download="civicfix-annotated.webm"><ArrowUpRight size={14} />Download annotated video</a>}{!annotated && <span>{name}</span>}</div>
  return <div className="authenticated-media">{annotated ? <a className="annotated-link" href={objectUrl} download="civicfix-annotated.jpg"><ImagePlus size={15} />Download annotated image<ArrowUpRight size={14} /></a> : <a href={objectUrl} target="_blank" rel="noreferrer"><img src={objectUrl} alt={name} /></a>}{!annotated && <span>{name}</span>}</div>
}
function initials(name: string) { return name.split(/\s+/).slice(0, 2).map((part) => part[0] || '').join('').toUpperCase() || 'CF' }
function greeting() { const hour = new Date().getHours(); return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening' }
function shortMonth(month: string) { const date = new Date(`${month}-01T00:00:00`); return Number.isNaN(date.getTime()) ? month : date.toLocaleString(undefined, { month: 'short' }) }
function pageTitle(page: Page, role: Role) {
  if (page === 'overview') return 'Overview'
  if (page === 'reports') return role === 'citizen' ? 'Your reports' : 'Reports'
  if (page === 'map') return 'Live map'
  if (page === 'repairs') return 'Repair progress'
  if (page === 'impact') return 'Community impact'
  return 'Administration'
}
function messageOf(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please try again.' }

export default App
