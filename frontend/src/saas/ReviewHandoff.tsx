import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'
import type { Report } from '../types'
import type { Lease } from './types'

export default function ReviewHandoff({ report, lease, disabled, body, onSaved, onBusy }: { report: Report; lease: Lease | null; disabled: boolean; body: () => string; onSaved: (report: Report) => Promise<void>; onBusy: (busy: boolean) => void }) {
  const [members, setMembers] = useState<{ userId: string; employeeCode: string | null }[]>([])
  const [reviewerId, setReviewerId] = useState(''); const action = useAction()
  useEffect(() => { void action.run('load', async () => {
    const response = await apiFetch('/api/clinical/reviewers'); const value = await response.json()
    if (!response.ok) throw new Error(value.error || 'Could not load radiologists.')
    setMembers(value.members)
  }) }, [])
  return <form className="lease-banner" onSubmit={event => { event.preventDefault(); void action.run('handoff', async () => {
    onBusy(true)
    try {
    const response = await apiFetch(`/api/reports/${report.id}/handoff`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewerId, body: body(), version: report.version, lease }) })
    const value = await response.json()
    if (!response.ok) throw new Error(value.error || 'Could not send report for review.')
    await onSaved(value)
    } finally { onBusy(false) }
  }) }}><label>Send to radiologist<select required value={reviewerId} disabled={disabled || Boolean(action.pending)} onChange={event => setReviewerId(event.target.value)}><option value="">Choose radiologist</option>{members.map(member => <option value={member.userId} key={member.userId}>{member.employeeCode || member.userId}</option>)}</select></label>
    <ActionButton type="submit" className="button primary" disabled={disabled || !reviewerId || Boolean(action.pending)} busy={action.pending === 'handoff'} busyLabel="Saving and sending…">Save & send for review</ActionButton>{action.error && <p role="alert">{action.error}</p>}
  </form>
}
