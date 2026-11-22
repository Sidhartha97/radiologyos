import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlignCenter, AlignLeft, AlignRight, Bold, Check, Clipboard, FileText, History, Italic, LayoutDashboard, Link, List, ListOrdered, Menu, Plus, Printer, Redo2, Search, Settings, Sparkles, Strikethrough, Table2, Trash2, Underline, Undo2, Upload, Users, X } from 'lucide-react'
import type { Data, Patient, Report } from './types'
import HistoricalLibrary from './HistoricalLibrary'
import ReportChat from './PatientChat'
import Login from './Login'
import { apiFetch, setWorkspaceForRequests } from './apiFetch'
import SessionBadge from './SessionBadge'
import { ActionButton, LoadingStatus, useAction } from './Loading'
import WorkspaceHome from './saas/WorkspaceHome'
import type { Session, ActiveWorkspace } from './saas/types'
import { useEditorLease } from './saas/useEditorLease'
import AssignmentControls from './saas/AssignmentControls'
import ReviewHandoff from './saas/ReviewHandoff'
import { printSavedReport } from './saas/printSavedReport'

type View = 'dashboard' | 'patients' | 'reports' | 'templates' | 'modalities' | 'editor' | 'library'
const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await apiFetch(`/api/${path}`, options)
  const result = await response.json()
  if (!response.ok) throw new Error(result?.error || `Request failed (${response.status})`)
  return result as T
}

export default function App() {
  const [data, setData] = useState<Data | null>(null)
  const [view, setView] = useState<View>('dashboard')
  const [report, setReport] = useState<Report | null>(null)
  const [aiOpen, setAiOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [loggedIn, setLoggedIn] = useState(false)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [session, setSession] = useState<Session | null>(null)
  const [workspaceChooser, setWorkspaceChooser] = useState(false)
  const checkSession = async () => {
    const response = await apiFetch('/api/auth/me')
    if (!response.ok) { setLoggedIn(false); return }
    const value: Session = await response.json()
    setWorkspaceForRequests(value.workspace?.tenantId || '')
    setSession(value); setLoggedIn(true)
  }
  const clinicalAccess = !session?.saas || Boolean(session.workspace?.roles.some(role => ['radiologist', 'transcriptionist'].includes(role)))
  const fullClinicalAccess = !session?.saas || Boolean(session.workspace?.roles.includes('radiologist'))

  const reload = async () => {
    setRefreshing(true); setLoadError('')
    try { setData(await api<Data>('data')) }
    catch (cause) { setLoadError(cause instanceof Error ? cause.message : 'Could not load application data.'); throw cause }
    finally { setRefreshing(false) }
  }
  useEffect(() => {
    void checkSession().catch(() => setLoggedIn(false)).finally(() => setSessionLoading(false))
    const expired=() => { setLoggedIn(false); setSession(null); setWorkspaceForRequests(''); setData(null); setReport(null); setAiOpen(false) }
    window.addEventListener('radiology-session-expired', expired)
    return () => window.removeEventListener('radiology-session-expired', expired)
  }, [])
  useEffect(() => { if (loggedIn && clinicalAccess && !workspaceChooser) void reload().catch(e => setLoadError(String(e))) }, [loggedIn, session?.workspace?.tenantId, clinicalAccess, workspaceChooser])
  if (sessionLoading) return <div className="loading"><LoadingStatus>Checking session…</LoadingStatus></div>
  if (!loggedIn) return <Login onLogin={() => { setLoadError(''); void checkSession().catch(cause => setLoadError(String(cause))) }} />
  if (session?.saas && (workspaceChooser || !session.workspace || !clinicalAccess)) return <WorkspaceHome session={session} onSelect={async tenantId => {
    await api('session/workspace', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tenantId }) })
    setData(null); setReport(null); setAiOpen(false); setView('dashboard'); setWorkspaceChooser(false); await checkSession()
  }} onLogout={async () => { await api('auth/logout', { method: 'POST' }); window.dispatchEvent(new Event('radiology-session-expired')) }} />
  if (!data) return <div className="loading">{loadError ? <><p role="alert">{loadError}</p><ActionButton className="button primary" busy={refreshing} busyLabel="Loading…" onClick={() => void reload().catch(() => {})}>Retry loading</ActionButton></> : <LoadingStatus>Loading RadiologyOS…</LoadingStatus>}</div>

  const openNewReport = (patientId: string) => {
    const draft: Report = { id: null, patientId, modality: data.modalities[0] || 'CT', body: '', status: 'Draft' }
    if (!session?.saas) { setReport(draft); setView('editor'); return }
    setRefreshing(true); setLoadError('')
    void api<Report>('reports', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) }).then(result => { setReport(result); setView('editor'); return reload() }).catch(cause => setLoadError(String(cause))).finally(() => setRefreshing(false))
  }
  const openReport = (r: Report) => { setReport(r); setView('editor') }
  const patient = report ? data.patients.find(p => p.id === report.patientId) : undefined
  const nav = (next: View) => {
    if (view === 'editor' && next !== 'editor' && !window.confirm('Leave this report? Any unsaved changes will be lost.')) return
    setView(next); setAiOpen(false)
  }

  return <div className="app-shell">
    {refreshing && <div className="app-refresh"><LoadingStatus>Refreshing workspace…</LoadingStatus></div>}
    <aside className="sidebar"><div className="logo">Radiology<span>OS</span></div><div className="nav-label">WORKSPACE</div>
      <Nav active={view} value="dashboard" icon={<LayoutDashboard />} label="Dashboard" onClick={nav}/><Nav active={view} value="patients" icon={<Users />} label="Patients" onClick={nav}/><Nav active={view} value="reports" icon={<FileText />} label="Reports" onClick={nav}/>{fullClinicalAccess && <><Nav active={view} value="templates" icon={<FileText />} label="Templates" onClick={nav}/><Nav active={view} value="library" icon={<Upload />} label="Historical library" onClick={nav}/><div className="nav-label">CONFIGURATION</div><Nav active={view} value="modalities" icon={<Settings />} label="Modalities" onClick={nav}/></>}<SessionBadge/>
    </aside>
    <main className="main"><header className="topbar"><div><h1>{view === 'dashboard' ? session?.saas ? 'Dashboard' : 'Good morning, Dr. Menon' : view === 'library' ? 'Historical reports' : view[0].toUpperCase() + view.slice(1)}</h1><p>RadiologyOS · {session?.saas ? session.workspace?.name : 'St. Mary’s Diagnostic Centre'}</p></div><div className="top-actions"><input value={search} onChange={e => setSearch(e.target.value)} placeholder="⌕  Search anything" />{view === 'patients' && fullClinicalAccess && <button className="button primary" onClick={() => nav('library')}><Upload size={16}/> Upload old report</button>}</div></header>
      {session?.saas && <div className="workspace-toolbar"><b>{session.workspace?.name}</b><button className="button ghost" onClick={() => { if (!report || window.confirm('Leave this report? Unsaved changes will be lost.')) { setReport(null); setAiOpen(false); setWorkspaceChooser(true) } }}>Change workspace</button></div>}
      {loadError && <p className="action-error" role="alert">{loadError}</p>}
      {view === 'library' && <HistoricalLibrary/>}
      {view === 'dashboard' && <Dashboard data={data} onOpen={openReport} onView={() => nav('reports')} />}
      {view === 'patients' && <Patients data={data} search={search} onNew={openNewReport} onReload={reload} canManage={fullClinicalAccess} />}
      {view === 'reports' && <Reports data={data} search={search} onOpen={openReport} />}
      {view === 'templates' && <Templates data={data} onReload={reload} />}
      {view === 'modalities' && <Modalities data={data} onReload={reload} />}
      {view === 'editor' && report && patient && <Editor key={`${patient.id}:${report.id || 'new'}`} data={data} report={report} patient={patient} aiOpen={aiOpen} setAiOpen={setAiOpen} onReport={setReport} onReload={reload} onOpen={openReport} onBack={() => { setReport(null); setAiOpen(false); setView('reports') }} workspace={session?.workspace} />}
    </main>
  </div>
}


function Nav({ active, value, icon, label, onClick }: { active: View; value: View; icon: React.ReactNode; label: string; onClick: (v: View) => void }) { return <button className={`nav ${active === value ? 'active' : ''}`} onClick={() => onClick(value)}>{icon}<span>{label}</span></button> }
function Dashboard({ data, onOpen, onView }: { data: Data; onOpen: (r: Report) => void; onView: () => void }) { return <><div className="stats">{[['Patients', data.patients.length, <Users/>], ['Reports today', data.reports.length, <FileText/>], ['Draft reports', data.reports.filter(r => r.status === 'Draft').length, <History/>], ['Finalized', data.reports.filter(r => r.status === 'Final').length, <Check/>]].map(([label, value, icon]) => <div className="stat card" key={String(label)}><div><small>{label}</small><strong>{value as number}</strong><em>Updated just now</em></div><span className="stat-icon">{icon}</span></div>)}</div><div className="two-col"><section className="card"><div className="section-head"><h2>Recent reports</h2><button className="button ghost" onClick={onView}>View all</button></div><ReportTable data={data} reports={data.reports.slice().reverse().slice(0, 5)} onOpen={onOpen}/></section><section className="card"><h2>Department activity</h2><div className="activity"><Activity size={18}/><div><b>Daily workflow ready</b><small>{data.reports.length} reports in the system</small></div></div><div className="activity"><Sparkles size={18}/><div><b>AI drafting assistant ready</b><small>Review every generated draft before finalization</small></div></div></section></div></> }
function Patients({ data, search, onNew, onReload, canManage = true }: { data: Data; search: string; onNew: (id: string) => void; onReload: () => Promise<void>; canManage?: boolean }) {
  const rows = data.patients.filter(p => `${p.name} ${p.mrn}`.toLowerCase().includes(search.toLowerCase()))
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const action = useAction()
  const [form, setForm] = useState({ name: '', mrn: '', dob: '', sex: '', phone: '' })
  const update = (key: keyof typeof form, value: string) => setForm(current => ({ ...current, [key]: value }))
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!form.name.trim()) { setError('Patient name is required'); return }
    setError('')
    await action.run('patient', async () => {
      await api('patients', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ ...form, name: form.name.trim(), mrn: form.mrn.trim() || `MRN-${Date.now().toString().slice(-6)}` }) })
      setForm({ name: '', mrn: '', dob: '', sex: '', phone: '' }); setOpen(false); await onReload()
    })
  }
  return <section className="card"><div className="section-head"><h2>Patient registry <small>{rows.length} patients</small></h2>{canManage && <button className="button primary" onClick={() => { setError(''); setOpen(true) }}><Plus size={16}/> Register patient</button>}</div><table><thead><tr><th>Patient</th><th>MRN</th><th>DOB / Sex</th><th>Phone</th><th>Actions</th></tr></thead><tbody>{rows.map(p => <tr key={p.id}><td><b>{p.name}</b></td><td>{p.mrn}</td><td>{p.dob} · {p.sex}</td><td>{p.phone}</td><td><button className="button ghost" onClick={() => onNew(p.id)}>+ Report</button></td></tr>)}</tbody></table>
    {open && <div className="modal-backdrop" onMouseDown={event => { if (!action.pending && event.target === event.currentTarget) setOpen(false) }}><form className="modal card" aria-busy={!!action.pending} onSubmit={submit} onMouseDown={event => event.stopPropagation()}><div className="section-head"><h2>Register patient</h2><button type="button" disabled={!!action.pending} className="icon-button" onClick={() => setOpen(false)}><X size={18}/></button></div><div className="modal-grid" inert={!!action.pending}><label>Full name<input autoFocus value={form.name} onChange={event => update('name', event.target.value)} placeholder="Patient name" /></label><label>MRN<input value={form.mrn} onChange={event => update('mrn', event.target.value)} placeholder="Auto-generated if blank" /></label><label>Date of birth<input type="date" value={form.dob} onChange={event => update('dob', event.target.value)} /></label><label>Sex<select value={form.sex} onChange={event => update('sex', event.target.value)}><option value="">Select</option><option>M</option><option>F</option><option>O</option></select></label><label>Phone<input value={form.phone} onChange={event => update('phone', event.target.value)} placeholder="Phone number" /></label></div>{(error || action.error) && <p role="alert" className="form-error">{error || action.error}</p>}<div className="modal-actions"><button type="button" disabled={!!action.pending} className="button ghost" onClick={() => setOpen(false)}>Cancel</button><ActionButton type="submit" className="button primary" busy={!!action.pending} busyLabel="Saving patient…">Save patient</ActionButton></div></form></div>}
  </section>
}
function Reports({ data, search, onOpen }: { data: Data; search: string; onOpen: (r: Report) => void }) {
  const [status, setStatus] = useState(''); const [modality, setModality] = useState('')
  const reports = data.reports.filter(r => {
    const p = data.patients.find(x => x.id === r.patientId)
    return (!status || (r.workflowStatus || r.status) === status) && (!modality || r.modality === modality) && `${p?.name} ${p?.mrn} ${r.modality}`.toLowerCase().includes(search.toLowerCase())
  }).slice().sort((a,b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))
  const states = data.saas ? ['DRAFT','IN_TRANSCRIPTION','READY_FOR_REVIEW','UNDER_REVIEW','RETURNED_FOR_CORRECTION','FINALIZED','AMENDED','CANCELLED'] : ['Draft','Final']
  return <section className="card"><div className="section-head"><h2>Report worklist <small>{reports.length} matching reports</small></h2></div><div className="row">
    <label>Status <select aria-label="Report workflow filter" value={status} onChange={event => setStatus(event.target.value)}><option value="">All statuses</option>{states.map(value => <option key={value} value={value}>{value.replace(/_/g,' ')}</option>)}</select></label>
    <label>Modality <select aria-label="Report modality filter" value={modality} onChange={event => setModality(event.target.value)}><option value="">All modalities</option>{data.modalities.map(value => <option key={value}>{value}</option>)}</select></label>
    {(status || modality) && <button className="button ghost" onClick={() => { setStatus(''); setModality('') }}>Clear filters</button>}
  </div><ReportTable data={data} reports={reports} onOpen={onOpen}/>{!reports.length && <p>No reports match these filters. Only records you are authorized to view are included.</p>}</section>
}
function ReportTable({ data, reports, onOpen }: { data: Data; reports: Report[]; onOpen: (r: Report) => void }) { return <table><thead><tr><th>Patient</th><th>Modality</th><th>Updated</th><th>Status</th><th></th></tr></thead><tbody>{reports.map(r => { const p = data.patients.find(x => x.id === r.patientId); return <tr key={r.id}><td><b>{p?.name}</b><small>{p?.mrn}</small></td><td><span className="badge">{r.modality}</span></td><td>{r.updatedAt}</td><td><span className={`badge ${r.status === 'Draft' ? 'draft' : ''}`}>{r.workflowStatus?.replace(/_/g, ' ') || r.status}</span></td><td><button className="button ghost" onClick={() => onOpen(r)}>Open</button></td></tr> })}</tbody></table> }
function reportTextToHtml(value: string) {
  const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
  const inline = (text: string) => escape(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/__(.+?)__/g, '<strong>$1</strong>')
  let html = '', list: 'ul' | 'ol' | '' = '', section = ''
  const closeList = () => { if (list) { html += `</${list}>`; list = '' } }
  for (const raw of value.replace(/\r/g, '').split('\n')) {
    if (/^\[PRELIMINARY REPORT/i.test(raw.trim())) break
    // Models sometimes return Markdown headings even though the prompt asks
    // for plain headings. Normalize hashes and bold wrappers before parsing.
    const line = raw.trim().replace(/^#{1,6}\s*/, '').replace(/^\*\*(.+)\*\*$/, '$1').replace(/\s+#+$/, '').trim()
    // Markdown blank lines delimit paragraphs; they are not empty editor rows.
    if (!line) continue
    const heading = line.match(/^(STUDY|CLINICAL HISTORY|TECHNIQUE|COMPARISON|FINDINGS|IMPRESSION)\s*:?$/i)
    if (heading) { closeList(); section = heading[1].toUpperCase(); html += `<h3>${escape(section)}</h3>`; continue }
    const bullet = line.match(/^(?:•|[-*])\s+(.*)$/)
    const numbered = line.match(/^\d+[.)]\s+(.*)$/)
    if (bullet || numbered) { const next = bullet ? 'ul' : 'ol'; if (list !== next) { closeList(); list = next; html += `<${list}>` } const item = inline((bullet || numbered)![1]); html += `<li>${section === 'IMPRESSION' ? `<strong>${item.replace(/^<strong>|<\/strong>$/g, '')}</strong>` : item}</li>`; continue }
    closeList(); html += section === 'STUDY' ? `<p style="margin:0;line-height:1.5"><strong>${inline(line)}</strong></p>` : `<p style="margin:0;line-height:1.5">${inline(line)}</p>`
  }
  closeList(); return html
}
function Editor({ data, report, patient, aiOpen, setAiOpen, onReport, onReload, onOpen, onBack, workspace }: { data: Data; report: Report; patient: Patient; aiOpen: boolean; setAiOpen: (v: boolean) => void; onReport: (r: Report) => void; onReload: () => Promise<void>; onOpen: (r: Report) => void; onBack: () => void; workspace?: ActiveWorkspace | null }) {
  const editorRef = useRef<HTMLDivElement>(null)
  const dirty = useRef(false)
  const [body, setBody] = useState(report.body)
  const [findOpen, setFindOpen] = useState(false)
  const [findText, setFindText] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [fullscreen, setFullscreen] = useState(false)
  const [savedAt, setSavedAt] = useState(report.id ? 'Saved report' : 'Not saved')
  const [fontSize, setFontSize] = useState('3')
  const [templateId, setTemplateId] = useState('')
  const action = useAction()
  const [deleted, setDeleted] = useState(false)
  const [handoffBusy, setHandoffBusy] = useState(false)
  const [previousAiBody, setPreviousAiBody] = useState<string | null>(null)
  const previousAiModality = useRef(report.modality)
  const modalityTemplates = data.templates.filter(t => t.modality === report.modality)
  const final = Boolean(data.saas && ['FINALIZED', 'AMENDED', 'CANCELLED'].includes(report.workflowStatus || ''))
  const submitted = Boolean(data.saas && ['READY_FOR_REVIEW', 'UNDER_REVIEW'].includes(report.workflowStatus || '') && !workspace?.roles.includes('radiologist'))
  const access = useEditorLease(report.id, report.version, Boolean(data.saas && workspace?.kind === 'HOSPITAL'), !final && !submitted)
  const readOnly = handoffBusy || Boolean(data.saas && !access.canEdit)
  const canSign = !data.saas || Boolean(workspace?.canSign)

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty.current) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

  useEffect(() => {
    setBody(report.body)
    setPreviousAiBody(null)
    if (editorRef.current) {
      if (/<[a-z][\s\S]*>/i.test(report.body || '')) editorRef.current.innerHTML = report.body || ''
      else editorRef.current.innerText = report.body || ''
    }
  }, [report.id, report.patientId])

  const updateBody = () => { const html = editorRef.current?.innerHTML || ''; setBody(html); dirty.current = true }
  const command = (name: string, value?: string) => { editorRef.current?.focus(); document.execCommand(name, false, value); updateBody() }
  const save = (status: string) => action.run(status, async () => {
    if (deleted || readOnly) return
    updateBody()
    const nextBody = editorRef.current?.innerHTML || body
    const r = await api<Report>(`reports${report.id ? `/${report.id}` : ''}`, {method: report.id ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({...report, body: nextBody, status, ...(data.saas ? { version: report.version, lease: access.lease } : {})})})
    setSavedAt(new Date().toLocaleTimeString()); dirty.current = false; onReport(r)
    if (data.saas && status === 'Final') await access.release()
    // Saving succeeded even if a subsequent list refresh fails: do not invite a duplicate save.
    await onReload().catch(() => {})
  })
  const deleteReport = () => {
    if (!report.id || deleted || readOnly || !window.confirm(data.saas ? 'Cancel this draft report? Its history will be retained.' : 'Delete this report? This cannot be undone.')) return
    const reason = data.saas ? window.prompt('Reason for cancellation') : null
    if (data.saas && !reason?.trim()) return
    void action.run('delete', async () => {
      await api(`reports/${report.id}`, {method:'DELETE', ...(data.saas ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: report.version, lease: access.lease, reason }) } : {})})
      setDeleted(true); setAiOpen(false); setSavedAt('Report deleted')
      await onReload().catch(() => {})
    })
  }
  const insertAi = (text: string, modality?: string) => {
    if (readOnly) return
    dirty.current = true
    setPreviousAiBody(editorRef.current?.innerHTML || body)
    previousAiModality.current = report.modality
    const html = reportTextToHtml(text)
    if (editorRef.current) editorRef.current.innerHTML = html
    setBody(html)
    if (modality && data.modalities.includes(modality) && modality !== report.modality) {
      onReport({...report, modality}); setTemplateId('')
    }
    setSavedAt('AI draft inserted · Not saved')
  }
  const undoAi = () => {
    if (previousAiBody === null) return
    if (editorRef.current) editorRef.current.innerHTML = previousAiBody
    setBody(previousAiBody); setPreviousAiBody(null)
    if (previousAiModality.current !== report.modality) {
      onReport({...report, modality: previousAiModality.current}); setTemplateId('')
    }
    setSavedAt('AI change undone · Not saved')
  }
  const loadTemplate = (selectedId: string) => { setTemplateId(selectedId); const template = data.templates.find(t => String(t.id) === selectedId); if (!template || !editorRef.current) return; editorRef.current.innerHTML = template.body; updateBody() }
  const replaceAll = () => { if (!findText || !editorRef.current) return; editorRef.current.innerHTML = editorRef.current.innerHTML.split(findText).join(replaceText); updateBody() }
  const copyReport = () => action.run('copy', async () => {
    if (!navigator.clipboard) throw new Error('Clipboard access is unavailable in this browser.')
    await navigator.clipboard.writeText(editorRef.current?.innerText || ''); setSavedAt('Copied')
  })
  const keyDown = (event: React.KeyboardEvent<HTMLDivElement>) => { if (event.ctrlKey && event.key.toLowerCase() === 's') { event.preventDefault(); if (!readOnly) void save('Draft') } }
  const reloadSaved = () => action.run('reload', async () => {
    if (!window.confirm('Reload the saved report? Unsaved changes in this editor will be lost.')) return
    await access.release()
    const fresh = await api<Data>('data')
    const saved = fresh.reports.find(item => item.id === report.id)
    if (!saved) throw new Error('This report is no longer available.')
    onReport(saved); setBody(saved.body)
    if (editorRef.current) {
      if (/<[a-z][\s\S]*>/i.test(saved.body)) editorRef.current.innerHTML = saved.body
      else editorRef.current.innerText = saved.body
    }
    dirty.current = false; setSavedAt('Saved report reloaded'); await onReload().catch(() => {})
  })
  const transition = (name: 'submit' | 'review' | 'return') => action.run(name, async () => {
    const reason = name === 'return' ? window.prompt('Reason for returning this report') : null
    if (name === 'return' && !reason?.trim()) return
    const saved = await api<Report>(`reports/${report.id}/${name}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: report.version, lease: access.lease, body: editorRef.current?.innerHTML || body, reason }) })
    dirty.current = false; onReport(saved); setSavedAt(saved.workflowStatus || 'Updated')
    if (name !== 'review') await access.release()
    await onReload().catch(() => {})
  })
  const amend = () => action.run('amend', async () => {
    const reason = window.prompt('Reason for amending this signed report')
    if (!reason?.trim()) return
    const draft = await api<Report>(`reports/${report.id}/amend`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version: report.version, reason }) })
    await onReload().catch(() => {}); onOpen(draft)
  })
  const wordCount = (editorRef.current?.innerText || body.replace(/<[^>]+>/g, ' ')).trim().split(/\s+/).filter(Boolean).length
  return <div className={fullscreen ? 'editor-screen fullscreen' : 'editor-screen'}>
    <div className="workspace-toolbar"><ActionButton className="button ghost" disabled={Boolean(action.pending) || handoffBusy} busy={action.pending === 'back'} onClick={() => void action.run('back', async () => {
      if (dirty.current && !window.confirm('Leave the editor? Unsaved changes will be lost.')) return
      await access.release(); onBack()
    })}>← Back to reports</ActionButton>{data.saas && report.id && <ActionButton className="button ghost" busy={action.pending === 'print-saved'} disabled={Boolean(action.pending)} onClick={() => void action.run('print-saved', () => printSavedReport(report.id!, patient))}>Print saved report</ActionButton>}</div>
    {data.saas && <div className="lease-banner" role={access.error ? 'alert' : 'status'}><span>{access.error || (final ? 'This report is read-only.' : submitted ? 'Submitted for radiologist review.' : access.canEdit ? `Editing · revision ${report.version}` : access.busy ? 'Reserving this report for editing…' : 'Read-only. Editing access is required.')}</span>
      {!final && !submitted && !access.lease && workspace?.kind === 'HOSPITAL' && <ActionButton className="button primary" disabled={!!action.pending} busy={access.busy} busyLabel="Reserving report…" onClick={() => void access.acquire()}>Retry editing access</ActionButton>}
      <ActionButton className="button ghost" busy={action.pending === 'reload'} disabled={!!action.pending} onClick={() => void reloadSaved()}>Reload saved report</ActionButton>
      {final && report.workflowStatus !== 'CANCELLED' && workspace?.roles.includes('radiologist') && <ActionButton className="button primary" busy={action.pending === 'amend'} disabled={!!action.pending} onClick={() => void amend()}>Create amendment</ActionButton>}
    </div>}
    <div className="patient-strip"><div><b>{patient.name}</b><small> · {patient.mrn} · {patient.dob} · {patient.sex}</small></div><span className="badge">{report.modality}</span></div><div className="editor-layout"><aside className="history card"><h2>Previous reports</h2>{data.reports.filter(r => r.patientId === patient.id).map(r => <button key={r.id} disabled={!!action.pending || handoffBusy} onClick={() => {
      if (r.id === report.id) return
      if (dirty.current && !window.confirm('Open another report? Unsaved changes will be lost.')) return
      void action.run('open-report', async () => { await access.release(); onOpen(r) })
    }}><b>{r.modality} report</b><small>{r.updatedAt} · {r.workflowStatus?.replace(/_/g, ' ') || r.status}</small></button>)}</aside><section className="editor-pane" aria-busy={!!action.pending || handoffBusy} data-busy={!!action.pending || handoffBusy}><div className="toolbar" inert={!!action.pending || deleted || readOnly}>
    <button title="Undo" onClick={() => command('undo')}><Undo2/></button><button title="Redo" onClick={() => command('redo')}><Redo2/></button><span className="toolbar-divider"/>
    <select title="Style" defaultValue="p" onChange={e => command('formatBlock', e.target.value)}><option value="p">Normal</option><option value="h1">Heading 1</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option><option value="blockquote">Quote</option></select>
    <select title="Font size" value={fontSize} onChange={e => { setFontSize(e.target.value); command('fontSize', e.target.value) }}><option value="2">Small</option><option value="3">Normal</option><option value="4">Large</option><option value="5">Huge</option></select><span className="toolbar-divider"/>
    <button title="Bold" onClick={() => command('bold')}><Bold/></button><button title="Italic" onClick={() => command('italic')}><Italic/></button><button title="Underline" onClick={() => command('underline')}><Underline/></button><button title="Strikethrough" onClick={() => command('strikeThrough')}><Strikethrough/></button>
    <label className="color-tool" title="Text color">A<input type="color" onChange={e => command('foreColor', e.target.value)}/></label><label className="color-tool highlight" title="Highlight">▰<input type="color" defaultValue="#fff19a" onChange={e => command('hiliteColor', e.target.value)}/></label><span className="toolbar-divider"/>
    <button title="Align left" onClick={() => command('justifyLeft')}><AlignLeft/></button><button title="Align center" onClick={() => command('justifyCenter')}><AlignCenter/></button><button title="Align right" onClick={() => command('justifyRight')}><AlignRight/></button><button title="Bulleted list" onClick={() => command('insertUnorderedList')}><List/></button><button title="Numbered list" onClick={() => command('insertOrderedList')}><ListOrdered/></button>
    <button title="Insert link" onClick={() => { const url = window.prompt('Enter URL'); if (url) command('createLink', url) }}><Link/></button><button title="Insert table" onClick={() => command('insertHTML', '<table><tbody><tr><td>Header</td><td>Value</td></tr><tr><td></td><td></td></tr></tbody></table>')}><Table2/></button><button title="Clear formatting" onClick={() => command('removeFormat')}>Tx</button>
    <span className="toolbar-spacer"/><button title="Find and replace" onClick={() => setFindOpen(!findOpen)}><Search/></button><ActionButton title="Copy report" busy={action.pending === 'copy'} busyLabel="Copying…" onClick={copyReport}><Clipboard/></ActionButton><button title="Print report" onClick={() => window.print()}><Printer/></button><button title="Fullscreen" onClick={() => setFullscreen(!fullscreen)}>⛶</button>
  </div>{findOpen && <div className="find-bar" inert={!!action.pending || deleted || readOnly}><input value={findText} onChange={e => setFindText(e.target.value)} placeholder="Find"/><input value={replaceText} onChange={e => setReplaceText(e.target.value)} placeholder="Replace with"/><button className="button ghost" onClick={replaceAll}>Replace all</button></div>}
    <div className="template-bar" inert={!!action.pending || deleted || readOnly}><select value={report.modality} onChange={e => { dirty.current = true; onReport({...report, modality:e.target.value}); setTemplateId('') }}>{data.modalities.map(m => <option key={m}>{m}</option>)}</select><select value={templateId} onChange={e => loadTemplate(e.target.value)}><option value="">Choose {report.modality} template</option>{modalityTemplates.map(t => <option key={t.id} value={String(t.id)}>{t.name}</option>)}</select></div>
    <div ref={editorRef} className="editor" contentEditable={!action.pending && !deleted && !readOnly} suppressContentEditableWarning onInput={updateBody} onKeyDown={keyDown} role="textbox" aria-label="Radiology report editor"/>
    {action.error && <p className="action-error" role="alert">{action.error}</p>}
    <div className="editor-status"><span>{wordCount} words · {savedAt}</span>{previousAiBody !== null && <button className="chat-undo" disabled={!!action.pending || deleted || readOnly} onClick={undoAi}>Undo AI replacement</button>}<span>Ctrl+S to save draft</span></div>
    {data.saas && !final && workspace?.kind === 'HOSPITAL' && workspace.roles.includes('radiologist') && <AssignmentControls report={report} lease={access.lease} disabled={readOnly || Boolean(action.pending)} onSaved={onReport} />}
    {data.saas && ['DRAFT', 'IN_TRANSCRIPTION', 'RETURNED_FOR_CORRECTION'].includes(report.workflowStatus || '') && workspace?.kind === 'HOSPITAL' && <ReviewHandoff report={report} lease={access.lease} disabled={readOnly || Boolean(action.pending)} body={() => editorRef.current?.innerHTML || body} onBusy={setHandoffBusy} onSaved={async saved => { dirty.current = false; setSavedAt('Saved and sent for review'); onReport(saved); await access.release(); await onReload().catch(() => {}) }} />}
    {data.saas && !final && <div className="editor-actions"><ActionButton className="button ghost" disabled={readOnly || !!action.pending} busy={action.pending === 'submit'} onClick={() => void transition('submit')}>Submit for review</ActionButton>{workspace?.roles.includes('radiologist') && <><ActionButton className="button ghost" disabled={readOnly || !!action.pending} busy={action.pending === 'review'} onClick={() => void transition('review')}>Begin review</ActionButton><ActionButton className="button ghost" disabled={readOnly || !!action.pending} busy={action.pending === 'return'} onClick={() => void transition('return')}>Return for correction</ActionButton></>}</div>}
    <div className="editor-actions"><ActionButton className="button ghost danger" disabled={!!action.pending || !report.id || deleted || readOnly || Boolean(data.saas && !workspace?.roles.includes('radiologist'))} busy={action.pending === 'delete'} busyLabel="Deleting…" onClick={deleteReport}><Trash2 size={15}/> {data.saas ? 'Cancel report' : 'Delete'}</ActionButton><div><ActionButton className="button ghost" disabled={!!action.pending || deleted || readOnly} busy={action.pending === 'Draft'} busyLabel="Saving draft…" onClick={() => void save('Draft')}>Save draft</ActionButton><ActionButton className="button primary" disabled={!!action.pending || deleted || readOnly || !canSign} busy={action.pending === 'Final'} busyLabel="Finalizing…" onClick={() => void save('Final')}><Check size={15}/> Save & finalize</ActionButton></div></div></section></div>
    {(!data.saas || (workspace?.canUseAi && workspace.roles.some(role => ['radiologist','transcriptionist'].includes(role)))) && <><button className="copilot-launcher" onClick={() => setAiOpen(!aiOpen)} aria-label="Open AI report assistant"><Sparkles/></button><ReportChat key={data.saas && !workspace?.roles.includes('radiologist') ? `${patient.id}:${report.id}` : patient.id} reportScoped={Boolean(data.saas && !workspace?.roles.includes('radiologist'))} patientId={patient.id} patientName={patient.name} reportId={report.id} modality={report.modality} editorBusy={!!action.pending || deleted || readOnly} open={aiOpen} currentReport={() => editorRef.current?.innerText || body} renderReport={reportTextToHtml} onInsert={insertAi} onClose={() => setAiOpen(false)}/></>}
  </div>
}
function Templates({ data, onReload }: { data: Data; onReload: () => Promise<void> }) {
  const action = useAction()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({name:'', modality:'', body:'STUDY\n\nTECHNIQUE\n\nFINDINGS\n\nIMPRESSION\n'})
  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    void action.run('create', async () => {
      if (!form.name.trim() || !form.body.trim() || !form.modality) throw new Error('Enter a name, choose a modality and enter template content.')
      await api('templates', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:form.name.trim(),modality:form.modality,body:reportTextToHtml(form.body)})})
      setOpen(false)
      await onReload().catch(() => {})
    })
  }
  return <section className="card"><div className="section-head"><h2>Reporting templates</h2><button className="button primary" disabled={!!action.pending} onClick={() => { action.clearError(); setForm({name:'',modality:data.modalities[0] || '',body:'STUDY\n\nTECHNIQUE\n\nFINDINGS\n\nIMPRESSION\n'}); setOpen(true) }}><Plus size={16}/> New template</button></div><table><thead><tr><th>Name</th><th>Modality</th><th>Preview</th></tr></thead><tbody>{data.templates.map(t=><tr key={t.id}><td><b>{t.name}</b></td><td><span className="badge">{t.modality}</span></td><td>{t.body.replace(/<[^>]*>/g,' ').slice(0,80)}…</td></tr>)}</tbody></table>
    {!data.templates.length && <p>No templates yet. Create your first modality-specific template.</p>}
    {open && <CreationDialog title="New reporting template" busy={!!action.pending} error={action.error} onClose={() => setOpen(false)} onSubmit={submit} submitLabel="Create template" busyLabel="Creating template…">
      <div className="modal-grid"><label>Template name<input required autoFocus maxLength={120} value={form.name} placeholder="e.g. Normal USG abdomen" onChange={e=>setForm({...form,name:e.target.value})}/></label><label>Modality<select required value={form.modality} onChange={e=>setForm({...form,modality:e.target.value})}><option value="">Choose modality</option>{data.modalities.map(m=><option key={m}>{m}</option>)}</select></label></div>
      {!data.modalities.length && <p>Add a modality on the Modalities page before creating a template.</p>}
      <label className="template-content">Report template content<textarea required rows={12} maxLength={200000} value={form.body} onChange={e=>setForm({...form,body:e.target.value})} placeholder="Enter headings and report text. Use **text** for bold."/></label>
      <small>Headings, paragraphs and **bold text** will be formatted when loaded in the report editor.</small>
    </CreationDialog>}
  </section>
}
function Modalities({ data, onReload }: { data: Data; onReload: () => Promise<void> }) {
  const action = useAction()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    void action.run('create', async () => {
      const code = name.trim().toUpperCase()
      if (!/^[A-Z][A-Z0-9_-]{1,19}$/.test(code)) throw new Error('Use 2–20 characters: letters, numbers, hyphens or underscores, starting with a letter.')
      if (data.modalities.some(m=>m.toUpperCase()===code)) throw new Error('This modality already exists.')
      await api('modalities',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:code})})
      setOpen(false); setName('')
      await onReload().catch(() => {})
    })
  }
  return <section className="card"><div className="section-head"><h2>Modality catalogue</h2><button className="button primary" disabled={!!action.pending} onClick={() => { action.clearError(); setName(''); setOpen(true) }}><Plus size={16}/> Add modality</button></div><div className="modality-grid">{data.modalities.map(m=><div className="modality" key={m}><span className="badge">{m}</span><small>{data.reports.filter(r=>r.modality===m).length} reports</small></div>)}</div>
    {open && <CreationDialog title="Add modality" busy={!!action.pending} error={action.error} onClose={() => setOpen(false)} onSubmit={submit} submitLabel="Add modality" busyLabel="Adding modality…"><div className="modal-grid"><label>Modality code<input required autoFocus maxLength={20} value={name} onChange={e=>setName(e.target.value)} placeholder="e.g. PET"/></label></div><p>Use a unique code. Letters will be converted to uppercase.</p></CreationDialog>}
  </section>
}

function CreationDialog({ title, busy, error, onClose, onSubmit, submitLabel, busyLabel, children }: {
  title:string; busy:boolean; error:string; onClose:()=>void; onSubmit:(event:React.FormEvent)=>void; submitLabel:string; busyLabel:string; children:React.ReactNode
}) {
  const ref = useRef<HTMLFormElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    return () => previous?.focus()
  }, [])
  return <div className="modal-backdrop" onMouseDown={e=>{if(!busy && e.target===e.currentTarget)onClose()}}>
    <form ref={ref} className="modal card creation-dialog" role="dialog" aria-modal="true" aria-label={title} aria-busy={busy} onSubmit={onSubmit} onKeyDown={e=>{
      if(e.key==='Escape' && !busy){e.preventDefault();onClose()}
      if(e.key==='Tab'){
        const nodes=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled)') || []).filter(node=>!node.closest('[inert]'))
        const first=nodes[0],last=nodes[nodes.length-1]
        if(e.shiftKey && document.activeElement===first){e.preventDefault();last?.focus()}
        else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first?.focus()}
        else if(!nodes.length)e.preventDefault()
      }
    }}><div className="section-head"><h2>{title}</h2><button type="button" className="icon-button" disabled={busy} aria-label="Close dialog" onClick={onClose}><X size={18}/></button></div><div inert={busy}>{children}</div>{error && <p className="form-error" role="alert">{error}</p>}<div className="modal-actions"><button type="button" className="button ghost" disabled={busy} onClick={onClose}>Cancel</button><ActionButton type="submit" className="button primary" busy={busy} busyLabel={busyLabel}>{submitLabel}</ActionButton></div></form>
  </div>
}
