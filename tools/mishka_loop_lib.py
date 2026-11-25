import numpy as np, sys, subprocess, cv2
from PIL import Image
from scipy import ndimage as nd
T=10.0; N=300; TAU=2*np.pi
G={}
def init(path):
    im8=np.asarray(Image.open(path).convert('RGB'))
    G['im8']=im8; G['im']=im8.astype(np.float32)/255; G['H'],G['W']=im8.shape[:2]
def per(t,c,ph=0): return np.sin(TAU*c*t/T+ph)
def noise(t,cycles,seed):
    r=np.random.default_rng(seed); ph=r.uniform(0,TAU,len(cycles)); a=r.uniform(0.5,1,len(cycles))
    return sum(ai*per(t,c,p) for ai,c,p in zip(a,cycles,ph))/sum(a)
def poly(pts):
    m=np.zeros((G['H'],G['W']),np.uint8); cv2.fillPoly(m,[np.array(pts,np.int32)],1); return m>0
def hsvsplit():
    hsv=cv2.cvtColor(G['im8'],cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    return hsv[...,0]*360/255, hsv[...,1]/255, hsv[...,2]/255
def clean(m,minsz=40,close=2,dil=0):
    m=nd.binary_closing(m,iterations=close); m=nd.binary_fill_holes(m); m=nd.binary_opening(m,iterations=1)
    lab,n=nd.label(m)
    if n==0: return m
    sz=nd.sum(m,lab,range(1,n+1)); m=np.isin(lab,np.where(sz>minsz)[0]+1)
    if dil: m=nd.binary_dilation(m,iterations=dil)
    return m

class Layer:   # in-focus foliage moved over an inpainted background
    def __init__(s,mask,box,amp,seed,pin=None):
        im,im8=G['im'],G['im8']
        x0,y0,x1,y1=box; s.box=box
        inm=nd.binary_dilation(mask,iterations=4).astype(np.uint8)*255
        s.bg=cv2.inpaint(im8[y0:y1,x0:x1].copy(),inm[y0:y1,x0:x1],6,cv2.INPAINT_TELEA).astype(np.float32)/255
        a=np.clip(nd.gaussian_filter(mask.astype(np.float32),0.8)[y0:y1,x0:x1]*1.3,0,1)
        s.pre=np.concatenate([im[y0:y1,x0:x1]*a[...,None],a[...,None]],-1)
        s.yy,s.xx=np.mgrid[y0:y1,x0:x1].astype(np.float32)
        s.amp=amp*(pin(s.xx,s.yy) if pin else 1.0); s.seed=seed
    def apply(s,out,t):
        x0,y0,x1,y1=s.box
        gust=0.6+0.4*per(t,1,s.seed)
        ph=TAU*(s.xx/300.0+s.yy/500.0-2*t/T)+s.seed
        dx=s.amp*gust*(np.sin(ph)+0.35*np.sin(TAU*(s.xx/90.0+s.yy/70.0-5*t/T)+1.1+s.seed))
        dy=0.4*s.amp*gust*np.sin(ph+1.3)
        w=np.stack([nd.map_coordinates(s.pre[...,c],[s.yy-y0-dy,s.xx-x0-dx],order=1,mode='nearest') for c in range(4)],-1)
        a=w[...,3:4]
        out[y0:y1,x0:x1]=s.bg*(1-a)+w[...,:3]

class Soft:    # blurry foreground: smooth warp
    def __init__(s,mask,box,amp,seed,blur=14):
        im=G['im']; x0,y0,x1,y1=box; s.box=box
        s.w=np.clip(nd.gaussian_filter(mask.astype(np.float32),blur)[y0:y1,x0:x1]*1.4,0,1)
        s.src=im[y0:y1,x0:x1].copy()
        s.yy,s.xx=np.mgrid[0:y1-y0,0:x1-x0].astype(np.float32)
        s.amp=amp; s.seed=seed
    def apply(s,out,t):
        x0,y0,x1,y1=s.box
        gust=0.6+0.4*per(t,1,s.seed)
        ph=TAU*((s.xx+x0)/400.0+(s.yy+y0)/600.0-2*t/T)+s.seed
        dx=s.amp*gust*(np.sin(ph)+0.3*np.sin(TAU*((s.xx+x0)/150.0-3*t/T)+s.seed))*s.w
        dy=0.5*s.amp*gust*np.sin(ph+1.2)*s.w
        out[y0:y1,x0:x1]=np.stack([nd.map_coordinates(s.src[...,c],[s.yy-dy,s.xx-dx],order=1,mode='nearest') for c in range(3)],-1)

class Glow:    # flickering light source
    def __init__(s,box,inner,center,radius,seed,k_in=0.10,k_out=0.05):
        im=G['im']; x0,y0,x1,y1=box; s.box=box
        ly,lx=np.mgrid[y0:y1,x0:x1].astype(np.float32)
        lreg=im[y0:y1,x0:x1]
        ix0,iy0,ix1,iy1=inner
        m=np.clip((lreg.mean(-1)-0.45)/0.3,0,1)*((lx>ix0)&(lx<ix1)&(ly>iy0)&(ly<iy1))
        s.m=nd.gaussian_filter(m,1.0)
        s.g=np.exp(-(((lx-center[0])/radius)**2+((ly-center[1])/radius)**2))
        s.seed=seed; s.k=(k_in,k_out)
    def apply(s,out,t):
        x0,y0,x1,y1=s.box
        f=noise(t,[7,11,17,23,31],s.seed)
        g=(s.k[0]*f*s.m+s.k[1]*f*s.g)[...,None]*np.array([1,0.85,0.6],np.float32)
        out[y0:y1,x0:x1]=out[y0:y1,x0:x1]*(1+g)

class Breath:
    def __init__(s,dogmask,box,cx,ytop,ybot,xl,xr,A=2.3,width=280):
        im=G['im']; x0,y0,x1,y1=box; s.box=box
        byy,bxx=np.mgrid[y0:y1,x0:x1].astype(np.float32); s.byy,s.bxx=byy,bxx
        wx=np.clip((bxx-xl[0])/xl[1],0,1)*np.clip((xr[0]-bxx)/xr[1],0,1); wx=wx*wx*(3-2*wx)
        wy=np.clip((ybot-byy)/(ybot-ytop),0,1); wy=wy*wy*(3-2*wy)
        s.bw=nd.gaussian_filter((wx*wy*np.exp(-((bxx-cx)/width)**2)).astype(np.float32),6)
        dA=nd.gaussian_filter(dogmask[y0:y1,x0:x1].astype(np.float32),0.8)
        s.src=np.concatenate([im[y0:y1,x0:x1]*dA[...,None],dA[...,None]],-1)
        s.cx=cx; s.A=A
    def apply(s,out,t):
        x0,y0,x1,y1=s.box
        ph=(3*t/T)%1.0
        b=0.5-0.5*np.cos(np.pi*ph/0.4) if ph<0.4 else 0.5+0.5*np.cos(np.pi*(ph-0.4)/0.6)
        A=s.A*b
        dy=-A*s.bw; dx=0.4*A*s.bw*np.clip((s.bxx-s.cx)/300,-1,1)
        w=np.stack([nd.map_coordinates(s.src[...,c],[s.byy-y0-dy,s.bxx-x0-dx],order=1,mode='nearest') for c in range(4)],-1)
        al=w[...,3:4]
        out[y0:y1,x0:x1]=out[y0:y1,x0:x1]*(1-al)+w[...,:3]

def dogseg(polypts,crop,outpng=None):
    img=cv2.cvtColor(G['im8'],cv2.COLOR_RGB2BGR); H,W=img.shape[:2]
    m=np.full((H,W),cv2.GC_BGD,np.uint8)
    pm=np.zeros((H,W),np.uint8); cv2.fillPoly(pm,[np.array(polypts,np.int32)],1)
    big=cv2.dilate(pm,np.ones((41,41),np.uint8)); small=cv2.erode(pm,np.ones((41,41),np.uint8))
    m[big>0]=cv2.GC_PR_BGD; m[pm>0]=cv2.GC_PR_FGD; m[small>0]=cv2.GC_FGD
    bgd=np.zeros((1,65)); fgd=np.zeros((1,65))
    x0,y0,x1,y1=crop
    sub=m[y0:y1,x0:x1].copy()
    cv2.grabCut(img[y0:y1,x0:x1],sub,None,bgd,fgd,6,cv2.GC_INIT_WITH_MASK)
    dog=np.zeros((H,W),bool); dog[y0:y1,x0:x1]=(sub==cv2.GC_FGD)|(sub==cv2.GC_PR_FGD)
    dog=nd.binary_fill_holes(nd.binary_opening(dog,iterations=2))
    lab,n=nd.label(dog); sz=nd.sum(dog,lab,range(1,n+1)); dog=lab==(np.argmax(sz)+1)
    if outpng:
        vis=img.astype(np.float32)*0.5; vis[dog]+=np.array([120,0,0])
        e=dog^nd.binary_erosion(dog); vis[e]=(0,255,255)
        cv2.imwrite(outpng,np.clip(vis,0,255).astype(np.uint8)[y0:y1,x0:x1])
    return dog

class Blink:
    def __init__(s,mainbox,farbox,farpts,times,far_thr=None,bot_sigma=1.5,far_fill='inpaint'):
        s.bot_sigma=bot_sigma
        im,im8=G['im'],G['im8']
        def eye_setup(box,thr=None):
            x0,y0,x1,y1=box
            hsv=cv2.cvtColor(im8[y0:y1,x0:x1],cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
            v=hsv[...,2]/255; h=hsv[...,0]*360/255; sat=hsv[...,1]/255
            m=(v<0.42)|((h>180)&(h<240)&(sat>0.25)) if thr is None else (v<thr)
            m=nd.binary_closing(m,iterations=2); m=nd.binary_fill_holes(m)
            lab,n=nd.label(m); sz=nd.sum(m,lab,range(1,n+1)); m=lab==(np.argmax(sz)+1)
            return dict(box=box,m=m)
        e=eye_setup(mainbox); e['m']=nd.binary_opening(e['m'],iterations=2); s.eyes=[e]
        s.fe=eye_setup(farbox,far_thr); fx0,fy0,fx1,fy1=farbox; s.fbox=farbox
        fm=nd.binary_dilation(s.fe['m'],iterations=1)
        closed8=cv2.inpaint(im8[fy0-6:fy1+6,fx0-6:fx1+6].copy(),np.pad(fm,6).astype(np.uint8)*255,4,cv2.INPAINT_TELEA)
        fc=closed8.astype(np.float32)[6:-6,6:-6]/255
        if far_fill=='left':   # far eye on a silhouette edge: extend the fur from the left
            fc=im[fy0:fy1,fx0:fx1].copy()
            for yy0 in range(fm.shape[0]):
                cols=np.where(fm[yy0])[0]
                if len(cols): 
                    src=im[fy0+yy0,fx0+max(cols.min()-2,0)]
                    fc[yy0,cols]=src
        fc=np.where(fm[...,None],nd.gaussian_filter(fc,(0.7,0.7,0)),im[fy0:fy1,fx0:fx1])
        pts=np.array(farpts,np.float32)
        yy_,xx_=np.mgrid[fy0:fy1,fx0:fx1].astype(np.float32)
        dmin=np.full(yy_.shape,99.0,np.float32)
        for (ax,ay),(bx,by) in zip(pts[:-1],pts[1:]):
            vx,vy=bx-ax,by-ay; L2=vx*vx+vy*vy
            tt=np.clip(((xx_-ax)*vx+(yy_-ay)*vy)/L2,0,1)
            dmin=np.minimum(dmin,np.hypot(xx_-(ax+tt*vx),yy_-(ay+tt*vy)))
        la=np.clip(1.1-dmin,0,1)*0.75
        s.fclosed=fc*(1-la[...,None])+np.array([0.22,0.13,0.08],np.float32)*la[...,None]
        frow=np.where(fm.any(1))[0]; ftop,fbot=frow.min(),frow.max()
        s.fwipe=np.clip(((yy_-fy0)-ftop)/(fbot-ftop+1),0,1)
        s.fregion=nd.gaussian_filter(nd.binary_dilation(fm,iterations=2).astype(np.float32),0.8)
        s.times=times
    def p(s,t):
        p=0.0
        for tb in s.times:
            dt=t-tb
            if 0<=dt<0.10: p=max(p,(dt/0.10))
            elif 0.10<=dt<0.16: p=max(p,1.0)
            elif 0.16<=dt<0.34: p=max(p,1-((dt-0.16)/0.18))
        return p*p*(3-2*p)
    def apply(s,out,t):
        im=G['im']; p=s.p(t)
        if p<=0: return
        fx0,fy0,fx1,fy1=s.fbox
        al=np.clip((p*1.25-s.fwipe)*4,0,1)*s.fregion
        reg=out[fy0:fy1,fx0:fx1]; reg[:]=reg*(1-al[...,None])+s.fclosed*al[...,None]
        for e in s.eyes:
            x0,y0,x1,y1=e['box']; m=e['m']; hh,ww=m.shape
            has=m.sum(0)>=3
            idx=np.where(has)[0]; cc=np.arange(ww)
            top=np.interp(cc,idx,np.argmax(m,0)[idx]).astype(np.float32)
            bot=np.interp(cc,idx,(hh-1-np.argmax(m[::-1],0))[idx]).astype(np.float32)
            top=nd.gaussian_filter1d(nd.minimum_filter1d(top,5),1.5)-2
            bot=nd.gaussian_filter1d(bot,s.bot_sigma)
            ylid=top+p*(bot-top+0.8)
            B=11.0; PAD=20; y0p=y0-PAD; hh2=hh+PAD
            top=top+PAD; bot=bot+PAD; ylid=ylid+PAD
            Y=np.arange(hh2,dtype=np.float32)[:,None]
            start=top-B
            src=np.where(Y<=ylid, start+(Y-start)*B/(ylid-start+1e-6), Y)
            src=np.where(Y<start, Y, src)
            X=np.broadcast_to(np.arange(ww,dtype=np.float32)[None,:]+x0,(hh2,ww))
            rep=np.stack([nd.map_coordinates(im[...,c],[src+y0p,X],order=1,mode='nearest') for c in range(3)],-1)
            cov=np.clip(ylid-Y+0.5,0,1)*np.clip((Y-start)/6.0,0,1)
            colw=nd.gaussian_filter1d(has.astype(np.float32),3.0)
            colw=np.clip(colw*1.6,0,1)*nd.binary_dilation(has,iterations=3)
            al=(cov*colw[None,:])[...,None]
            reg=out[y0p:y1,x0:x1]; reg[:]=reg*(1-al)+rep*al
            la=np.clip(1.25-np.abs(Y-ylid),0,1)*colw[None,:]*0.8*np.sqrt(p)
            la=la*(Y>=top-1)*has[None,:]
            reg[:]=reg*(1-la[...,None])+np.array([0.20,0.12,0.08],np.float32)*la[...,None]

class Page:
    def __init__(s,corner,p1,p2,polypts,box,R=60.0,LMAX=11.0,events=((3.0,1.0,0.9,1.4),(7.4,0.55,0.7,1.1))):
        im=G['im']; C=np.array(corner,np.float32)
        u1=np.array(p1)-C; u1/=np.linalg.norm(u1); u2=np.array(p2)-C; u2/=np.linalg.norm(u2)
        nb=u1+u2; s.nb=nb/np.linalg.norm(nb); s.C=C
        x0,y0,x1,y1=box; s.box=box
        s.pyy,s.pxx=np.mgrid[y0:y1,x0:x1].astype(np.float32)
        pm=poly(polypts)&(im.mean(-1)>0.55)
        pm=nd.binary_dilation(nd.binary_closing(pm,iterations=1),iterations=1)[y0:y1,x0:x1].astype(np.float32)
        s.pm=pm; pa=nd.gaussian_filter(pm,0.6)
        s.pre=np.concatenate([im[y0:y1,x0:x1]*pa[...,None],pa[...,None]],-1)
        s.R=R; s.LMAX=LMAX; s.events=events
    def w(s,x,y):
        d=(x-s.C[0])*s.nb[0]+(y-s.C[1])*s.nb[1]; q=np.clip(1-d/s.R,0,1); return q*q
    def a(s,t):
        a=0.0
        for tc,amp,rise,fall in s.events:
            dt=t-tc
            if -rise<=dt<0: x=(dt+rise)/rise; a=max(a,amp*x*x*(3-2*x))
            elif 0<=dt<fall: x=1-dt/fall; a=max(a,amp*x*x*(3-2*x))
        return a*(1+0.10*np.sin(TAU*8*t/T))
    def apply(s,out,t):
        a=s.a(t)
        if a<=1e-4: return
        x0,y0,x1,y1=s.box; nb=s.nb
        sx,sy=s.pxx.copy(),s.pyy.copy()
        for _ in range(3):
            L=s.LMAX*a*s.w(sx,sy); sx=s.pxx-nb[0]*L*0.3; sy=s.pyy+L-nb[1]*L*0.3
        w=s.w(sx,sy)
        lay=np.stack([nd.map_coordinates(s.pre[...,c],[sy-y0,sx-x0],order=1,mode='constant') for c in range(4)],-1)
        reg=out[y0:y1,x0:x1]
        sh=nd.gaussian_filter(s.w(s.pxx,s.pyy)*s.pm,3.0); sh=np.roll(np.roll(sh,2,0),2,1)
        reg[:]=reg*(1-0.32*a*sh)[...,None]
        al=lay[...,3:4]; reg[:]=reg*(1-al)+lay[...,:3]*(1+0.07*a*w)[...,None]

class Twinkle:  # small bright points (stars / city lights) that twinkle
    def __init__(s,region,thr,size,seed,depth=(0.35,1.25),cycles=(2,3,4,5)):
        im=G['im']; l=im.mean(-1)
        resid=np.clip(im-np.stack([nd.grey_opening(im[...,c],size=(size,size)) for c in range(3)],-1),0,1)
        pts=(resid.mean(-1)>thr)&region
        lab,n=nd.label(nd.binary_dilation(pts,iterations=1))
        s.n=n
        rng=np.random.default_rng(seed)
        ph=rng.uniform(0,TAU,n+1); cy=rng.choice(cycles,n+1)
        s.ys,s.xs=np.where(lab>0); s.lab=lab[s.ys,s.xs]
        s.res=resid[s.ys,s.xs]; s.ph=ph[s.lab]; s.cy=cy[s.lab]; s.depth=depth
    def apply(s,out,t):
        k=0.5+0.5*np.sin(TAU*s.cy*t/T+s.ph)
        g=s.depth[0]+(s.depth[1]-s.depth[0])*k   # multiplier of the point's own brightness
        out[s.ys,s.xs]+=s.res*(g-1)[:,None]

class Rain:  # falling rain streaks behind/over glass
    def __init__(s,glass,box,n,seed,speed_cycles=(3,4,5),length=(14,30),alpha=0.22):
        x0,y0,x1,y1=box; s.box=box; s.glass=glass[y0:y1,x0:x1]
        rng=np.random.default_rng(seed)
        s.x=rng.uniform(0,x1-x0,n); s.ph=rng.uniform(0,1,n); s.cy=rng.choice(speed_cycles,n)
        s.L=rng.uniform(*length,n); s.a=rng.uniform(0.5,1,n)*alpha
        s.h=y1-y0; s.w=x1-x0; s.slant=0.08
    def apply(s,out,t):
        x0,y0,x1,y1=s.box
        lay=np.zeros((s.h,s.w),np.float32)
        span=s.h+40
        for x,ph,cy,L,a in zip(s.x,s.ph,s.cy,s.L,s.a):
            yh=((ph+cy*t/T)%1.0)*span-20
            for k in range(int(L)):
                y=yh-k; 
                if 0<=y<s.h:
                    xx=x+s.slant*(y)
                    xi=int(xx); fr=xx-xi; yi=int(y)
                    v=a*(1-k/L)
                    if 0<=xi<s.w-1: lay[yi,xi]+=v*(1-fr); lay[yi,xi+1]+=v*fr
        lay=nd.gaussian_filter(lay,0.5)*s.glass
        reg=out[y0:y1,x0:x1]
        reg[:]=reg+(np.array([0.75,0.82,1.0],np.float32)-reg)*np.clip(lay,0,1)[...,None]

def run(frame,argv,W,H,outname='out.mp4'):
    """preview: python3 anim.py preview 0 75 150 299  |  render: python3 anim.py render [out.mp4]
    Output is ALWAYS MP4 at 1920x1080 (landscape) or 1080x1920 (portrait), 30 fps, 300 frames.
    All-intra H.264 (-g 1) so frame 1 and frame 300 decode identically."""
    if argv[1]=='preview':
        for i in map(int,argv[2:]): Image.fromarray(frame(i)).save(f'p{i}.png')
        return
    if len(argv)>2: outname=argv[2]
    size='1920:1080' if W>=H else '1080:1920'
    p=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r','30','-i','-',
      '-vf',f'scale={size}:flags=lanczos,setsar=1','-c:v','libx264','-qp','16','-g','1','-bf','0','-preset','fast',
      '-pix_fmt','yuv420p','-movflags','+faststart',outname],stdin=subprocess.PIPE)
    for i in range(N): p.stdin.write(frame(i).tobytes())
    p.stdin.close(); p.wait()
def verify(path):
    """Checks first==last frame, 300 frames, 10 s, and output size."""
    out=subprocess.run(['ffmpeg','-v','error','-i',path,'-vf',"select='eq(n\\,0)+eq(n\\,299)'",'-vsync','0','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True).stdout
    d=np.frombuffer(out,np.uint8).reshape(2,-1).astype(int)
    info=subprocess.run(['ffprobe','-v','error','-count_frames','-show_entries','stream=width,height,nb_read_frames','-of','csv=p=0',path],capture_output=True,text=True).stdout.strip()
    print(path,'| first-vs-last max diff:',int(abs(d[0]-d[1]).max()),'| w,h,frames:',info)
def finish(out):
    return (np.clip(out,0,1)*255+0.5).astype(np.uint8)

class Steam:   # existing painted steam: separate from background and make it drift upward
    def __init__(s,box,source_y,excl=None,diff_thr=0.03,sat_thr=0.45,amp=3.0,move_to=None):
        im,im8=G['im'],G['im8']; x0,y0,x1,y1=box; s.box=box
        reg=im[y0:y1,x0:x1]; hh,ww=reg.shape[:2]
        op=np.stack([nd.grey_opening(reg[...,c],size=(25,25)) for c in range(3)],-1)
        sat=(reg.max(-1)-reg.min(-1))/(reg.max(-1)+1e-6)
        cand=((reg-op).mean(-1)>diff_thr)&(sat<sat_thr)
        yy_,xx_=np.mgrid[0:hh,0:ww]
        cand&=yy_<(source_y-y0)
        if excl is not None: cand&=~excl[y0:y1,x0:x1]
        cand=nd.binary_dilation(nd.binary_opening(cand,iterations=1),iterations=5)
        bg8=cv2.inpaint(im8[y0:y1,x0:x1].copy(),(cand*255).astype(np.uint8),9,cv2.INPAINT_TELEA)
        bg=bg8.astype(np.float32)/255
        bg=np.where(cand[...,None],nd.gaussian_filter(bg,(4,4,0)),reg)
        S=np.array([0.97,0.93,0.86],np.float32); dS=S-bg
        al=np.clip(((reg-bg)*dS).sum(-1)/((dS*dS).sum(-1)+1e-4),0,1)*cand
        edge=np.clip(np.minimum.reduce([xx_/10,(ww-1-xx_)/10,yy_/10,(source_y-y0-yy_)/4]),0,1)
        al=nd.gaussian_filter(al*edge,0.6); al=np.where(al<0.04,0,al)
        s.bg=bg; s.al=al; s.C=reg-bg*(1-al[...,None]); s.C*=(al[...,None]>0)
        s.syy,s.sxx=np.mgrid[0:hh,0:ww].astype(np.float32)
        s.up=np.clip((source_y-y0-s.syy)/150.0,0,1); s.amp=amp; s.mask=cand
        s.off=(0,0)
        if move_to is not None:   # relocate the steam so its base sits at move_to (e.g. the spout)
            rows=np.where(al.sum(1)>0.5)[0]; b=rows.max(); band=al[max(b-15,0):b+1]
            bx=(band.sum(0)*np.arange(ww)).sum()/band.sum()+x0
            s.off=(int(round(move_to[0]-bx)),int(round(move_to[1]-(b+y0))))
    def apply(s,out,t):
        x0,y0,x1,y1=s.box; syy,sxx=s.syy,s.sxx
        ph=TAU*(syy/60.0+3*t/T)
        dx=s.amp*(np.sin(ph)+0.45*np.sin(TAU*(syy/26.0+4*t/T)+1.3))*s.up**1.1
        dy=-0.6*s.amp*s.up*np.sin(TAU*(syy/45.+2*t/T))
        cs=[syy+dy,sxx-dx]
        aw=nd.map_coordinates(s.al,cs,order=1,mode='constant')
        Cw=np.stack([nd.map_coordinates(s.C[...,c],cs,order=1,mode='constant') for c in range(3)],-1)
        op=0.82+0.22*np.sin(TAU*(syy/55.0+3*t/T)+0.5)
        aw=np.clip(aw*op,0,1); Cw=Cw*op[...,None]
        reg=out[y0:y1,x0:x1]
        if s.off==(0,0):
            reg[:]=reg+s.bg*(s.al-aw)[...,None]+(Cw-s.C); return
        reg[:]=reg+s.bg*s.al[...,None]-s.C            # remove painted steam
        ox,oy=s.off; tr=out[y0+oy:y1+oy,x0+ox:x1+ox]
        tr[:]=tr*(1-aw[...,None])+Cw                    # draw it at the new base

def remove_soft(region,protect=None,scale=6,feather=6,grain=0.006,seed=0,method='fsr',detail_dx=None,detail_ok=None):
    """Remove painted haze/steam over a blurry (bokeh) background.
    region: bool mask of what to erase (generous, include the haze). protect: bool mask of
    objects that must stay (teapot, handle...). Fill = low-res inpaint (smooth like bokeh). Updates G."""
    im=G['im']; H,W=G['H'],G['W']
    hole=region|(protect if protect is not None else False)
    hole=nd.binary_dilation(hole,iterations=3)
    # texture-aware fill (FSR) on a crop around the hole, at half resolution
    ys,xs=np.where(hole); pad=90
    y0,y1=max(ys.min()-pad,0),min(ys.max()+pad,H); x0,x1=max(xs.min()-pad,0),min(xs.max()+pad,W)
    crop=(np.clip(im[y0:y1,x0:x1],0,1)*255).astype(np.uint8); hc=hole[y0:y1,x0:x1]
    sc=2; cw,ch=(x1-x0)//sc,(y1-y0)//sc
    cs=cv2.resize(crop,(cw,ch),interpolation=cv2.INTER_AREA)
    hm=(cv2.resize(hc.astype(np.uint8),(cw,ch),interpolation=cv2.INTER_NEAREST)|cv2.resize(hc.astype(np.uint8),(cw,ch),interpolation=cv2.INTER_AREA))
    hm=cv2.dilate(hm,np.ones((3,3),np.uint8))
    valid=(1-hm).astype(np.uint8)
    dst=np.zeros_like(cs)
    if method=='fsr': cv2.xphoto.inpaint(cs,valid,dst,cv2.xphoto.INPAINT_FSR_FAST)
    else:
        dst=cv2.inpaint(cs,hm*255,9,cv2.INPAINT_TELEA); dst=cv2.GaussianBlur(dst,(0,0),2.0)
    f=cv2.resize(dst,(x1-x0,y1-y0),interpolation=cv2.INTER_CUBIC).astype(np.float32)/255
    fill=im.copy(); fill[y0:y1,x0:x1]=cv2.GaussianBlur(f,(0,0),0.8)
    if detail_dx is not None:   # borrow bokeh/grass detail (high-pass) from a clean area shifted horizontally
        hp=im-cv2.GaussianBlur(im,(0,0),4)
        ok=np.ones((H,W),np.float32) if detail_ok is None else nd.gaussian_filter(detail_ok.astype(np.float32),2)
        dxs=detail_dx if isinstance(detail_dx,(list,tuple)) else [detail_dx]
        acc=np.zeros_like(hp); wsum=np.zeros((H,W),np.float32)
        for dx in dxs:
            w=np.roll(ok,dx,axis=1); acc+=np.roll(hp*ok[...,None],dx,axis=1); wsum+=w
        fill=fill+acc/np.maximum(wsum,1e-3)[...,None]*np.clip(wsum,0,1)[...,None]*0.9
    rng=np.random.default_rng(seed)
    fill+=nd.gaussian_filter(rng.normal(0,1,(H,W)).astype(np.float32),0.7)[...,None]*grain
    a=nd.gaussian_filter(nd.binary_dilation(region,iterations=feather).astype(np.float32),feather/2)
    if protect is not None: a*=1-nd.gaussian_filter(protect.astype(np.float32),0.8)
    out=im*(1-a[...,None])+fill*a[...,None]
    G['im']=out; G['im8']=(np.clip(out,0,1)*255+0.5).astype(np.uint8)

class SpoutSteam:  # procedural loopable steam rising from a point (spout tip)
    def __init__(s,tip,height=190,w0=5,w1=34,lean=-18,alpha=0.5,amp=7,seed=3,occl=None,col=(0.98,0.94,0.88)):
        s.tip=tip; s.h=height; s.w0=w0; s.w1=w1; s.lean=lean; s.alpha=alpha; s.amp=amp; s.col=np.array(col,np.float32)
        x0=int(tip[0]-w1-abs(lean)-amp-30); x1=int(tip[0]+w1+abs(lean)+amp+30)
        y0=int(tip[1]-height-10); y1=int(tip[1]+4)
        s.box=(x0,y0,x1,y1)
        s.yy,s.xx=np.mgrid[y0:y1,x0:x1].astype(np.float32)
        s.hh=np.clip((tip[1]-s.yy)/height,0,1.2)          # 0 at tip, 1 at top
        rng=np.random.default_rng(seed)
        P=128; s.P=P
        n=rng.normal(0,1,(P,P)).astype(np.float32)
        n=nd.gaussian_filter(n,(7,2.2),mode='wrap'); n=(n-n.min())/(n.max()-n.min())
        s.tex=n
        s.occl=None if occl is None else occl[y0:y1,x0:x1].astype(np.float32)
    def apply(s,out,t):
        x0,y0,x1,y1=s.box; h=s.hh
        ph=TAU*(h*1.8-2*t/T)
        cx=s.tip[0]+s.lean*h**1.3+s.amp*h**0.9*(np.sin(ph)+0.4*np.sin(TAU*(h*3.7-3*t/T)+1.1))
        w=s.w0+(s.w1-s.w0)*h**0.9
        d=(s.xx-cx)/w
        prof=np.exp(-d*d*2.2)
        # texture flows upward: sample tileable noise at (v - k*t)
        v=(s.tip[1]-s.yy); u=(s.xx-cx)
        vv=(v*0.9+ 2*s.P*t/T)%s.P; uu=(u*1.6+s.P*0.5)%s.P
        tx=nd.map_coordinates(s.tex,[vv,uu],order=1,mode='wrap')
        dens=prof*(0.45+1.1*tx**1.3)
        fade=np.clip(h/0.012,0,1)*np.clip((1-h)/0.65,0,1)**1.2
        a=np.clip(dens*fade*s.alpha,0,1)
        a=nd.gaussian_filter(a,1.0)
        if s.occl is not None: a*=1-s.occl
        reg=out[y0:y1,x0:x1]
        reg[:]=reg*(1-a[...,None])+s.col*a[...,None]

class SteamWarp:  # animate painted steam in place by warping its pixels (works for tinted steam on dark bg)
    def __init__(s,box,source_y,amp=3.0,thr=0.04):
        im=G['im']; x0,y0,x1,y1=box; s.box=box
        reg=im[y0:y1,x0:x1]; s.src=reg.copy()
        op=np.stack([nd.grey_opening(reg[...,c],size=(21,21)) for c in range(3)],-1)
        m=((reg-op).mean(-1)>thr)
        yy_,xx_=np.mgrid[0:y1-y0,0:x1-x0]
        m&=yy_<(source_y-y0)
        m=nd.binary_dilation(nd.binary_opening(m,iterations=1),iterations=6)
        edge=np.clip(np.minimum.reduce([xx_/8,(x1-x0-1-xx_)/8,yy_/8,(source_y-y0-yy_)/3]),0,1)
        s.w=nd.gaussian_filter(m.astype(np.float32),5)*edge
        s.yy,s.xx=yy_.astype(np.float32),xx_.astype(np.float32)
        s.up=np.clip((source_y-y0-s.yy)/(source_y-y0),0,1); s.amp=amp
    def apply(s,out,t):
        x0,y0,x1,y1=s.box
        ph=TAU*(s.yy/40.0+2*t/T)
        dx=s.amp*s.up*(np.sin(ph)+0.4*np.sin(TAU*(s.yy/18.0+3*t/T)+1.2))*s.w
        dy=0.5*s.amp*s.up*np.sin(TAU*(s.yy/30.0+2*t/T)+0.7)*s.w
        w=np.stack([nd.map_coordinates(s.src[...,c],[s.yy+dy,s.xx-dx],order=1,mode='nearest') for c in range(3)],-1)
        f=(0.9+0.12*np.sin(TAU*(s.yy/45.0+2*t/T)))[...,None]
        base=out[y0:y1,x0:x1]
        out[y0:y1,x0:x1]=base*(1-s.w[...,None])+w*s.w[...,None]*f+base*s.w[...,None]*(1-f)

class Fire:   # painted fireplace flames: upward-flowing warp + flicker, masked to the flames
    def __init__(s,mask,box,amp=3.0,lift=4.0,seed=0,glow=None):
        im=G['im']; x0,y0,x1,y1=box; s.box=box
        s.src=im[y0:y1,x0:x1].copy()
        s.w=nd.gaussian_filter(mask[y0:y1,x0:x1].astype(np.float32),3)
        hh=y1-y0
        s.yy,s.xx=np.mgrid[0:hh,0:x1-x0].astype(np.float32)
        base=np.where(mask[y0:y1,x0:x1].any(1))[0].max() if mask[y0:y1,x0:x1].any() else hh
        s.up=np.clip((base-s.yy)/max(base,1),0,1)
        s.amp=amp; s.lift=lift; s.glow=glow; s.seed=seed
    def apply(s,out,t):
        x0,y0,x1,y1=s.box; yy,xx=s.yy,s.xx
        ph=TAU*(yy/38.0+6*t/T)
        dx=s.amp*s.up*(np.sin(ph+xx/23.0)+0.5*np.sin(TAU*(yy/15.0+9*t/T)+xx/11.0))
        dy=s.lift*s.up*(0.5+0.5*np.sin(TAU*(yy/28.0+5*t/T)+xx/31.0))
        w=np.stack([nd.map_coordinates(s.src[...,c],[yy+dy,xx-dx],order=1,mode='nearest') for c in range(3)],-1)
        f=1+0.06*np.sin(TAU*(7*t/T)+xx/40.0)*s.up+0.04*np.sin(TAU*(11*t/T)+1.3)
        m=s.w[...,None]
        reg=out[y0:y1,x0:x1]
        reg[:]=reg*(1-m)+np.clip(w*f[...,None],0,1)*m
