"""Verify the server-side Smart Turn port against the APP's golden fixtures.

Same bar the JS port was held to: the fixtures carry real audio, the features
produced by Pipecat's actual Python code, and the expected model score. DSP
ports are exactly the kind of thing that look right and are subtly wrong
(off-by-one frames, pad direction, filter normalization), so this compares
numbers, not vibes.
"""
import base64
import json
import sys
import time
from pathlib import Path

import numpy as np

import turn_engine

FIXTURES = (Path(__file__).resolve().parents[2]
            / "scripts/dashboard/client/src/lib/__fixtures__/smart-turn-golden.json")

FEATURE_TOL = 1e-5   # the JS port lands at 5.5e-6
SCORE_TOL = 1e-3


def f32(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<f4")


def main() -> int:
    data = json.loads(FIXTURES.read_text())
    det = turn_engine.TurnDetector()

    worst_feat = 0.0
    worst_score = 0.0
    failed = False

    for case in data["cases"]:
        audio = f32(case["audioB64"]).astype(np.float64)
        expected = f32(case["featuresB64"])

        got = turn_engine.compute_whisper_log_mel(audio).reshape(-1)
        if got.shape != expected.shape:
            print(f"{case['name']:28} FEATURE SHAPE {got.shape} != {expected.shape}")
            failed = True
            continue
        d = float(np.max(np.abs(got - expected)))
        worst_feat = max(worst_feat, d)

        t0 = time.perf_counter()
        score = det.score(audio)
        ms = (time.perf_counter() - t0) * 1000
        ds = abs(score - case["score"])
        worst_score = max(worst_score, ds)

        ok = d <= FEATURE_TOL and ds <= SCORE_TOL
        failed = failed or not ok
        print(f"{case['name']:28} feat_maxdiff={d:.2e}  score={score:.6f} "
              f"expected={case['score']:.6f} diff={ds:.2e}  {ms:.0f} ms  "
              f"{'ok' if ok else 'FAIL'}")

    print(f"\nworst feature diff {worst_feat:.3e} (tol {FEATURE_TOL:.0e})")
    print(f"worst score   diff {worst_score:.3e} (tol {SCORE_TOL:.0e})")
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
