import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'
import type { Report } from '../types'
import type { Lease } from './types'

export default function AssignmentControls({ report, lease, disabled, onSaved }: { report: Report; lease: Lease | null; disabled: boolean; onSaved: (report: Report) => void }) {
  const [members, setMembers] = useState<{ userId: string; employeeCode: string | null; roles: string[] }[]>([])
  const [userId, setUserId] = useState('')
  const [assigned, setAssigned] = useState(true)
  const [error, setError] = useState('')
  const action = useAction()
  useEffect(() => {
    let active = true
    void apiFetch('/api/clinical/members').then(async response => {
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not load clinical staff.')
      if (active) setMembers(result.members)
    }).catch(cause => { if (active) setError(String(cause)) })
    return () => { active = false }
  }, [])
  return <form className="lease-banner" aria-label="Report assignment" onSubmit={event => {
    event.preventDefault(); void action.run('assignment', async () => {
      const response = await apiFetch(`/api/reports/${report.id}/assign`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ userId, assigned, version: report.version, lease }) })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not change assignment.')
      onSaved(result)
    })
  }}><label>Clinical employee<select required disabled={disabled || Boolean(action.pending)} value={userId} onChange={event => setUserId(event.target.value)}><option value="">Choose employee</option>{members.map(member => <option key={member.userId} value={member.userId}>{member.employeeCode || member.userId} · {member.roles.join(', ')}</option>)}</select></label>
    <label>Action<select value={String(assigned)} disabled={disabled || Boolean(action.pending)} onChange={event => setAssigned(event.target.value === 'true')}><option value="true">Assign report</option><option value="false">Remove assignment</option></select></label>
    <ActionButton className="button ghost" type="submit" disabled={disabled || !userId || Boolean(action.pending)} busy={action.pending === 'assignment'}>Apply assignment</ActionButton>
    {(error || action.error) && <p role="alert">{error || action.error}</p>}
  </form>
}
