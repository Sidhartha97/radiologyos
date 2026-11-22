import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, LoadingStatus, useAction } from '../Loading'

type Status = { state: string; jobId?: string; stage?: string; errorCode?: string; quarantineUntil?: string }
type Preview = { jobId: string; confirmationToken: string; counts: { reports: number; patients: number; members: number }; cloudCounts: { objectVersions: number; indexedDocuments: number } }
export default function CustomerDeletion({ tenant, onClose, onDeleted }: { tenant: { tenantId: string; name: string }; onClose: () => void; onDeleted: () => void }) {
  const [status, setStatus] = useState<Status | null>(null); const [preview, setPreview] = useState<Preview | null>(null)
  const [reason, setReason] = useState(''); const [error, setError] = useState(''); const action = useAction()
  const base = `/api/platform/tenants/${encodeURIComponent(tenant.tenantId)}/deletion`
  const request = async (suffix = '', payload?: object) => {
    const response = await apiFetch(base + suffix, payload ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : undefined)
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || 'Customer cleanup request failed.')
    return result
  }
  useEffect(() => { let active = true; void request().then(result => { if (active) setStatus(result) }).catch(cause => { if (active) setError(String(cause)) }); return () => { active = false } }, [tenant.tenantId])
  useEffect(() => {
    if (!status || !['CONFIRMED', 'RUNNING'].includes(status.state)) return
    const timer = window.setTimeout(() => { void request().then(setStatus).catch(cause => setError(String(cause))) }, 10000)
    return () => window.clearTimeout(timer)
  }, [status])
  const requested = status && ['CONFIRMED', 'RUNNING', 'FAILED', 'COMPLETE'].includes(status.state)
  return <div className="modal-backdrop"><section className="modal creation-dialog" role="dialog" aria-modal="true" aria-label="Permanently delete customer account">
    <div className="section-head"><h2>Delete customer · {tenant.name}</h2><button className="button ghost" disabled={Boolean(action.pending)} onClick={onClose}>Close</button></div>
    <p><b>Permanent customer deletion is irreversible.</b> This removes this workspace’s reports, signed history, chats, historical files and S3 versions, indexed documents, account records and exclusive logins. Shared hospital identities and shared AWS infrastructure remain.</p>
    <p>Staff accounts are only deactivated through Member access; they cannot be permanently deleted individually.</p>
    {!status && !error && <LoadingStatus>Checking customer cleanup status…</LoadingStatus>}
    {requested ? <div role="status"><p>Cleanup: <b>{status.state}</b> · {status.stage}</p>
      {status.quarantineUntil && <p>Access is suspended immediately. Cleanup waits until at least {new Date(status.quarantineUntil).toLocaleString()} so previously issued upload links can expire and workers can stop.</p>}
      {status.errorCode && <p className="action-error">Stage {status.stage}: {status.errorCode}. Cleanup is not complete. Correct the cause before retrying.</p>}
      {status.state === 'FAILED' && <ActionButton className="button danger" busy={Boolean(action.pending)} onClick={() => void action.run('retry', async () => setStatus(await request('/retry', { jobId: status.jobId })))}>Retry cleanup</ActionButton>}
      {status.state === 'COMPLETE' && <button className="button primary" onClick={onDeleted}>Close and refresh accounts</button>}
    </div> : <>
      {!preview && status && <ActionButton className="button ghost danger" busy={Boolean(action.pending)} onClick={() => void action.run('preview', async () => setPreview(await request('/preview', {})))}>Review deletion preview</ActionButton>}
      {preview && <form onSubmit={event => { event.preventDefault(); void action.run('delete', async () => setStatus(await request('/confirm', { jobId: preview.jobId, confirmationToken: preview.confirmationToken, confirm: true, reason }))) }}>
        <p>Preview: {preview.counts.patients} patients, {preview.counts.reports} reports, {preview.counts.members} members, {preview.cloudCounts.objectVersions} stored object versions and {preview.cloudCounts.indexedDocuments} indexed documents.</p>
        <p>A minimal non-clinical cleanup receipt/block marker is kept to prevent re-import and record errors. No report text or deleted user profile is kept in that receipt.</p>
        <label>Reason<input required maxLength={1000} value={reason} disabled={Boolean(action.pending)} onChange={event => setReason(event.target.value)}/></label>
        <p><b>Are you sure you want to permanently delete {tenant.name} and its workspace data?</b></p>
        <div className="modal-actions"><button type="button" className="button ghost" disabled={Boolean(action.pending)} onClick={onClose}>No, keep account</button><ActionButton type="submit" className="button danger" busy={Boolean(action.pending)} busyLabel="Confirming deletion…">Yes, permanently delete</ActionButton></div>
      </form>}
    </>}
    {(error || action.error) && <p role="alert" className="action-error">{error || action.error}</p>}
  </section></div>
}
