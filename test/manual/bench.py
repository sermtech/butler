import sys, time, wave, array, statistics
import transcribe_cpp as t

def load(path):
    with wave.open(path,"rb") as w:
        assert w.getframerate()==16000 and w.getnchannels()==1 and w.getsampwidth()==2
        pcm16=array.array("h"); pcm16.frombytes(w.readframes(w.getnframes()))
    return array.array("f",(s/32768.0 for s in pcm16))

model_path, audio = sys.argv[1], sys.argv[2]
pcm=load(audio); dur=len(pcm)/16000.0; chunk=int(16000*0.25)
for backend in ("vulkan","cpu"):
    m=t.Model(model_path, backend=backend)
    s=m.session()
    st=s.stream()
    first=None; t0=time.perf_counter()
    for i in range(0,len(pcm),chunk):
        u=st.feed(pcm[i:i+chunk])
        if first is None and st.text().committed.strip():
            first=time.perf_counter()-t0
    st.finalize(); total=time.perf_counter()-t0
    print(f"{backend:6s} audio={dur:.1f}s first_committed={first*1000:.0f}ms full_stream_compute={total*1000:.0f}ms RTF={total/dur:.3f} ({dur/total:.0f}x)")
    st.close() if hasattr(st,'close') else None
    del st,s,m
