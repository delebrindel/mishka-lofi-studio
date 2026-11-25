# Extensions to mishka_loop_lib.py (added for 02 Mishka Christmas / Christmas Morning).
# Usage: from mishka_loop_lib import *; then paste/import these classes.
# Snow: loopable falling snow clipped to a glass mask (build mask: window box minus wood (hue 5-50, sat>.35, v<.62),
#       minus title text (R-B>.10 & bright, inside title bbox, dilated 3) and holly).
# EyeBlink: blink for a single eye (profile shots); use thr=0.25 for a tiny far eye on a silhouette edge.
import numpy as np, cv2
from scipy import ndimage as nd
from mishka_loop_lib import G, T, TAU

class Snow:   # loopable falling snow, clipped to a mask (window glass)
    def __init__(s,mask,n,seed,size=(0.8,2.4),alpha=(0.45,0.95),fall_cycles=(1,2),sway=(4,12),col=(0.96,0.97,1.0)):
        ys,xs=np.where(mask); s.box=(xs.min(),ys.min(),xs.max()+1,ys.max()+1)
        x0,y0,x1,y1=s.box; s.m=nd.gaussian_filter(mask[y0:y1,x0:x1].astype(np.float32),0.7)
        s.h=y1-y0; s.w=x1-x0
        r=np.random.default_rng(seed)
        s.x=r.uniform(0,s.w,n); s.ph=r.uniform(0,1,n)
        sz=r.uniform(0,1,n)**2; s.r=size[0]+(size[1]-size[0])*sz      # few big near flakes
        s.cy=np.where(sz>0.45,fall_cycles[-1],fall_cycles[0])           # bigger = nearer = faster
        s.a=alpha[0]+(alpha[1]-alpha[0])*sz
        s.sw=r.uniform(*sway,n)*(0.5+sz); s.sc=r.choice([1,2],n); s.sp=r.uniform(0,TAU,n)
        s.col=np.array(col,np.float32)
    def apply(s,out,t):
        x0,y0,x1,y1=s.box; span=s.h+20
        lay=np.zeros((s.h,s.w),np.float32)
        yc=((s.ph+s.cy*t/T)%1.0)*span-10
        xc=s.x+s.sw*np.sin(TAU*s.sc*t/T+s.sp)
        for X,Y,R,A in zip(xc,yc,s.r,s.a):
            rr=int(np.ceil(R*2.2)); xi,yi=int(X),int(Y)
            ya,yb=max(yi-rr,0),min(yi+rr+1,s.h); xa,xb=max(xi-rr,0),min(xi+rr+1,s.w)
            if ya>=yb or xa>=xb: continue
            gy,gx=np.mgrid[ya:yb,xa:xb]
            d2=(gx-X)**2+(gy-Y)**2
            lay[ya:yb,xa:xb]=np.maximum(lay[ya:yb,xa:xb],A*np.exp(-d2/(2*(R*0.6)**2)))
        a=(np.clip(lay,0,1)*s.m)[...,None]
        reg=out[y0:y1,x0:x1]; reg[:]=reg*(1-a)+s.col*a


class EyeBlink:  # single visible eye (profile): upper lid slides down over the eye
    def __init__(s,box,times,thr=None,bot_sigma=1.5,lid=(0.20,0.12,0.08)):
        im8=G['im8']; x0,y0,x1,y1=box; s.box=box
        hsv=cv2.cvtColor(im8[y0:y1,x0:x1],cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
        v=hsv[...,2]/255; h=hsv[...,0]*360/255; sat=hsv[...,1]/255
        m=(v<0.42)|((h>170)&(h<250)&(sat>0.25)) if thr is None else (v<thr)
        m=nd.binary_closing(m,iterations=2); m=nd.binary_fill_holes(m)
        lab,n=nd.label(m); sz=nd.sum(m,lab,range(1,n+1)); m=lab==(np.argmax(sz)+1)
        s.m=nd.binary_opening(m,iterations=1); s.times=times; s.bot_sigma=bot_sigma; s.lid=np.array(lid,np.float32)
    def p(s,t):
        p=0.0
        for tb in s.times:
            dt=t-tb
            if 0<=dt<0.10: p=max(p,dt/0.10)
            elif 0.10<=dt<0.16: p=max(p,1.0)
            elif 0.16<=dt<0.34: p=max(p,1-(dt-0.16)/0.18)
        return p*p*(3-2*p)
    def apply(s,out,t):
        im=G['im']; p=s.p(t)
        if p<=0: return
        x0,y0,x1,y1=s.box; m=s.m; hh,ww=m.shape
        has=m.sum(0)>=2; idx=np.where(has)[0]; cc=np.arange(ww)
        top=np.interp(cc,idx,np.argmax(m,0)[idx]).astype(np.float32)
        bot=np.interp(cc,idx,(hh-1-np.argmax(m[::-1],0))[idx]).astype(np.float32)
        top=nd.gaussian_filter1d(nd.minimum_filter1d(top,5),1.5)-2
        bot=nd.gaussian_filter1d(bot,s.bot_sigma)
        ylid=top+p*(bot-top+0.8)
        B=11.0; PAD=20; y0p=y0-PAD; hh2=hh+PAD
        top=top+PAD; bot=bot+PAD; ylid=ylid+PAD
        Y=np.arange(hh2,dtype=np.float32)[:,None]; start=top-B
        src=np.where(Y<=ylid, start+(Y-start)*B/(ylid-start+1e-6), Y); src=np.where(Y<start,Y,src)
        X=np.broadcast_to(np.arange(ww,dtype=np.float32)[None,:]+x0,(hh2,ww))
        rep=np.stack([nd.map_coordinates(im[...,c],[src+y0p,X],order=1,mode='nearest') for c in range(3)],-1)
        cov=np.clip(ylid-Y+0.5,0,1)*np.clip((Y-start)/6.0,0,1)
        colw=nd.gaussian_filter1d(has.astype(np.float32),3.0); colw=np.clip(colw*1.6,0,1)*nd.binary_dilation(has,iterations=3)
        al=(cov*colw[None,:])[...,None]
        reg=out[y0p:y1,x0:x1]; reg[:]=reg*(1-al)+rep*al
        la=np.clip(1.25-np.abs(Y-ylid),0,1)*colw[None,:]*0.8*np.sqrt(p); la=la*(Y>=top-1)*has[None,:]
        reg[:]=reg*(1-la[...,None])+s.lid*la[...,None]
