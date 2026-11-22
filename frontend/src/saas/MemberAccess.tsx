import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, LoadingStatus, useAction } from '../Loading'

type Member = { userId: string; employeeCode: string | null; roles: string[]; enabled: boolean; canSign: boolean; canUseAi: boolean; version: number }

export default function MemberAccess({ tenant, onClose }: { tenant: { tenantId: string; name: string; kind: string }; onClose: () => void }) {
  const [members, setMembers] = useState<Member[] | null>(null)
  const [selected, setSelected] = useState<Member | null>(null)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const action = useAction()
  const load = async () => {
    setError('')
    try {
      const response = await apiFetch(`/api/platform/tenants/${tenant.tenantId}/members`)
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not load member access.')
      setMembers(result.members)
    } catch (cause) { setError(String(cause)) }
  }
  useEffect(() => { void load() }, [tenant.tenantId])
  const toggleRole = (role: string, checked: boolean) => {
    if (!selected) return
    const roles = checked ? [...selected.roles.filter(r => !(role === 'radiologist' && r === 'transcriptionist') && !(role === 'transcriptionist' && r === 'radiologist')), role] : selected.roles.filter(r => r !== role)
    setSelected({ ...selected, roles, canSign: roles.includes('radiologist') && selected.canSign, canUseAi: roles.some(role => ['radiologist', 'transcriptionist'].includes(role)) && selected.canUseAi })
  }
  return <div className="modal-backdrop"><section className="modal creation-dialog" role="dialog" aria-modal="true" aria-label="Workspace member access">
    <div className="section-head"><h2>Member access · {tenant.name}</h2><button className="button ghost" disabled={Boolean(action.pending)} onClick={onClose}>Close</button></div>
    <p>Business access controls only. Platform administration does not grant access to patient reports.</p>
    {error && <p role="alert" className="action-error">{error}</p>}
    {!members && !error && <LoadingStatus>Loading members…</LoadingStatus>}
    <table><thead><tr><th>Member</th><th>Roles</th><th>Access</th><th /></tr></thead><tbody>{members?.map(member => <tr key={member.userId}><td>{member.employeeCode || member.userId}</td><td>{member.roles.join(', ')}</td><td>{member.enabled ? 'Active' : 'Inactive'}<small>Signing: {member.canSign ? 'yes' : 'no'} · AI permission: {member.canUseAi ? 'yes' : 'no'}</small></td><td><button className="button ghost" disabled={Boolean(action.pending)} onClick={() => { setSelected({ ...member }); setReason('') }}>Edit access</button></td></tr>)}</tbody></table>
    {members?.length === 0 && <p>No members have been provisioned for this workspace yet.</p>}
    {selected && <form onSubmit={event => {
      event.preventDefault()
      void action.run('access', async () => {
        const response = await apiFetch(`/api/platform/tenants/${tenant.tenantId}/members/${selected.userId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ roles: selected.roles, enabled: selected.enabled, canSign: selected.canSign, canUseAi: selected.canUseAi, version: selected.version, reason }) })
        const result = await response.json()
        if (!response.ok) throw new Error(result.error || 'Could not update access.')
        setSelected(null); await load()
      })
    }}><h3>Access settings</h3><fieldset className="access-fields" disabled={Boolean(action.pending)}>
      {tenant.kind === 'HOSPITAL' ? ['hospital_admin', 'radiologist', 'transcriptionist'].map(role => <label key={role}><input type="checkbox" checked={selected.roles.includes(role)} onChange={event => toggleRole(role, event.target.checked)} /> {role.replace(/_/g, ' ')}</label>) : <p>Individual owner and radiologist roles remain fixed.</p>}
      <label><input type="checkbox" checked={selected.enabled} onChange={event => setSelected({ ...selected, enabled: event.target.checked })} /> Active membership</label>
      <label><input type="checkbox" disabled={!selected.roles.includes('radiologist')} checked={selected.canSign} onChange={event => setSelected({ ...selected, canSign: event.target.checked })} /> Verified permission to sign reports</label>
      <label><input type="checkbox" disabled={!selected.roles.some(role => ['radiologist', 'transcriptionist'].includes(role))} checked={selected.canUseAi} onChange={event => setSelected({ ...selected, canUseAi: event.target.checked })} /> AI permission (transcriptionists use assigned-report chat only)</label>
      <label>Reason / verification reference<input required maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
    </fieldset><p>Changes revoke any active editing lease. Sign in again if recent authentication is requested.</p>
    {action.error && <p role="alert" className="action-error">{action.error}</p>}
    <ActionButton className="button primary" type="submit" disabled={!selected.roles.length || Boolean(action.pending)} busy={action.pending === 'access'} busyLabel="Updating access…">Save access</ActionButton>
    </form>}
  </section></div>
}
