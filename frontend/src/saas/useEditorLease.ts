import { useEffect, useRef, useState } from 'react'
import { apiFetch, getWorkspaceForRequests } from '../apiFetch'
import type { Lease } from './types'

export function useEditorLease(reportId: string | null, version: number | undefined, hospital: boolean, enabled: boolean) {
  const [lease, setLease] = useState<Lease | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const current = useRef<Lease | null>(null)
  const acquiring = useRef(false)
  const alive = useRef(true)
  const editingSession = useRef(crypto.randomUUID())
  const workspace = getWorkspaceForRequests()
  const update = (value: Lease | null) => { current.current = value; setLease(value) }
  const request = async (action: string, value: unknown) => {
    const response = await apiFetch(`/api/reports/${reportId}/lock${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workspace-ID': workspace }, body: JSON.stringify(value), keepalive: action === '/release' })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || 'Editing access is unavailable.')
    return result
  }
  const release = async () => {
    const held = current.current
    if (held) { update(null); await request('/release', { lease: held }).catch(() => {}) }
  }
  const acquire = async () => {
    if (acquiring.current || !enabled || !reportId || !hospital) return
    acquiring.current = true; setBusy(true); setError('')
    try {
      const result: Lease = await request('', { editingSession: editingSession.current })
      if (!alive.current || result.version !== version) {
        await request('/release', { lease: result }).catch(() => {})
        if (alive.current) throw new Error('The saved report has changed. Reload it before editing.')
      } else update(result)
    } catch (cause) { if (alive.current) setError(cause instanceof Error ? cause.message : 'Could not start editing.') }
    finally { acquiring.current = false; if (alive.current) setBusy(false) }
  }
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; const held = current.current; current.current = null; if (held) void request('/release', { lease: held }).catch(() => {}) }
  }, [reportId, workspace])
  useEffect(() => {
    if (!hospital || !enabled || !reportId) return
    // Defer one tick so React StrictMode's probe mount never acquires a lease.
    const timer = window.setTimeout(() => void acquire(), 0)
    return () => { window.clearTimeout(timer); void release() }
  }, [reportId, workspace, hospital, enabled])
  useEffect(() => {
    if (!lease) return
    let stopped = false
    const heartbeat = window.setInterval(() => {
      const held = current.current
      if (!held) return
      void request('/renew', { lease: held }).then(result => {
        if (!stopped && current.current === held) update({ ...held, expiresAt: result.expiresAt })
      }).catch(cause => { if (!stopped) { update(null); setError(String(cause)) } })
    }, 30000)
    const expiry = window.setInterval(() => {
      if (current.current && Date.parse(current.current.expiresAt) <= Date.now()) { update(null); setError('Editing access expired. Start editing again after reloading the saved report.') }
    }, 1000)
    return () => { stopped = true; window.clearInterval(heartbeat); window.clearInterval(expiry) }
  }, [lease])
  return { lease, busy, error, acquire, release, canEdit: enabled && (!hospital || Boolean(lease)) }
}
