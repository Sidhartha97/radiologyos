import { useEffect, useRef, useState } from 'react'
import { Upload, RefreshCw, Download, FolderOpen } from 'lucide-react'
import './library.css'
import { apiFetch } from './apiFetch'
import { ActionButton, LoadingStatus, Spinner, useAction } from './Loading'

type Detail = {time?:string; stage:string; code:string; reason:string; action:string; requestId?:string}
type ReportFile = {id:string; name:string; status:string; error?:Detail}
type Batch = {id:string; modality:string; status:string; createdAt:string; files:ReportFile[]; events:unknown[]; error?:Detail; job?:{job_id:string;status:string}; jobStatus?:string; failureReasons?:string[]}
type Snapshot = {counts:Record<string,Record<string,number>>; batches:Batch[]; workerError?:Detail; mode?:string; notice?:string}
type Preview = {name:string; size:number; sha256:string; modality:string; words:number; duplicate:boolean}
type Selection = {file:File; status:string; preview?:Preview; error?:string}

async function api<T>(path:string, payload?:unknown):Promise<T> {
  const response=await apiFetch(`/api/library${path}`,payload===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
  const result=await response.json()
  if (!response.ok) throw new Error(result.detail ? `${result.detail.stage}: ${result.detail.reason} ${result.detail.action} [${result.detail.code}${result.detail.requestId ? ` / ${result.detail.requestId}`:''}]` : result.error || 'Request failed')
  return result
}
const describe=(e:unknown)=>e instanceof Error?e.message:String(e)
const base64=(file:File)=>new Promise<string>((resolve,reject)=>{
  const reader=new FileReader(); reader.onload=()=>resolve(String(reader.result).split(',')[1]); reader.onerror=()=>reject(new Error('Could not read this file')); reader.readAsDataURL(file)
})
function directUpload(url:string, fields:Record<string,string>, file:File, progress:(n:number)=>void) {
  return new Promise<void>((resolve,reject)=>{
    const xhr=new XMLHttpRequest(); xhr.open('POST',url); xhr.timeout=120000
    xhr.upload.onprogress=e=>{if(e.lengthComputable) progress(Math.round(e.loaded/e.total*100))}
    xhr.onerror=()=>reject(new Error(`S3 upload could not connect from ${window.location.origin}. Check network access and ensure this exact origin is allowed by the reports bucket CORS rule.`))
    xhr.ontimeout=()=>reject(new Error('S3 upload timed out. Retry this file.'))
    xhr.onload=()=>{
      if(xhr.status>=200&&xhr.status<300) resolve()
      else {
        const xml=new DOMParser().parseFromString(xhr.responseText,'text/xml')
        reject(new Error(`S3 upload failed (${xhr.status}): ${xml.querySelector('Code')?.textContent || 'Unknown error'} — ${xml.querySelector('Message')?.textContent || 'Check S3/KMS permissions.'}`))
      }
    }
    const form=new FormData(); Object.entries(fields).forEach(([k,v])=>form.append(k,v)); form.append('file',file); xhr.send(form)
  })
}

export default function HistoricalLibrary() {
  const [modality,setModality]=useState('USG')
  const [selected,setSelected]=useState<Selection[]>([])
  const [snapshot,setSnapshot]=useState<Snapshot>({counts:{},batches:[]})
  const action=useAction()
  const busy=!!action.pending
  const [loading,setLoading]=useState(true)
  const [refreshing,setRefreshing]=useState(false)
  const refreshLock=useRef(false)
  const [error,setError]=useState('')
  const [activity,setActivity]=useState<string[]>([])
  const [completion,setCompletion]=useState<{id:string; uploaded:string[]}|null>(null)
  const files=useRef<HTMLInputElement>(null), folder=useRef<HTMLInputElement>(null)
  const log=(message:string)=>setActivity(old=>[`${new Date().toLocaleTimeString()} ${message}`,...old].slice(0,200))
  const refresh=async(manual=false)=>{
    if(refreshLock.current)return
    refreshLock.current=true
    if(manual)setRefreshing(true)
    try {setSnapshot(await api<Snapshot>('')); setError('')} catch(e){setError(describe(e))}
    finally{refreshLock.current=false;setLoading(false);if(manual)setRefreshing(false)}
  }
  useEffect(()=>{void refresh(); const timer=setInterval(()=>void refresh(),8000); return()=>clearInterval(timer)},[])
  useEffect(()=>{folder.current?.setAttribute('webkitdirectory','')},[])
  const choose=(list:FileList|null)=>{
    if(!list)return
    const candidates=Array.from(list).filter(f=>!['manifest.json','manifest.csv','review_required.json'].includes(f.name))
    setSelected(candidates.slice(0,100).map(file=>({file,status:'Not validated'})))
    if(candidates.length>100) log('Selected the first 100 files. Upload another batch for the remainder.')
    setError('')
  }
  const validate=()=>action.run('validate',async()=>{
    setError('')
    const result:Selection[]=[]
    for(const item of selected){
      try{
        if(!/\.(docx|pdf|txt)$/i.test(item.file.name)) throw new Error('Supported formats: DOCX, PDF and TXT.')
        if(item.file.size>10*1024*1024)throw new Error('File exceeds 10 MB.')
        const preview=await api<Preview>('/validate',{name:item.file.name,content:await base64(item.file),modality})
        result.push({...item,preview,status:preview.duplicate?'Already imported':'Ready',error:undefined})
      }catch(e){result.push({...item,status:'Needs review',error:describe(e)});log(`${item.file.name}: ${describe(e)}`)}
      setSelected([...result,...selected.slice(result.length)])
    }
    log(`Validation complete: ${result.filter(x=>x.status==='Ready').length} ready, ${result.filter(x=>x.preview?.duplicate).length} duplicates, ${result.filter(x=>x.error).length} need review.`)
  })
  const finish=async(value:{id:string;uploaded:string[]})=>{
    const result=await api<{status:string}>(`/batches/${value.id}/complete`,{uploaded:value.uploaded})
    setCompletion(null); log(result.status==='LOCAL_STAGED' ? `Batch ${value.id}: staged locally only. S3 upload and indexing are disabled in this demo.` : `Batch ${value.id}: processing status ${result.status}.`); await refresh()
  }
  const upload=()=>action.run('upload',async()=>{
    const ready=selected.filter(x=>x.status==='Ready'&&x.preview)
    if(!ready.length)return
    setError('')
    try{
      const batch=await api<{batchId:string;uploads:{id:string;url:string;fields:Record<string,string>;transport?:string}[]}>('/batches',{modality,files:ready.map(x=>x.preview)})
      const uploaded:string[]=[]
      for(let i=0;i<ready.length;i++){
        const item=ready[i], target=batch.uploads[i]
        const update=(status:string,reason?:string)=>setSelected(old=>old.map(x=>x.file===item.file?{...x,status,error:reason}:x))
        try{
          if(target.transport==='local-demo') {
            update('Staging locally…'); await api('/demo-upload',{...target.fields,content:await base64(item.file)})
            uploaded.push(target.id); update('Staged locally · not indexed'); log(`${item.file.name}: staged in local demo memory only.`)
          } else {
            await directUpload(target.url,target.fields,item.file,n=>update(`Uploading ${n}%`))
            uploaded.push(target.id);update('Uploaded');log(`${item.file.name}: uploaded to S3.`)
          }
        }catch(e){update('Upload failed',describe(e));log(`${item.file.name}: ${describe(e)}`)}
      }
      const value={id:batch.batchId,uploaded};setCompletion(value);await finish(value)
    }catch(e){setError(describe(e));log(describe(e))}
  })
  const retry=(id:string)=>action.run(`retry:${id}`,async()=>{try{await api(`/batches/${id}/retry`,{});log(`Retry requested for ${id}`);await refresh()}catch(e){setError(describe(e));log(describe(e))}})
  const download=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify({exportedAt:new Date().toISOString(),activity,...snapshot},null,2)],{type:'application/json'}))
    const a=document.createElement('a');a.href=url;a.download='historical-import-log.json';a.click();URL.revokeObjectURL(url)
  }
  return <div className="library">
    <section className="card"><div className="section-head"><div><h2>Historical Report Library</h2><p>Upload reference reports by modality and track when they become searchable.</p></div><ActionButton className="button ghost" disabled={busy || loading} busy={refreshing} busyLabel="Refreshing…" onClick={()=>void refresh(true)}><RefreshCw size={16}/> Refresh</ActionButton></div>
      {loading&&<LoadingStatus>Loading historical library…</LoadingStatus>}
      {snapshot.notice&&<p role="status" className="library-notice">{snapshot.notice}</p>}
      <div className="library-counts">{['CT','MRI','USG','XRAY','MAMMOGRAM','BMD'].map(m=><div key={m}><b>{m}</b><strong>{snapshot.counts[m]?.COMPLETED||0}</strong><small>indexed · {snapshot.counts[m]?.PROCESSING||0} processing · {snapshot.counts[m]?.FAILED||0} failed</small></div>)}</div>
      {error&&<p role="alert" className="library-error">{error}</p>}
      {action.error&&<p role="alert" className="library-error">{action.error}</p>}
      {snapshot.workerError&&<p role="alert" className="library-error">Background processing: {snapshot.workerError.reason} {snapshot.workerError.action} ({snapshot.workerError.code})</p>}
      <label>Modality <select disabled={busy} value={modality} onChange={e=>{setModality(e.target.value);setSelected(old=>old.map(x=>({file:x.file,status:'Not validated'})))}}>{['CT','MRI','USG','XRAY','MAMMOGRAM','BMD'].map(m=><option key={m}>{m}</option>)}</select></label>
      <div className="library-drop" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!busy)choose(e.dataTransfer.files)}}><Upload/><b>Drop reports here</b><span>One report per file · DOCX, PDF, TXT · 10 MB each · up to 100 files per batch</span><div className="row"><button disabled={busy} className="button ghost" onClick={()=>files.current?.click()}>Choose files</button><button disabled={busy} className="button ghost" onClick={()=>folder.current?.click()}><FolderOpen size={16}/> Choose folder</button></div></div>
      <input hidden ref={files} type="file" multiple accept=".docx,.pdf,.txt" onChange={e=>{choose(e.target.files);e.target.value=''}}/><input hidden ref={folder} type="file" multiple onChange={e=>{choose(e.target.files);e.target.value=''}}/>
      <div className="row"><ActionButton disabled={busy||loading||!selected.length} busy={action.pending==='validate'} busyLabel="Validating files…" className="button ghost" onClick={validate}>Validate selected files</ActionButton><ActionButton disabled={busy||loading||!selected.some(x=>x.status==='Ready')} busy={action.pending==='upload'} busyLabel={snapshot.mode==='local-demo'?'Staging locally…':'Uploading & queuing…'} className="button primary" onClick={upload}>{snapshot.mode==='local-demo'?'Stage files locally':'Upload & index'}</ActionButton><span>{selected.length} selected</span></div>
      {busy&&<LoadingStatus>{action.pending==='validate'?'Checking selected reports…':action.pending==='upload'?snapshot.mode==='local-demo'?'Staging files in local demo memory…':'Uploading reports to S3 and queuing indexing…':'Submitting processing request…'}</LoadingStatus>}
      {completion&&<ActionButton className="button ghost" disabled={busy} busy={action.pending==='finish'} busyLabel="Starting batch…" onClick={()=>void action.run('finish',()=>finish(completion))}>Retry starting this uploaded batch</ActionButton>}
      {!!selected.length&&<div className="library-table"><table><thead><tr><th>File</th><th>Validation / upload</th><th>Details</th></tr></thead><tbody>{selected.map((x,i)=><tr key={i}><td>{x.file.name}</td><td>{x.status.startsWith('Uploading')&&<Spinner/>} {x.status}</td><td>{x.error|| (x.preview ? `${x.preview.words} words · ${x.preview.modality}`:'')}</td></tr>)}</tbody></table></div>}
    </section>
    <section className="card"><div className="section-head"><h2>Recent batches</h2><button className="button ghost" onClick={download}><Download size={16}/> Download log</button></div>{!snapshot.batches.length&&<p>No browser upload batches yet. Previously imported reports appear in the modality counts above.</p>}
      {snapshot.batches.map(batch=><details className="library-batch" key={batch.id} open={['ERROR','COMPLETED_WITH_ERRORS'].includes(batch.status)}><summary>{batch.modality} · {batch.files.length} files · {batch.status.replace(/_/g,' ')} · {new Date(batch.createdAt).toLocaleString()}</summary><small>Batch {batch.id} {batch.job&&`· Job ${batch.job.job_id} · ${batch.jobStatus||batch.job.status}`}</small>
        {batch.error&&<p className="library-error">{batch.error.stage}: {batch.error.reason} {batch.error.action} [{batch.error.code}]</p>}
        {batch.failureReasons?.map((reason,i)=><p className="library-error" key={i}>{reason}</p>)}
        <table><thead><tr><th>File</th><th>Status</th><th>Error reason / next step</th></tr></thead><tbody>{batch.files.map(file=><tr key={file.id}><td>{file.name}</td><td>{file.status}</td><td>{file.error&&`${file.error.stage}: ${file.error.reason} ${file.error.action} (${file.error.code}${file.error.requestId?` / ${file.error.requestId}`:''})`}</td></tr>)}</tbody></table>
        <ActionButton className="button ghost" disabled={busy} busy={action.pending===`retry:${batch.id}`} busyLabel="Requesting retry…" onClick={()=>void retry(batch.id)}>Retry failed / resume processing</ActionButton>
        <details><summary>Activity log</summary><pre className="library-log">{JSON.stringify(batch.events,null,2)}</pre></details>
      </details>)}
    </section><section className="card"><h2>Session activity</h2><pre className="library-log">{activity.join('\n')||'Select reports and validate them to begin.'}</pre></section>
  </div>
}
