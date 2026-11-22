import { useState } from 'react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'

export default function PasswordRecovery({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState(''); const [code, setCode] = useState('')
  const [password, setPassword] = useState(''); const [confirmation, setConfirmation] = useState('')
  const [stage, setStage] = useState<'request' | 'confirm' | 'done'>('request')
  const [message, setMessage] = useState(''); const action = useAction()
  return <div className="login"><form className="login-card" onSubmit={event => {
    event.preventDefault(); void action.run('recovery', async () => {
      if (stage === 'confirm' && password !== confirmation) throw new Error('The new passwords do not match.')
      const response = await apiFetch(`/api/auth/password-reset${stage === 'confirm' ? '/confirm' : ''}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(stage === 'request' ? { email } : { email, code, password }) })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Password recovery failed.')
      setMessage(result.message); setPassword(''); setConfirmation(''); setStage(stage === 'request' ? 'confirm' : 'done')
    })
  }}><div className="logo centered">Radiology<span>OS</span></div><h1>Password recovery</h1>
    {message && <p role="status">{message}</p>}
    {stage !== 'done' && <><label>Account email<input type="email" required disabled={stage === 'confirm' || Boolean(action.pending)} value={email} onChange={event => setEmail(event.target.value)} /></label>
    {stage === 'confirm' && <><label>Recovery code<input required autoComplete="one-time-code" maxLength={64} value={code} onChange={event => setCode(event.target.value)} /></label><label>New password<input type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={password} onChange={event => setPassword(event.target.value)} /></label><label>Confirm new password<input type="password" autoComplete="new-password" required value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label></>}
    <ActionButton type="submit" className="button primary full" busy={Boolean(action.pending)} busyLabel="Processing…">{stage === 'request' ? 'Request recovery code' : 'Set new password'}</ActionButton></>}
    {action.error && <p role="alert">{action.error}</p>}
    <button type="button" className="button ghost full" disabled={Boolean(action.pending)} onClick={onBack}>Back to sign in</button>
  </form></div>
}
