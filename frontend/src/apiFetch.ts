import { requestReauthentication } from './saas/reauthBroker'
// Tokens remain in an HttpOnly same-origin cookie, never local/session storage.
let workspaceId = ''
export function setWorkspaceForRequests(value: string) { workspaceId = value }
export function getWorkspaceForRequests() { return workspaceId }
export async function apiFetch(input: string, options?: RequestInit) {
  const headers = new Headers(options?.headers)
  if (workspaceId && !headers.has('X-Workspace-ID')) headers.set('X-Workspace-ID', workspaceId)
  let response = await fetch(input, { ...options, headers, credentials: 'same-origin' })
  if (response.status === 409 && !input.endsWith('/auth/reauth')) {
    const result = await response.clone().json().catch(() => ({}))
    if (result.code === 'ReauthenticationRequired') {
      await requestReauthentication()
      // Retry once only. The backend rejected the first attempt before mutation.
      response = await fetch(input, { ...options, headers, credentials: 'same-origin' })
    }
  }
  if (response.status === 401 && !input.endsWith('/auth/login')) {
    workspaceId = ''
    window.dispatchEvent(new Event('radiology-session-expired'))
  }
  return response
}
