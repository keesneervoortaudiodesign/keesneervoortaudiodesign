import numpy as np, scipy.signal as ss
fs=96000; N=fs*6; B=96  # 1 ms blocks
rng=np.random.default_rng(1)
FS=2**23
def pink(n):
    w=rng.standard_normal(n); f=np.fft.rfftfreq(n,1/fs); X=np.fft.rfft(w); X[1:]/=np.sqrt(f[1:]); X[0]=0
    return np.fft.irfft(X,n)
def norm(x,dbfs): return x/np.sqrt(np.mean(x**2))*FS*10**(dbfs/20)
def q(x): return np.clip(np.round(x),-FS,FS-1).astype(np.int64)
def rice_bits(r):
    u=np.where(r>=0,2*r,-2*r-1)
    best=None
    for k in range(0,26):
        b=np.sum(u>>k)+len(u)*(k+1)
        best=b if best is None or b<best else best
    return best+5
def fixed_block(x):  # FLAC fixed predictors order 0..4, per block, 3 bits side info
    best=None
    for o in range(5):
        r=np.diff(x,n=o) if o else x
        # warmup: first o samples verbatim approx 24 bits each
        b=rice_bits(r)+o*24+3
        best=b if best is None or b<best else best
    return best
def lpc_backward(x,order=16,hist=2048):
    # backward adaptive: coefficients from past decoded samples (no side info)
    bits=0; a=np.zeros(order)
    for s in range(0,len(x)-B+1,B):
        if s>=hist:
            h=x[s-hist:s].astype(float)
            r=np.correlate(h,h,'full')[hist-1:hist+order]
            r[0]*=1+1e-9
            try: a=np.linalg.solve(ss.toeplitz(r[:order]),r[1:order+1])
            except: pass
        blk=x[s:s+B]; ctx=x[max(0,s-order):s+B].astype(float)
        pred=np.zeros(B)
        for i in range(B):
            j=i+ (order if s>=order else s)
            past=ctx[j-order:j][::-1] if j>=order else np.zeros(order)
            pred[i]=np.dot(a,past)
        bits+=rice_bits(blk-np.round(pred).astype(np.int64))
    return bits
def code(L,R,method):
    tot=0
    for s in range(0,N-B+1,B):
        l=L[s:s+B]; r=R[s:s+B]
        if method=='fixed':
            lr=fixed_block(l)+fixed_block(r)
            m=(l+r)>>1; sd=l-r
            ms=fixed_block(m)+fixed_block(sd)+1
            tot+=min(lr,ms)+1
    return tot
def lp(x,fc,order=8):
    sos=ss.butter(order,fc,fs=fs,output='sos'); return ss.sosfilt(sos,x)
cases={}
w1,w2=rng.standard_normal(N),rng.standard_normal(N)
cases['A white noise flat to 48k, -18 dBFS, uncorrelated L/R']=(norm(w1,-18),norm(w2,-18))
p1,p2=pink(N),pink(N)
cases['B pink noise to 48k, -18 dBFS']=(norm(p1,-18),norm(p2,-18))
cases['B2 pink noise to 48k, -30 dBFS']=(norm(p1,-30),norm(p2,-30))
c=pink(N); d1,d2=pink(N),pink(N)
cL=norm(lp(0.8*c+0.2*d1,20000),-18)+norm(rng.standard_normal(N),-115)
cR=norm(lp(0.8*c+0.2*d2,20000),-18)+norm(rng.standard_normal(N),-115)
cases['C music-like: pink to 20k, correlated L/R, -115 dBFS noise floor to 48k']=(cL,cR)
e=pink(N); hf=norm(ss.sosfilt(ss.butter(4,20000,'high',fs=fs,output='sos'),rng.standard_normal(N)),-40)
cases['D pink + strong ultrasonic content (-40 dBFS flat 20-48k)']=(norm(e,-18)+hf,norm(pink(N),-18)+hf)
for name,(L,R) in cases.items():
    L,R=q(L),q(R)
    bf=code(L,R,'fixed')/(2*N)
    Lb=lpc_backward(L[:fs])/fs  # 1 s mono sample for speed
    print(f"{name}\n   fixed-pred per 1ms block: {bf:.2f} bits/sample -> ratio {24/bf:.2f}  | backward LPC16 (L ch): {Lb:.2f} b/s -> ratio {24/Lb:.2f}")

print("\nTheoretical bound (Gaussian entropy rate from PSD, per channel):")
for name,(L,R) in cases.items():
    x=q(L).astype(float)
    f,P=ss.welch(x,fs=fs,nperseg=8192)   # one-sided PSD, LSB^2/Hz
    S=P*fs/2   # per-sample variance density normalized so mean(S)=var
    S=np.maximum(S,1e-3)
    h=0.5*np.log2(2*np.pi*np.e)+0.5*np.mean(np.log2(S))
    print(f"  {name[:60]:60s} {h:5.2f} bits/sample -> max ratio {24/h:.2f}")
