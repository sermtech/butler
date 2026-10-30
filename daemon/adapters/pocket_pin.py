"""The ONE pinned Pocket TTS release and the facts read off it.

Standard library only: `scripts/fetch-pocket-tts.py` imports this before
anything else is installed, and `tts_pocket.py` imports it to find and check
the binary. Ported from an earlier prototype's `engines/pocket/pin.js` and
`core/tts-engines.js` (2026-10-30), where the same binary is the default.

Re-pinning is a deliberate act: change RELEASE, re-run the fetch script and the
slow conformance suite, and record what changed. The Rust binary lags the
Python package and its audio is not bit-identical.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Release v4.0.1 ships the Rust binary built as 3.3.0 (checked 2026-10-29).
RELEASE = {"repo": "kyutai-labs/pocket-tts", "tag": "v4.0.1", "version": "3.3.0"}

# One asset per machine Butler runs on, keyed by (sys.platform,
# platform.machine()). SHA-256 from the release's own asset digests (GitHub
# API, 2026-10-30); the Mac one was also hashed on the Mac mini itself.
ASSETS = {
    ("win32", "AMD64"): {
        "name": "pocket-tts-3.3.0-x86_64-pc-windows-msvc.zip",
        "sha256": "98753041f2e65744b989776f87bccb93f1f574a08afe1890c5427ec94cd6309c"},
    ("darwin", "arm64"): {
        "name": "pocket-tts-3.3.0-aarch64-apple-darwin.tar.gz",
        "sha256": "ee9ca6c7c000ee72c5846e3a8ba01d08f56636d94a614d2259c3f12b8fd69afa"},
}


def asset_for(key: tuple[str, str] | None = None) -> dict:
    """The pinned asset for this machine; a machine with none is refused."""
    key = key or (sys.platform, platform.machine())
    if key not in ASSETS:
        raise RuntimeError(f"no pinned Pocket TTS build for {key[0]} {key[1]}; "
                           f"pinned: {sorted(ASSETS)}")
    return ASSETS[key]


def url(asset: dict) -> str:
    return (f"https://github.com/{RELEASE['repo']}/releases/download/"
            f"{RELEASE['tag']}/{asset['name']}")


# Under the repo, gitignored: a tool Butler runs, not the owner's data.
INSTALL_DIR = REPO / "vendor" / "pocket-tts"
EXE_NAME = "pocket-tts.exe" if sys.platform == "win32" else "pocket-tts"
EXE = INSTALL_DIR / EXE_NAME
FETCH_COMMAND = "python -X utf8 scripts/fetch-pocket-tts.py"

# Primary subtag -> (the binary's --language model, the locale a voice
# declares, the model's native voice). The locales are the recognizer
# catalogue's own codes (daemon/engines/catalog.json) [DECIDED]:
# `ports.voice_ids` matches a bare `pt` to `pt-BR` but never a pinned `pt-BR`
# to a bare `pt`, so a region-less declaration would leave an owner who pinned
# a locale with no voice. The native voice is the one the binary itself picks
# for that model [READ: `pocket-tts.exe generate --help`, 3.3.0]; it is listed
# first because `hub._voice_for` takes the first voice of a language.
LANGUAGES = {
    "de": ("german", "de-DE", "juergen"),
    "en": ("english", "en-US", "alba"),
    "es": ("spanish", "es-ES", "lola"),
    "fr": ("french", "fr-FR", "estelle"),
    "it": ("italian", "it-IT", "giovanni"),
    "nl": ("dutch", "nl-NL", "alba"),
    "pt": ("portuguese", "pt-BR", "rafael"),
}

# Every language model ships all of these (checked against
# kyutai/pocket-tts-without-voice-cloning by an earlier prototype, 2026-10-30).
VOICES = (
    "alba", "anna", "azelma", "bill_boerst", "caro_davy", "charles", "cosette",
    "daan", "eponine", "estelle", "eve", "fantine", "george", "giovanni",
    "jane", "javert", "jean", "juergen", "lola", "marius", "mary", "michael",
    "paul", "peter_yearsley", "rafael", "stuart_bell", "vera",
)

DEFAULT_VOICE = "en_alba"


def voice_entries() -> list[dict]:
    """`Tts.info["voices"]`: one `{id, language}` per voice per language.

    The id is `<subtag>_<voice>` because the port carries a voice and nothing
    else, while Pocket loads one MODEL per language: the language has to ride
    in the id.
    """
    return [{"id": f"{sub}_{name}", "language": locale}
            for sub, (_model, locale, native) in LANGUAGES.items()
            for name in (native, *(v for v in VOICES if v != native))]


def split_voice(voice: str) -> tuple[str, str]:
    """`pt_alba` -> (`portuguese`, `alba`). Raises ValueError for anything
    Pocket cannot speak, naming it: a Kokoro name left in the settings must
    fail loudly rather than come out in another voice (AC-7b)."""
    sub, _, name = voice.partition("_")
    if sub not in LANGUAGES or name not in VOICES:
        raise ValueError(
            f"pocket: no voice {voice!r}; a Pocket voice is <language>_<name>, "
            f"for example {DEFAULT_VOICE!r} or 'pt_rafael'")
    return LANGUAGES[sub][0], name


def hf_hub_cache(env: dict, home: Path) -> str:
    """Where the Hugging Face weight cache is, by the hub's own precedence.

    Passed to every server as HF_HUB_CACHE. Left alone the exe derives it from
    HOME, which Git Bash sets and cmd.exe does not: a daemon started by
    start.bat would miss every cached file and reach for the network
    (an earlier prototype incident, 2026-10-30).
    """
    if env.get("HF_HUB_CACHE"):
        return env["HF_HUB_CACHE"]
    if env.get("HF_HOME"):
        return str(Path(env["HF_HOME"]) / "hub")
    return str(home / ".cache" / "huggingface" / "hub")
