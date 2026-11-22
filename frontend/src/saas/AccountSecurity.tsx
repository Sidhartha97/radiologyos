import { useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'

export default function AccountSecurity() {
  const [secret, setSecret] = useState(''); const [code, setCode] = useState(''); const [message, setMessage] = useState('')
  const action = useAction()
  const post = async (operation: string, payload = {}) => {
    const response = await apiFetch(`/api/auth/mfa/${operation}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || 'Account security action failed.')
    return result
  }
  return <section className="card"><h2>Account security</h2><p>Add an authenticator to your own login. This affects all your workspaces. Keep access to your authenticator; recovery requires a reviewed support process.</p>
    {!secret && !message && <ActionButton className="button ghost" busy={Boolean(action.pending)} onClick={() => void action.run('setup', async () => { const result = await post('setup'); setSecret(result.secret) })}>Set up authenticator</ActionButton>}
    {secret && <form onSubmit={event => { event.preventDefault(); void action.run('confirm', async () => { const result = await post('confirm', { code }); setSecret(''); setCode(''); setMessage(result.message) }) }}>
      <p>Add a time-based account in your authenticator using this private setup key. Do not share it.</p><code>{secret}</code>
      <label>Six-digit authenticator code<input required pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" inputMode="numeric" value={code} onChange={event => setCode(event.target.value)} /></label>
      <ActionButton type="submit" className="button primary" busy={Boolean(action.pending)}>Verify and enable</ActionButton><button type="button" className="button ghost" disabled={Boolean(action.pending)} onClick={() => { setSecret(''); setCode('') }}>Cancel</button>
    </form>}
    <hr/><h3>Application sessions</h3><p>Sign out every Radiology OS session for your account, including this browser. This does not sign you out of other Cognito applications.</p><ActionButton className="button ghost danger" busy={action.pending === 'sessions'} disabled={Boolean(action.pending)} onClick={()=>void action.run('sessions',async()=>{
      if(!window.confirm('Sign out all your Radiology OS sessions? Save any report changes first.'))return
      const response=await apiFetch('/api/auth/logout-all',{method:'POST'});const result=await response.json()
      if(!response.ok)throw new Error(result.error||'Could not revoke sessions.')
      window.dispatchEvent(new Event('radiology-session-expired'))
    })}>Sign out all application sessions</ActionButton>
    {message && <p role="status">{message}</p>}{action.error && <p className="action-error" role="alert">{action.error}</p>}
  </section>
}
