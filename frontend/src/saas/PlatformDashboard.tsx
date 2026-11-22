import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Activity, ArrowUpRight, Building2, ChartNoAxesColumn, CreditCard, LayoutDashboard, LogOut, Plus, Search, Settings2, ShieldCheck, UserPlus, Users } from 'lucide-react'
import { ActionButton, LoadingStatus, useAction } from '../Loading'
import PlatformOverview from './PlatformOverview'
import PlatformAccounting from './PlatformAccounting'
import AccountCreation from './AccountCreation'
import AccountSecurity from './AccountSecurity'
import ProductionChecks from './ProductionChecks'
import type { Session } from './types'
import './platform.css'

type Tenant = { tenantId: string; name: string; kind: string; status: string; maxUsers: number | null; allowStaffCreation: boolean; version: number | null }
type Page = 'overview' | 'accounts' | 'create' | 'usage' | 'payments' | 'audit' | 'security'
const pages = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard }, { id: 'accounts', label: 'Customer accounts', icon: Building2 },
  { id: 'create', label: 'Create account', icon: UserPlus }, { id: 'usage', label: 'Token usage', icon: ChartNoAxesColumn },
  { id: 'payments', label: 'Manual payments', icon: CreditCard }, { id: 'audit', label: 'Audit activity', icon: Activity },
  { id: 'security', label: 'Account security', icon: ShieldCheck },
] as const

export default function PlatformDashboard({ session, tenants, cursor, loading, error, loadTenants, onConfigure, onAccess, onDelete, onLogout, onSelect, dialogs }: {
  session: Session; tenants: Tenant[]; cursor: string | null; loading: boolean; error: string;
  loadTenants: (after?: string) => Promise<void>; onConfigure: (tenant: Tenant) => void; onAccess: (tenant: Tenant) => void;
  onDelete: (tenant: Tenant) => void;
  onLogout: () => Promise<void>; onSelect: (id: string) => Promise<void>; dialogs: ReactNode;
}) {
  const [page, setPage] = useState<Page>('overview'); const [search, setSearch] = useState('')
  // Customer pages are fetched on demand, not alongside every overview load.
  useEffect(() => { if (page === 'accounts') void loadTenants() }, [page])
  const [kind, setKind] = useState(''); const [status, setStatus] = useState(''); const action = useAction()
  const active = pages.find(item => item.id === page)!
  const accounts = (selectedKind = '', selectedStatus = '') => { setKind(selectedKind); setStatus(selectedStatus); setPage('accounts') }
  const rows = tenants.filter(tenant => (!kind || tenant.kind === kind) && (!status || tenant.status === status) && `${tenant.name} ${tenant.tenantId}`.toLowerCase().includes(search.toLowerCase()))
  return <div className="platform-shell">
    <aside className="platform-sidebar"><div className="platform-brand">Radiology<span>OS</span><small>ADMIN CONSOLE</small></div>
      <div className="platform-nav-label">CONTROL CENTER</div><nav aria-label="Platform navigation">{pages.map(item => <button key={item.id} className={page === item.id ? 'selected' : ''} aria-current={page === item.id ? 'page' : undefined} onClick={() => setPage(item.id)}><item.icon size={18}/><span>{item.label}</span>{page === item.id && <span className="nav-dot"/>}</button>)}</nav>
      <div className="platform-role"><div className="platform-avatar"><ShieldCheck size={20}/></div><div><b>Super administrator</b><small>Business-only access</small></div></div>
      <ActionButton className="platform-signout" busy={action.pending === 'logout'} onClick={() => void action.run('logout', onLogout)}><LogOut size={17}/> Sign out</ActionButton>
    </aside>
    <main className="platform-main"><header className="platform-topbar"><span>Administration <span className="platform-slash">/</span> <b>{active.label}</b></span><span className="platform-access-label"><ShieldCheck size={15}/>{session.demoMode ? 'LOCAL DEMO' : 'RESTRICTED ACCESS'}</span></header>
      <div className="platform-page-head"><div><p className="platform-eyebrow">RADIOLOGY OS · PLATFORM</p><h1>{active.label === 'Overview' ? 'Platform overview' : active.label}</h1><p>Manage your customers, team access and platform operations.</p></div>{page !== 'create' && <button className="button primary" onClick={() => setPage('create')}><Plus size={17}/> Create account</button>}</div>
      {session.demoMode && <div className="admin-notice" role="status"><b>Local testing environment.</b> No emails, AWS requests or AI calls. Any valid email can be a local test identity. Historical files can be validated and staged locally, but are not indexed.</div>}
      {action.error && <p className="action-error" role="alert">{action.error}</p>}
      {page === 'overview' && <><PlatformOverview onAccounts={accounts}/><section className="platform-quick-actions" aria-label="Quick actions">{[
        { title: 'Customer management', text: 'Configure accounts, seats and member permissions.', icon: Building2, go: () => accounts() },
        { title: 'AI usage visibility', text: 'Review actual input and output tokens by user.', icon: ChartNoAxesColumn, go: () => setPage('usage') },
        { title: 'Manual payments', text: 'Record and independently verify INR payments.', icon: CreditCard, go: () => setPage('payments') },
      ].map(item => <button key={item.title} onClick={item.go}><item.icon size={23}/><b>{item.title}</b><span>{item.text}</span><ArrowUpRight className="quick-arrow" size={18}/></button>)}</section></>}
      {page === 'accounts' && <section className="card"><div className="section-head"><div><h2>Hospital and radiologist accounts</h2><p className="platform-muted">Account configuration and permission management.</p></div><ActionButton className="button ghost" busy={loading} onClick={() => void loadTenants()}>Refresh accounts</ActionButton></div>
        <div className="platform-filters"><label className="platform-search"><Search size={17}/><input aria-label="Search customer accounts" placeholder="Search name or workspace ID…" value={search} onChange={event => setSearch(event.target.value)}/></label><select aria-label="Account type filter" value={kind} onChange={event => setKind(event.target.value)}><option value="">All account types</option><option value="HOSPITAL">Hospitals</option><option value="INDIVIDUAL">Individuals</option></select><select aria-label="Account status filter" value={status} onChange={event => setStatus(event.target.value)}><option value="">All statuses</option>{['ACTIVE','PENDING','SUSPENDED'].map(value => <option key={value}>{value}</option>)}</select>{(kind || status || search) && <button className="button ghost" onClick={() => { setKind(''); setStatus(''); setSearch('') }}>Clear filters</button>}</div>
        {error && <p className="action-error" role="alert">{error}</p>}
        <div className="platform-table-scroll"><table><thead><tr><th>Customer</th><th>Account type</th><th>Status</th><th>Seats</th><th>Actions</th></tr></thead><tbody>{rows.map(tenant => <tr key={tenant.tenantId}><td><b>{tenant.name}</b><small className="workspace-id">{tenant.tenantId}</small></td><td><span className="platform-type"><Users size={15}/>{tenant.kind === 'HOSPITAL' ? 'Hospital' : 'Individual'}</span></td><td><span className={`platform-status status-${tenant.status.toLowerCase()}`}>{tenant.status}</span></td><td>{tenant.maxUsers ?? 'Not configured'}</td><td><div className="platform-row-actions"><button className="button ghost" disabled={!tenant.version} onClick={() => onConfigure(tenant)}><Settings2 size={14}/>Configure</button><button className="button ghost" onClick={() => onAccess(tenant)}>Member access</button><button className="button ghost danger" onClick={() => onDelete(tenant)}>Delete account</button></div></td></tr>)}</tbody></table></div>
        {loading && <LoadingStatus>Loading accounts…</LoadingStatus>}{!loading && !rows.length && <div className="platform-empty"><Building2 size={28}/><h3>No matching accounts</h3><p>{tenants.length ? 'Try another search or clear your filters.' : 'Create your first hospital or individual account to get started.'}</p></div>}
        <div className="platform-table-footer"><small>Showing {rows.length} of {tenants.length} loaded accounts. Filters apply to loaded pages.</small>{cursor && <button className="button ghost" disabled={loading} onClick={() => void loadTenants(cursor)}>Load more accounts</button>}</div>
      </section>}
      {page === 'create' && <AccountCreation onCreated={loadTenants} demoMode={session.demoMode}/>}
      {page === 'usage' && <PlatformAccounting key="usage" section="usage"/>}
      {page === 'payments' && <PlatformAccounting key="payments" section="payments"/>}
      {page === 'audit' && <PlatformOverview auditOnly/>}
      {page === 'security' && <><AccountSecurity/><ProductionChecks/>{Boolean(session.workspaces?.length) && <section className="card"><h2>Your explicitly granted workspaces</h2>{session.workspaces?.map(workspace => <div className="workspace-choice" key={workspace.tenantId}><b>{workspace.name}</b><ActionButton className="button primary" busy={action.pending === workspace.tenantId} onClick={() => void action.run(workspace.tenantId, () => onSelect(workspace.tenantId))}>Open workspace</ActionButton></div>)}</section>}<p className="platform-muted">Platform administration does not grant permission to read patient records or sign clinical reports.</p></>}
      <footer className="platform-footer">Radiology OS Administration <span>No subscriptions or automatic payment processing enabled</span></footer>
    </main>{dialogs}
  </div>
}
