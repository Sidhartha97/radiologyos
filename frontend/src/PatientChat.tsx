import { useEffect, useRef, useState } from 'react'
import { ArrowUp, LoaderCircle, Sparkles, Trash2, X } from 'lucide-react'
import './report-chat.css'
import { apiFetch } from './apiFetch'
import { ActionButton, LoadingStatus, Spinner, useAction } from './Loading'

type Message = { id: string; role: 'user' | 'assistant'; text: string; draft?: string; error?: boolean; createdAt: string; reportId?: string; modality?: string }
type Conversation = { messages: Message[]; pending: { id: string; startedAt: string } | null; expiresAt?: string }
type Props = {
  patientId: string; patientName: string; reportId: string | null; modality: string; open: boolean; editorBusy?: boolean
  currentReport: () => string; renderReport: (value: string) => string
  onInsert: (value: string, modality?: string) => void; onClose: () => void
}

export default function PatientChat(props: Props) {
  const [conversation, setConversation] = useState<Conversation>({ messages: [], pending: null })
  const [prompt, setPrompt] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [inserted, setInserted] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const clearing = useAction()
  const mounted = useRef(true)
  const submitting = useRef(false)
  const retry = useRef<{ body: string; id: string } | null>(null)
  const end = useRef<HTMLDivElement>(null)
  const busy = sending || Boolean(conversation.pending)
  const url = `/api/patient-chat/${encodeURIComponent(props.patientId)}`
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => { setInserted(null) }, [props.reportId, props.modality])
  useEffect(() => {
    let active = true
    let timer: number
    const controller = new AbortController()
    const load = async () => {
      try {
        const response = await apiFetch(url, { signal: controller.signal, cache: 'no-store' })
        const result = await response.json()
        if (!response.ok) throw new Error(result.error || 'Could not load patient chat.')
        if (!active) return
        setConversation(result); setLoading(false); setLoadError('')
        if (props.open || result.pending) timer = window.setTimeout(load, result.pending ? 2000 : 15000)
      } catch (cause) {
        if (!active) return
        setLoading(false); setLoadError(cause instanceof Error ? cause.message : String(cause))
        timer = window.setTimeout(load, 5000)
      }
    }
    void load()
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [url, props.open, refresh])
  useEffect(() => { if (props.open) end.current?.scrollIntoView({ block: 'nearest' }) }, [conversation.messages.length, busy, props.open])
  useEffect(() => {
    if (!busy) return
    const started = conversation.pending ? Date.parse(conversation.pending.startedAt) : Date.now()
    const update = () => setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)))
    update()
    const timer = window.setInterval(update, 1000)
    return () => window.clearInterval(timer)
  }, [busy, conversation.pending?.startedAt])

  const send = async () => {
    const instruction = prompt.trim()
    if (!instruction || busy || clearing.pending || props.editorBusy || submitting.current) return
    setError('')
    const current = props.currentReport()
    const body = JSON.stringify({ patientId: props.patientId, reportId: props.reportId, modality: props.modality,
      prompt: instruction, currentReport: current })
    const requestId = retry.current?.body === body ? retry.current.id : crypto.randomUUID()
    retry.current = { body, id: requestId }
    submitting.current = true; setSending(true); setElapsed(0); setInserted(null)
    try {
      let request = { ...JSON.parse(body), requestId }
      let response = await apiFetch('/api/rag/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request) })
      let result = await response.json()
      if (response.status === 409 && result.code === 'ModalityConflict') {
        if (!window.confirm(result.error + '\nUse ' + result.detected + ' for this request? Cancel to revise the prompt or dropdown.')) return
        request = { ...request, confirmedModality: result.detected }
        response = await apiFetch('/api/rag/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request) })
        result = await response.json()
      }
      if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`)
      if (!mounted.current) return
      setPrompt(''); retry.current = null
      setConversation(old => ({ ...old, pending: { id: requestId, startedAt: new Date().toISOString() } }))
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      submitting.current = false
      if (mounted.current) { setSending(false); setRefresh(value => value + 1) }
    }
  }
  const clear = async () => {
    if (clearing.pending || busy) return
    if (!window.confirm(`Clear chat history for ${props.patientName}? Saved reports will remain.`)) return
    await clearing.run('clear', async () => {
      const response = await apiFetch(url, { method: 'DELETE' })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'Could not clear chat.')
      if (mounted.current) { setConversation(result); setInserted(null); retry.current = null; setRefresh(value => value + 1) }
    })
  }

  return <aside className="report-chat" style={{ display: props.open ? undefined : 'none' }} aria-label="AI report assistant">
    <header className="chat-header"><span className="chat-brand"><Sparkles size={20}/></span>
      <div><h2>Report assistant</h2><span>{props.patientName} · {props.modality}</span></div>
      <button className="chat-icon" aria-label={clearing.pending ? 'Clearing conversation' : 'Clear patient conversation'} aria-busy={!!clearing.pending} title="Clear patient conversation" disabled={busy || loading || !!clearing.pending} onClick={() => void clear()}>{clearing.pending ? <Spinner/> : <Trash2 size={17}/>}</button>
      <button className="chat-icon" aria-label="Close assistant" onClick={props.onClose}><X size={20}/></button>
    </header>
    <div className="chat-safety">Patient chat is kept for 14 days after the last message. Review AI drafts before finalizing.</div>
    <div className="chat-thread" role="log" aria-label="Patient conversation" aria-live="polite" aria-relevant="additions">
      {loading && <LoadingStatus>Loading this patient’s conversation…</LoadingStatus>}
      {(error || loadError || clearing.error) && <div className="chat-message chat-error" role="alert">{error || loadError || clearing.error}<ActionButton className="chat-insert" busy={loading} busyLabel="Loading chat…" onClick={() => { setError(''); clearing.clearError(); setLoading(true); setRefresh(value => value + 1) }}>Reload chat</ActionButton></div>}
      {!loading && !conversation.messages.length && <div className="chat-empty"><Sparkles size={28}/><h3>Start with your findings</h3><p>Create a draft or ask for changes to the report in your editor. This conversation stays with {props.patientName}.</p><span>Follow-ups use the latest 5 messages and current report.</span></div>}
      {conversation.messages.map(message => {
        return <article key={message.id} className={`chat-message ${message.role}${message.error ? ' chat-error' : ''}`}>
          <div className="chat-author">{message.role === 'user' ? 'You' : 'Report assistant'} · {message.modality} · {new Date(message.createdAt).toLocaleString()}</div>
          <div className="chat-message-text">{message.text}</div>
          {message.draft && <><div className="chat-report" dangerouslySetInnerHTML={{ __html: props.renderReport(message.draft) }}/>
            <button className="chat-insert" disabled={busy || !!clearing.pending || props.editorBusy} onClick={() => { props.onInsert(message.draft!, message.modality); setInserted(message.id) }}>{inserted === message.id ? 'Inserted · insert again' : 'Insert into text editor'}</button></>}
        </article>
      })}
      {busy && <div className="chat-working" role="status"><LoaderCircle className="chat-spin" size={17}/><span>Generating draft · {elapsed}s</span><span className="chat-dots" aria-hidden="true"><i/><i/><i/></span></div>}
      {busy && elapsed >= 30 && <p className="chat-wait">Generation continues if you close this panel. You can return to this patient’s chat later.</p>}
      <div ref={end}/>
    </div>
    <form className="chat-composer" onSubmit={event => { event.preventDefault(); void send() }}>
      <textarea aria-label="Report request" placeholder="Describe your findings or ask for changes to the previous draft…" value={prompt} maxLength={6000} disabled={busy || !!clearing.pending || props.editorBusy}
        onChange={event => setPrompt(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }}/>
      <div className="chat-composer-footer"><small>Enter to send · Shift + Enter for a new line</small><button type="submit" aria-busy={busy} aria-label={busy ? 'Generating report' : 'Send request'} disabled={busy || !!clearing.pending || props.editorBusy || !prompt.trim()}>{busy ? <LoaderCircle className="chat-spin" size={19}/> : <ArrowUp size={20}/>}</button></div>
    </form>
  </aside>
}
