import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, LoadingStatus, useAction } from '../Loading'

type Event = { time: string; tenantId: string; action: string; outcome: string; requestId: string }
type Overview = { accounts: { kind: string; status: string; count: number }[]; activeMemberships: number }

export default function PlatformOverview({ onAccounts, auditOnly = false }: { onAccounts?: (kind?: string, status?: string) => void; auditOnly?: boolean }) {
  const [overview, setOverview] = useState<Overview | null>(null); const [events, setEvents] = useState<Event[]>([])
  const [search, setSearch] = useState(''); const [updated, setUpdated] = useState(''); const action = useAction()
  const load = () => action.run('overview', async () => {
    const response = await apiFetch(auditOnly ? '/api/platform/audit' : '/api/platform/dashboard')
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || 'Could not load platform overview.')
    if (!auditOnly) setOverview(result.overview)
    setEvents(result.events); setUpdated(new Date().toLocaleTimeString())
  })
  useEffect(() => { void load() }, [])
  const count = (kind: string, status = '') => overview?.accounts.filter(row => (!kind || row.kind === kind) && (!status || row.status === status)).reduce((sum, row) => sum + row.count, 0) || 0
  const matching = events.filter(event => `${event.tenantId} ${event.action} ${event.outcome}`.toLowerCase().includes(search.toLowerCase()))
  return <>
    {!auditOnly && <div className="platform-metrics" aria-label="Platform summary">
      <button className="platform-metric" disabled={!overview} onClick={() => onAccounts?.('HOSPITAL')}><span>Hospital accounts</span><strong>{overview ? count('HOSPITAL') : '—'}</strong><small>{count('HOSPITAL','ACTIVE')} active hospitals ↗</small></button>
      <button className="platform-metric" disabled={!overview} onClick={() => onAccounts?.('INDIVIDUAL')}><span>Individual accounts</span><strong>{overview ? count('INDIVIDUAL') : '—'}</strong><small>{count('INDIVIDUAL','ACTIVE')} active individuals ↗</small></button>
      <div className="platform-metric"><span>Active memberships</span><strong>{overview?.activeMemberships ?? '—'}</strong><small>Across all customer workspaces</small></div>
      <button className="platform-metric" disabled={!overview} onClick={() => onAccounts?.('', 'PENDING')}><span>Pending activation</span><strong>{overview ? count('', 'PENDING') : '—'}</strong><small>Review customer account settings ↗</small></button>
    </div>}
    <section className="card"><div className="section-head"><div><h2>Latest administrative activity</h2><p className="platform-muted">Business events only · latest 100 events{updated && ` · Updated ${updated}`}</p></div><ActionButton className="button ghost" busy={Boolean(action.pending)} onClick={() => void load()}>Refresh overview</ActionButton></div>
      {action.error && <p role="alert" className="action-error">{action.error}</p>}
      {action.pending && !updated && <LoadingStatus>Loading platform activity…</LoadingStatus>}
      {auditOnly && <div className="platform-filters"><label className="platform-search"><input aria-label="Search audit activity" placeholder="Search workspace, action or outcome…" value={search} onChange={event => setSearch(event.target.value)}/></label></div>}
      <div className="platform-table-scroll"><table><thead><tr><th>Time</th><th>Workspace</th><th>Action</th><th>Result</th></tr></thead><tbody>{(auditOnly ? matching : matching.slice(0,6)).map((event,index) => <tr key={`${event.requestId}-${index}`}><td>{new Date(event.time).toLocaleString()}</td><td className="workspace-id">{event.tenantId}</td><td>{event.action.replace(/\./g,' · ').replace(/_/g,' ')}</td><td><span className="platform-status">{event.outcome}</span></td></tr>)}</tbody></table></div>
      {!action.pending && !matching.length && !action.error && <div className="platform-empty"><h3>No administrative activity yet</h3><p>Account and permission changes will appear here. Clinical report content is never shown.</p></div>}
    </section>
  </>
}
