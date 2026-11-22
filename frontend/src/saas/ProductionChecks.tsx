import { useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert } from 'lucide-react'
import { apiFetch } from '../apiFetch'
import { ActionButton, useAction } from '../Loading'

export default function ProductionChecks() {
  const [checks, setChecks] = useState<{name:string;ok:boolean}[]>([]); const [notice, setNotice] = useState(''); const action=useAction()
  const load=()=>action.run('check',async()=>{
    const response=await apiFetch('/api/platform/readiness'); const result=await response.json()
    if(!response.ok)throw new Error(result.error||'Readiness checks unavailable.')
    setChecks(result.checks);setNotice(result.notice)
  })
  useEffect(()=>{void load()},[])
  return <section className="card"><div className="section-head"><div><h2>Deployment configuration checks</h2><p className="platform-muted">Read-only checks. This screen does not deploy or modify AWS.</p></div><ActionButton className="button ghost" busy={Boolean(action.pending)} onClick={()=>void load()}>Run checks</ActionButton></div>
    {checks.map(check=><div className="workspace-choice" key={check.name}><span style={{display:'flex',gap:10,alignItems:'center'}}>{check.ok?<CheckCircle2 size={17} color="#278665"/>:<CircleAlert size={17} color="#af7b25"/>}{check.name}</span><span className={`platform-status ${check.ok?'status-active':'status-pending'}`}>{check.ok?'PASS':'REVIEW REQUIRED'}</span></div>)}
    {notice&&<p className="admin-notice">{notice}</p>}{action.error&&<p className="action-error" role="alert">{action.error}</p>}
  </section>
}
