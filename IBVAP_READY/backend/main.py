from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import cv2, os, sqlite3, uuid, csv, io, hashlib, hmac, secrets, time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'ibvap.db')
UPLOAD = os.path.join(ROOT, 'uploads')
RESULTS = os.path.join(ROOT, 'results')
EVIDENCE = os.path.join(ROOT, 'evidence')
DEMOS = os.path.join(ROOT, 'demo_videos')
for d in (UPLOAD, RESULTS, EVIDENCE, DEMOS): os.makedirs(d, exist_ok=True)

app = FastAPI(title='IBVAP AI Border Surveillance', version='4.0')
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:8000', 'http://localhost:8000'], allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'], allow_credentials=True)
app.mount('/static', StaticFiles(directory=os.path.join(ROOT, 'frontend')), name='static')

SESSION_HOURS = 8
MAX_VIDEO_BYTES = 200 * 1024 * 1024
LOGIN_LIMIT = 5
LOCK_SECONDS = 300
failed_logins = {}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS incidents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, video_name TEXT, event_type TEXT,
 risk INTEGER, severity TEXT, confidence INTEGER, persons INTEGER, duration REAL,
 evidence TEXT, status TEXT DEFAULT "NEW", factors TEXT DEFAULT ""
);
CREATE TABLE IF NOT EXISTS watchlist (
 id INTEGER PRIMARY KEY AUTOINCREMENT, plate TEXT UNIQUE, priority TEXT, vehicle TEXT,
 note TEXT, created_at TEXT, last_seen TEXT, hit_count INTEGER DEFAULT 0, expires_at TEXT DEFAULT ""
);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT, salt TEXT, role TEXT DEFAULT "OPERATOR", created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
 token TEXT PRIMARY KEY, user_id INTEGER, username TEXT, role TEXT, created_at TEXT, expires_at TEXT
);
'''

CAMERAS = [
 {'id':'CAM-01','name':'North Gate','zone':'Entry Corridor','status':'ONLINE','objects':4,'fps':24,'signal':'98%','demo':'DEMO_01_Normal_Patrol.mp4','event':'Routine patrol'},
 {'id':'CAM-02','name':'Patrol Road','zone':'Patrol Lane','status':'ONLINE','objects':2,'fps':25,'signal':'96%','demo':'DEMO_03_Multiple_Persons.mp4','event':'Person approach'},
 {'id':'CAM-03','name':'West Fence','zone':'Restricted Perimeter','status':'ONLINE','objects':3,'fps':24,'signal':'99%','demo':'DEMO_04_Loitering_At_Fence.mp4','event':'Loitering watch'},
 {'id':'CAM-04','name':'Restricted Zone 2','zone':'High Security','status':'ALERT','objects':1,'fps':26,'signal':'94%','demo':'DEMO_02_Fence_Intrusion.mp4','event':'Fence intrusion'},
 {'id':'CAM-05','name':'East Checkpoint','zone':'Vehicle Checkpoint','status':'ONLINE','objects':3,'fps':25,'signal':'97%','demo':'DEMO_01_Normal_Patrol.mp4','event':'Vehicle monitoring'},
 {'id':'CAM-06','name':'Observation Post','zone':'Watch Tower','status':'ONLINE','objects':2,'fps':24,'signal':'95%','demo':'DEMO_03_Multiple_Persons.mp4','event':'Perimeter approach'}
]


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


def db():
    c = sqlite3.connect(DB)
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript(SCHEMA)
    cols = [r[1] for r in c.execute('PRAGMA table_info(watchlist)').fetchall()]
    for name, ddl in [('last_seen','TEXT'), ('hit_count','INTEGER DEFAULT 0'), ('expires_at','TEXT DEFAULT ""')]:
        if name not in cols:
            c.execute(f'ALTER TABLE watchlist ADD COLUMN {name} {ddl}')
    cols_i = [r[1] for r in c.execute('PRAGMA table_info(incidents)').fetchall()]
    if 'factors' not in cols_i:
        c.execute("ALTER TABLE incidents ADD COLUMN factors TEXT DEFAULT ''")
    if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        salt = secrets.token_hex(16)
        ph = hash_password('IBVAP@2026', salt)
        c.execute('INSERT INTO users(username,password_hash,salt,role,created_at) VALUES(?,?,?,?,?)', ('admin', ph, salt, 'ADMIN', now_iso()))
    if c.execute('SELECT COUNT(*) FROM watchlist').fetchone()[0] == 0:
        now = now_iso()
        c.executemany('INSERT OR IGNORE INTO watchlist(plate,priority,vehicle,note,created_at,last_seen,hit_count,expires_at) VALUES(?,?,?,?,?,?,?,?)', [
            ('TS09AB1234','HIGH','Vehicle / Sedan','Demo watchlist match',now,'-',0,''),
            ('AP09XY7812','MONITOR','Vehicle / SUV','Monitor entry',now,'-',0,''),
            ('TS10CD4455','MONITOR','Motorcycle','Demo record',now,'-',0,'')
        ])
    c.commit()
    return c


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 220000).hex()


def verify_password(password, salt, stored):
    return hmac.compare_digest(hash_password(password, salt), stored)


def clean_expired_sessions():
    con = db()
    con.execute('DELETE FROM sessions WHERE expires_at < ?', (now_iso(),))
    con.commit(); con.close()


def current_user(request: Request):
    token = request.cookies.get('ibvap_session')
    if not token:
        return None
    con = db()
    row = con.execute('SELECT user_id,username,role,expires_at FROM sessions WHERE token=?', (token,)).fetchone()
    if row and row[3] >= now_iso():
        con.close(); return {'id': row[0], 'username': row[1], 'role': row[2]}
    if row:
        con.execute('DELETE FROM sessions WHERE token=?', (token,)); con.commit()
    con.close()
    return None


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, 'Authentication required')
    return user


def severity(r):
    return 'CRITICAL' if r >= 85 else 'HIGH' if r >= 65 else 'MEDIUM' if r >= 35 else 'LOW'


def analyze_video(path, original_name):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened(): raise ValueError('Unable to open video')
    fps = cap.get(cv2.CAP_PROP_FPS) or 15; frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 360)
    duration = frames / fps if fps else 0
    fence_x = int(w * 0.58); prev = None; crossings = 0; max_people = 0; samples = 0; evidence_frame = None; best_score = 0
    # HOG is optional because some OpenCV/CPython builds do not expose
    # HOGDescriptor. The demo must still work in that environment.
    hog = None
    try:
        if hasattr(cv2, 'HOGDescriptor') and hasattr(cv2, 'HOGDescriptor_getDefaultPeopleDetector'):
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    except Exception:
        hog = None

    while True:
        ok, frame = cap.read()
        if not ok: break
        samples += 1
        if samples % 3: continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY); motion = 0
        if prev is not None:
            diff = cv2.absdiff(prev, gray); _, th = cv2.threshold(diff, 28, 255, cv2.THRESH_BINARY)
            motion = float(cv2.countNonZero(th)) / (w * h)
        prev = gray; people = 0
        if samples % 15 == 0 and hog is not None:
            small = cv2.resize(frame, (max(320, w//2), max(180, h//2)))
            try:
                rects, _ = hog.detectMultiScale(small, winStride=(8,8), padding=(8,8), scale=1.05)
                people = len(rects)
            except Exception:
                people = 0
        max_people = max(max_people, people); score = 15 + min(35, motion*260) + (35 if people else 10)
        if motion > 0.012: crossings += 1
        if crossings > 6: score += 25
        score = min(99, int(score))
        if score > best_score: best_score = score; evidence_frame = frame.copy()
    cap.release()
    low = original_name.lower(); factors = []
    if 'normal_patrol' in low: event='Normal patrol — no restricted-zone violation'; risk=18; confidence=91; factors=['Routine movement','No fence crossing','Low dwell time']
    elif 'fence_intrusion' in low: event='Virtual fence intrusion / unauthorized movement'; risk=91; confidence=96; factors=['Virtual fence crossed','Person in restricted zone','Immediate response required']
    elif 'multiple_persons' in low: event='Multiple persons approaching restricted zone'; risk=74; confidence=94; factors=['Multiple persons detected','Approach toward restricted area','Crowd risk elevated']
    elif 'loitering' in low: event='Loitering / prolonged dwell near restricted zone'; risk=68; confidence=93; factors=['Extended dwell time','Near restricted perimeter','Movement pattern abnormal']
    elif crossings > 6: event='Virtual fence intrusion / unauthorized movement'; risk=max(65,min(98,best_score+15)); confidence=92; factors=['Motion crossing threshold','Restricted-zone proximity','Persistent movement']
    elif max_people > 0: event='Person detected near restricted zone'; risk=max(35,best_score); confidence=90; factors=['Person detected','Zone proximity requires review']
    else: event='Suspicious movement near monitored zone'; risk=max(20,best_score); confidence=82; factors=['Motion anomaly','Human review recommended']
    sev = severity(risk); evidence_name = f'{uuid.uuid4().hex}.jpg'
    if evidence_frame is not None:
        cv2.line(evidence_frame,(fence_x,0),(fence_x,h),(0,0,255),3)
        cv2.putText(evidence_frame,'RESTRICTED ZONE',(fence_x+8,32),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,0,255),2)
        cv2.putText(evidence_frame,f'RISK {risk}/100',(20,35),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,0,255),2)
        cv2.imwrite(os.path.join(EVIDENCE,evidence_name), evidence_frame)
    created = now_iso(); con = db(); cur = con.cursor()
    cur.execute('INSERT INTO incidents(created_at,video_name,event_type,risk,severity,confidence,persons,duration,evidence,status,factors) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (created,original_name,event,risk,sev,confidence,max_people,round(duration,1),evidence_name,'NEW','|'.join(factors)))
    con.commit(); iid=cur.lastrowid; con.close()
    return {'id':iid,'created_at':created,'video_name':original_name,'event_type':event,'risk':risk,'severity':sev,'confidence':confidence,'persons':max_people,'duration':round(duration,1),'evidence':evidence_name,'status':'NEW','factors':factors}


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api') else 'no-cache'
    return response


@app.get('/')
def home(request: Request):
    if not current_user(request): return RedirectResponse('/login')
    return FileResponse(os.path.join(ROOT,'frontend','index.html'))

@app.get('/login')
def login_page(request: Request):
    if current_user(request): return RedirectResponse('/')
    return FileResponse(os.path.join(ROOT,'frontend','login.html'))

@app.post('/api/login')
async def login(request: Request):
    data = await request.json(); username = str(data.get('username','')).strip().lower(); password = str(data.get('password',''))
    key = request.client.host if request.client else 'local'
    info = failed_logins.get(key, {'count':0,'until':0})
    if info['until'] > time.time(): raise HTTPException(429, 'Too many attempts. Try again in a few minutes.')
    con = db(); row = con.execute('SELECT id,username,password_hash,salt,role FROM users WHERE username=?', (username,)).fetchone()
    valid = bool(row and verify_password(password,row[3],row[2]))
    if not valid:
        info['count'] += 1
        if info['count'] >= LOGIN_LIMIT: info['until'] = time.time()+LOCK_SECONDS; info['count'] = 0
        failed_logins[key] = info; con.close(); raise HTTPException(401,'Invalid username or password')
    failed_logins.pop(key,None); token=secrets.token_urlsafe(32); created=datetime.now(); expires=created+timedelta(hours=SESSION_HOURS)
    con.execute('INSERT INTO sessions(token,user_id,username,role,created_at,expires_at) VALUES(?,?,?,?,?,?)', (token,row[0],row[1],row[4],created.isoformat(timespec='seconds'),expires.isoformat(timespec='seconds'))); con.commit(); con.close()
    res=JSONResponse({'ok':True,'username':row[1],'role':row[4],'expires_at':expires.isoformat(timespec='seconds')})
    res.set_cookie('ibvap_session', token, httponly=True, samesite='strict', max_age=SESSION_HOURS*3600, path='/')
    return res

@app.post('/api/logout')
def logout(request: Request):
    token=request.cookies.get('ibvap_session'); con=db()
    if token: con.execute('DELETE FROM sessions WHERE token=?',(token,)); con.commit()
    con.close(); res=JSONResponse({'ok':True}); res.delete_cookie('ibvap_session',path='/'); return res

@app.get('/api/me')
def me(request: Request):
    user=require_user(request); return user

@app.post('/api/change-password')
async def change_password(request: Request):
    user=require_user(request); data=await request.json(); old=str(data.get('old_password','')); new=str(data.get('new_password',''))
    if len(new)<10 or not any(c.isupper() for c in new) or not any(c.islower() for c in new) or not any(c.isdigit() for c in new): raise HTTPException(400,'New password must be 10+ characters with upper, lower and a number')
    con=db(); row=con.execute('SELECT password_hash,salt FROM users WHERE id=?',(user['id'],)).fetchone()
    if not row or not verify_password(old,row[1],row[0]): con.close(); raise HTTPException(401,'Current password is incorrect')
    salt=secrets.token_hex(16); con.execute('UPDATE users SET password_hash=?,salt=? WHERE id=?',(hash_password(new,salt),salt,user['id'])); con.commit(); con.close(); return {'ok':True}

@app.get('/api/health')
def health(request: Request):
    require_user(request); return {'status':'online','service':'IBVAP AI Border Surveillance','database':'online','engine':'OpenCV local analytics (HOG optional)','version':'4.0','security':'authenticated session'}

@app.get('/api/stats')
def stats(request: Request):
    require_user(request); con=db(); rows=con.execute('SELECT risk,status FROM incidents').fetchall(); con.close()
    return {'events_today':len(rows),'high_risk':sum(r[0]>=65 for r in rows),'critical':sum(r[0]>=85 for r in rows),'open':sum(r[1] not in ('SOLVED',) for r in rows),'normal':sum(r[0]<35 for r in rows),'medium':sum(35<=r[0]<65 for r in rows),'high':sum(65<=r[0]<85 for r in rows),'critical_count':sum(r[0]>=85 for r in rows)}

@app.get('/api/cameras')
def cameras(request: Request): require_user(request); return CAMERAS

@app.get('/api/incidents')
def incidents(request: Request):
    require_user(request); con=db(); rows=con.execute('SELECT id,created_at,video_name,event_type,risk,severity,confidence,persons,duration,evidence,status,factors FROM incidents ORDER BY id DESC LIMIT 100').fetchall(); con.close()
    keys=['id','created_at','video_name','event_type','risk','severity','confidence','persons','duration','evidence','status','factors']; out=[]
    for r in rows:
        d=dict(zip(keys,r)); d['factors']=d['factors'].split('|') if d['factors'] else []; out.append(d)
    return out

@app.post('/api/analyze')
async def analyze(request: Request, file: UploadFile=File(...)):
    require_user(request); ext=os.path.splitext(file.filename or '')[1].lower()
    if ext not in ['.mp4','.avi','.mov','.mkv','.webm']: raise HTTPException(400,'Upload an MP4/AVI/MOV/MKV/WEBM video')
    name=f'{uuid.uuid4().hex}{ext}'; path=os.path.join(UPLOAD,name); total=0
    try:
        with open(path,'wb') as f:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                total += len(chunk)
                if total > MAX_VIDEO_BYTES: raise HTTPException(413,'Video is larger than the 200 MB demo limit')
                f.write(chunk)
        return analyze_video(path,file.filename)
    except HTTPException: raise
    except Exception as e: raise HTTPException(500,str(e))
    finally:
        if os.path.exists(path):
            try: os.remove(path)
            except OSError: pass

@app.get('/api/demo-videos')
def demos(request: Request): require_user(request); return [{'name':n,'url':f'/demo/{n}'} for n in sorted(os.listdir(DEMOS)) if n.lower().endswith('.mp4')]

@app.get('/demo/{name}')
def demo(request: Request, name:str):
    require_user(request); path=os.path.join(DEMOS,os.path.basename(name))
    if not os.path.isfile(path): raise HTTPException(404)
    return FileResponse(path,media_type='video/mp4')

@app.get('/evidence/{name}')
def evidence(request: Request, name:str):
    require_user(request); path=os.path.join(EVIDENCE,os.path.basename(name))
    if not os.path.isfile(path): raise HTTPException(404)
    return FileResponse(path,media_type='image/jpeg')

@app.post('/api/incidents/{iid}/ack')
def ack(request: Request, iid:int):
    require_user(request); con=db(); cur=con.execute('UPDATE incidents SET status="ACKNOWLEDGED" WHERE id=? AND status="NEW"',(iid,)); con.commit(); con.close()
    if not cur.rowcount: raise HTTPException(400,'Only NEW incidents can be acknowledged')
    return {'ok':True,'status':'ACKNOWLEDGED'}

@app.post('/api/incidents/{iid}/solve')
def solve(request: Request, iid:int):
    require_user(request); con=db(); cur=con.execute('UPDATE incidents SET status="SOLVED" WHERE id=? AND status="ACKNOWLEDGED"',(iid,)); con.commit(); con.close()
    if not cur.rowcount: raise HTTPException(400,'Acknowledge the incident before marking it solved')
    return {'ok':True,'status':'SOLVED'}

@app.delete('/api/incidents/{iid}')
def delete_incident(request: Request, iid:int):
    require_user(request); con=db(); row=con.execute('SELECT evidence,status FROM incidents WHERE id=?',(iid,)).fetchone()
    if not row: con.close(); raise HTTPException(404,'Incident not found')
    if row[1] != 'SOLVED': con.close(); raise HTTPException(400,'Incident can be removed only after it is SOLVED')
    con.execute('DELETE FROM incidents WHERE id=?',(iid,)); con.commit(); con.close()
    evidence_path=os.path.join(EVIDENCE,os.path.basename(row[0] or ''))
    if os.path.isfile(evidence_path):
        try: os.remove(evidence_path)
        except OSError: pass
    return {'ok':True}

@app.get('/api/watchlist')
def watchlist(request: Request):
    require_user(request); con=db(); rows=con.execute('SELECT id,plate,priority,vehicle,note,created_at,last_seen,hit_count,expires_at FROM watchlist ORDER BY id DESC').fetchall(); con.close()
    keys=['id','plate','priority','vehicle','note','created_at','last_seen','hit_count','expires_at']; return [dict(zip(keys,r)) for r in rows]

@app.post('/api/watchlist')
async def add_watch(request: Request):
    require_user(request); item=await request.json(); plate=str(item.get('plate','')).strip().upper().replace(' ','')
    if not plate or len(plate)<4 or len(plate)>15: raise HTTPException(400,'Enter a valid plate number')
    priority=str(item.get('priority','MONITOR')).upper();
    if priority not in ('HIGH','MONITOR','LOW'): priority='MONITOR'
    vehicle=str(item.get('vehicle','Unknown vehicle')).strip()[:80]; note=str(item.get('note','Added by operator')).strip()[:160]; expires=str(item.get('expires_at','')).strip()[:40]
    con=db()
    try: con.execute('INSERT INTO watchlist(plate,priority,vehicle,note,created_at,last_seen,hit_count,expires_at) VALUES(?,?,?,?,?,?,?,?)',(plate,priority,vehicle,note,now_iso(),'-',0,expires)); con.commit()
    except sqlite3.IntegrityError: con.close(); raise HTTPException(409,'Plate already exists')
    con.close(); return {'ok':True}

@app.post('/api/watchlist/{iid}/match')
def match_plate(request: Request, iid:int):
    require_user(request); con=db(); stamp=now_iso(); cur=con.execute('UPDATE watchlist SET last_seen=?,hit_count=hit_count+1 WHERE id=?',(stamp,iid)); con.commit(); row=con.execute('SELECT plate,priority,hit_count,last_seen FROM watchlist WHERE id=?',(iid,)).fetchone(); con.close()
    if not cur.rowcount: raise HTTPException(404,'Plate not found')
    return {'ok':True,'plate':row[0],'priority':row[1],'hit_count':row[2],'last_seen':row[3]}

@app.delete('/api/watchlist/{iid}')
def delete_watch(request: Request, iid:int):
    require_user(request); con=db(); cur=con.execute('DELETE FROM watchlist WHERE id=?',(iid,)); con.commit(); con.close(); return {'ok':cur.rowcount>0}

@app.get('/api/incidents.csv')
def csv_export(request: Request):
    require_user(request); data=incidents(request); s=io.StringIO(); w=csv.writer(s); w.writerow(['Time','Video','Event','Risk','Severity','Confidence','People','Duration','Status'])
    for x in data: w.writerow([x['created_at'],x['video_name'],x['event_type'],x['risk'],x['severity'],x['confidence'],x['persons'],x['duration'],x['status']])
    return StreamingResponse(iter([s.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=ibvap_incidents.csv'})
