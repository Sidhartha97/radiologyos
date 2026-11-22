import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ActionButton } from '../Loading'
import { registerReauthentication } from './reauthBroker'

export default function Reauthentication() {
  const continuation = useRef<{ resolve: () => void; reject: (error: Error) => void } | null>(null)
  const [open, setOpen] = useState(false); const [answer, setAnswer] = useState('')
  const [challenge, setChallenge] = useState<{ challenge: string; challengeId: string; setupSecret?: string } | null>(null)
  const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null); const submitting = useRef(false)
  useEffect(() => registerReauthentication(() => new Promise<void>((resolve, reject) => {
    continuation.current = { resolve, reject }; setOpen(true); setError(''); setAnswer(''); setChallenge(null)
  })), [])
  useEffect(() => { if (open) input.current?.focus() }, [open, challenge])
  const cancel = () => {
    setOpen(false); setAnswer(''); setChallenge(null)
    continuation.current?.reject(new Error('Identity confirmation cancelled. Your changes have not been saved.')); continuation.current = null
  }
  if (!open) return null
  return createPortal(<div className="modal-backdrop" style={{ zIndex: 10000 }}><form className="modal creation-dialog" role="dialog" aria-modal="true" aria-label="Confirm your identity" onSubmit={async event => {
    event.preventDefault(); if (submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    try {
      const response = await fetch('/api/auth/reauth', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(challenge ? { challengeId: challenge.challengeId, answer } : { password: answer }) })
      setAnswer('')
      const result = await response.json()
      if (!response.ok) { setChallenge(null); throw new Error(result.error || 'Identity confirmation failed.') }
      if (result.challenge) setChallenge(result)
      else { setOpen(false); setChallenge(null); continuation.current?.resolve(); continuation.current = null }
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Identity confirmation failed.') }
    finally { submitting.current = false; setBusy(false) }
  }}><h2>Confirm your identity</h2><p>Your administrative sign-in confirmation expired. Confirm this same account to continue; the access settings you entered are preserved.</p>
    {challenge?.setupSecret && <p>Add this private key to your authenticator: <code style={{ overflowWrap: 'anywhere' }}>{challenge.setupSecret}</code></p>}
    <label>{challenge ? 'Verification code' : 'Current password'}<input ref={input} required disabled={busy} type={challenge ? 'text' : 'password'} autoComplete={challenge ? 'one-time-code' : 'current-password'} value={answer} onChange={event => setAnswer(event.target.value)} maxLength={256}/></label>
    {error && <p role="alert" className="action-error">{error}</p>}<div className="modal-actions"><button type="button" className="button ghost" disabled={busy} onClick={cancel}>Cancel</button><ActionButton type="submit" className="button primary" busy={busy} busyLabel="Verifying…">Confirm and continue</ActionButton></div>
  </form></div>, document.body)
}
