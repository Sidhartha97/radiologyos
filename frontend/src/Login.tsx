import { useRef, useState } from 'react'
import { apiFetch } from './apiFetch'
import { ActionButton } from './Loading'

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [challenge, setChallenge] = useState<{ challenge: string; session: string; username: string } | null>(null)
  const [answer, setAnswer] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const [error, setError] = useState('')
  return <div className="login"><form className="login-card" onSubmit={async event => {
    event.preventDefault()
    if (submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    try {
      if (challenge?.challenge === 'NEW_PASSWORD_REQUIRED' && answer !== confirmation) throw new Error('The new passwords do not match.')
      const response = await apiFetch('/api/auth/login', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(challenge ? { ...challenge, answer } : { username, password }) })
      const result = await response.json()
      if (!response.ok) {
        if (result.passwordChanged) { setChallenge(null); setPassword(''); setAnswer(''); setConfirmation('') }
        throw new Error(result.error || 'Sign-in failed.')
      }
      setPassword(''); setAnswer(''); setConfirmation('')
      if (result.challenge) setChallenge(result)
      else onLogin()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Sign-in failed.') }
    finally { submitting.current = false; setBusy(false) }
  }}>
    <div className="logo centered">Radiology<span>OS</span></div>
    <h1>{challenge?.challenge === 'NEW_PASSWORD_REQUIRED' ? 'Set your new password' : 'Sign in'}</h1>
    <p>{challenge?.challenge === 'NEW_PASSWORD_REQUIRED' ? 'Your temporary password was accepted. Choose a new password and use it for future sign-ins.' : 'Use your hospital Cognito account.'}</p>
    {!challenge ? <><label>Email<input type="email" required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)}/></label>
      <label>Password<input type="password" required autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)}/></label></>
      : <label>{challenge.challenge === 'NEW_PASSWORD_REQUIRED' ? 'New password' : 'Verification code'}
        <input required autoComplete={challenge.challenge === 'NEW_PASSWORD_REQUIRED' ? 'new-password' : 'one-time-code'} type={challenge.challenge === 'NEW_PASSWORD_REQUIRED' ? 'password' : 'text'} value={answer} onChange={e => setAnswer(e.target.value)}/></label>}
    {challenge?.challenge === 'NEW_PASSWORD_REQUIRED' && <><small>At least 12 characters, including uppercase, lowercase, a number and a symbol.</small><label>Confirm new password<input type="password" required autoComplete="new-password" value={confirmation} onChange={e => setConfirmation(e.target.value)}/></label></>}
    {error && <p role="alert">{error}</p>}
    <ActionButton type="submit" className="button primary full" busy={busy} busyLabel={challenge?.challenge === 'NEW_PASSWORD_REQUIRED' ? 'Setting password…' : challenge ? 'Verifying…' : 'Signing in…'}>Continue</ActionButton>
    {challenge && <button type="button" className="button ghost full" disabled={busy} onClick={() => { setChallenge(null); setAnswer(''); setConfirmation(''); setPassword(''); setError('') }}>Start sign-in again</button>}
  </form></div>
}
