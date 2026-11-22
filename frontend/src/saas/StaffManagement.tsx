import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, LoadingStatus, useAction } from '../Loading'

type Member = { userId: string; employeeCode: string | null; roles: string[]; enabled: boolean; canSign: boolean; canUseAi: boolean; version: number }
type Invitation = { id: string; employeeCode: string; email: string; role: string; status: string; errorCode?: string }
type Roster = { members: Member[]; invitations: Invitation[] }

export default function StaffManagement({ allowed, userId }: { allowed: boolean; userId?: string }) {
  const [roster, setRoster] = useState<Roster | null>(null)
  const [error, setError] = useState('')
  const [form, setForm] = useState({ employeeCode: '', email: '', role: 'radiologist' })
  const action = useAction()
  const load = async () => {
    setError('')
    try {
      const response = await apiFetch('/api/tenant/members'); const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not load hospital staff.')
      setRoster(result)
    } catch (cause) { setError(String(cause)) }
  }
  useEffect(() => { void load() }, [])
  const mutate = async (path: string, payload = {}) => {
    const response = await apiFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || 'Staff action failed.')
    await load()
  }
  return <section className="card"><div className="section-head"><h2>Hospital staff</h2><button className="button ghost" onClick={() => void load()}>Refresh</button></div>
    {error && <p className="action-error" role="alert">{error}</p>}{action.error && <p className="action-error" role="alert">{action.error}</p>}
    {!roster && !error && <LoadingStatus>Loading staff…</LoadingStatus>}
    <p>Staff sign in with your hospital ID and their employee code. Email is used for verification and password recovery.</p>
    <table><thead><tr><th>Employee code</th><th>Roles</th><th>Status</th><th>Actions</th></tr></thead><tbody>{roster?.members.map(member => <tr key={member.userId}><td>{member.employeeCode || 'Hospital owner'}</td><td>{member.roles.map(role => role.replace(/_/g, ' ')).join(', ')}<small>Signing: {member.canSign ? 'enabled' : 'not granted'} · AI: {member.canUseAi ? 'permitted' : 'not granted'}</small></td><td>{member.enabled ? 'Active' : 'Inactive'}</td><td><ActionButton className={`button ghost ${member.enabled ? 'danger' : ''}`} disabled={member.userId === userId || Boolean(action.pending)} busy={action.pending === member.userId} onClick={() => { if (window.confirm(`${member.enabled ? 'Deactivate' : 'Reactivate'} this hospital member? Report history will be retained.`)) void action.run(member.userId, () => mutate(`/api/tenant/members/${member.userId}/${member.enabled ? 'deactivate' : 'reactivate'}`, { version: member.version })) }}>{member.enabled ? 'Deactivate' : 'Reactivate'}</ActionButton></td></tr>)}</tbody></table>
    <h3>Invite an employee</h3>{!allowed && <p>Your account configuration currently disables staff creation.</p>}
    <form onSubmit={event => { event.preventDefault(); void action.run('invite', async () => { await mutate('/api/tenant/invitations', form); setForm({ employeeCode: '', email: '', role: 'radiologist' }) }) }}>
      <div className="modal-grid" inert={!allowed || Boolean(action.pending)}><label>Employee code<input required maxLength={40} value={form.employeeCode} onChange={event => setForm({ ...form, employeeCode: event.target.value })} /></label><label>Email for verification/recovery<input required type="email" value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label><label>Role<select value={form.role} onChange={event => setForm({ ...form, role: event.target.value })}><option value="radiologist">Radiologist</option><option value="transcriptionist">Transcriptionist</option></select></label></div>
      <ActionButton className="button primary" type="submit" disabled={!allowed || Boolean(action.pending)} busy={action.pending === 'invite'} busyLabel="Reserving employee seat…">Create invitation</ActionButton>
    </form>
    <h3>Invitations</h3><table><thead><tr><th>Employee</th><th>Email</th><th>Status</th><th>Actions</th></tr></thead><tbody>{roster?.invitations.map(invitation => <tr key={invitation.id}><td>{invitation.employeeCode}</td><td>{invitation.email}</td><td>{invitation.status}<small>{invitation.errorCode}</small></td><td>{['PENDING', 'FAILED', 'PROVISIONING'].includes(invitation.status) && <ActionButton className="button ghost" disabled={!allowed || Boolean(action.pending)} busy={action.pending === invitation.id} busyLabel="Delivering…" onClick={() => void action.run(invitation.id, () => mutate(`/api/tenant/invitations/${invitation.id}/send`))}>Send invitation</ActionButton>}{['PENDING', 'FAILED', 'PROVISIONING', 'INVITED', 'EXPIRED'].includes(invitation.status) && <ActionButton className="button ghost danger" disabled={Boolean(action.pending)} busy={action.pending === `revoke-${invitation.id}`} onClick={() => { if (window.confirm('Revoke this invitation and release its reserved seat?')) void action.run(`revoke-${invitation.id}`, () => mutate(`/api/tenant/invitations/${invitation.id}/revoke`)) }}>Revoke</ActionButton>}</td></tr>)}</tbody></table>
  </section>
}
