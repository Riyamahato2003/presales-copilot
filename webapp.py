"""Browser interface for Pre-Sales Copilot."""
from __future__ import annotations
import base64, hmac, json, logging, os, threading, time, uuid, zipfile
from io import BytesIO
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from copilot.llm import GeminiLLM
from copilot.pipeline import run_pipeline

ROOT=Path(__file__).resolve().parent
JOBS=Path(os.getenv('COPILOT_DATA_DIR',str(ROOT/'data'/'web_jobs')))
try: JOBS.mkdir(parents=True,exist_ok=True)
except OSError: pass
app=FastAPI(title='Pre-Sales Copilot')
app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
state={}; lock=threading.Lock(); logging.basicConfig(level=logging.INFO)

def persist(jid):
    folder=JOBS/jid
    try:
        temp=folder/'.state.tmp'
        temp.write_text(json.dumps(state[jid]),encoding='utf-8')
        temp.replace(folder/'state.json')
    except OSError:
        logging.exception('Could not persist job state')

if JOBS.exists():
    for f in JOBS.glob('*/state.json'):
        try:
            saved=json.loads(f.read_text(encoding='utf-8'))
            if saved.get('status') in ('queued','running'):
                saved.update(status='failed',error='Server restarted before this job completed. Start a new run; successful LLM responses remain cached locally.')
            state[f.parent.name]=saved
        except Exception:
            logging.warning('Ignoring unreadable job state: %s',f)

@app.middleware('http')
async def access_and_size_guard(request: Request, call_next):
    password=os.getenv('COPILOT_ACCESS_PASSWORD','')
    if os.getenv('APP_ENV','').lower()=='production' and not password:
        from starlette.responses import PlainTextResponse
        return PlainTextResponse('Service is not configured with an access password.',status_code=503)
    if password:
        auth=request.headers.get('authorization','')
        try:
            scheme, token=auth.split(' ',1)
            user, supplied=base64.b64decode(token,validate=True).decode().split(':',1)
        except Exception:
            user=supplied=''
            scheme=''
        if scheme.lower()!='basic' or user!='copilot' or not hmac.compare_digest(supplied,password):
            from starlette.responses import Response
            return Response('Authentication required',status_code=401,headers={'WWW-Authenticate':'Basic realm="Pre-Sales Copilot"'})
    length=request.headers.get('content-length')
    try: too_large=bool(length and int(length)>30*1024*1024)
    except ValueError: too_large=True
    if too_large:
        from starlette.responses import PlainTextResponse
        return PlainTextResponse('Upload is too large (30 MB total limit).',status_code=413)
    return await call_next(request)

@app.get('/',response_class=HTMLResponse)
def home(): return (ROOT/'web'/'index.html').read_text(encoding='utf-8')

def execute(jid,tender,brochures,out):
    def log(msg):
        with lock:
            state[jid]['logs'].append(str(msg))
            state[jid]['logs']=state[jid]['logs'][-500:]
            persist(jid)
    try:
        with lock: state[jid]['status']='running'; persist(jid)
        llm=GeminiLLM()
        run_pipeline(llm,str(tender),[str(p) for p in brochures],str(out),log=log,model_name=llm.model)
        with lock: state[jid].update(status='complete',download=f'/api/jobs/{jid}/download'); persist(jid)
    except Exception as exc:
        logging.exception('Job failed')
        with lock: state[jid].update(status='failed',error=f'Run failed. Contact the administrator with job ID {jid}.'); persist(jid)

def valid_file(filename:str, content:bytes)->bool:
    suffix=Path(filename).suffix.lower()
    if suffix=='.pdf': return content.startswith(b'%PDF-')
    if suffix=='.doc': return content.startswith(bytes.fromhex('d0cf11e0a1b11ae1'))
    if suffix in {'.docx','.xlsx'} and content.startswith(b'PK'):
        try:
            with zipfile.ZipFile(BytesIO(content)) as z:
                info=z.infolist()
                if len(info)>10000 or sum(i.file_size for i in info)>100*1024*1024:
                    return False
                if any(i.compress_size and i.file_size/i.compress_size>1000 for i in info):
                    return False
                names={i.filename for i in info}
                return ('word/document.xml' in names) if suffix=='.docx' else ('xl/workbook.xml' in names)
        except zipfile.BadZipFile: return False
    return False

@app.post('/api/jobs')
async def create_job(tender: UploadFile=File(...),brochures: list[UploadFile]=File(...)):
    if not JOBS.exists(): raise HTTPException(503,'Job storage is unavailable on this host.')
    if not brochures: raise HTTPException(400,'Upload at least one brochure or offer.')
    cutoff=time.time()-7*24*60*60
    for old in JOBS.iterdir():
        if not old.is_dir(): continue
        try:
            state_path=old/'state.json'
            meta=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            created=meta.get('created_at',old.stat().st_mtime)
            if meta.get('status') not in ('queued','running') and created<cutoff:
                import shutil
                shutil.rmtree(old,ignore_errors=True)
                with lock: state.pop(old.name,None)
        except (OSError,ValueError): pass
    if len(brochures)>5: raise HTTPException(400,'Upload no more than five manufacturer documents per run.')
    with lock:
        if sum(s.get('status') in ('queued','running') for s in state.values())>=1:
            raise HTTPException(429,'Another run is in progress. Wait for it to finish before starting another.')
        from datetime import datetime, timezone
        today=datetime.now(timezone.utc).date()
        daily_limit=max(1,int(os.getenv('MAX_DAILY_JOBS','5')))
        today_count=sum(1 for s in state.values() if datetime.fromtimestamp(s.get('created_at',0),timezone.utc).date()==today)
        if today_count>=daily_limit:
            raise HTTPException(429,f'Daily run limit reached ({daily_limit}). Try again tomorrow or ask the administrator to adjust MAX_DAILY_JOBS.')
    files=[tender,*brochures]; allowed={'.pdf','.doc','.docx','.xlsx'}
    if any(Path(f.filename or '').suffix.lower() not in allowed for f in files):
        raise HTTPException(400,'Only PDF, DOC, DOCX, and XLSX files are supported.')
    payloads=[]; total=0
    for f in files:
        content=await f.read(15*1024*1024+1)
        total+=len(content)
        if len(content)>15*1024*1024 or total>30*1024*1024:
            raise HTTPException(413,'Each file must be under 15 MB and total upload under 30 MB.')
        if not valid_file(f.filename or '',content):
            raise HTTPException(400,f'File content does not match supported format: {Path(f.filename or "file").name}')
        payloads.append(content)
    jid=uuid.uuid4().hex; folder=JOBS/jid; folder.mkdir(parents=True)
    def safe_name(i, upload):
        name=Path(upload.filename or f'document_{i}').name
        clean=''.join(c for c in name if c.isalnum() or c in ' ._-()').strip(' .')[:100]
        return f'{i:02d}_{clean or "document"}'
    tp=folder/safe_name(0,tender); tp.write_bytes(payloads[0])
    bp=[]
    for i,f in enumerate(brochures,1):
        p=folder/safe_name(i,f); p.write_bytes(payloads[i]); bp.append(p)
    with lock:
        state[jid]={'status':'queued','logs':[],'error':None,'download':None,'created_at':time.time()}
        persist(jid)
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
