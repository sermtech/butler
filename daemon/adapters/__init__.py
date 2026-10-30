"""Adapters: the only place a concrete engine is named outside config/.

Importing this package registers every adapter. Keep the imports at the
bottom and keep them unconditional -- a lazily-registered adapter turns an
unknown-name KeyError (loud, at boot) into a mystery at first use.

Each port ships with two implementations from day one: the real one and a
fake. That is not a testing convenience, it is the proof the seam exists
(AC-7a). An interface with one implementation is a guess.
"""
from . import asr_fake, tts_fake, brain_fake      # noqa: F401  (registration)
from . import asr_transcribecpp, tts_kokoro, tts_pocket  # noqa: F401  (registration)
from . import brain_endpoint, brain_harness       # noqa: F401  (registration)
from . import harness_fake, harness_pi            # noqa: F401  (registration)
from . import harness_claude, harness_opencode    # noqa: F401  (registration)
from . import notify_telegram                     # noqa: F401  (registration)
from . import notify_whatsapp                     # noqa: F401  (registration)

# endpointer_smartturn.py lives here too but is deliberately NOT imported:
# it is not a port implementation and has no registry to register with
# (plan.md 4.10 caps the seams at six). butlerd names it directly.
