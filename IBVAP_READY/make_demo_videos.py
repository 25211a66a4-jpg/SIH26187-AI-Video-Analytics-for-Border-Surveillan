import cv2, os, math
import numpy as np
ROOT=os.path.dirname(os.path.abspath(__file__)); OUT=os.path.join(ROOT,'demo_videos'); os.makedirs(OUT,exist_ok=True)
W,H,FPS=640,360,15

def person(img,x,y,scale=1.0,accent=(230,230,230)):
    x=int(x); y=int(y); s=scale
    cv2.circle(img,(x,y-28),10,int(accent[0]),-1)
    cv2.rectangle(img,(x-8,y-18),(x+8,y+25),int(accent[0]),-1)
    cv2.line(img,(x-5,y+25),(x-18,y+55),accent,5); cv2.line(img,(x+5,y+25),(x+18,y+55),accent,5)
    cv2.line(img,(x-7,y-5),(x-28,y+10),accent,5); cv2.line(img,(x+7,y-5),(x+28,y+10),accent,5)

def base(t, title):
    im=np.zeros((H,W,3),np.uint8); im[:]=[18,27,34]
    # sky/ground
    cv2.rectangle(im,(0,0),(W,190),(55,82,96),-1); cv2.rectangle(im,(0,190),(W,H),(37,43,40),-1)
    for i in range(10): cv2.line(im,(0,210+i*16),(W,210+i*16),(50,57,53),1)
    # fence / restricted line
    fx=370; cv2.line(im,(fx,0),(fx,H),(0,0,210),3)
    for x in range(40,fx,45): cv2.line(im,(x,120),(x,330),(110,115,118),3)
    for yy in (145,185,225,265,305): cv2.line(im,(35,yy,),(fx,yy),(120,125,128),2)
    cv2.rectangle(im,(fx,0),(W,H),(35,24,27),-1) # restricted side
    cv2.putText(im,'SAFE ZONE',(70,35),cv2.FONT_HERSHEY_SIMPLEX,.75,(80,220,170),2)
    cv2.putText(im,'RESTRICTED ZONE',(405,35),cv2.FONT_HERSHEY_SIMPLEX,.62,(90,90,255),2)
    cv2.putText(im,'IBVAP  •  LOCAL CCTV DEMO',(18,H-15),cv2.FONT_HERSHEY_SIMPLEX,.5,(160,175,185),1)
    cv2.putText(im,title,(W-245,H-15),cv2.FONT_HERSHEY_SIMPLEX,.42,(210,215,220),1)
    return im

def make(name,scenario,title):
    path=os.path.join(OUT,name); vw=cv2.VideoWriter(path,cv2.VideoWriter_fourcc(*'mp4v'),FPS,(W,H))
    for f in range(FPS*8):
        t=f/(FPS*8)
        im=base(t,title)
        if scenario==1: # safe patrol outside
            x=100+180*t; person(im,x,250,1,(210,215,220))
        elif scenario==2: # crosses boundary
            x=130+430*t; person(im,x,250,1,(230,230,230))
            if x>365: cv2.putText(im,'FENCE BREACH',(385,80),cv2.FONT_HERSHEY_SIMPLEX,.7,(60,70,255),2)
        elif scenario==3: # two persons approaching
            x1=90+300*t; x2=210+220*t; person(im,x1,245,1,(215,215,215)); person(im,x2,275,.9,(185,195,205))
            if t>.62: cv2.putText(im,'MULTIPLE PERSONS',(390,82),cv2.FONT_HERSHEY_SIMPLEX,.58,(70,90,255),2)
        elif scenario==4: # loitering at fence
            x=335+8*math.sin(t*18); person(im,x,255,1,(230,230,230));
            cv2.putText(im,'DWELL / LOITERING',(390,82),cv2.FONT_HERSHEY_SIMPLEX,.58,(70,90,255),2)
        # timestamp
        cv2.putText(im,f'{f/FPS:04.1f}s',(18,55),cv2.FONT_HERSHEY_SIMPLEX,.5,(220,225,230),1)
        vw.write(im)
    vw.release(); print(path)

make('DEMO_01_Normal_Patrol.mp4',1,'NORMAL PATROL')
make('DEMO_02_Fence_Intrusion.mp4',2,'FENCE INTRUSION')
make('DEMO_03_Multiple_Persons.mp4',3,'MULTIPLE PERSONS')
make('DEMO_04_Loitering_At_Fence.mp4',4,'LOITERING')
