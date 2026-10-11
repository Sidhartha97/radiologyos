// Tokens remain in an HttpOnly same-origin cookie, never local/session storage.
export async function apiFetch(input: string, options?: RequestInit) {
  const response = await fetch(input, { ...options, credentials: 'same-origin' })
  if (response.status === 401 && !input.endsWith('/auth/login')) {
    window.dispatchEvent(new Event('radiology-session-expired'))
  }
  return response
}
