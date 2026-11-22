import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'
import type { Session } from './types'
import './saas.css'
import StaffManagement from './StaffManagement'
import MemberAccess from './MemberAccess'
import AccountSecurity from './AccountSecurity'
import PlatformDashboard from './PlatformDashboard'
import CustomerDeletion from './CustomerDeletion'

type Tenant = { tenantId: string; name: string; kind: string; status: string; maxUsers: number | null; allowStaffCreation: boolean; version: number | null }

export default function WorkspaceHome({ session, onSelect, onLogout }: { session: Session; onSelect: (id: string) => Promise<void>; onLogout: () => Promise<void> }) {
  const action = useAction()
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [settings, setSettings] = useState<{ maxUsers?: number; allowStaffCreation?: boolean; activeMembers?: number; reservedSeats?: number } | null>(null)
  const [selected, setSelected] = useState<Tenant | null>(null)
  const [accessTenant, setAccessTenant] = useState<Tenant | null>(null)
  const [deletingTenant, setDeletingTenant] = useState<Tenant | null>(null)
  const loadTenants = async (after = '') => {
    setLoading(true); setError('')
    try {
      const response = await apiFetch(`/api/platform/tenants?after=${encodeURIComponent(after)}`)
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not load customers.')
      setTenants(current => after ? [...current, ...result.tenants] : result.tenants); setCursor(result.nextCursor)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load customers.') }
    finally { setLoading(false) }
  }
  useEffect(() => {
    if (!session.workspace?.roles.some(role => ['hospital_admin', 'individual_owner'].includes(role))) return
    let active = true
    void apiFetch('/api/tenant/settings').then(async response => {
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not load workspace settings.')
      if (active) setSettings(result)
    }).catch(cause => { if (active) setError(String(cause)) })
    return () => { active = false }
  }, [session.workspace?.tenantId])
  if (session.platformAdmin) return <PlatformDashboard session={session} tenants={tenants} cursor={cursor} loading={loading} error={error} loadTenants={loadTenants} onConfigure={setSelected} onAccess={setAccessTenant} onDelete={setDeletingTenant} onLogout={onLogout} onSelect={onSelect} dialogs={<>
    {selected && <TenantSettings key={selected.tenantId} tenant={selected} onClose={() => setSelected(null)} onSaved={async () => { setSelected(null); await loadTenants() }}/>} 
    {accessTenant && <MemberAccess key={accessTenant.tenantId} tenant={accessTenant} onClose={() => setAccessTenant(null)}/>}
    {deletingTenant && <CustomerDeletion key={deletingTenant.tenantId} tenant={deletingTenant} onClose={() => setDeletingTenant(null)} onDeleted={() => { setDeletingTenant(null); void loadTenants() }}/>} 
  </>}/>
  return <main className="saas-home">
    <header className="topbar"><div><div className="logo">Radiology<span>OS</span></div><h1>{session.platformAdmin ? 'Platform administration' : 'Your workspaces'}</h1></div>
      <ActionButton className="button ghost" busy={action.pending === 'logout'} onClick={() => void action.run('logout', onLogout)}>Sign out</ActionButton></header>
    {error && <p className="action-error" role="alert">{error}</p>}
    {action.error && <p className="action-error" role="alert">{action.error}</p>}
    {session.workspace && <section className="card"><h2>{session.workspace.name}</h2><p>{session.workspace.kind === 'HOSPITAL' ? 'Hospital ID' : 'Workspace ID'}: <code>{session.workspace.tenantId}</code></p><p>Your roles: {session.workspace.roles.map(role => role.replace(/_/g, ' ')).join(', ')}</p><p>Report signing: {session.workspace.canSign ? 'Explicitly granted' : 'Not granted'}</p><p>AI access permission: {session.workspace.canUseAi ? 'Granted; generation remains subject to its separate feature gate' : 'Not granted'}</p></section>}
    {settings && <section className="card"><h2>Workspace settings</h2><p>User seats: {settings.maxUsers ?? '—'} · Active: {settings.activeMembers ?? '—'} · Reserved invitations: {settings.reservedSeats ?? '—'}</p><p>Hospital staff creation: {settings.allowStaffCreation ? 'Enabled' : 'Disabled'}</p></section>}
    {settings && session.workspace?.kind === 'HOSPITAL' && session.workspace.roles.includes('hospital_admin') && <StaffManagement key={session.workspace.tenantId} allowed={Boolean(settings.allowStaffCreation)} userId={session.userId} />}
    <section className="card"><h2>Select a workspace</h2>
      {(session.workspaces || []).map(workspace => <div className="workspace-choice" key={workspace.tenantId}><div><b>{workspace.name}</b><small>{workspace.kind === 'HOSPITAL' ? 'Hospital' : 'Individual radiologist'} · {workspace.roles.map(role => role.replace(/_/g, ' ')).join(', ')}</small></div>
        <ActionButton className="button primary" disabled={Boolean(action.pending)} busy={action.pending === workspace.tenantId} busyLabel="Opening…" onClick={() => void action.run(workspace.tenantId, () => onSelect(workspace.tenantId))}>Open workspace</ActionButton></div>)}
      {!session.workspaces?.length && <p>{session.platformAdmin ? 'Your platform account has no clinical workspace memberships.' : 'No active workspace is available. Contact your administrator to activate your account.'}</p>}
    </section>
    {selected && <TenantSettings key={selected.tenantId} tenant={selected} onClose={() => setSelected(null)} onSaved={async () => { setSelected(null); await loadTenants() }} />}
    {accessTenant && <MemberAccess key={accessTenant.tenantId} tenant={accessTenant} onClose={() => setAccessTenant(null)} />}
    <AccountSecurity />
  </main>
}

function TenantSettings({ tenant, onClose, onSaved }: { tenant: Tenant; onClose: () => void; onSaved: () => Promise<void> }) {
  const [name, setName] = useState(tenant.name)
  const [status, setStatus] = useState(tenant.status)
  const [seats, setSeats] = useState(tenant.maxUsers || 1)
  const [staff, setStaff] = useState(tenant.allowStaffCreation)
  const [reason, setReason] = useState('')
  const action = useAction()
  return <div className="modal-backdrop"><form className="modal creation-dialog" role="dialog" aria-modal="true" aria-label="Customer configuration" onSubmit={event => {
    event.preventDefault()
    void action.run('save', async () => {
      const response = await apiFetch(`/api/platform/tenants/${encodeURIComponent(tenant.tenantId)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, status, maxUsers: seats, allowStaffCreation: staff, version: tenant.version, reason }) })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not update customer configuration.')
      await onSaved()
    })
  }}><h2>Customer configuration</h2><p>{tenant.tenantId}</p><div className="modal-grid" inert={Boolean(action.pending)}>
    <label>Name<input required value={name} maxLength={160} onChange={event => setName(event.target.value)} /></label>
    <label>Status<select value={status} onChange={event => setStatus(event.target.value)}><option value="PENDING">Pending</option><option value="ACTIVE">Active</option><option value="SUSPENDED">Suspended</option></select></label>
    <label>User seats<input required type="number" min={1} max={100000} disabled={tenant.kind === 'INDIVIDUAL'} value={seats} onChange={event => setSeats(Number(event.target.value))} /></label>
    <label>Hospital staff creation<select disabled={tenant.kind === 'INDIVIDUAL'} value={String(staff)} onChange={event => setStaff(event.target.value === 'true')}><option value="false">Disabled</option><option value="true">Enabled</option></select></label>
    <label>Reason<input required value={reason} maxLength={1000} onChange={event => setReason(event.target.value)} /></label>
  </div>{action.error && <p className="action-error" role="alert">{action.error}</p>}<div className="modal-actions"><button className="button ghost" type="button" disabled={Boolean(action.pending)} onClick={onClose}>Cancel</button><ActionButton className="button primary" type="submit" busy={Boolean(action.pending)} busyLabel="Saving configuration…">Save configuration</ActionButton></div></form></div>
}
