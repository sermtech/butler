"""Drive the engine directly (no server/ws) through TWO turns to isolate the
multi-turn fix from the transport."""
import sys, wave, time, threading
import stt_engine

WAV = r"C:\PROJECTS\other-project\transcribe.cpp\samples\jfk.wav"
with wave.open(WAV, "rb") as w:
    PCM = w.readframes(w.getnframes())

events = []
final_ev = threading.Event()

def emit(kind, payload):
    events.append((kind, payload))
    if kind == "final":
        final_ev.set()

def one_turn(sess, n):
    global events
    events = []
    final_ev.clear()
    frame = 512 * 2
    for i in range(0, len(PCM), frame):
        sess.feed(PCM[i:i + frame])
        time.sleep(0.004)          # faster than realtime, just pacing
    sess.finalize()
    ok = final_ev.wait(timeout=30)
    partials = sum(1 for k, _ in events if k == "partial")
    final = next((p for k, p in events if k == "final"), None)
    print(f"TURN {n}: got_final={ok} partials={partials} text={final['text'][:45] if final else None!r}")
    return ok and partials >= 3 and final and final["text"]

sess = stt_engine.StreamingSession(
    slug="parakeet-unified-en-0.6b", quant=None, backend="auto",
    latency="low", language=None, emit=emit)
r1 = one_turn(sess, 1)
time.sleep(0.3)
r2 = one_turn(sess, 2)
time.sleep(0.3)
r3 = one_turn(sess, 3)
sess.close()
print("RESULT:", "PASS" if (r1 and r2 and r3) else "FAIL")
sys.exit(0 if (r1 and r2 and r3) else 1)
