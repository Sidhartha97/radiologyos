import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'
import { createRequestId } from '../requestId'

type Invitation = { id: string; tenantId: string; email: string; status: string; errorCode?: string }

export default function AccountCreation({ onCreated, demoMode = false }: { onCreated: () => Promise<void>; demoMode?: boolean }) {
  const [form, setForm] = useState({ kind: 'HOSPITAL', name: '', email: '', maxUsers: 5, allowStaffCreation: true, status: 'PENDING', reason: '' })
  const [requestId, setRequestId] = useState(createRequestId)
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const action = useAction()
  const load = async () => {
    try {
      const response = await apiFetch('/api/platform/account-invitations'); const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not load account invitations.')
      setInvitations(result.invitations); setError('')
    } catch (cause) { setError(String(cause)) }
  }
  useEffect(() => { void load() }, [])
  return <section className="card"><h2>Create hospital or individual account</h2>
    <p>Only the super admin can create these accounts. Email verification is required. Clinical signing and AI permissions are not granted automatically.</p>
    {demoMode && <p className="admin-notice">Local demo: any valid email works as a test identity. No email is sent. After sending the local invitation, sign in with that email and <code>DemoOnly123!</code>. Never use this password in production.</p>}
    <form onSubmit={event => { event.preventDefault(); void action.run('create', async () => {
      setSuccess('')
      const response = await apiFetch('/api/platform/tenants', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, requestId }) })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not create account.')
      setRequestId(createRequestId()); setForm({ ...form, name: '', email: '', reason: '' }); await load(); await onCreated()
      setSuccess('Account created. Send its invitation below to complete setup.')
    }) }}><div className="modal-grid" inert={Boolean(action.pending)}>
      <label>Account type<select value={form.kind} onChange={event => setForm({ ...form, kind: event.target.value, maxUsers: event.target.value === 'INDIVIDUAL' ? 1 : 5, allowStaffCreation: event.target.value === 'HOSPITAL' })}><option value="HOSPITAL">Hospital with administrator</option><option value="INDIVIDUAL">Individual radiologist</option></select></label>
      <label>Workspace name<input required maxLength={160} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
      <label>Administrator / owner email<input type="email" required value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label>
      <label>Development user seats<input required type="number" min={1} max={100000} disabled={form.kind === 'INDIVIDUAL'} value={form.maxUsers} onChange={event => setForm({ ...form, maxUsers: Number(event.target.value) })} /></label>
      <label>Initial status<select value={form.status} onChange={event => setForm({ ...form, status: event.target.value })}><option value="PENDING">Pending activation</option><option value="ACTIVE">Active (development access; no subscription)</option></select></label>
      <label>Hospital may invite employees<select disabled={form.kind === 'INDIVIDUAL'} value={String(form.allowStaffCreation)} onChange={event => setForm({ ...form, allowStaffCreation: event.target.value === 'true' })}><option value="true">Yes</option><option value="false">No</option></select></label>
      <label>Reason<input required maxLength={1000} value={form.reason} onChange={event => setForm({ ...form, reason: event.target.value })} /></label>
    </div><ActionButton type="submit" className="button primary" busy={action.pending === 'create'} disabled={Boolean(action.pending)} busyLabel="Creating account…">Create account</ActionButton></form>
    {(error || action.error) && <p className="action-error" role="alert">{error || action.error}</p>}
    {success && <p className="admin-success" role="status">{success}</p>}
    <h3>Account invitations</h3><p>Delivery must be explicitly enabled for real Cognito. No passwords are shown or stored by the application.</p>
    <table><thead><tr><th>Email</th><th>Workspace</th><th>Status</th><th /></tr></thead><tbody>{invitations.map(invitation => <tr key={invitation.id}><td>{invitation.email}</td><td>{invitation.tenantId}</td><td>{invitation.status}<small>{invitation.errorCode}</small></td><td>{['PENDING', 'FAILED', 'PROVISIONING'].includes(invitation.status) && <ActionButton className="button ghost" busy={action.pending === invitation.id} disabled={Boolean(action.pending)} onClick={() => void action.run(invitation.id, async () => {
      setSuccess('')
      const response = await apiFetch(`/api/platform/account-invitations/${invitation.id}/send`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not deliver invitation.')
      await load()
      if (result.status === 'FAILED') throw new Error(`Invitation failed (${result.errorCode || 'ProvisioningFailed'}). Check Cognito email delivery, app permissions and the user-pool configuration, then retry.`)
      setSuccess(demoMode ? `Local invitation ready for ${invitation.email}. No email was sent; use the demo password to sign in.` : result.alreadyRegistered ? `${invitation.email} already has an active sign-in. No new invitation was sent; they should sign in with their existing credentials.` : `Invitation sent to ${invitation.email}. Ask the recipient to check their inbox and spam folder.`)
    })}>Send invitation</ActionButton>}</td></tr>)}</tbody></table>
    {!invitations.length && !error && <p className="platform-muted">No account invitations yet. Create an account above to get started.</p>}
  </section>
}
