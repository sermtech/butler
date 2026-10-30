"""The wake detector's arbitration between words (docs/wake-words.md AC-806).

`web/wake.js` is run for real under node with FAKE stages: the mel and
embedding stages return fixed shapes and each word's head returns a scripted
score per chunk, so what is tested is the detector's own rule -- every loaded
word is scored on the same embeddings, only the highest score at or over the
threshold fires and is named, and ONE refractory follows any fire, so two
words never fire for one utterance.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WAKE_JS = ROOT / "web" / "wake.js"

# One chunk is 1280 samples (80 ms). The fake mel stage answers a full
# 76-frame window on every call, so the detector scores from the first chunk.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
globalThis.window = {};
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
const { createWakeWordDetector } = window.WakeWord;
const CHUNK = 1280;

async function run(spec) {
  let chunk = -1;
  const released = [];
  const stages = {
    mel: async () => { chunk += 1; return new Float32Array(76 * 32); },
    embed: async () => new Float32Array(96),
    wake: spec.words.map((id) => ({
      id,
      run: async (feat) => {
        if (feat.length !== 16 * 96) throw new Error("bad feature length");
        return Float32Array.of(spec.scores[chunk][id]);
      },
    })),
    release: async () => { released.push("all"); },
  };
  const det = createWakeWordDetector(stages, {
    threshold: spec.threshold, refractoryMs: spec.refractoryMs,
    chunkSamples: CHUNK });
  const out = [];
  for (let i = 0; i < spec.scores.length; i++) {
    if (spec.setThresholdAt && spec.setThresholdAt[0] === i) {
      det.setThreshold(spec.setThresholdAt[1]);
    }
    const r = await det.feed(new Float32Array(CHUNK));
    out.push({ score: r.score, fired: r.fired, word: r.word });
  }
  await det.close();
  return { out, released };
}

(async () => {
  const specs = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
  const results = {};
  for (const [name, spec] of Object.entries(specs)) results[name] = await run(spec);
  process.stdout.write(JSON.stringify(results));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""

TWO = ["hey_butler", "hey_jarvis"]


def _run(tmp_path: Path, specs: dict) -> dict:
    node = shutil.which("node")
    assert node, "node is not on PATH: the wake detector cannot be run"
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8", newline="")
    spec_file = tmp_path / "specs.json"
    spec_file.write_text(json.dumps(specs), encoding="utf-8")
    proc = subprocess.run([node, str(harness), str(WAKE_JS), str(spec_file)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    s = lambda b, j: {"hey_butler": b, "hey_jarvis": j}  # noqa: E731
    specs = {
        # The higher of two words over the line fires, and is the one named.
        "max_wins": {"words": TWO, "threshold": 0.5, "refractoryMs": 2000,
                     "scores": [s(0.6, 0.9)]},
        # Under the line, nothing fires; the score reported is the max.
        "under": {"words": TWO, "threshold": 0.5, "refractoryMs": 2000,
                  "scores": [s(0.3, 0.45), s(0.49, 0.1)]},
        # Butler fires; Jarvis scoring higher on the next chunks is inside the
        # SHARED refractory (2 chunks = 160 ms) and must not fire; after it
        # expires, it may.
        "shared_refractory": {"words": TWO, "threshold": 0.5,
                              "refractoryMs": 160,
                              "scores": [s(0.9, 0.2), s(0.1, 0.99),
                                         s(0.1, 0.99)]},
        # One word: today's behaviour.
        "single": {"words": ["hey_jarvis"], "threshold": 0.5,
                   "refractoryMs": 2000,
                   "scores": [{"hey_jarvis": 0.2}, {"hey_jarvis": 0.7},
                              {"hey_jarvis": 0.8}]},
        # A threshold change applies to the next chunk without a rebuild.
        "set_threshold": {"words": TWO, "threshold": 0.5,
                          "refractoryMs": 2000, "setThresholdAt": [1, 0.9],
                          "scores": [s(0.4, 0.2), s(0.8, 0.2)]},
    }
    return _run(tmp_path_factory.mktemp("wake"), specs)


def test_the_higher_scoring_word_fires_and_is_named(results):
    [r] = results["max_wins"]["out"]
    assert r == {"score": pytest.approx(0.9), "fired": True,
                 "word": "hey_jarvis"}


def test_a_word_under_the_threshold_never_fires(results):
    out = results["under"]["out"]
    assert [r["fired"] for r in out] == [False, False]
    assert [r["word"] for r in out] == [None, None]
    assert out[0]["score"] == pytest.approx(0.45)
    assert out[1]["score"] == pytest.approx(0.49)


def test_one_refractory_is_shared_by_every_word(results):
    out = results["shared_refractory"]["out"]
    assert out[0]["fired"] and out[0]["word"] == "hey_butler"
    assert not out[1]["fired"], "the other word fired inside the refractory"
    assert out[1]["word"] is None
    assert out[2]["fired"] and out[2]["word"] == "hey_jarvis"


def test_a_single_word_behaves_as_before(results):
    out = results["single"]["out"]
    assert [r["fired"] for r in out] == [False, True, False]
    assert [r["word"] for r in out] == [None, "hey_jarvis", None]


def test_a_threshold_change_needs_no_rebuild(results):
    out = results["set_threshold"]["out"]
    assert [r["fired"] for r in out] == [False, False]


def test_close_releases_the_sessions(results):
    assert results["single"]["released"] == ["all"]
