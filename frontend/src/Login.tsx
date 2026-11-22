import { useEffect, useRef, useState } from 'react'
import { apiFetch } from './apiFetch'
import { ActionButton } from './Loading'
import PasswordRecovery from './saas/PasswordRecovery'

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [saas, setSaas] = useState(false)
  const [loginType, setLoginType] = useState<'email' | 'employee'>('email')
  const [hospitalId, setHospitalId] = useState('')
  const [employeeCode, setEmployeeCode] = useState('')
  const [challenge, setChallenge] = useState<{ challenge: string; session?: string; username?: string; challengeId?: string; setupSecret?: string } | null>(null)
  const [answer, setAnswer] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const [error, setError] = useState('')
  const [recovering, setRecovering] = useState(false)
  useEffect(() => { void apiFetch('/api/auth/config').then(response => response.json()).then(result => setSaas(Boolean(result.saas))).catch(() => {}) }, [])
  if (recovering) return <PasswordRecovery onBack={() => setRecovering(false)} />
  return <div className="login"><form className="login-card" onSubmit={async event => {
    event.preventDefault()
    if (submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    try {
      if (challenge?.challenge === 'NEW_PASSWORD_REQUIRED' && answer !== confirmation) throw new Error('The new passwords do not match.')
      const response = await apiFetch('/api/auth/login', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(challenge ? { challenge: challenge.challenge, challengeId: challenge.challengeId, ...(challenge.session ? {session: challenge.session, username: challenge.username} : {}), answer } : { username, password, loginType, hospitalId, employeeCode }) })
      const result = await response.json()
      if (!response.ok) {
        if (result.passwordChanged) { setChallenge(null); setPassword(''); setAnswer(''); setConfirmation('') }
        if (challenge?.challengeId) { setChallenge(null); setPassword(''); setAnswer(''); setConfirmation('') }
        throw new Error(result.error || 'Sign-in failed.')
      }
      setPassword(''); setAnswer(''); setConfirmation('')
      if (result.challenge) setChallenge(result)
      else onLogin()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Sign-in failed.') }
    finally { submitting.current = false; setBusy(false) }
  }}>
    <div className="logo centered">Radiology<span>OS</span></div>
    <h1>{challenge?.challenge === 'NEW_PASSWORD_REQUIRED' ? 'Set your new password' : challenge?.challenge === 'MFA_SETUP' ? 'Set up your authenticator' : 'Sign in'}</h1>
    {challenge?.challenge === 'MFA_SETUP' && <div className="admin-notice"><p>Add a time-based account in your authenticator using this setup key, then enter its six-digit code. Keep this key private; it is not saved in browser storage.</p><code style={{overflowWrap:'anywhere'}}>{challenge.setupSecret}</code></div>}
    <p>{challenge?.challenge === 'NEW_PASSWORD_REQUIRED' ? 'Your temporary password was accepted. Choose a new password and use it for future sign-ins.' : challenge?.challenge === 'VERIFY_EMAIL' ? 'Enter the verification code sent to your account email.' : saas ? 'Use your email or hospital employee code.' : 'Use your hospital Cognito account.'}</p>
    {saas && !challenge && <div className="login-modes"><button type="button" disabled={busy} className={`button ${loginType === 'email' ? 'primary' : 'ghost'}`} onClick={() => { setLoginType('email'); setError('') }}>Email</button><button type="button" disabled={busy} className={`button ${loginType === 'employee' ? 'primary' : 'ghost'}`} onClick={() => { setLoginType('employee'); setError('') }}>Hospital employee</button></div>}
    {!challenge ? <>{loginType === 'employee' && saas ? <><label>Hospital ID<input required value={hospitalId} onChange={e => setHospitalId(e.target.value)} maxLength={80}/></label><label>Employee code<input required autoComplete="username" value={employeeCode} onChange={e => setEmployeeCode(e.target.value)} maxLength={40}/></label></> : <label>Email<input type="email" required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)}/></label>}
      <label>Password<input type="password" required autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)}/></label></>
      : <label>{challenge.challenge === 'NEW_PASSWORD_REQUIRED' ? 'New password' : 'Verification code'}
        <input required autoComplete={challenge.challenge === 'NEW_PASSWORD_REQUIRED' ? 'new-password' : 'one-time-code'} type={challenge.challenge === 'NEW_PASSWORD_REQUIRED' ? 'password' : 'text'} value={answer} onChange={e => setAnswer(e.target.value)}/></label>}
    {challenge?.challenge === 'NEW_PASSWORD_REQUIRED' && <><small>At least 12 characters, including uppercase, lowercase, a number and a symbol.</small><label>Confirm new password<input type="password" required autoComplete="new-password" value={confirmation} onChange={e => setConfirmation(e.target.value)}/></label></>}
    {error && <p role="alert">{error}</p>}
    <ActionButton type="submit" className="button primary full" busy={busy} busyLabel={challenge?.challenge === 'NEW_PASSWORD_REQUIRED' ? 'Setting password…' : challenge ? 'Verifying…' : 'Signing in…'}>Continue</ActionButton>
    {saas && !challenge && <button type="button" className="button ghost full" disabled={busy} onClick={() => { setPassword(''); setRecovering(true) }}>Forgot password?</button>}
    {challenge && <button type="button" className="button ghost full" disabled={busy} onClick={() => { setChallenge(null); setAnswer(''); setConfirmation(''); setPassword(''); setError('') }}>Start sign-in again</button>}
  </form></div>
}
