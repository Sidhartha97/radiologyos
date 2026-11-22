// Single in-memory confirmation request; no credential/session persistence.
let handler: (() => Promise<void>) | null = null
let pending: Promise<void> | null = null
export function registerReauthentication(value: () => Promise<void>) {
  handler = value
  return () => { if (handler === value) handler = null }
}
export function requestReauthentication() {
  if (!handler) return Promise.reject(new Error('Refresh the page to confirm your identity. Your changes have not been saved.'))
  if (!pending) pending = handler().finally(() => { pending = null })
  return pending
}
