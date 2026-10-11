import { useEffect, useState } from 'react'
import { apiFetch } from './apiFetch'
import { ActionButton, useAction } from './Loading'

export default function SessionBadge() {
  const [identity, setIdentity] = useState<{ tenantId: string; role: string } | null>(null)
  const action = useAction()
  useEffect(() => { void apiFetch('/api/auth/me').then(r => r.json()).then(setIdentity) }, [])
  return <div className="user"><div className="avatar">DR</div><div>
    <b>{identity?.tenantId || 'Hospital account'}</b><small>{identity?.role}</small>
    <ActionButton className="button ghost" busy={!!action.pending} busyLabel="Signing out…" onClick={() => void action.run('logout', async () => {
      const response = await apiFetch('/api/auth/logout', { method: 'POST' })
      if (!response.ok) throw new Error('Could not sign out. Please try again.')
      window.dispatchEvent(new Event('radiology-session-expired'))
    })}>Sign out</ActionButton>
    {action.error && <small role="alert">{action.error}</small>}
  </div></div>
}
