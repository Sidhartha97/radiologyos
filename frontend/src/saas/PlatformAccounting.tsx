import { useEffect, useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'

type UsageUser = { tenantId: string; userId: string; email: string | null; employeeCode: string | null; inputTokens: number; outputTokens: number; totalTokens: number; calls: number; unresolvedCalls: number }
type Usage = { users: UsageUser[]; totals: { inputTokens: number; outputTokens: number; totalTokens: number; unresolvedCalls: number }; nextCursor: string | null }
type Payment = { paymentId: string; tenantId: string; amount: string; reference: string; method: string; state: string; version: number }

export default function PlatformAccounting({ section = 'all' }: { section?: 'all' | 'usage' | 'payments' }) {
  const [usage, setUsage] = useState<Usage | null>(null); const [payments, setPayments] = useState<Payment[]>([])
  const [form, setForm] = useState({ tenantId: '', amount: '', method: 'BANK', reference: '', reason: '' })
  const action = useAction()
  const request = async (path: string, payload?: unknown) => {
    const response = await apiFetch(path, payload === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const value = await response.json()
    if (!response.ok) throw new Error(value.error || 'Could not load business data.')
    return value
  }
  const load = () => action.run('load', async () => {
    const [tokens, records] = await Promise.all([request('/api/platform/token-usage'), request('/api/platform/manual-payments')])
    setUsage(tokens); setPayments(records.payments)
  })
  useEffect(() => { void load() }, [])
  const decide = (payment: Payment, state: 'VERIFIED' | 'REJECTED') => action.run(payment.paymentId, async () => {
    const receivedAmount = state === 'VERIFIED' ? window.prompt('Amount independently verified as received (INR)', payment.amount) : undefined
    if (state === 'VERIFIED' && receivedAmount === null) return
    const reason = window.prompt(state === 'VERIFIED' ? 'Verification evidence/reference (do not enter bank credentials)' : 'Reason for rejecting the payment record')
    if (!reason?.trim()) return
    await request(`/api/platform/manual-payments/${payment.paymentId}/decision`, { state, version: payment.version, receivedAmount, reason })
    setPayments((await request('/api/platform/manual-payments')).payments)
  })
  return <>{section !== 'payments' && <section className="card"><div className="section-head"><h2>User token usage</h2><ActionButton className="button ghost" busy={action.pending === 'load'} disabled={Boolean(action.pending)} onClick={() => void load()}>Refresh usage</ActionButton></div>
    <p>Actual Bedrock Converse report-generation counters only. Input includes your prompt, system instructions, retrieved references and conversation context—not just typed words. Output is generated-report tokens. No customer charging.</p>
    <div className="usage-counters"><div><small>INPUT TOKENS</small><b>{usage?.totals.inputTokens.toLocaleString() ?? '—'}</b></div><div><small>OUTPUT TOKENS</small><b>{usage?.totals.outputTokens.toLocaleString() ?? '—'}</b></div><div><small>TOTAL TOKENS</small><b>{usage?.totals.totalTokens.toLocaleString() ?? '—'}</b></div></div>
    {Boolean(usage?.totals.unresolvedCalls) && <p role="status">Pending/incomplete/unknown calls: {usage?.totals.unresolvedCalls}. They are not counted as zero or included in known token totals.</p>}
    <table><thead><tr><th>User / employee</th><th>Workspace</th><th>Input</th><th>Output</th><th>Total</th><th>Calls / unresolved</th></tr></thead><tbody>{usage?.users.map(user => <tr key={`${user.tenantId}:${user.userId}`}><td>{user.email || user.userId}<small>{user.employeeCode}</small></td><td>{user.tenantId}</td><td>{user.inputTokens}</td><td>{user.outputTokens}</td><td>{user.totalTokens}</td><td>{user.calls} / {user.unresolvedCalls}</td></tr>)}</tbody></table>
    {usage?.nextCursor && <ActionButton className="button ghost" busy={action.pending === 'more'} disabled={Boolean(action.pending)} onClick={() => void action.run('more', async () => {
      const page = await request(`/api/platform/token-usage?after=${encodeURIComponent(usage.nextCursor!)}`)
      setUsage({ ...page, users: [...usage.users, ...page.users] })
    })}>More users</ActionButton>}
    {action.error && <p className="action-error" role="alert">{action.error}</p>}
  </section>}{section !== 'usage' && <section className="card"><div className="section-head"><h2>Manual payment records · INR</h2><ActionButton className="button ghost" busy={action.pending === 'load'} disabled={Boolean(action.pending)} onClick={() => void load()}>Refresh payments</ActionButton></div><p>Record and independently verify payments received outside this application. This does not initiate a transfer, activate AI, grant credits, create a subscription or issue a tax invoice. Latest 100 records.</p>
    {action.error && <p className="action-error" role="alert">{action.error}</p>}
    <form onSubmit={event => { event.preventDefault(); void action.run('payment', async () => {
      await request('/api/platform/manual-payments', form); setPayments((await request('/api/platform/manual-payments')).payments)
      setForm({ ...form, amount: '', reference: '', reason: '' })
    }) }}><div className="modal-grid" inert={Boolean(action.pending)}>
      <label>Customer workspace ID<input required maxLength={80} value={form.tenantId} onChange={event => setForm({ ...form, tenantId: event.target.value })} /></label>
      <label>Amount (INR)<input required inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" value={form.amount} onChange={event => setForm({ ...form, amount: event.target.value })} /></label>
      <label>Method<select value={form.method} onChange={event => setForm({ ...form, method: event.target.value })}>{['BANK','UPI','CASH','OTHER'].map(method => <option key={method}>{method}</option>)}</select></label>
      <label>Transaction / cash receipt reference<input required maxLength={160} value={form.reference} onChange={event => setForm({ ...form, reference: event.target.value })} /></label>
      <label>Reason<input required maxLength={1000} value={form.reason} onChange={event => setForm({ ...form, reason: event.target.value })} /></label>
    </div><ActionButton type="submit" className="button primary" busy={action.pending === 'payment'} disabled={Boolean(action.pending)}>Record manual payment</ActionButton></form>
    <table><thead><tr><th>Workspace</th><th>INR</th><th>Method/reference</th><th>Status</th><th>Decision</th></tr></thead><tbody>{payments.map(payment => <tr key={payment.paymentId}><td>{payment.tenantId}</td><td>{payment.amount}</td><td>{payment.method}<small>{payment.reference}</small></td><td>{payment.state}</td><td>{payment.state === 'RECORDED' && <><ActionButton className="button ghost" disabled={Boolean(action.pending)} busy={action.pending === payment.paymentId} onClick={() => void decide(payment, 'VERIFIED')}>Verify received</ActionButton><button className="button ghost danger" disabled={Boolean(action.pending)} onClick={() => void decide(payment, 'REJECTED')}>Reject record</button></>}</td></tr>)}</tbody></table>
  </section>}</>
}
