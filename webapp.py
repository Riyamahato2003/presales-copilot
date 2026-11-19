"""Browser interface for Pre-Sales Copilot."""
from __future__ import annotations
import logging, threading, uuid
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from copilot.llm import GeminiLLM
from copilot.pipeline import run_pipeline

ROOT=Path(__file__).resolve().parent
JOBS=ROOT/'data'/'web_jobs'; JOBS.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='Pre-Sales Copilot')
app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
state={}; lock=threading.Lock(); logging.basicConfig(level=logging.INFO)

@app.get('/',response_class=HTMLResponse)
def home(): return (ROOT/'web'/'index.html').read_text(encoding='utf-8')

def execute(jid,tender,brochures,out):
    def log(msg):
        with lock: state[jid]['logs'].append(str(msg))
    try:
        with lock: state[jid]['status']='running'
        llm=GeminiLLM()
        run_pipeline(llm,str(tender),[str(p) for p in brochures],str(out),log=log,model_name=llm.model)
        with lock: state[jid].update(status='complete',download=f'/api/jobs/{jid}/download')
    except Exception as exc:
        logging.exception('Job failed')
        with lock: state[jid].update(status='failed',error=str(exc)[:500])

@app.post('/api/jobs')
async def create_job(tender: UploadFile=File(...),brochures: list[UploadFile]=File(...)):
    if not brochures: raise HTTPException(400,'Upload at least one brochure or offer.')
    files=[tender,*brochures]; allowed={'.pdf','.docx','.xlsx'}
    if any(Path(f.filename or '').suffix.lower() not in allowed for f in files):
        raise HTTPException(400,'Only PDF, DOCX, and XLSX files are supported.')
    jid=uuid.uuid4().hex; folder=JOBS/jid; folder.mkdir(parents=True)
    tp=folder/('tender'+Path(tender.filename or 'tender.pdf').suffix.lower()); tp.write_bytes(await tender.read())
    bp=[]
    for i,f in enumerate(brochures,1):
        p=folder/(f'product_{i}'+Path(f.filename or 'brochure.pdf').suffix.lower()); p.write_bytes(await f.read()); bp.append(p)
    with lock: state[jid]={'status':'queued','logs':[],'error':None,'download':None}
    threading.Thread(target=execute,args=(jid,tp,bp,folder/'compliance_matrix.xlsx'),daemon=True).start()
    return {'job_id':jid,'status':'queued'}

@app.get('/api/jobs/{jid}')
def get_job(jid:str):
    with lock:
        if jid not in state: raise HTTPException(404,'Job not found')
        return dict(state[jid])

@app.get('/api/jobs/{jid}/download')
def download(jid:str):
    with lock:
        if state.get(jid,{}).get('status')!='complete': raise HTTPException(409,'Output not ready')
    p=JOBS/jid/'compliance_matrix.xlsx'
    if not p.is_file(): raise HTTPException(404,'Output not found')
    return FileResponse(p,filename='compliance_matrix.xlsx')
