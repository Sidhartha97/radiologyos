import { apiFetch } from '../apiFetch'
import type { Patient, Report } from '../types'

// Print only an authorized saved revision; never execute pasted HTML/scripts.
export async function printSavedReport(reportId: string, patient: Patient) {
  const response = await apiFetch(`/api/reports/${encodeURIComponent(reportId)}`)
  const report: Report & { error?: string } = await response.json()
  if (!response.ok) throw new Error(report.error || 'Could not load the saved report.')
  const frame = document.createElement('iframe'); frame.title = 'Saved report print document'
  frame.style.cssText = 'position:fixed;width:0;height:0;border:0;'
  document.body.appendChild(frame)
  const target = frame.contentDocument
  if (!target || !frame.contentWindow) { frame.remove(); throw new Error('Printing is unavailable.') }
  target.title = `${report.modality} report — ${patient.mrn}`
  const style = target.createElement('style'); style.textContent = 'body{font:12pt Arial,sans-serif;color:#111;margin:20mm}h1{font-size:16pt}p{line-height:1.5}table{border-collapse:collapse}td,th{border:1px solid #888;padding:6px}@page{margin:15mm}'
  target.head.appendChild(style)
  const title = target.createElement('h1'); title.textContent = `${report.modality} report — ${report.status}`; target.body.appendChild(title)
  const details = target.createElement('p'); details.textContent = `${patient.name} | ${patient.mrn} | Revision ${report.version ?? '—'}`; target.body.appendChild(details)
  if (/<[a-z][\s\S]*>/i.test(report.body)) {
    const parsed = new DOMParser().parseFromString(report.body, 'text/html')
    const allowed = new Set(['P','DIV','BR','SPAN','STRONG','B','EM','I','U','S','SUP','SUB','UL','OL','LI','H1','H2','H3','H4','TABLE','THEAD','TBODY','TR','TD','TH'])
    const blocked = new Set(['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','SVG','MATH','TEMPLATE','FORM','INPUT'])
    const copy = (node: Node, parent: Node) => {
      if (node.nodeType === Node.TEXT_NODE) { parent.appendChild(target.createTextNode(node.textContent || '')); return }
      if (!(node instanceof Element) || blocked.has(node.tagName)) return
      const clean = allowed.has(node.tagName) ? target.createElement(node.tagName.toLowerCase()) : parent
      if (clean !== parent) parent.appendChild(clean)
      node.childNodes.forEach(child => copy(child, clean))
    }
    parsed.body.childNodes.forEach(node => copy(node, target.body))
  } else { const content = target.createElement('div'); content.style.whiteSpace = 'pre-wrap'; content.textContent = report.body; target.body.appendChild(content) }
  frame.contentWindow.addEventListener('afterprint', () => frame.remove(), { once: true })
  frame.contentWindow.focus(); frame.contentWindow.print()
  window.setTimeout(() => frame.remove(), 60000)
}
