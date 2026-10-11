import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlignCenter, AlignLeft, AlignRight, Bold, Check, Clipboard, FileText, History, Italic, LayoutDashboard, Link, List, ListOrdered, Menu, Plus, Printer, Redo2, Search, Settings, Sparkles, Strikethrough, Table2, Trash2, Underline, Undo2, Upload, Users, X } from 'lucide-react'
import type { Data, Patient, Report } from './types'
import HistoricalLibrary from './HistoricalLibrary'
import ReportChat from './PatientChat'
import Login from './Login'
import { apiFetch } from './apiFetch'
import SessionBadge from './SessionBadge'
import { ActionButton, LoadingStatus, useAction } from './Loading'

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

  const reload = async () => {
    setRefreshing(true); setLoadError('')
    try { setData(await api<Data>('data')) }
    catch (cause) { setLoadError(cause instanceof Error ? cause.message : 'Could not load application data.'); throw cause }
    finally { setRefreshing(false) }
  }
  useEffect(() => {
    void apiFetch('/api/auth/me').then(r => { setLoggedIn(r.ok); setSessionLoading(false) }).catch(() => setSessionLoading(false))
    const expired=() => { setLoggedIn(false); setData(null); setReport(null); setAiOpen(false) }
    window.addEventListener('radiology-session-expired', expired)
    return () => window.removeEventListener('radiology-session-expired', expired)
  }, [])
  useEffect(() => { if (loggedIn) void reload().catch(e => setLoadError(String(e))) }, [loggedIn])
  if (sessionLoading) return <div className="loading"><LoadingStatus>Checking session…</LoadingStatus></div>
  if (!loggedIn) return <Login onLogin={() => { setLoadError(''); setLoggedIn(true) }} />
  if (!data) return <div className="loading">{loadError ? <><p role="alert">{loadError}</p><ActionButton className="button primary" busy={refreshing} busyLabel="Loading…" onClick={() => void reload().catch(() => {})}>Retry loading</ActionButton></> : <LoadingStatus>Loading RadiologyOS…</LoadingStatus>}</div>

  const openNewReport = (patientId: string) => { setReport({ id: null, patientId, modality: data.modalities[0] || 'CT', body: '', status: 'Draft' }); setView('editor') }
  const openReport = (r: Report) => { setReport(r); setView('editor') }
  const patient = report ? data.patients.find(p => p.id === report.patientId) : undefined
  const nav = (next: View) => { setView(next); setAiOpen(false) }

  return <div className="app-shell">
    {refreshing && <div className="app-refresh"><LoadingStatus>Refreshing workspace…</LoadingStatus></div>}
    <aside className="sidebar"><div className="logo">Radiology<span>OS</span></div><div className="nav-label">WORKSPACE</div>
      <Nav active={view} value="dashboard" icon={<LayoutDashboard />} label="Dashboard" onClick={nav}/><Nav active={view} value="patients" icon={<Users />} label="Patients" onClick={nav}/><Nav active={view} value="reports" icon={<FileText />} label="Reports" onClick={nav}/><Nav active={view} value="templates" icon={<FileText />} label="Templates" onClick={nav}/>
      <Nav active={view} value="library" icon={<Upload />} label="Historical library" onClick={nav}/><div className="nav-label">CONFIGURATION</div><Nav active={view} value="modalities" icon={<Settings />} label="Modalities" onClick={nav}/><SessionBadge/>
    </aside>
    <main className="main"><header className="topbar"><div><h1>{view === 'dashboard' ? 'Good morning, Dr. Menon' : view === 'library' ? 'Historical reports' : view[0].toUpperCase() + view.slice(1)}</h1><p>RadiologyOS · St. Mary’s Diagnostic Centre</p></div><div className="top-actions"><input value={search} onChange={e => setSearch(e.target.value)} placeholder="⌕  Search anything" />{view === 'patients' && <button className="button primary" onClick={() => nav('library')}><Upload size={16}/> Upload old report</button>}</div></header>
      {loadError && <p className="action-error" role="alert">{loadError}</p>}
      {view === 'library' && <HistoricalLibrary/>}
      {view === 'dashboard' && <Dashboard data={data} onOpen={openReport} onView={() => nav('reports')} />}
      {view === 'patients' && <Patients data={data} search={search} onNew={openNewReport} onReload={reload} />}
      {view === 'reports' && <Reports data={data} search={search} onOpen={openReport} />}
      {view === 'templates' && <Templates data={data} onReload={reload} />}
      {view === 'modalities' && <Modalities data={data} onReload={reload} />}
      {view === 'editor' && report && patient && <Editor key={`${patient.id}:${report.id || 'new'}`} data={data} report={report} patient={patient} aiOpen={aiOpen} setAiOpen={setAiOpen} onReport={setReport} onReload={reload} onOpen={openReport} />}
    </main>
  </div>
}


function Nav({ active, value, icon, label, onClick }: { active: View; value: View; icon: React.ReactNode; label: string; onClick: (v: View) => void }) { return <button className={`nav ${active === value ? 'active' : ''}`} onClick={() => onClick(value)}>{icon}<span>{label}</span></button> }
function Dashboard({ data, onOpen, onView }: { data: Data; onOpen: (r: Report) => void; onView: () => void }) { return <><div className="stats">{[['Patients', data.patients.length, <Users/>], ['Reports today', data.reports.length, <FileText/>], ['Draft reports', data.reports.filter(r => r.status === 'Draft').length, <History/>], ['Finalized', data.reports.filter(r => r.status === 'Final').length, <Check/>]].map(([label, value, icon]) => <div className="stat card" key={String(label)}><div><small>{label}</small><strong>{value as number}</strong><em>Updated just now</em></div><span className="stat-icon">{icon}</span></div>)}</div><div className="two-col"><section className="card"><div className="section-head"><h2>Recent reports</h2><button className="button ghost" onClick={onView}>View all</button></div><ReportTable data={data} reports={data.reports.slice().reverse().slice(0, 5)} onOpen={onOpen}/></section><section className="card"><h2>Department activity</h2><div className="activity"><Activity size={18}/><div><b>Daily workflow ready</b><small>{data.reports.length} reports in the system</small></div></div><div className="activity"><Sparkles size={18}/><div><b>AI drafting assistant ready</b><small>Review every generated draft before finalization</small></div></div></section></div></> }
function Patients({ data, search, onNew, onReload }: { data: Data; search: string; onNew: (id: string) => void; onReload: () => Promise<void> }) {
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
  return <section className="card"><div className="section-head"><h2>Patient registry <small>{rows.length} patients</small></h2><button className="button primary" onClick={() => { setError(''); setOpen(true) }}><Plus size={16}/> Register patient</button></div><table><thead><tr><th>Patient</th><th>MRN</th><th>DOB / Sex</th><th>Phone</th><th>Actions</th></tr></thead><tbody>{rows.map(p => <tr key={p.id}><td><b>{p.name}</b></td><td>{p.mrn}</td><td>{p.dob} · {p.sex}</td><td>{p.phone}</td><td><button className="button ghost" onClick={() => onNew(p.id)}>+ Report</button></td></tr>)}</tbody></table>
    {open && <div className="modal-backdrop" onMouseDown={event => { if (!action.pending && event.target === event.currentTarget) setOpen(false) }}><form className="modal card" aria-busy={!!action.pending} onSubmit={submit} onMouseDown={event => event.stopPropagation()}><div className="section-head"><h2>Register patient</h2><button type="button" disabled={!!action.pending} className="icon-button" onClick={() => setOpen(false)}><X size={18}/></button></div><div className="modal-grid" inert={!!action.pending}><label>Full name<input autoFocus value={form.name} onChange={event => update('name', event.target.value)} placeholder="Patient name" /></label><label>MRN<input value={form.mrn} onChange={event => update('mrn', event.target.value)} placeholder="Auto-generated if blank" /></label><label>Date of birth<input type="date" value={form.dob} onChange={event => update('dob', event.target.value)} /></label><label>Sex<select value={form.sex} onChange={event => update('sex', event.target.value)}><option value="">Select</option><option>M</option><option>F</option><option>O</option></select></label><label>Phone<input value={form.phone} onChange={event => update('phone', event.target.value)} placeholder="Phone number" /></label></div>{(error || action.error) && <p role="alert" className="form-error">{error || action.error}</p>}<div className="modal-actions"><button type="button" disabled={!!action.pending} className="button ghost" onClick={() => setOpen(false)}>Cancel</button><ActionButton type="submit" className="button primary" busy={!!action.pending} busyLabel="Saving patient…">Save patient</ActionButton></div></form></div>}
  </section>
}
function Reports({ data, search, onOpen }: { data: Data; search: string; onOpen: (r: Report) => void }) { const reports = data.reports.filter(r => { const p = data.patients.find(x => x.id === r.patientId); return `${p?.name} ${p?.mrn} ${r.modality}`.toLowerCase().includes(search.toLowerCase()) }); return <section className="card"><div className="section-head"><h2>All reports</h2></div><ReportTable data={data} reports={reports} onOpen={onOpen}/></section> }
function ReportTable({ data, reports, onOpen }: { data: Data; reports: Report[]; onOpen: (r: Report) => void }) { return <table><thead><tr><th>Patient</th><th>Modality</th><th>Updated</th><th>Status</th><th></th></tr></thead><tbody>{reports.map(r => { const p = data.patients.find(x => x.id === r.patientId); return <tr key={r.id}><td><b>{p?.name}</b><small>{p?.mrn}</small></td><td><span className="badge">{r.modality}</span></td><td>{r.updatedAt}</td><td><span className={`badge ${r.status === 'Draft' ? 'draft' : ''}`}>{r.status}</span></td><td><button className="button ghost" onClick={() => onOpen(r)}>Open</button></td></tr> })}</tbody></table> }
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
function Editor({ data, report, patient, aiOpen, setAiOpen, onReport, onReload, onOpen }: { data: Data; report: Report; patient: Patient; aiOpen: boolean; setAiOpen: (v: boolean) => void; onReport: (r: Report) => void; onReload: () => Promise<void>; onOpen: (r: Report) => void }) {
  const editorRef = useRef<HTMLDivElement>(null)
  const [body, setBody] = useState(report.body)
  const [findOpen, setFindOpen] = useState(false)
  const [findText, setFindText] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [fullscreen, setFullscreen] = useState(false)
  const [savedAt, setSavedAt] = useState('Not saved')
  const [fontSize, setFontSize] = useState('3')
  const [templateId, setTemplateId] = useState('')
  const action = useAction()
  const [deleted, setDeleted] = useState(false)
  const [previousAiBody, setPreviousAiBody] = useState<string | null>(null)
  const previousAiModality = useRef(report.modality)
  const modalityTemplates = data.templates.filter(t => t.modality === report.modality)

  useEffect(() => {
    setBody(report.body)
    setPreviousAiBody(null)
    if (editorRef.current) {
      if (/<[a-z][\s\S]*>/i.test(report.body || '')) editorRef.current.innerHTML = report.body || ''
      else editorRef.current.innerText = report.body || ''
    }
  }, [report.id, report.patientId])

  const updateBody = () => { const html = editorRef.current?.innerHTML || ''; setBody(html) }
  const command = (name: string, value?: string) => { editorRef.current?.focus(); document.execCommand(name, false, value); updateBody() }
  const save = (status: string) => action.run(status, async () => {
    if (deleted) return
    updateBody()
    const nextBody = editorRef.current?.innerHTML || body
    const r = await api<Report>(`reports${report.id ? `/${report.id}` : ''}`, {method: report.id ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({...report, body: nextBody, status})})
    setSavedAt(new Date().toLocaleTimeString()); onReport(r)
    // Saving succeeded even if a subsequent list refresh fails: do not invite a duplicate save.
    await onReload().catch(() => {})
  })
  const deleteReport = () => {
    if (!report.id || deleted || !window.confirm('Delete this report? This cannot be undone.')) return
    void action.run('delete', async () => {
      await api(`reports/${report.id}`, {method:'DELETE'})
      setDeleted(true); setAiOpen(false); setSavedAt('Report deleted')
      await onReload().catch(() => {})
    })
  }
  const insertAi = (text: string, modality?: string) => {
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
  const keyDown = (event: React.KeyboardEvent<HTMLDivElement>) => { if (event.ctrlKey && event.key.toLowerCase() === 's') { event.preventDefault(); save('Draft') } }
  const wordCount = (editorRef.current?.innerText || body.replace(/<[^>]+>/g, ' ')).trim().split(/\s+/).filter(Boolean).length
  return <div className={fullscreen ? 'editor-screen fullscreen' : 'editor-screen'}><div className="patient-strip"><div><b>{patient.name}</b><small> · {patient.mrn} · {patient.dob} · {patient.sex}</small></div><span className="badge">{report.modality}</span></div><div className="editor-layout"><aside className="history card"><h2>Previous reports</h2>{data.reports.filter(r => r.patientId === patient.id).map(r => <button key={r.id} disabled={!!action.pending} onClick={() => onOpen(r)}><b>{r.modality} report</b><small>{r.updatedAt} · {r.status}</small></button>)}</aside><section className="editor-pane" aria-busy={!!action.pending} data-busy={!!action.pending}><div className="toolbar" inert={!!action.pending || deleted}>
    <button title="Undo" onClick={() => command('undo')}><Undo2/></button><button title="Redo" onClick={() => command('redo')}><Redo2/></button><span className="toolbar-divider"/>
    <select title="Style" defaultValue="p" onChange={e => command('formatBlock', e.target.value)}><option value="p">Normal</option><option value="h1">Heading 1</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option><option value="blockquote">Quote</option></select>
    <select title="Font size" value={fontSize} onChange={e => { setFontSize(e.target.value); command('fontSize', e.target.value) }}><option value="2">Small</option><option value="3">Normal</option><option value="4">Large</option><option value="5">Huge</option></select><span className="toolbar-divider"/>
    <button title="Bold" onClick={() => command('bold')}><Bold/></button><button title="Italic" onClick={() => command('italic')}><Italic/></button><button title="Underline" onClick={() => command('underline')}><Underline/></button><button title="Strikethrough" onClick={() => command('strikeThrough')}><Strikethrough/></button>
    <label className="color-tool" title="Text color">A<input type="color" onChange={e => command('foreColor', e.target.value)}/></label><label className="color-tool highlight" title="Highlight">▰<input type="color" defaultValue="#fff19a" onChange={e => command('hiliteColor', e.target.value)}/></label><span className="toolbar-divider"/>
    <button title="Align left" onClick={() => command('justifyLeft')}><AlignLeft/></button><button title="Align center" onClick={() => command('justifyCenter')}><AlignCenter/></button><button title="Align right" onClick={() => command('justifyRight')}><AlignRight/></button><button title="Bulleted list" onClick={() => command('insertUnorderedList')}><List/></button><button title="Numbered list" onClick={() => command('insertOrderedList')}><ListOrdered/></button>
    <button title="Insert link" onClick={() => { const url = window.prompt('Enter URL'); if (url) command('createLink', url) }}><Link/></button><button title="Insert table" onClick={() => command('insertHTML', '<table><tbody><tr><td>Header</td><td>Value</td></tr><tr><td></td><td></td></tr></tbody></table>')}><Table2/></button><button title="Clear formatting" onClick={() => command('removeFormat')}>Tx</button>
    <span className="toolbar-spacer"/><button title="Find and replace" onClick={() => setFindOpen(!findOpen)}><Search/></button><ActionButton title="Copy report" busy={action.pending === 'copy'} busyLabel="Copying…" onClick={copyReport}><Clipboard/></ActionButton><button title="Print report" onClick={() => window.print()}><Printer/></button><button title="Fullscreen" onClick={() => setFullscreen(!fullscreen)}>⛶</button>
  </div>{findOpen && <div className="find-bar" inert={!!action.pending || deleted}><input value={findText} onChange={e => setFindText(e.target.value)} placeholder="Find"/><input value={replaceText} onChange={e => setReplaceText(e.target.value)} placeholder="Replace with"/><button className="button ghost" onClick={replaceAll}>Replace all</button></div>}<div className="template-bar" inert={!!action.pending || deleted}><select value={report.modality} onChange={e => { onReport({...report, modality:e.target.value}); setTemplateId('') }}>{data.modalities.map(m => <option key={m}>{m}</option>)}</select><select value={templateId} onChange={e => loadTemplate(e.target.value)}><option value="">Choose {report.modality} template</option>{modalityTemplates.map(t => <option key={t.id} value={String(t.id)}>{t.name}</option>)}</select></div><div ref={editorRef} className="editor" contentEditable={!action.pending && !deleted} suppressContentEditableWarning onInput={updateBody} onKeyDown={keyDown} role="textbox" aria-label="Radiology report editor"/>{action.error && <p className="action-error" role="alert">{action.error}</p>}<div className="editor-status"><span>{wordCount} words · {savedAt}</span>{previousAiBody !== null && <button className="chat-undo" disabled={!!action.pending || deleted} onClick={undoAi}>Undo AI replacement</button>}<span>Ctrl+S to save draft</span></div><div className="editor-actions"><ActionButton className="button ghost danger" disabled={!!action.pending || !report.id || deleted} busy={action.pending === 'delete'} busyLabel="Deleting…" onClick={deleteReport}><Trash2 size={15}/> Delete</ActionButton><div><ActionButton className="button ghost" disabled={!!action.pending || deleted} busy={action.pending === 'Draft'} busyLabel="Saving draft…" onClick={() => void save('Draft')}>Save draft</ActionButton><ActionButton className="button primary" disabled={!!action.pending || deleted} busy={action.pending === 'Final'} busyLabel="Finalizing…" onClick={() => void save('Final')}><Check size={15}/> Save & finalize</ActionButton></div></div></section></div><button className="copilot-launcher" onClick={() => setAiOpen(!aiOpen)} aria-label="Open AI report assistant"><Sparkles/></button>{<ReportChat key={patient.id} patientId={patient.id} patientName={patient.name} reportId={report.id} modality={report.modality} editorBusy={!!action.pending || deleted} open={aiOpen} currentReport={() => editorRef.current?.innerText || body} renderReport={reportTextToHtml} onInsert={insertAi} onClose={() => setAiOpen(false)}/>}</div>
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
