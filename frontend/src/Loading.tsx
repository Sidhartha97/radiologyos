import { useRef, useState } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { LoaderCircle } from 'lucide-react'
import './loading.css'

export function Spinner() {
  return <LoaderCircle className="loading-spinner" size={17} aria-hidden="true"/>
}

export function LoadingStatus({ children }: { children: ReactNode }) {
  return <div className="loading-status" role="status"><Spinner/><span>{children}</span></div>
}

export function ActionButton({ busy, busyLabel = 'Working…', children, disabled, ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean; busyLabel?: string }) {
  return <button {...props} disabled={disabled || busy} aria-busy={busy || undefined}>
    {busy ? <><Spinner/>{busyLabel}</> : children}
  </button>
}

// Ref protects even same-frame repeated clicks and keyboard submissions.
export function useAction() {
  const lock = useRef(false)
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const run = async (label: string, task: () => Promise<void>) => {
    if (lock.current) return
    lock.current = true; setPending(label); setError('')
    try { await task() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Action failed. Please try again.') }
    finally { lock.current = false; setPending('') }
  }
  return { pending, error, run, clearError: () => setError('') }
}
