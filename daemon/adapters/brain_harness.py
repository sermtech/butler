"""Harness mode: talking to a coding agent. plan.md 4.4 piece 3 and 4.4c.

The third real `Brain`, and the same composite shape as `brain_endpoint`:
`dialogue.Dialogue` with the router in FRONT and this file's converse half
behind it. Harness mode replaces the substantive tier, never the routing -- a
second copy of the route-to-intent mapping is how the third mode ends up
rewriting every call site (4.4).

Four behaviours here are the substance rather than the plumbing.

**A child per CONVERSATION, held across turns that COMPLETED (AC-59).** The
first turn that needs one builds an adapter from the injected `harness_for(chat)`
callable and starts it on the spec `spec_for` answers with; every later turn
prompts THAT child again, so only the first question in a conversation pays
the 1.5-2.3 s spawn [MEASURED, docs/harness-mode.md section 1] instead of
every question paying it. This replaces AC-46 (one child per message), which
the owner ruled on 2026-08-18 and reversed on 2026-08-19 after living with
it: the spawn was not only dead air but a failure point -- two of five turns
in a measured live conversation never got a child at all -- and it matches
the reference implementation's pi lane, which holds ONE session for the
process's lifetime and rebuilds it only on an explicit event
(`agent-chat.js` `_forceNewSession`, [READ 2026-08-19]).

**`HarnessSpec` equality IS the invalidation rule, and it is the whole rule
(AC-60).** The spec is a frozen dataclass carrying model, working directory,
tool posture and session file, so every event that should end a child already
changes one of its fields: a new conversation mints a new session file (and
carries its own project's `cwd`), and a `harness`/`harness_model`/
`harness_tools` write changes the rest. `ensure()` therefore compares the
running spec against the desired one and reuses or respawns -- no list of
events to keep in step with the settings schema, and no way for a fifth
invalidation to be forgotten. The settings are still read fresh every turn;
what changed is that reading them the same twice now costs nothing.

**N CONVERSATIONS HOLD N CHILDREN, AND THE COMPARISON IS PER CHAT ID**
(AC-241, `docs/parallel-agents.md` phase 5). `_Host` held four scalars and
exactly one child, so a second conversation's first turn disposed the first
one's agent and the owner's ten parallel agents were one agent taking turns.
It is a REGISTRY keyed by chat id now. AC-60 does not change and no
invalidation event joins it: what changed is the comparison's MEANING, from
"the running child" to "the child for THIS conversation". Getting that wrong
is silent -- the child that answers is a real child with a real session file,
just the wrong project's -- which is why the lookup is the thing validated by
mutation rather than the spawn count.

**THE CAP IS ENFORCED IN `ensure()` AND IT IS A SETTING** (AC-237, AC-245).
`harness_agents_max`, ten by default, one to fifty. Here because this is the
only construction site and it is reachable only from `turn()`, so no route, no
page and no spoken utterance can go round it. A REPLACEMENT never counts as an
extra child: a conversation that already holds a slot is exempt, because it is
replacing its own child and the count cannot go up -- so a changed spec and
the one-per-turn `restart()` replay both still work at the cap, and only a
conversation holding nothing can ever be refused.
The refusal NAMES where the slots are ("4 in butler, 3 in SITE, 1
waiting on you") and what frees one, because a cap that states a number and no
way out is a dead end rather than a limit. Nothing is queued: a refused send
is refused, said out loud and recorded, since a queue is a promise and Butler
has no surface that shows one.

**A held child is held only between turns that ENDED.** A turn that was
barged in on, abandoned mid-prompt by the caller, or failed disposes its
child, and the next turn starts a fresh one. That is not tidiness: an
interrupted prompt still owes a terminal event, and a held-open stream
carrying the PREVIOUS prompt's ending is what used to answer the next thing
the owner said with silence and a `Done`. Disposing is the one move that
makes those leftovers unreachable without a caller-side settle loop -- the
machinery the phase-7 simplification deleted, which would have to come back
otherwise. The cost is one spawn after an interruption, which the owner was
paying on every turn until now.

This adapter reads no setting and holds no store. `spec_for` and
`harness_for` are the daemon's own resolvers (`butlerd`), the
`TargetResolver` idea one seam out: a brain needing configuration that no
connection can serve gets more CALLABLES, never the settings store, because a
store is an open door and the next adapter walks through it.

**The child runs at FULL capability, `bash` included** (AC-29, RETIRED
2026-08-18). There is no tool set, no permission mode and no spoken grant
here: the owner ruled the ceremony out, so nothing in this file decides what
the agent may do. What still stands between a misheard utterance and a write
is the router's verdict and the configured project list (AC-31), and that is
stated rather than implied because it is now the whole of it.

**A turn is never silent (AC-47) and ends exactly once (AC-48).** ONE fixed
line is spoken the moment a turn reaches this file, BEFORE the spawn, so the
spawn's dead air has a voice in it (D-10). Then text deltas are
sentence-split and spoken as they arrive, so the first sentence synthesizes
while the rest is still being generated. A measured provider emits NO deltas
at all and puts the whole answer in the turn's message, so that text is
recovered when nothing streamed -- and only then, because a recovery that
fired on every provider would read the whole answer out twice. Nothing at all
is a fixed line, and a `Failed` event is SPOKEN, never raised and never
swallowed. Every path yields exactly one `Done` (D-1).

**A failed turn is replayed ONCE, from the verbatim input** (4.4c). Butler
owns the retry policy -- which is exactly why `harness_pi` turns pi's own
auto-retry off, since each attempt emitted a whole `agent_end` (AC-48) -- and
until this file existed that rule was implemented by nobody, so a transient
blip was a spoken failure with nothing behind it. Verbatim, never rebuilt from
a text history, which is what silently drops images. The room hears nothing
about the retry: a voice assistant that apologises for recovering is worse
than one that just recovers. It is replayed only while NOTHING has been said,
which is also what bounds it once a child can write: a failure that arrives
after the agent has already spoken about what it did is announced rather than
retried, so a replay cannot repeat a write the owner has already heard about.
"""
from __future__ import annotations

from pathlib import Path

import asyncio
import time
from typing import AsyncIterator

from .. import chats as chats_mod
from .. import dialogue, llm, ports, speech
from .. import shortcuts as shortcuts_mod
from .. import trace
from ..ports import (Activity, AgentEnd, Answer, Ask, AskHuman, Done, Failed,
                     HarnessSpec, Intent, Say, TextDelta, ToolEnd, ToolStart,
                     TurnContext, TurnEnd, WorkerEvent)

# How long the answer stream may be SILENT before the turn is given up on
# (D-34). On SILENCE, never on duration: a coding agent legitimately thinks
# for minutes and emits deltas and tool events the whole time it is working,
# so a total budget would cut off real work -- the more expensive of the two
# failures. Only a stream producing nothing at all trips this.
#
# The first value, 180 s, was MEASURED rather than chosen. Six real pi turns on the
# owner's own `zen_bridge/big-pickle`, 146 gaps [MEASURED 2026-09-09]:
# p90 3.25 s, p95 7.27 s, p99 40.2 s, max **78.6 s**. The first draft of this
# constant was 120 s on the reasoning that it was "well past any gap seen in a
# healthy turn", and the measurement says it would have KILLED one -- a turn
# that finished correctly after a 78.6 s silence.
#
# Two things generate the long tail and neither is a fault. This model does
# not stream: a pi turn emits one `TextDelta` at the end, so the silence
# before a long answer is the whole generation, measured at 40.2 and 49.5 s.
# And a parallel tool batch (`ls` plus two `find`s over this repo) ran 78.6 s
# with no event at all, because pi emits `tool_execution_end` only on return.
# That second number scales with the DIRECTORY rather than the model, so a
# bigger project pushes past it with nothing wrong; 180 s is about 2.3x the
# observed max for that reason.
#
# The pre-first-event wait is NOT the long pole -- it topped out at 7.4 s --
# so one uniform deadline armed at the prompt and re-armed on every event is
# enough, and no separate startup window is needed.
#
# RAISED TO 600 s on 2026-09-26 (AC-387, docs/turn-feedback.md 3). Everything
# above was measured on a CLOUD model. A local model that does not stream is
# silent for its WHOLE generation, which scales with the answer's length on
# this GPU rather than with a provider's throughput, so a 180 s deadline
# would end such a turn while it was working [REVIEW, not reproduced]. 600 s is ~7.6x the measured cloud
# maximum; the owner's reference sizes a model call at 6x an ordinary request
# (an earlier prototype: 90 s judge vs 15 s HTTP). It is CHOSEN, not measured on a local
# model -- nobody has timed the slowest one. A long budget is now visible
# rather than harmful: the heartbeat tells the page how long the agent has
# been quiet and the page offers Stop, so the owner decides, not a constant.
# Reasoning and tool-argument streaming re-arm it too (`Activity`, AC-388).
HARNESS_IDLE_S = 600.0

# The schema's own defaults for `project_agent_limit` and
# `project_idle_grace_minutes` (`daemon/settings.py`), named here so the
# three constructors that need a fallback (`_Host`, `HarnessBrain`,
# `HarnessOnlyBrain`) read ONE definition rather than hand-copying "1" and
# "60" a third time with a chance to drift from the schema or each other.
def _default_project_limit() -> int:
    return 1


def _default_project_grace_minutes() -> int:
    return 60


class CapReached(RuntimeError):
    """The cap refused an eleventh child, and the message IS the sentence.

    A distinct type rather than a flag, because `turn()` already catches every
    other spawn failure and speaks "the harness could not finish that one" --
    which is exactly the wrong thing to say here. This is not a failure: it is
    the daemon declining, and the words it declines with are the whole of
    AC-245. Carrying them in the exception keeps the sentence built where the
    counts are known (`_Host`) and spoken where the room is reachable
    (`HarnessConverse`), with nothing in between able to reword it.
    """


class _Child:
    """One conversation's running agent: the adapter, its spec, its stream.

    The three scalars `_Host` used to hold, now one object per chat id so that
    "which child" is a lookup rather than an assumption (AC-241). `questions`
    is the set of `qid`s this child is blocked on and it lives HERE rather than
    on the converse half for two reasons: an answer has to reach the child that
    asked (with one child that was free, with ten it is a crossed wire waiting
    to happen), and a child blocked on a human is holding a slot, which is what
    AC-245's refusal has to be able to say. Disposing the child takes its open
    questions with it, which is the honest ending -- the only process that
    could have received the answer is gone.
    """

    __slots__ = ("harness", "spec", "stream", "questions")

    def __init__(self, harness, spec: HarnessSpec,
                 stream: AsyncIterator | None):
        self.harness = harness
        self.spec = spec
        self.stream = stream
        self.questions: set[str] = set()


class _Host:
    """Every live conversation's child, keyed by chat id (AC-59, AC-241).

    It reads NO settings and knows no path. `harness_for(chat)` builds the
    adapter for THAT conversation's agent (the one it was born with, else the
    `harness` setting: `butlerd.chat_harness`, AC-771), `spec_for` answers what that child
    should be and `cap_for` answers how many there may be (`butlerd`), and all
    three are called per turn -- that is what makes a preference the owner
    changed reach the next thing they say. What changed with AC-59 is what
    happens to the ANSWER: a spec equal to the one THIS CONVERSATION's child is
    running reuses it, and a different one replaces it (AC-60).
    Nothing here decides what the child may DO: a harness runs at full
    capability (AC-29, retired 2026-08-18).
    """

    def __init__(self, harness_for, spec_for, chats, cap_for,
                 project_limit_for=_default_project_limit,
                 project_grace_for=_default_project_grace_minutes,
                 stop_for=None) -> None:
        self._harness_for = harness_for
        self._spec_for = spec_for
        # How many children may be alive at once (AC-237). A CALLABLE for
        # `harness_for`'s reason and not a number: a cap captured at
        # construction would be the value the daemon booted with, and this one
        # is meant to take effect on the next thing said. No default and no
        # fallback -- a brain built without it must fail at construction, not
        # spawn an eleventh agent because nobody wired the setting (D-36).
        self._cap_for = cap_for
        # How many of THOSE children may share ONE project at once
        # (docs/filing-and-pacing.md 1.6), and how long an idle one keeps
        # its slot before a lazy check reaps it. Two MORE callables for
        # `cap_for`'s own reason. Unlike `cap_for` these DO default -- to the
        # schema's own defaults (1, 60) -- because dozens of fixtures across
        # this suite build a brain with no project concept at all and a
        # required resolver here would be a signature change with no
        # behavioural point for them; only `butlerd.py`'s real boot wires the
        # settings-backed callables.
        self._project_limit_for = project_limit_for
        self._project_grace_for = project_grace_for
        # THE OTHER HALF OF THE CONFIRM-TO-STOP MECHANISM
        # (docs/filing-and-pacing.md 1.7, phase 5), and a CALLABLE for the
        # same reason as the other three resolvers -- but this one cannot
        # default to a settings-shaped function the way `cap_for` and the
        # two above do, because there is no setting behind it: it needs
        # `Hub.stop_conversation` itself, which does not exist until the
        # Hub is built, and the Hub is built AFTER this brain (butlerd.py's
        # own construction order). So `None` is the honest default here --
        # every fixture across this suite builds a `_Host` with nothing
        # wired -- and `stop_conversation` below raises loudly rather than
        # silently no-op'ing if it is ever called that way. `butlerd.py`
        # wires the real one with `set_stop_for` once the Hub exists.
        # NOT called from `_enforce_project_limit` below: wiring the
        # CAPABILITY is not the same as wiring the CONFIRM, and a daemon
        # that silently stopped a busy agent because a page asked would be
        # exactly what the owner's confirm ruling exists to prevent. That
        # wiring is phase 5 step 2 -- the page's dialog -- and it does not
        # exist yet.
        self._stop_for = stop_for
        # Butler's OWN record of the conversation: one flat JSON file per chat
        # (AC-35, 4.4c, docs/harness-mode.md 10.2). A record, not a preferences
        # store -- this adapter is the only thing that sees the conversation,
        # so it is the only thing that can write it, while the settings store
        # stays outside every adapter (4.10).
        self._chats = chats
        # One entry per LIVE conversation, keyed by chat id (AC-241). A slot in
        # the cap's accounting is exactly one entry in here: a child is up, or
        # there is no entry at all.
        self._children: dict[str, _Child] = {}
        # The one bit a per-conversation STATUS cannot derive from state held
        # elsewhere (`docs/parallel-agents.md` phase 7 item 1, AC-233).
        # `_children` answers "a child is up" for RUNNING and DONE together,
        # and `questions_open` answers WAITING, but nothing else distinguishes
        # a conversation whose last turn FAILED from one nobody has spoken
        # into yet -- both hold no child. Set only at the two places a turn
        # actually ends (`HarnessConverse._answer`, `._give_up`), never at a
        # barge-in or an abandon, which leave whatever it already said alone.
        self._last_outcome: dict[str, str] = {}
        # THE CAP'S REFUSAL of this conversation's last turn, READ ONCE by
        # the automations runner (docs/automations.md phase 7b). A scheduled
        # run checks busy before it births its chat, and a turn the owner
        # starts in the sub-second gap before the run's own `plan()` claims
        # the slot refuses the RUN with `CapReached` -- which reaches the run
        # as an ordinary spoken answer. Without this the runner recorded that
        # refusal as the run's `completed` summary. Not a sixth status word:
        # `status_for` stays idle/running/waiting/done/error.
        self._refused: dict[str, str] = {}
        # WHEN THIS CONVERSATION'S CHILD LAST WENT IDLE (docs/filing-and-
        # pacing.md 1.6, stage 3), or absent while it is BUSY -- no turn has
        # reached either of the two places that set it
        # (`HarnessConverse._answer`, `._give_up`), which is also true of a
        # child nobody has ever finished a turn with yet. Set ONLY at those
        # two places and NEVER on a socket disconnect: a harness turn that
        # SURVIVES its own socket closing (`017f8a1`) is still busy, and
        # marking it idle here would let the per-project reaper below take
        # its slot out from under the owner still waiting on it (section 7
        # risk #2). Cleared the moment this chat's child is REUSED for a new
        # turn (`HarnessConverse.turn()`), so a respawned child is never read
        # as idle from a turn it has not run yet.
        self._idle_since: dict[str, float] = {}
        # THE LAST TOOL A BACKGROUND ROW CANNOT HEAR (owner's ruling,
        # 2026-09-16, `docs/triage-list.md` phase 2 continued). `worker_event`
        # reaches only the ONE session driving a turn (D-6), so nothing kept a
        # per-conversation memory of the last tool a coding agent started --
        # every row but the one on screen showed a clock and nothing else.
        # Reused ownership rather than a second registry (D-18): this dict
        # lives beside `_last_outcome` for the same reason and is cleared at
        # the same two places a turn actually ends, PLUS `dispose()` itself,
        # so every path that drops a child also drops what it was doing.
        # THE FAILURE MODE THIS EXISTS TO PREVENT, named so the next reader
        # does not "simplify" it away: a row saying "editing web/index.html"
        # about an agent that finished minutes ago is worse than a row saying
        # nothing at all.
        self._last_action: dict[str, str] = {}
        # WHO CURRENTLY HOLDS ONE OF A PROJECT'S SLOTS (docs/filing-and-
        # pacing.md 1.6, D-12 correction 2026-09-17). This is the fix for a
        # confirmed TOCTOU: `_enforce_project_limit` used to decide "this
        # project has room" by scanning `self._children` -- which `dispose()`
        # had already POPPED the outgoing occupant from, synchronously,
        # before ever awaiting that occupant's real teardown. A conversation
        # RACING that eviction with its own new turn (this exact chat's own
        # `plan()`, running concurrently while a DIFFERENT chat's turn is
        # busy disposing it) saw the project as empty on BOTH sides of the
        # race and spawned twice. `_project_claims` (project key -> the set
        # of chat ids currently granted a slot there) and `_claimed_project`
        # (the reverse lookup, chat id -> project key, so a project switch or
        # a dispose can find and drop exactly one entry) are updated
        # SYNCHRONOUSLY -- with no `await` between reading them and writing
        # them -- so the grant a `plan()` call makes is durable before that
        # coroutine ever yields to another one. See `_enforce_project_limit`
        # for the ordering argument in full, and `_detach_child` /
        # `_release_project_claim` for the one place each dict is mutated.
        self._project_claims: dict[str, set[str]] = {}
        self._claimed_project: dict[str, str] = {}

    # -- the registry ------------------------------------------------------ #
    def current_chat(self) -> dict:
        """The record a turn would go to. Read ONCE per turn, by the caller.

        `HarnessConverse` reads it at the top of a turn and passes it back
        down, so the conversation the child is ensured for, the record the
        session file is minted into and the conversation disposed at the end
        are the same one by construction rather than by three lookups that
        could disagree if the owner clicked a row mid-turn.
        """
        return self._chats.current()

    def chat_for_turn(self, chat_id: str) -> dict:
        """The record the DAEMON said this turn is in (AC-246).

        `current_chat()` was what a turn read, and it is the wrong question by
        one hop: the hub already resolved the conversation through its one
        resolver (`hub.chat_for`, from the device's own view) and had no way
        to say so. Blank is the hands-free answer and is the current
        conversation; see `ChatStore.for_turn` for the whole rule.
        """
        return self._chats.for_turn(chat_id)

    def child(self, chat_id: str) -> _Child | None:
        """The child THIS conversation is holding, or None.

        The one lookup, and the thing to break when validating the instrument:
        a version that ignores `chat_id` and hands back whatever child exists
        answers the owner from another project's repository, silently (AC-241).
        """
        return self._children.get(chat_id)

    def live(self) -> int:
        """How many children are up. A slot is a child process (AC-245)."""
        return len(self._children)

    def busy(self) -> bool:
        """Is any child in the middle of a prompt? A child is idle exactly
        when its turn ENDED (`note_idle`); every other live child is working,
        or blocked on the owner's answer. The automatic restart asks this
        (`Hub.busy`, AC-715) and never kills a working agent."""
        return any(cid not in self._idle_since for cid in self._children)

    @property
    def harness(self):
        """The child the CURRENT conversation is holding, or None.

        A convenience read over `child()`, and since AC-241 its only callers
        are TESTS asking "is anything up". Nothing in the daemon reads it and
        nothing on a turn's path may: a turn knows which conversation it is in
        and says so, because a scalar read in the middle of a turn is the
        crossed wire this registry exists to make unrepresentable. Three call
        sites in this file were exactly that read and became keyed lookups.
        """
        held = self._children.get(self._chats.current()["id"])
        return held.harness if held is not None else None

    @property
    def spec(self) -> HarnessSpec | None:
        """What the CURRENT conversation's child was actually started with.

        The only honest account of what it is, and the only one a real
        adapter can be asked for: `harness_fake` records every spec it was
        started with, and a live child records nothing at all.
        """
        held = self._children.get(self._chats.current()["id"])
        return held.spec if held is not None else None

    def desired(self) -> HarnessSpec:
        """What the next child will be: the daemon's answer, per turn.

        Everything in it -- model, working directory, session file, and the
        refusal to guess a blank working directory (AC-31) -- belongs to the
        resolver. This file decides nothing about the child: even WHERE it
        runs is read off the conversation's own record (`_session_cwd`).

        **THE CONVERSATION IS NAMED HERE** (`docs/parallel-agents.md` phase
        4). The spawn path hands the resolver the record the session file must
        be minted into, so it can never be minted into a conversation the owner
        merely had open a moment ago. It is read ONCE, because `session_ref`
        writes: resolving the directory off one record and the reference off
        another is precisely the crossed wire this argument exists to stop.

        The no-argument form answers for the conversation a turn would go to,
        and since AC-241 it is a TEST read: every SPAWN goes through
        `desired_for` with the record the turn already read, which is what
        stops a session file being minted into whichever conversation happened
        to be current at that instant.
        """
        return self.desired_for(self._chats.current())

    def desired_for(self, chat: dict) -> HarnessSpec:
        """`desired()`, for ONE named conversation (AC-241)."""
        return self._spec_for(self._session_cwd(chat), chat)

    def _session_cwd(self, chat: dict) -> str | None:
        """Where this CONVERSATION works, or None for the configured default.

        **DERIVED FROM THE RECORD AND NOTHING ELSE** (`docs/filing-and-
        pacing.md` 8, R-B, AC-289). A chat belongs to the project it was born
        in, so the child's working directory is read off the record at every
        spawn. There used to be a SECOND copy -- `_cwd`, this process's memory
        of the last spoken "work in project X" -- which overrode the record
        and had to be cleared by hand on every path that changed conversation
        (`session_open` did; a restart lost it and answered from
        `harness_cwd` while the page showed the moved workspace, AC-7b,
        measured by review 2026-09-11). With one copy there is nothing to
        clear and nothing to disagree with.

        A recorded project the owner has since REMOVED from the pane is ignored
        LOUDLY rather than raising. The gate would refuse it at every spawn, and
        a daemon that cannot start a child cannot answer at all -- never latch
        an error into a state that disables the microphone (D-1).

        **IT IS NO LONGER SILENT AT THE OTHER END EITHER, AND THAT MATTERED
        MORE THAN THIS LINE** (review, 2026-09-11). Where the child starts is
        the owner's decision and is unchanged; what was wrong is that only this
        console saw it. Two things closed the gap. Most of the reports were not
        this branch at all -- a row re-spelled with a forward slash, a trailing
        separator or a different case reached a spawn gate comparing raw
        strings, so a directory the listing happily filed conversations under
        landed here and looked like a removed project (fixed in
        `shortcuts.same_dir`). And for a project that really is gone, the page
        now draws its name STRUCK THROUGH in the conversation list, because
        `_filed` gives it an empty `projectId` and the label was otherwise
        indistinguishable from a live workspace.

        `desired()` is called once per turn, so this prints once per turn while
        the mismatch lasts. Deliberate: it is the only line that names the
        directory that was dropped, and one that printed once a boot would be
        scrolled away by the time anybody asked.
        """
        # The record `desired_for` mints the session reference into, handed in
        # rather than looked up again, read here for its `project` alone.
        recorded = str(chat.get("project") or "").strip()
        if not recorded:
            return None
        try:
            self._spec_for(recorded)
        except Exception as e:                            # noqa: BLE001
            print(f"[butler] this conversation records the project {recorded} "
                  f"and it is no longer usable ({e}); the agent starts in the "
                  f"configured working directory instead, and the page draws "
                  f"that project struck through in the conversation list",
                  flush=True)
            return None
        return recorded

    async def plan(self, chat: dict) -> HarnessSpec | None:
        """What this turn's child must BE, or None to reuse the held one.

        The DECISION half of `ensure`, and it is separate for one reason: the
        cap's refusal has to be known before "Working on it." is spoken (D-10).
        A turn that narrated and then said "ten of ten agents are running"
        would be Butler contradicting itself inside one breath. ASYNC since
        docs/filing-and-pacing.md 1.6's per-project reaper (below) tears down
        a stale child as part of deciding.

        **A PRIOR VERSION OF THIS PARAGRAPH SAID "NOTHING HERE AWAITS THE
        EVENT LOOP AWAY FROM THIS DECISION EXCEPT THAT ONE DISPOSE" -- AND
        THAT WAS THE BUG, NAMED WHILE BEING DENIED (D-12, confirmed by a
        reviewer's scratch reproduction 2026-09-17).** `dispose()` pops its
        target from `self._children` and only THEN awaits the real teardown;
        awaiting it from inside the decision meant a SECOND conversation's
        own concurrent `plan()` call could run in that gap, see the project
        as empty on both counts (its old child already popped, the new
        claimant not spawned yet) and spawn -- twice, for one project slot.
        Fixed by moving the GRANT (`_project_claims`/`_claimed_project`)
        before the only await left in this method, not after it:
        `_enforce_project_limit` now decides and commits everything
        synchronously, and defers the actual `harness.dispose()` calls to
        the very end. Python's cooperative scheduling makes a block with no
        `await` in it atomic against every other coroutine, so nothing can
        observe "granted but not yet torn down" as "free" -- there is
        nothing between the decision and the sentence that could change
        either, and this time that is provably true rather than merely
        stated.

        The comparison is the WHOLE invalidation rule (AC-60). `HarnessSpec`
        is frozen, so `==` is structural, and every event that ought to end a
        child already shows up as a different field -- a new conversation as a
        new `session_file` (and its own project's `cwd`), a
        settings write as a different `model` or `tools`. Keeping a list of
        those events here instead would be a list that has to be updated the
        day a fifth one is added, and the failure of forgetting is silent: an
        agent still answering from the old repository.

        **THE COMPARISON IS NOW PER CONVERSATION** (AC-241). It is the same one
        rule against this chat's own child; what it must never be is a
        comparison against SOME child, which passes every test a single-child
        daemon can write and hands the owner another project's agent.

        **THE PROJECT LIMIT IS HERE TOO, AND IT RUNS FIRST** (docs/filing-
        and-pacing.md 1.6). One active chat per project, designed for N via
        `project_agent_limit` rather than a hardcoded "1" -- see
        `_enforce_project_limit`. Before the global cap, so a room refused for
        BOTH reasons hears the one that actually explains it: a project
        already busy is not "every slot is full."

        **THE CAP IS HERE** (AC-237), and a conversation that ALREADY holds a
        slot is never refused: it is replacing its own child, so the count
        cannot go up. That is the whole of "a replacement does not count as an
        eleventh", and it is what makes a changed setting, a spoken project
        switch and the one `restart()` replay all still work at the cap. It
        follows that `CapReached` only ever reaches a conversation with no
        child, which is why the caller has nothing to dispose on that path.

        **THE PROJECT GRANT IS RELEASED IF THE GLOBAL CAP REFUSES RIGHT
        BEHIND IT.** `_enforce_project_limit` already committed this chat's
        claim on the project's slot before returning; if the very next check
        raises, this conversation will never call `ensure()` and will never
        hold a real child, so the claim must not survive to block every
        other conversation from ever using that slot again (fail-closed,
        docs/filing-and-pacing.md 1.6's own orphan requirement).
        """
        spec = self.desired_for(chat)
        held = self._children.get(chat["id"])
        if held is not None and held.spec == spec:
            return None
        await self._enforce_project_limit(chat["id"], spec.cwd)
        if held is None and self.live() >= self.cap():
            self._release_project_claim(chat["id"])
            raise CapReached(self.cap_refusal())
        return spec

    async def ensure(self, chat: dict, spec: HarnessSpec | None) -> None:
        """Spawn what `plan()` just answered for THIS turn. None reuses.

        The two are a PAIR and this half never re-decides: resolving the spec a
        second time would mean the cap was checked against one answer and the
        child started on another, and the resolver mints a session reference
        and prints about an unusable project as it goes.

        Raises rather than papering over anything: a spawn that fails is the
        turn failing, and the caller SPEAKS it (AC-47). A failed `start` leaves
        NOTHING in the registry, so the next turn spawns again rather than
        prompting a child that never started, and a half-started adapter is
        still disposed here rather than left for the garbage collector
        (AC-36, D-24).

        **THE SELF-REPLACEMENT BELOW USES `_detach_child`, NEVER `dispose()`**
        (docs/filing-and-pacing.md 1.6). This chat id's PROJECT CLAIM was
        already moved onto whatever `spec.cwd` names by `plan()`'s call into
        `_enforce_project_limit` -- the generic `dispose()` would drop that
        claim along with the old child, opening the slot this same chat id is
        one line away from re-occupying to anyone else's concurrent `plan()`.
        And **ANY FAILURE BELOW RELEASES THE CLAIM** `plan()` granted: a spec
        this method never manages to spawn must not go on reserving a
        project's slot for a child that does not exist (fail-closed).
        """
        if spec is None:
            return
        chat_id = chat["id"]
        if chat_id in self._children:
            # Fixed at spawn, all of it: moving directory or model is
            # dispose-plus-start and never a setter (AC-31, ports.HarnessSpec).
            # `_detach_child`, not `dispose()`: see the docstring above.
            old = self._detach_child(chat_id)
            if old is not None:
                await old.harness.dispose()
        try:
            # THIS conversation's agent (AC-771): a chat keeps the harness it
            # was born with, whatever the setting says now.
            harness = self._harness_for(chat)
            # Owned BEFORE the await, not after. Starting a child is the
            # 1.5-2.3 s window this design pays on every question, and it is
            # exactly where a barge-in lands; a spawn cancelled halfway must
            # still leave something `dispose()` can find, or the process
            # outlives the turn with nobody holding its handle (AC-36, D-24).
            self._children[chat_id] = _Child(harness, spec, None)
            try:
                await harness.start(spec)
            except BaseException:
                # A child that never started holds no slot and must not look
                # like one: leaving the entry in place would count against
                # the cap for the rest of the daemon's life and refuse a
                # conversation that has nothing running.
                self._detach_child(chat_id)
                await harness.dispose()
                raise
            # Subscribed ONCE per child and held: the port promises one
            # reader, and a second iterator would race this one for the same
            # events. Held ACROSS turns with the child (AC-59) -- `_answer`
            # breaks out of the `async for` at the prompt's terminal event,
            # which stops iterating without closing the generator, so the
            # next turn resumes the same one.
            self._children[chat_id].stream = harness.subscribe()
        except BaseException:
            self._release_project_claim(chat_id)
            raise

    async def restart(self, chat: dict) -> None:
        """Drop this conversation's child and start another, for the ONE replay.

        The session reference goes with it, so the conversation survives the
        second attempt (AC-35, 4.4c). Disposing FIRST is what makes this
        different from `ensure()`: the spec has not changed -- the child is
        being replaced because it broke, not because it is wrong. It is also
        what makes a replay work AT the cap: the slot this conversation already
        holds is the one the new child takes.
        """
        await self.dispose(chat["id"])
        await self.ensure(chat, await self.plan(chat))

    # -- the cap, which is the owner's number (AC-237, AC-245) ------------- #
    def cap(self) -> int:
        """How many children may be alive at once, read per spawn.

        Read from the daemon's resolver every time rather than captured, so the
        owner's change reaches the next thing they say with no restart. No
        clamping and no fallback: the schema's own minimum and maximum are the
        validation, and a value that got past them is a bug to see rather than
        to paper over.
        """
        return int(self._cap_for())

    def cap_refusal(self) -> str:
        """The sentence said at the cap. It NAMES where the slots are (AC-245).

        "10 of 10 agents are running. 1 in butler, 1 in SITE, 1 in
        owner... 1 waiting on you. Open one and press stop to free a slot."
        Every project reads 1 now that `project_agent_limit` enforces it
        (docs/filing-and-pacing.md 1.6) -- the earlier illustration, "4 in
        butler, 3 in SITE", described a world this cap refuses to reach
        any more. The wording below did not change: it already counts
        rather than assuming, so raising the limit past 1 later makes a
        truthful "4 in butler" possible again with no edit here.

        A number alone was the first version and the owner's own question
        killed it -- "refusing to create more is fine, but I don't know how to
        KILL/STOP an agent so there are more slots available?". So the sentence
        carries the two facts that answer it: which projects are holding the
        slots, and that opening one and pressing stop frees one.

        The project is read off the child's own SPEC, which is the directory it
        is actually running in, rather than off the chat record or the pane: a
        slot is a child process, and the honest name for where a slot is, is
        where its process is. The waiting count is a SECOND facet over the same
        children rather than a bucket of its own -- a waiting agent is also in
        a project, so the per-project counts always sum to the cap and the
        waiting clause says how many of them are blocked on a human.
        """
        by_project: dict[str, int] = {}
        for held in self._children.values():
            name = Path(held.spec.cwd).name or held.spec.cwd
            by_project[name] = by_project.get(name, 0) + 1
        where = ", ".join(
            f"{count} in {name}" for name, count in
            sorted(by_project.items(), key=lambda kv: (-kv[1], kv[0])))
        waiting = sum(1 for held in self._children.values() if held.questions)
        if waiting:
            where = f"{where}, {waiting} waiting on you"
        return (f"{self.live()} of {self.cap()} agents are running. {where}. "
                f"Open one and press stop to free a slot.")

    # -- the questions a child is blocked on (AC-234, AC-245) -------------- #
    def note_question(self, chat_id: str, qid: str) -> None:
        """This conversation's child is blocked on `qid`.

        Two things read it and they are different claims: the idle deadline is
        suspended while it is non-empty (D-34, AC-234), and the child is
        holding a slot while doing nothing at all, which is what the cap's
        refusal says out loud.
        """
        held = self._children.get(chat_id)
        if held is not None:
            held.questions.add(qid)

    def close_question(self, qid: str) -> None:
        """Stop waiting on `qid`, whichever conversation raised it."""
        for held in self._children.values():
            held.questions.discard(qid)

    def close_questions(self, chat_id: str) -> None:
        """Every question this conversation raised dies with its turn."""
        held = self._children.get(chat_id)
        if held is not None:
            held.questions.clear()

    def child_for_question(self, qid: str):
        """The child that ASKED `qid`, or None if no live child holds it.

        With one child this was "the" child and the lookup was free; with ten
        it is the difference between an answer reaching the dialog that raised
        it and an answer being written into another project's agent (AC-241).
        """
        for held in self._children.values():
            if qid in held.questions:
                return held.harness
        return None

    def questions_open(self, chat_id: str) -> bool:
        held = self._children.get(chat_id)
        return bool(held is not None and held.questions)

    # -- the status the daemon owns (docs/parallel-agents.md phase 7) ------- #
    def mark_outcome(self, chat_id: str, outcome: str) -> None:
        """Record how this conversation's last turn ENDED, and only that.

        Called from exactly ONE place today: `_give_up`, on a failure.
        `_answer` reaching its own `Done` does NOT call this with "done" --
        AC-59 holds the child for the whole conversation, so `status_for`
        already answers "done" from the child's own presence before it ever
        reads this, and a write nothing reads is a claim rather than a
        contract. Never from a barge-in or an abandoned turn either -- an
        interrupted turn is not an outcome, and marking one would make a
        conversation the owner merely walked away from read as failed.
        """
        self._last_outcome[chat_id] = outcome

    def note_refused(self, chat_id: str, said: str) -> None:
        """The cap refused this conversation's turn with `said` (see
        `_refused`). Written from exactly one place, `_refuse`."""
        self._refused[chat_id] = said

    def take_refused(self, chat_id: str) -> str | None:
        """The refusal of this conversation's last turn, ONCE, or None."""
        return self._refused.pop(chat_id, None)

    def last_outcome(self, chat_id: str) -> str:
        """"idle" (never spoken into, or nothing recorded yet) or "error"
        (the last harness turn failed) -- the one bit `status_for` cannot
        derive from state already held elsewhere. "done" is never STORED
        here: a conversation whose last turn finished cleanly still holds
        its child (AC-59), so `status_for` reads that presence directly and
        never reaches this method for it (see `mark_outcome` above)."""
        return self._last_outcome.get(chat_id, "idle")

    def note_action(self, chat_id: str, detail: str) -> None:
        """The last tool this conversation's child STARTED, and nothing else.

        Called from exactly one place: the `ToolStart` branch below. Not
        updated on `ToolEnd` -- between two tool calls "was last doing X" is
        still the honest answer, and only a NEW `ToolStart` or the turn
        ending (`clear_action`) may replace it.
        """
        self._last_action[chat_id] = detail

    def clear_action(self, chat_id: str) -> None:
        """Drop the last action the moment there is no turn left to describe.

        Called from `dispose()` (every path that drops a child) AND from
        `HarnessConverse.turn()`'s own `finally` (the one path that does NOT
        drop a child, because AC-59 holds it for the whole conversation) --
        between the two, every ending this file has is covered. A no-op for a
        chat with nothing noted, same as `_last_outcome`'s own get-with-
        default shape.
        """
        self._last_action.pop(chat_id, None)

    def last_action(self, chat_id: str) -> str | None:
        """The last tool noted for this conversation, or None.

        None means either nothing has ever been noted, or a turn ended and
        `clear_action` ran -- the two are indistinguishable on purpose, both
        read the same way by a row: nothing to say past the age.
        """
        return self._last_action.get(chat_id)

    # -- the per-project reaper (docs/filing-and-pacing.md 1.6) ------------- #
    def project_limit(self) -> int:
        """How many children may share ONE project at once, read per spawn.

        `cap()`'s own reason for being a live read rather than a captured
        value: the owner's change reaches the next thing they say with no
        restart.
        """
        return int(self._project_limit_for())

    def project_grace_s(self) -> float:
        """The idle grace, in SECONDS -- the setting is minutes."""
        return float(self._project_grace_for()) * 60.0

    def note_idle(self, chat_id: str) -> None:
        """This conversation's child just finished a turn and owes nothing
        else (docs/filing-and-pacing.md 1.6, stage 3).

        Called from the two places a turn actually ends
        (`HarnessConverse._answer`, `._give_up`) -- `mark_outcome`'s own two
        call sites -- and from NOWHERE ELSE. In particular never from
        anything socket-shaped: a harness turn that SURVIVES its own socket
        closing (`017f8a1`) has not reached either of those two places, so
        it is still busy and this is simply never called for it. Marking a
        surviving turn idle here would let `_enforce_project_limit` reap the
        project slot out from under the owner still waiting on the answer
        (section 7 risk #2) -- the exact failure this method's callers list
        exists to make unrepresentable.
        """
        self._idle_since[chat_id] = time.monotonic()

    def clear_idle(self, chat_id: str) -> None:
        """The reverse of `note_idle`, for a child REUSED by a new turn.

        Called once, at the top of `HarnessConverse.turn()`, beside
        `close_questions` -- a new turn never inherits the previous one's
        idle mark any more than it inherits its suspended questions (AC-234).
        Without it a respawned or reused child would read as idle from a
        turn it has not run yet, the moment its OWN new turn has not reached
        `note_idle` itself.
        """
        self._idle_since.pop(chat_id, None)

    async def _enforce_project_limit(self, chat_id: str, cwd: str) -> None:
        """One busy or fresh agent per project, designed for N.

        The owner's round 2/3 ruling (docs/filing-and-pacing.md 1.6): "only
        one agent/active chat per project at once, but up to the max
        allowed agents in settings which would be on different projects."
        Run from `plan()`, BEFORE the global cap (AC-237), so the room hears
        the right refusal for the right reason.

        EXCLUDES this chat's OWN child by id, never by directory -- a
        conversation switching projects or replacing its own child (a
        changed setting, a spoken "work in X") is judged against every
        OTHER conversation's children, never against itself, or a
        conversation could never move at all once it held a slot.

        An EXPIRED idle child (`_idle_since` older than
        `project_idle_grace_minutes`) is reaped HERE, lazily, and does not
        count toward the limit -- the lazy check itself, preferred over a
        background timer for D-1's reason: nothing here can leak, because
        nothing is ever scheduled.

        A collision still at or over the limit after that reap is resolved
        two ways, and the difference is whether anyone is home. A BUSY
        match (no `_idle_since` at all -- the turn has not reached either
        of the two places that set it) has no confirm mechanism yet
        (docs/filing-and-pacing.md phase 5, not built) and is refused like
        the global cap. An IDLE match that has not yet expired is disposed
        and the turn proceeds with NO confirm -- D1's ruling (recommended
        option A): the cost of disposing an idle child is a 1.5-2.3s
        respawn, not a loss of memory, because the session file survives
        `dispose()` untouched (section 1.6).

        **THE DECISION RUNS TO COMPLETION WITH NO `await` IN IT, AND THAT IS
        THE WHOLE FIX FOR THE RACE THIS METHOD USED TO HAVE** (confirmed by a
        reviewer's scratch reproduction 2026-09-17; see `plan()`'s docstring
        for the shape of the bug). Every read of `_project_claims`/
        `_idle_since` and every write to them below -- the expired reap, the
        D1=A eviction, and the GRANT of this chat id's own claim -- happens
        before the first `await`, so a concurrent `plan()` call for the same
        project (this exact chat id's own reused turn among them) can only
        ever observe the decision fully made or not yet started, never
        half-made. Only the real process teardown (`held.harness.dispose()`,
        genuinely unbounded from this method's point of view, bounded from
        underneath by the adapter's own `DISPOSE_TIMEOUT_S`) happens after,
        and it happens to what this function ALREADY detached -- so a slot
        this decided to hand to someone else can never look occupied again
        just because the old occupant is slow to die.
        """
        key = shortcuts_mod.same_dir(cwd)
        # A changed working directory (a changed `harness_cwd` for an
        # unprojected record) releases whatever slot this chat id held ELSEWHERE first. Without
        # this a stale claim under the OLD project would hold that slot
        # forever: this chat id will never call `dispose()` naming the old
        # project again once its own spec points at the new one (fail-closed
        # for a switch, not only for an eviction).
        old_key = self._claimed_project.get(chat_id)
        if old_key is not None and old_key != key:
            self._project_claims.get(old_key, set()).discard(chat_id)
            self._claimed_project.pop(chat_id, None)

        limit = self.project_limit()
        grace_s = self.project_grace_s()
        now = time.monotonic()
        to_teardown: list[tuple[str, _Child, str]] = []

        # ---- SYNCHRONOUS SECTION: no `await` until the teardown loop. ---- #
        claimed = self._project_claims.get(key, set()) - {chat_id}
        remaining: list[str] = []
        for other_id in claimed:
            idle_since = self._idle_since.get(other_id)
            if idle_since is not None and now - idle_since >= grace_s:
                # Expired: reaped outright, and never counted at all.
                held = self._detach_child(other_id)
                self._release_project_claim(other_id)
                if held is not None:
                    to_teardown.append((other_id, held, "idle-expired"))
                continue
            remaining.append(other_id)
        if len(remaining) >= limit:
            idle_candidates = [oid for oid in remaining
                               if self._idle_since.get(oid) is not None]
            if not idle_candidates:
                # Every colliding child is BUSY. Nothing here can stop one
                # gracefully yet (phase 5), so this is refused like the cap.
                # Nothing has been granted or detached yet, so there is
                # nothing here for a refusal to leave dangling.
                raise CapReached(self._project_refusal(cwd, limit))
            # D1=A: the longest-idle one gives up its slot, no confirm.
            oldest = min(idle_candidates, key=lambda oid: self._idle_since[oid])
            held = self._detach_child(oldest)
            self._release_project_claim(oldest)
            if held is not None:
                to_teardown.append((oldest, held, "at-limit"))
        # THE GRANT. Durable the instant this line runs -- the last write in
        # the synchronous section, so anything that could still fail (the
        # global cap check back in `plan()`) fails AFTER it and must release
        # it explicitly rather than this method ever leaving it ungranted.
        self._project_claims.setdefault(key, set()).add(chat_id)
        self._claimed_project[chat_id] = key
        # ---- END of the synchronous section. ----------------------------- #

        for other_id, held, reason in to_teardown:
            await held.harness.dispose()
            self._note_reaped(other_id, cwd, reason)

    def _detach_child(self, chat_id: str) -> "_Child | None":
        """Remove `chat_id` from the LIVE-CHILD bookkeeping ONLY -- never the
        project claim (docs/filing-and-pacing.md 1.6). Synchronous, no
        `await`, so it can run inside `_enforce_project_limit`'s decision
        without opening the exact window that decision exists to close.

        `dispose()` calls this AND drops the claim, because a chat id being
        disposed for good is leaving its project too. `ensure()`'s
        self-replacement branch calls this ALONE, because the chat id is one
        line away from re-occupying the slot `plan()` already moved its
        claim onto -- releasing it here would let another conversation's
        concurrent `plan()` steal a slot this one has not finished using.
        """
        held = self._children.pop(chat_id, None)
        self._last_action.pop(chat_id, None)
        self._idle_since.pop(chat_id, None)
        return held

    def _release_project_claim(self, chat_id: str) -> None:
        """Drop `chat_id`'s project claim alone, touching no `_children`
        entry (docs/filing-and-pacing.md 1.6). The bare form for a claim
        that was granted and never became a real child: `plan()`'s own
        global-cap refusal right after the project check passes, and
        `ensure()`'s spawn failure, both need exactly this without also
        detaching a `_children` entry that was never created. Idempotent --
        called on a chat id already released, it is a no-op.
        """
        key = self._claimed_project.pop(chat_id, None)
        if key is not None:
            self._project_claims.get(key, set()).discard(chat_id)

    def _note_reaped(self, chat_id: str, cwd: str, reason: str) -> None:
        """Say, out loud to the log and into the conversation's own record,
        that this project's reaper just disposed `chat_id`'s child with no
        confirm (docs/filing-and-pacing.md 1.6, D1=A).

        Root `AGENTS.md`'s own standing rule is that silence is never how
        Butler reports something happened, and this branch was exactly
        that: a device showing `chat_id` learned nothing until its next
        message paid an unexplained 1.5-2.3s respawn. TWO channels, not a
        new wire frame:

        A `trace.trace` line, because that is the voice every other stage of
        a turn already logs to (`state/voice-trace.log`), and it needs no
        conversation to be open or a device to be connected to be useful --
        exactly the case here, since a reaped conversation is idle by
        definition.

        A note into the conversation's OWN transcript, `record_note`'s own
        shape (AC-234, 9.4: "Butler asked X, you answered Y") and for the
        same reason that one exists -- the owner reopening THIS conversation
        is the one reader neither a log file nor a live device can reach.
        This is deliberately NOT a `worker_event`: that frame rides
        `send_for_turn`, keyed to a TURN in flight (D-6), and a reaped
        conversation has no turn to attach one to and no guaranteed
        listener. A transcript line survives a reload and a closed tab; a
        push with nobody subscribed would not.
        """
        name = Path(cwd).name or cwd
        phrase = ("it had been idle past the project's grace period"
                  if reason == "idle-expired" else
                  "another chat needed the project's one slot")
        trace.trace("project_reaped", chat=chat_id, project=name,
                    reason=reason)
        self.record(chat_id, "assistant",
                    f"(Butler paused this agent to free {name}'s slot -- "
                    f"{phrase}. It remembers everything up to here and will "
                    f"pick back up the next time you ask it something.)")

    def _project_refusal(self, cwd: str, limit: int) -> str:
        """The sentence said when a PROJECT's own limit refuses a turn.

        Kept apart from `cap_refusal()`: the two are different numbers
        (`project_agent_limit` vs `harness_agents_max`), and the room needs
        to know which one it hit rather than a sentence that could mean
        either.
        """
        name = Path(cwd).name or cwd
        holding = "an agent" if limit == 1 else f"{limit} agents"
        return (f"{name} already has {holding} running there, and only "
                f"{limit} may run at once. Open it and press stop to free "
                f"the slot.")

    # -- the confirm-to-stop mechanism's caller (docs/filing-and-pacing.md
    # 1.7, phase 5) -------------------------------------------------------- #
    def busy_children_for_project(self, cwd: str) -> list[str]:
        """Chat ids holding a GRANTED, BUSY slot in the project `cwd`
        resolves to -- the confirm dialog's own pre-check, BEFORE the owner
        has said yes to anything (docs/filing-and-pacing.md 1.7, phase 5
        step 1: "the page must know a collision is coming before it
        commits").

        READ-ONLY: no claim taken, no teardown, nothing in `_project_claims`
        or `_idle_since` touched. Reads the SAME two dicts
        `_enforce_project_limit` itself decides a real collision from
        (D-18) -- an idle-but-still-held child (D1=A, section 1.6) is
        correctly excluded here exactly as it is there, so this can never
        disagree with what actually starting a turn in this project would
        find. `same_dir` is the one equality rule for a directory
        (`shortcuts.py`), the same one `_enforce_project_limit`,
        `harness_spec_resolver` and the listing route all use.

        A project with no live claim at all (nothing ever spawned there, or
        everything spawned there has since gone idle or been torn down)
        answers the empty list -- honest, since there is nothing here that
        starting a turn would collide with.
        """
        key = shortcuts_mod.same_dir(cwd)
        return [cid for cid in self._project_claims.get(key, set())
                if self._idle_since.get(cid) is None]

    def refusal_for(self, cwd: str) -> str | None:
        """What `plan()` would refuse a NEW conversation in `cwd` with right
        now, or None when it would start (docs/automations.md AU-13).

        A scheduled run asks this BEFORE it births anything, so a busy
        project makes the run WAIT rather than a refusal landing in a chat
        nobody asked for -- and it never stops or evicts a busy agent to
        make room (AC-460). READ-ONLY: nothing is claimed, detached or torn
        down. It walks the SAME two dicts `_enforce_project_limit` decides
        from and in the same order -- expired idle children do not count, an
        idle one at the limit would be evicted (D1=A, inherited), a busy one
        refuses -- then the global cap `plan()` checks behind it, less what
        that eviction frees. A test drives both this and `plan()` through
        the same states so the two cannot drift (D-18).
        """
        key = shortcuts_mod.same_dir(cwd)
        limit = self.project_limit()
        grace_s = self.project_grace_s()
        now = time.monotonic()
        freed = 0
        remaining = []
        for other_id in self._project_claims.get(key, set()):
            idle_since = self._idle_since.get(other_id)
            if idle_since is not None and now - idle_since >= grace_s:
                freed += other_id in self._children
                continue
            remaining.append(other_id)
        if len(remaining) >= limit:
            idle = [oid for oid in remaining
                    if self._idle_since.get(oid) is not None]
            if not idle:
                return self._project_refusal(cwd, limit)
            freed += 1
        if self.live() - freed >= self.cap():
            return self.cap_refusal()
        return None

    def set_stop_for(self, stop_for) -> None:
        """Wire the Hub-level "stop a named conversation" capability, once
        the Hub exists (`butlerd.py`'s own construction order: this `_Host`
        is built as part of the brain, and the brain is built BEFORE the
        Hub that would need to hand its own `stop_conversation` back in).
        A plain setter rather than a constructor argument for exactly that
        reason -- there is nothing to inject at `__init__` time.
        """
        self._stop_for = stop_for

    async def stop_conversation(self, chat_id: str) -> bool:
        """Stop `chat_id`'s turn through the injected `Hub.stop_conversation`
        (docs/filing-and-pacing.md 1.7, phase 5). The CALLER the per-project
        BUSY collision would need to resolve a collision with consent
        instead of refusing outright -- and it is deliberately NOT called
        from `_enforce_project_limit` above. That collision still raises
        `CapReached` on every busy match, exactly as it did before this
        method existed: the confirm has no UI yet, and a daemon that
        silently stops a busy agent because a page asked is exactly what
        the owner's confirm ruling exists to prevent
        (`test_harness_project_limit.py` pins the refusal;
        `test_project_stop.py` pins that wiring this resolver does not,
        on its own, change that outcome).

        Raises rather than silently doing nothing when nothing is wired:
        every fixture across this suite builds a `_Host` with no
        `stop_for` at all, and calling this method with nothing wired is a
        CONSTRUCTION bug, a different failure from "this chat has nothing
        to stop" (which `Hub.stop_conversation` itself raises `LookupError`
        for).
        """
        if self._stop_for is None:
            raise RuntimeError(
                f"_Host.stop_conversation({chat_id!r}) called with no "
                f"stop_for resolver wired -- this _Host was built without "
                f"Hub.stop_conversation (butlerd.py's set_stop_for call)")
        return await self._stop_for(chat_id)

    # -- the record, which is Butler's and not the agent's (AC-35, 4.4c) ---- #
    def record(self, chat_id: str, role: str, content: str,
               stopped: bool = False) -> None:
        """One message into a NAMED conversation's transcript (AC-241).

        Roles and text, which is all the flat chat file holds: the tool-level
        record went with `events.jsonl` on 2026-08-18, so a resumed page can
        show what the agent SAID and no longer what it DID (10.5).

        **THE CONVERSATION IS AN ARGUMENT.** It used to be read off the store's
        pointer at write time, so `turn()` threading a chat id through `plan`,
        `ensure` and `dispose` and then calling this with none meant a turn
        answered into whichever conversation was current when the answer
        finished. Loud, never fatal (D-1), and never a write into a different
        conversation -- see `ChatStore.append`.
        """
        self._chats.append(chat_id, role, content, stopped=stopped)

    def show(self, chat_id: str, role: str, entry: dict) -> None:
        """Something shown in a NAMED conversation (docs/rich-chat.md RC-4),
        waiting for that turn's next `role` message (`ChatStore.show`)."""
        self._chats.show(chat_id, role, entry)

    def mark_unspoken(self, chat_id: str, tail: str, since_ms: int) -> None:
        """Where the heard part of this turn's recorded answer ended
        (`ChatStore.mark_unspoken`). The HUB calls this, because only the
        hub knows what reached the room; the record's text stays whatever
        this adapter wrote (docs/filing-and-pacing.md item 2, AC-286)."""
        self._chats.mark_unspoken(chat_id, tail, since_ms)

    def mark_finished(self, chat_id: str, elapsed_ms: int,
                      since_ms: int) -> str | None:
        """Which agent finished this turn's recorded answer, and after how
        long (`ChatStore.mark_finished`, AC-695). The HUB calls this, because
        the turn clock is the hub's; answers the agent name, or None."""
        return self._chats.mark_finished(chat_id, elapsed_ms, since_ms)

    def session_state(self) -> dict:
        """What `ready` tells the page: which conversation, and how long.

        The new/resumed/lost line is GONE with the store that answered it
        (AC-35, rewritten 2026-08-18). The page can no longer say whether the
        agent still remembers this conversation, and that loss is recorded in
        10.5 rather than papered over with a guess.
        """
        return self._chats.summary()

    def session_transcript(self, limit: int | None = None) -> dict:
        """The chat file the page redisplays: `{id, harness, project, title,
        origin, messages}`. `origin` (RV-21) is how the page knows a guest's
        chat and disables its composer (AC-607); `ChatStore.find` answers it
        too, so the three conversation routes keep ONE shape.

        `title` arrived 2026-09-15, and it is the SAME rule the chat list uses
        (`ChatStore.title`), so a row and the header it opens cannot disagree
        about what a conversation is called. Without it the pane's own header
        read "current conversation" for every conversation opened by id or
        restored on a reload, while the row three inches to the left named it
        correctly -- measured on the scratch surface, not inferred.
        Empty for a conversation nobody has spoken in, deliberately: the page
        owns its words for that case.
        """
        chat = self._chats.current()
        return {"id": chat["id"], "harness": chat["harness"],
                "model": chat.get("model", ""),
                "project": chat["project"],
                "title": self._chats.title(),
                "origin": chat.get("origin"),
                "messages": self._chats.messages(limit=limit)}

    def session_list(self, limit: int | None = None,
                     belongs=None) -> list[dict]:
        """Every conversation on disk, newest first (AC-164).

        The store is reached through the brain like every other conversation
        method here, because the brain is the only thing the daemon hands it
        to (4.10): a route that opened `state/chats/` itself would be a
        second reader of the record, and the two would eventually disagree
        about which files in there are conversations.

        `belongs` is the workspace scope and is a PREDICATE rather than a
        project id, because nothing below this line knows what a project row
        is (AC-229) -- the route resolves the row and hands down the rule.
        """
        return self._chats.listing(limit=limit, belongs=belongs)

    def seed_note(self) -> str:
        """The kept transcript, when the harness cannot remember it (AC-193).

        "" in every ordinary turn, so the prompt is unchanged. It is not ""
        on the first turn after a rewind, because the rewind takes the
        harness's session file away with the dropped turns (AC-192) -- so the
        child that starts next has forgotten the KEPT turns as well, and this
        is what gives them back.

        `ChatStore.needs_seed` is the guard -- a record with messages and no
        `sessionRef` -- and it is self-limiting, because `ensure()` sets that
        reference on the very turn this is read for. Read BEFORE `record`, or
        the question being asked right now lands inside its own
        prior-conversation block.
        """
        if not self._chats.needs_seed():
            return ""
        return dialogue.seed_note(self._chats.messages())

    def session_rewind(self, message_id: str) -> dict | None:
        """Go back to an earlier point in the live conversation (AC-189).

        Through the brain like every other conversation method, for
        `session_list`'s reason (4.10). Nothing is done to a held child here:
        the rewind moves the session GENERATION, so the next turn's `ensure()`
        sees a changed spec and replaces the child on its own --
        `new_conversation`'s argument, reused rather than duplicated (AC-60).

        **THE GENERATION, NOT THE CLEARED `sessionRef`, AND THAT CORRECTION IS
        THE WHOLE BUG.** This docstring used to say clearing the reference was
        what changed the spec. `chats.rewind` said the opposite in the same
        repository -- nulling it "buys nothing", because the id does not change
        and the path is re-minted byte-identical -- and `chats.rewind` was
        right. The spec compared EQUAL, the held child was never disposed, and
        it went on answering with every turn the owner had just taken back
        still in its own memory. Reported by the owner on 2026-09-11 and
        measured at `disposals == 0`. `new_conversation` never had the problem
        because `rotate()` mints a new chat id; a rewind keeps the id, so the
        generation is what moves instead.
        """
        return self._chats.rewind(message_id)

    def session_search(self, query: str, limit: int | None = None,
                       belongs=None) -> list[dict]:
        """`session_list`, filtered by title or content (AC-185).

        Through the brain like every other conversation method, for
        `session_list`'s reason (4.10). A blank query is `session_list`
        exactly, so the route has one shape -- and that now carries the
        workspace scope with it, so the route has one call for all four
        combinations of scoped and searched.
        """
        return self._chats.search(query, limit=limit, belongs=belongs)

    def session_chat(self, chat_id: str,
                     limit: int | None = None) -> dict | None:
        """One PAST conversation, in `session_transcript`'s shape (AC-164).

        None when this daemon holds no record with that id, which the route
        answers as a 404 naming it.

        READ-ONLY and it stays that way: this is the peek that changes
        nothing. Making a conversation the one a turn goes to is
        `session_open`, which is a POST for exactly that reason.
        """
        return self._chats.find(chat_id, limit=limit)

    def session_open(self, chat_id: str,
                     limit: int | None = None) -> dict | None:
        """Make one conversation the one the next turn goes to (AC-240).

        `session_transcript`'s shape, so the page repaints the conversation it
        just opened with the renderer it already has, and None for an id
        nobody has -- which the route answers as a 404 naming it.

        **IT SPAWNS NOTHING** (AC-239). No child is started, disposed or asked
        anything: the next turn's `desired()` resolves THIS conversation's
        session file, the spec differs from whatever is running, and
        `ensure()` replaces the child through the one comparison AC-60 already
        requires. Browsing history must never start a full-capability coding
        agent, and the cheapest way to keep that true is for this path to have
        no spawn in it at all.

        **THE WORKING DIRECTORY FOLLOWS FOR FREE**: the next spawn reads the
        opened conversation's own `project` (`_session_cwd`, R-B), so there is
        no in-memory move to clear here.

        The shape comes from `session_transcript` rather than being built here
        a second time: `open()` has just made this conversation the current
        one, so the two answer about the same record, and "the same shape as
        `GET /api/session/current`" is then true by construction instead of by
        two dict literals somebody has to keep level (D-18).
        """
        if self._chats.open(chat_id) is None:
            return None
        return self.session_transcript(limit=limit)

    async def session_delete(self, chat_id: str) -> bool:
        """Throw one PAST conversation away for good. False if it was gone.

        Reached through the brain like every other conversation method here,
        for `session_list`'s reason (4.10): a route that opened
        `state/chats/` itself would be a second reader of the record, and
        the two would eventually disagree about which files in there are
        conversations.

        Deleting the LIVE conversation is allowed and the store rotates
        before it unlinks -- see `ChatStore.delete`.

        **THE CHILD IS DISPOSED HERE, AND THAT IS THE ONE PLACE AC-60 CANNOT
        REACH.** This used to do nothing to it, reasoning from AC-60 that the
        next turn's `ensure()` would see a changed spec -- but a DELETED
        conversation has no next turn, and no route, no click and no spoken
        phrase can name it again. Measured at a cap of 2 on 2026-09-12: the
        agent was still alive and still counted while its conversation was
        gone from the sidebar and could not be opened or stopped, so the only
        way to get the slot back was restarting the daemon. Disposing FIRST,
        because the record going away is what makes the child unreachable.
        """
        await self.dispose(chat_id)
        return self._chats.delete(chat_id)

    async def new_conversation(self, project: str | None = None,
                               focus: bool = True,
                               origin: dict | None = None) -> str:
        """A new conversation is a new FILE, BORN IN A PROJECT (10.2, AC-287).

        `focus=False` births WITHOUT taking the owner's conversation away --
        a scheduled run's chat (docs/automations.md AU-7, AC-461); `origin`
        marks it (AU-8). Both go straight to `ChatStore.rotate`, the one
        place a record is minted.

        `project` is the directory the conversation belongs to for the whole
        of its life (`docs/filing-and-pacing.md` 8, R-A): there is no method
        that re-files it, so this is the one moment the link is made. None
        carries the outgoing conversation's project (`ChatStore.rotate`).

        **AC-31's SECOND GATE RUNS HERE, AT BIRTH** (D-A2), where it used to
        run at the spoken MOVE (`work_in`, deleted with the move itself).
        Checked before the record is minted, and it has to BE a directory --
        `POST /api/terminal` has checked that since AC-148 -- because a chat
        born somewhere no child can start would tell the owner it is ready and
        fail on the first question. Raises `ValueError` naming why; nothing is
        minted.

        Still a RECORD operation with a child held (AC-59): rotating mints a
        new `sessionRef`, so the next turn's `ensure()` sees a spec that no
        longer matches and replaces the child on its own (AC-60). Disposing
        here as well would be a second mechanism for the same rule.
        """
        if project is not None:
            self._spec_for(project)    # raises for a directory nobody configured
            if not Path(project).is_dir():
                raise ValueError(
                    f"{project} is not a directory, so no coding agent can be "
                    f"started there. Check the project's path.")
        return self._chats.rotate(project, focus=focus, origin=origin)["id"]

    def forget_session_ref(self) -> None:
        """The harness says its own session reference is gone (AC-35, 4.4c).

        The reference is cleared and Butler's record is KEPT, and the turn is
        then replayed. Called only on the harness's literal words for a
        missing session: any broader rule discards the agent's context
        because something unrelated failed.

        The replay lands on a DIFFERENT reference only for a harness whose
        reference Butler could not have guessed -- an opaque id off a
        `--resume` (`caps.native_resume`). For pi, `ChatStore.session_ref`
        derives the path from the chat id, so clearing it and asking again
        returns the same path; that is harmless because this whole path is
        INERT for pi, which CREATES a missing session file rather than
        refusing it. Both halves wait here for the claude adapter (10.7).
        """
        self._chats.clear_session_ref()

    async def dispose(self, chat_id: str) -> None:
        """Drop ONE conversation's child, and free its slot.

        Idempotent, because every path that ends a turn badly calls it and the
        daemon's restart calls it too (D-1). Keyed, because a turn in one
        conversation disposing another's agent is AC-241's whole failure -- and
        an unkeyed dispose is the version of it nothing else would catch, since
        the turn that did the damage still answers correctly.

        `_detach_child` does the LIVE-CHILD half (D-18: one drop, not one per
        caller) -- LOAD-BEARING on its own for the paths OUTSIDE `turn()`'s
        generator (`dispose_all()`, AC-62, a restart; and `session_delete`),
        which never run that generator's `finally` at all: a child parked on
        a question when the daemon restarts has a noted action and no turn
        left to clear it. `_release_project_claim` does the other half: a
        chat id being disposed for good holds no project slot either way,
        idle or not (docs/filing-and-pacing.md 1.6) -- a gone child
        answering "idle since when" or "claiming which project" is a
        question with no meaning.
        """
        held = self._detach_child(chat_id)
        self._release_project_claim(chat_id)
        if held is not None:
            await held.harness.dispose()

    async def dispose_all(self) -> None:
        """Every child, for the daemon's own teardown (AC-62).

        A restart lands between turns, which is exactly when children are
        held, and `os.execv` keeps the process handles -- so with ten
        conversations this is ten coding agents that would otherwise survive
        into the new image with nobody holding them (AC-36, D-24).
        """
        for chat_id in list(self._children):
            await self.dispose(chat_id)


class _RunningTurn:
    """One conversation's in-flight turn: its id, and how it is ending.

    `_Child`'s own precedent (AC-241) one class up: `HarnessConverse` used to
    hold `_turn_id`/`_chat_id`/`_aborted`/`_completed` as SCALARS on the one
    instance every conversation shares, so a SECOND conversation merely
    starting a turn read as "the previous turn never released" and disposed
    the FIRST conversation's child -- measured, `docs/filing-and-pacing.md`
    item 1's own review. That guard's premise was "`hub._reply` closes the
    generator through `_let_go` on every exit, so in production this is
    unreachable" -- true until AC-249 let a turn legitimately SUSPEND on a
    question instead of always ending or being walked away from. A turn
    parked on a human is exactly a turn whose generator has not closed.

    One of these per chat id, in `HarnessConverse._turns`, so the guard at
    the top of `turn()` can ask "did THIS conversation's own previous turn
    fail to release" instead of "did ANY conversation's". The reentrancy case
    the guard exists for -- the SAME conversation's second turn starting
    before its first released -- still fires exactly as before; what stops
    is a different conversation tripping it.
    """

    __slots__ = ("turn_id", "aborted", "completed", "answered", "kept")

    def __init__(self, turn_id: int):
        self.turn_id = turn_id
        # Barge-in landed on THIS turn. The child is being taken away on
        # purpose, so the stream ending is not a failure to replay or
        # announce.
        self.aborted = False
        # This turn's prompt reached its terminal event, so the child owes
        # nothing and may be HELD for the next question (AC-59). False is the
        # careful value: it is what an abandoned, aborted or failed turn
        # leaves behind, and those are exactly the turns whose child must go.
        self.completed = False
        # Every sentence this turn has SHOWN, in order -- `_answer`'s list,
        # held here so a stop can still record it (AC-394). And whether the
        # assistant half of this turn is already on record (an answer, a
        # failure line), so a stop never writes a second one.
        self.answered: list[str] = []
        self.kept = False


class HarnessConverse:
    """The substantive half of harness mode: one prompt to a coding agent.

    Not registered as a Brain on its own, for the reason `EndpointConverse` is
    not: it answers everything it is given, so running it without the router
    would silently drop the routing the whole design rests on.
    """

    # ONE line, spoken the moment a turn reaches this file and BEFORE any
    # spawn. It stays on EVERY turn rather than only the ones that spawn: with
    # a child held (AC-59) the first question of a conversation pays the
    # 1.5-2.3 s startup and the rest pay the agent's own thinking time, and
    # both are silence after the owner stops speaking, which is what reads as
    # a dead microphone (D-10, docs/harness-mode.md 10.7). It is the SAME line
    # that used
    # to be spoken on the turn's first tool, and there is now only one place
    # that says it: two identical sentences in one turn is a defect, and of
    # the two the earlier one is the one that covers real silence -- the
    # first tool arrives after the child is already up and thinking. So the
    # first-tool narration is GONE, not suppressed, and
    # `test_no_line_is_ever_spoken_twice_in_one_turn` fails if it comes back.
    # SPOKEN AND NOT KEPT since 2026-09-11 (`ports.Say.keep`). It is a status
    # and it was being recorded like an answer: every harness reply in the
    # transcript, in `hub.history` and in `chats.py` opened with "Working on
    # it." and then the real answer, so a conversation reopened next month
    # reads as Butler having said it twice. Heard in the room, nowhere else.
    NARRATION = "Working on it."
    # Reports what happened and offers no advice, like the endpoint lines. The
    # harness's own reason goes to the log rather than into the room: it is a
    # stderr tail or a provider's JSON, and reading that aloud is not an answer.
    # A child that will not START is spoken with this line too: there is no
    # such thing as "no child" to refuse over, only a spawn that failed, and a
    # failed spawn is a failed turn.
    FAILED = "The harness could not finish that one."
    NOTHING_CAME_BACK = "The harness finished without saying anything."

    def __init__(self, host: _Host):
        self._host = host
        # ONE running turn PER CHAT ID (AC-241's own shape, one class up:
        # `_Host._children`). `abort(turn_id)` still tells "stop what you are
        # doing" from "stop something else" (AC-43) -- `Harness.abort()`
        # takes no turn id, and the Brain port contract (`ports.py`) takes
        # only a turn id too, so a reverse lookup happens inside this class
        # rather than by widening a port every caller would have to follow.
        # Turn ids are minted from one monotonic counter for the whole daemon
        # (`Hub._turn_seq`), so a linear scan here answers "which chat is
        # THIS turn's" without a second dict to keep in sync with this one
        # (D-18) -- and it is bounded by how many conversations may hold a
        # child at once (`harness_agents_max`, 1 to 50), never by how many
        # conversations exist.
        self._turns: dict[str, _RunningTurn] = {}
        # Questions the child has asked and nobody has closed yet live on the
        # CHILD, in `_Host` (`_Child.questions`): an answer has to reach the
        # child that raised it, and a blocked child is holding a slot the cap
        # has to be able to account for. What is read here is only whether this
        # turn's conversation has any -- a waiting child emits nothing by
        # definition, so `HARNESS_IDLE_S`, which exists to catch a child that
        # emits nothing, would otherwise fire at three minutes and take the
        # question with it.

    async def turn(self, text: str, ctx: TurnContext,
                   instruction: str | None = None) -> AsyncIterator[Intent]:
        # `instruction` is the router's cleaned reading of `text`, and it
        # arrives only on the dispatch route (AC-73, AC-28, via
        # `HarnessBrain.proceed`). It never displaces `text`: the owner's own
        # words are what goes into the record below, and the prompt is the
        # only thing cleaned.
        #
        # WHICH CONVERSATION, resolved FIRST so the reentrancy guard below can
        # be scoped to it (AC-241). TOLD rather than resolved a second way
        # (AC-246): the daemon decided this in `hub._begin_turn`, through the
        # one resolver that reads the device's own view; reading `current()`
        # here was a second answer to the same question, and the two
        # disagreed the moment a device was showing a conversation that was
        # not the daemon's pointer.
        chat = self._host.chat_for_turn(ctx.chat)
        chat_id = chat["id"]
        # A turn whose frame was never finalized still owns the child, and its
        # prompt may still be in flight (D-18). Reusing that child would put a
        # second prompt on a busy one and read the first prompt's ending as
        # this turn's answer, so it is dropped LOUDLY -- never silently, and
        # never fatally, because housekeeping may not cost the microphone
        # (D-1). `hub._reply` closes the generator through `_let_go` on every
        # exit, EXCEPT while a turn is legitimately parked on `Ask` (AC-234,
        # AC-249 reversed AC-49): a coding agent waiting on a human is a
        # generator that has not closed, so this is reachable in production
        # now, and only for THIS conversation's own previous turn -- never a
        # different one's, which is what a bare `self._turn_id` scalar used
        # to compare against, disposing conversation B's child the moment
        # conversation A's turn merely started (measured,
        # `docs/filing-and-pacing.md` item 1's own review).
        prior = self._turns.get(chat_id)
        if prior is not None:
            print(f"[butler] WARNING: turn {prior.turn_id} never released "
                  f"the harness child for conversation {chat_id!r} before "
                  f"turn {ctx.turn_id} asked for one. Disposing it rather "
                  f"than prompting a child that may still be working "
                  f"(AC-59, AC-241, D-18)")
            await self._host.dispose(chat_id)
        # The state THIS turn owns, held for the whole frame in a LOCAL as
        # well as in the registry: the local is what `finally` below checks
        # against identity, because a later turn on the SAME conversation
        # overwrites the registry entry and this frame's own cleanup must
        # then do nothing (the later turn owns that cleanup instead).
        state = _RunningTurn(ctx.turn_id)
        self._turns[chat_id] = state
        # A new turn never inherits the previous one's suspension (AC-234).
        # Leaving one behind would disarm `HARNESS_IDLE_S` for a turn nobody
        # is waiting on, which is the silent-child defect D-34 is named after
        # with its own guard turned off.
        self._host.close_questions(chat_id)
        # A new turn never inherits the previous one's IDLE mark either
        # (docs/filing-and-pacing.md 1.6): this child is about to be busy
        # again, whether it is reused as-is or replaced below, and reading
        # it as idle from its last turn while this one is running is exactly
        # the per-project reaper's failure mode.
        self._host.clear_idle(chat_id)
        try:
            # The owner's words go into the record BEFORE anything can fail,
            # and once (4.4c). This used to live past the spawn, so a spawn
            # that failed recorded only Butler's apology -- the owner's live
            # chat file carries two assistant entries with no question in
            # front of them [READ 2026-08-19,
            # state/chats/chat-1787089456529-c51265.json], which reads back as
            # Butler apologising to nobody. A replay does not record it again:
            # a retry is Butler's business, and the same utterance twice would
            # read as the owner having said it twice.
            # BEFORE the record, and that order is the whole of it (AC-193):
            # the seed is the transcript the harness has FORGOTTEN, and the
            # utterance being asked right now is not part of it. Recording
            # first would put the live question into its own prior-conversation
            # block, where a model reads it as something it already answered.
            #
            # These two now run BEFORE the narration, where they used to run
            # after it (AC-237). What decides that order is the cap below: a
            # refusal has to be able to speak instead of "Working on it."
            # rather than after it, and it must still have the question in
            # front of it in the record, which is this comment's own rule.
            # The cost is the pair of them -- a read of the held messages and
            # one atomic JSON write -- landing in front of the first spoken
            # word instead of behind it.
            seed = self._host.seed_note()
            self._host.record(chat_id, "user", text)
            try:
                # DECIDED before the narration and SPAWNED after it. Resolving
                # what this turn's child must be raises for the two reasons the
                # room hears differently: a directory nobody configured is a
                # failure (AC-31), and the cap is a refusal.
                spec = await self._host.plan(chat)
            except CapReached as e:
                # NOT a failure and not spoken as one (AC-237, AC-245). The
                # daemon is declining, the sentence names where the slots are,
                # and nothing is queued: a queue is a promise and Butler has no
                # surface that shows one. Caught ABOVE the generic handler
                # because that one speaks "the harness could not finish that
                # one", which would be a lie about a turn no agent ever saw.
                async for intent in self._refuse(chat_id, str(e), ctx):
                    yield intent
                return
            except Exception as e:                        # noqa: BLE001
                async for intent in self._give_up(
                        chat_id, ctx,
                        f"the harness child would not start: {e!r}"):
                    trace.trace("harness_failed", turn=getattr(ctx, "turn", "-"),
                                error=repr(str(e))[:200])
                    yield intent
                return
            # BEFORE the spawn, and spoken on EVERY turn rather than only the
            # ones that spawn (D-10). The first question of a conversation
            # pays the 1.5-2.3 s startup and the rest pay the agent's own
            # thinking time; both are silence after the owner stops speaking,
            # and silence is what reads as a dead microphone.
            yield Say(dialogue.line(self.NARRATION, ctx.language), tier="reflex",
                      keep=False)
            try:
                await self._host.ensure(chat, spec)
            except Exception as e:                        # noqa: BLE001
                async for intent in self._give_up(
                        chat_id, ctx,
                        f"the harness child would not start: {e!r}"):
                    # The most common real failure in harness mode, and the
                    # one the daemon console recorded while the trace could
                    # not: a model id the harness does not have kills every
                    # turn identically, and from the page all you see is
                    # "Working on it." followed by nothing.
                    trace.trace("harness_failed", turn=getattr(ctx, "turn", "-"),
                                error=repr(str(e))[:200])
                    yield intent
                return
            # ONE per turn, right where the child is confirmed up and BEFORE
            # anything is prompted (docs/parallel-agents.md phase 7, AC-233,
            # AC-235). `elapsed_ms` is stamped by the hub from the turn-start
            # record `_begin_turn` already keeps, so a conversation with no
            # tool event still has a frame to show its AGE by -- the room
            # cannot hear a silent text generation, and this is the one thing
            # between "Working on it." and the answer for it.
            yield WorkerEvent(kind="started")
            # The child gets ONE string, so the label is the only thing
            # between half a minute of a room and the sentence the owner
            # actually said (AC-70, docs/ambient.md 4). The request goes LAST
            # and the overheard block in front of it, so the instruction can
            # never read as a continuation of the room. Built ONCE, here,
            # because `_answer` replays the same object verbatim (4.4c) --
            # and built around `instruction or text`, so the cleaned reading
            # of a dispatched utterance is still what is asked (AC-73).
            # Nothing is wrapped when nothing was overheard: with ambient off
            # (AC-68) the prompt is the string it was before this existed.
            # The REWIND SEED goes in front of the overheard block and both go
            # in front of the request (AC-193). Order by how far each is from
            # the thing being asked: what the agent said before is furthest,
            # what the room said is nearer, and the instruction is last.
            # Nothing is wrapped when there is neither, so an ordinary turn's
            # prompt is the string it was before either existed.
            note = dialogue.overheard_note(ctx)
            # AC-201. A coding agent can OPEN a file, so this block names the
            # path and an attachment is useful here even when no model can
            # see -- which is most of items 5 and 6 for almost
            # none of the work. Labelled like the room is, and for the same
            # reason: a filename with no label reads as part of the request.
            attached = dialogue.attached_note(ctx)
            prompt = instruction or text
            blocks = [b for b in (seed, note, attached) if b]
            if state.aborted:
                # A SIBLING of `_answer`'s held-is-None window, and it does
                # not crash, which is what makes it easy to miss: `abort()`
                # can land during the `yield Say(NARRATION)` above or during
                # `await self._host.ensure(...)`'s own spawn, and if `ensure`
                # then finishes normally -- a FRESH child, because the one
                # `abort()` disposed is gone -- `_answer`'s `held` is no
                # longer None. Sending that child the abandoned prompt would
                # still be answering a question the owner cut off (AC-43),
                # just without the exception this method's other guard
                # catches. Disposed here rather than left for `turn()`'s own
                # `finally`, which only runs once this generator is done --
                # by then the prompt would already be sent.
                await self._host.dispose(chat_id)
                return
            async for intent in self._answer(
                    "\n\n".join(blocks + [f"Request: {prompt}"])
                    if blocks else prompt, ctx, chat, state):
                yield intent
        finally:
            # A child is held only between turns that ENDED (AC-59). This
            # frame is where every OTHER ending arrives: `hub._reply` walks
            # away from this generator on a dead speech segment (3.8, D-4) and
            # on its own swallowed exception, and closes it through `_let_go`,
            # which lands here as a GeneratorExit. The prompt is then still in
            # flight and the child still owes a terminal event nobody will
            # read -- and a held child would hand that leftover ending to the
            # next thing the owner said, which is the defect the whole
            # one-reader design exists to prevent. So an unfinished turn
            # disposes; a finished one does not.
            #
            # Guarded on still owning the turn, and that guard is load-bearing
            # rather than tidy: this frame can be finalized LATE, after the
            # next turn has already started, and an unguarded dispose here
            # would kill the child that is currently answering (D-6's identity
            # re-check, and D-1 if it lands mid-turn). IDENTITY rather than a
            # turn-id comparison, because `state` is this call's own object:
            # a later turn on the SAME conversation replaces the registry
            # entry with a DIFFERENT `_RunningTurn`, and this frame's
            # cleanup must then do nothing -- that later turn owns it.
            if self._turns.get(chat_id) is state:
                # A STOPPED TURN KEEPS WHAT IT SHOWED (AC-394,
                # docs/turn-feedback.md). The record used to be written only
                # at the terminal event, so a stop left the question with
                # nothing under it and a reload showed exactly that. `aborted`
                # is the stop's own mark -- `abandon()` and a chat-only stop
                # both reach `abort()` before any task is cancelled -- and it
                # is written FIRST in this frame, before any await, so the
                # hub's `_mark_unspoken` (which runs after this generator is
                # closed) finds the answer it marks. Empty when nothing was
                # shown yet: the marker alone still says what happened.
                if state.aborted and not state.kept:
                    state.kept = True
                    self._host.record(chat_id, "assistant",
                                      " ".join(state.answered).strip(),
                                      stopped=True)
                del self._turns[chat_id]
                # Every question this turn raised dies with it, answered or
                # not (AC-250's "the conversation was stopped"). The daemon
                # closes its own record on the same exit and broadcasts it;
                # this is the half that re-arms the idle deadline.
                self._host.close_questions(chat_id)
                # THE ONE PATH `dispose()`'s OWN CLEAR DOES NOT COVER
                # (docs/triage-list.md phase 2 continued): a turn that
                # COMPLETED holds its child for the whole conversation
                # (AC-59), so nothing below disposes it -- and a held child
                # whose last action was never dropped is exactly the stale
                # row the owner named. Unconditional, unlike the dispose
                # below: clearing twice is a no-op, clearing zero times is
                # the defect.
                self._host.clear_action(chat_id)
                if not state.completed:
                    await self._host.dispose(chat_id)

    async def _refuse(self, chat_id: str, said: str,
                      ctx: TurnContext) -> AsyncIterator[Intent]:
        """The cap's answer: said out loud, recorded, and no turn (AC-237).

        Recorded like any other thing Butler says, because a conversation whose
        record shows a question and no answer reads as an agent that ignored
        it -- and this is the one refusal the owner is meant to act on.
        Nothing is disposed here: a conversation already holding a slot is
        exempt from the count, so a refusal only ever reaches a conversation
        that holds no child.

        **IT SAYS SO WHEN A FILE WENT WITH IT.** `hub._reply` TAKES the
        session's pending attachments to build this context, and taking is
        what clears them -- so a send refused here has already consumed the
        photo the owner put up, and nothing told them. The file is still on
        disk; what is gone is the pending mark, and only the owner can put it
        back. Said rather than re-pended, because re-pending would hang a file
        on a session that may next speak into a different conversation
        (AC-247).
        """
        if ctx.attachments:
            said = (f"{said} The {len(ctx.attachments)} file(s) you attached "
                    f"went with that message -- attach them again.")
        print(f"[butler] the agent cap refused a send: {said}")
        self._host.note_refused(chat_id, said)
        self._host.record(chat_id, "assistant", said)
        yield Say(said, tier="conversation")
        yield Done("conversation")

    async def _answer(self, prompt: str, ctx: TurnContext, chat: dict,
                      state: _RunningTurn) -> AsyncIterator[Intent]:
        """One prompt, its replay if it fails, and exactly one `Done`.

        `prompt` is what the CHILD is asked, which on a dispatched turn is the
        router's cleaned instruction rather than the transcript (AC-73). The
        owner's own words never come this far: `turn()` has already put them
        in the record.

        `state` is passed rather than looked up by `chat_id`, because a
        reentrant turn on the SAME conversation (the case the guard in
        `turn()` still handles) replaces `self._turns[chat_id]` with a
        DIFFERENT `_RunningTurn` while this call is still running -- a lookup
        here would then read the NEW turn's flags instead of this call's own
        (AC-241, D-18).
        """
        spoke = 0
        attempt = 0
        respawn = False
        # EVERYTHING THE ROOM HEARS GOES IN HERE, and it is what becomes the
        # record at the bottom of this method. There are THREE paths that
        # speak -- the streamed sentences below, the leftover `tail`, and
        # AC-47's non-streaming recovery -- and until 2026-09-11 only the
        # first of them appended. MEASURED against the fake harness with
        # `stream=False`, which is AC-47's own measured provider: Butler spoke
        # 95 characters and recorded ZERO. The page showed the answer live and
        # the same conversation reopened showed the question with nothing under
        # it, because `closeItem` writes "no reply came back for this turn".
        # `test_brain_harness.py`'s AC-47 case asserted what was SAID and never
        # what was KEPT, which is D-18 exactly: the callee was green and the
        # caller's record was empty.
        answered: list[str] = state.answered
        # The user's half of this turn is already in the record: `turn()` puts
        # it there before the spawn, so a spawn that fails still leaves a
        # question in front of the apology.
        #
        # WHICH CONVERSATION, named once and carried (AC-241). Every reach for
        # a child below goes through this id; a bare "the child" read would be
        # the crossed wire the registry exists to make unrepresentable.
        chat_id = chat["id"]
        while True:
            attempt += 1
            if respawn:
                # The child died with the prompt, so the replay needs a new
                # one -- on the SAME session file, which is what carries the
                # conversation across the second attempt (AC-35).
                try:
                    await self._host.restart(chat)
                except Exception as e:                    # noqa: BLE001
                    async for intent in self._give_up(
                            chat_id, ctx,
                            f"the harness would not restart: {e!r}"):
                        yield intent
                    return
                respawn = False
            held = self._host.child(chat_id)
            if held is None:
                # Barge-in can land in the gap between the last yield and
                # here on EVERY pass through this line -- the plain entry
                # (phase 7's `WorkerEvent(kind="started")` now yields before
                # `_answer` is even called, AC-233) and the respawn one
                # (`await self._host.restart(chat)` above is itself a
                # suspension point). `abort()` sets `state.aborted` before its
                # own first await, so it is always readable here however far
                # its dispose has actually run (D-18). Aborted: nothing is
                # sent and nothing is announced, same as the mid-stream case
                # below (AC-43, D-6). Not aborted: the child is gone for a
                # reason this method does not know, and that is a failed turn
                # like any other rather than a crash (D-1).
                if state.aborted:
                    return
                async for intent in self._give_up(
                        chat_id, ctx, "the harness child is gone"):
                    yield intent
                return
            harness = held.harness
            stream = held.stream
            # VERBATIM (4.4c), and the rule is about the REPLAY. `prompt` is
            # the string this turn was handed and a replay sends that same
            # object -- rebuilding a prompt from a text history is what
            # silently drops images. Cleaning it ONCE, before the first send,
            # is a different act and is AC-73: the router decides what the
            # utterance meant, up there in `turn()`, and from here on the
            # string is untouched however many times it is sent.
            try:
                await harness.prompt(prompt)
            except Exception as e:                        # noqa: BLE001
                # A prompt that goes nowhere is a turn that never ends, which
                # is why the port raises here rather than queueing. It spends
                # the SAME one-replay budget: a fresh child on the same
                # session file, once. The room hears it either way (AC-47) and
                # the turn ends either way (D-1).
                print(f"[butler] turn {ctx.turn_id}: the harness would not "
                      f"take the prompt: {e!r}")
                if attempt == 1 and spoke == 0:
                    self._forget_a_dead_session(repr(e))
                    respawn = True
                    continue
                async for intent in self._give_up(
                        chat_id, ctx,
                        "the harness would not take the replay either"):
                    yield intent
                return
            buffer = ""
            turn_texts: list[str] = []
            failure: str | None = None
            timed_out = False

            while True:
                # Read with an IDLE deadline rather than an `async for`
                # (D-34). A child that REFUSES a prompt fails fast and
                # visibly; a child that ACCEPTS it and then emits nothing had
                # no bound at all, and the turn stayed open with the
                # microphone held (D-1). MEASURED 2026-09-09: past 25 s with
                # one `Say` delivered and no terminal event, which is what
                # turn 1 of 2026-09-08 looks like in `butler.log` -- a wake, a
                # user message, then 158 s of nothing.
                #
                # SUSPENDED WHILE A QUESTION IS OPEN (AC-234, AC-49
                # reversed). `wait_for(..., None)` is a plain await: a child
                # parked on a dialog is silent on purpose, and the deadline
                # that exists to catch accidental silence would otherwise end
                # the turn at three minutes and take the question with it --
                # the bucket emptying itself, which `docs/parallel-agents.md`
                # names as one of the three ways AC-234 fails. What bounds a
                # waiting turn instead is the owner: stop ends it, and pi's
                # own `deadline_ms` resolves a timed dialog agent-side, after
                # which the child emits again and this re-arms on its own.
                try:
                    event = await asyncio.wait_for(
                        anext(stream),
                        None if self._host.questions_open(chat_id)
                        else HARNESS_IDLE_S)
                except StopAsyncIteration:
                    # The stream ENDED without a terminal event: the child is
                    # gone. Treated as a failure rather than as an answer,
                    # because the alternative is announcing an answer nobody
                    # produced.
                    respawn = True
                    failure = "the harness stream ended before the turn did"
                    break
                except (asyncio.TimeoutError, TimeoutError):
                    respawn = True
                    timed_out = True
                    failure = (f"the harness went silent for "
                               f"{HARNESS_IDLE_S:.0f}s without ending the "
                               f"turn")
                    break
                if isinstance(event, TextDelta):
                    # EVIDENCE BEFORE SENTENCES (AC-390). The splitter can hold
                    # text back for minutes -- MEASURED 2026-09-26, a bullet
                    # list held 1:47 while the heartbeat called the turn quiet
                    # -- so what ARRIVED is reported, not only what was said.
                    yield WorkerEvent(kind="text", chars=len(event.text))
                    buffer += event.text
                    sentences, buffer = speech.drain_sentences(buffer)
                    for sentence in sentences:
                        spoke += 1
                        answered.append(sentence)
                        yield Say(sentence, tier="conversation")
                elif isinstance(event, TurnEnd):
                    if event.text.strip():
                        turn_texts.append(event.text.strip())
                elif isinstance(event, ToolStart):
                    # The LAST ACTION line the room cannot hear
                    # (docs/parallel-agents.md phase 7, AC-233, AC-235). Was
                    # silently dropped here -- no branch and no trailing
                    # `else` -- so every tool call this adapter has ever
                    # emitted (`harness_pi.py`) reached nobody.
                    #
                    # NOTED on `_Host` too (docs/triage-list.md phase 2
                    # continued), so a conversation nobody is looking at right
                    # now still has an answer when its row is next painted --
                    # `worker_event` alone only reaches the ONE session
                    # driving THIS turn (D-6).
                    self._host.note_action(chat_id, event.name)
                    yield WorkerEvent(kind="tool_start", detail=event.name)
                elif isinstance(event, ToolEnd):
                    yield WorkerEvent(
                        kind="tool_end",
                        detail=(f"{event.name} failed" if event.is_error
                                else event.name))
                elif isinstance(event, Activity):
                    # Reasoning, or a tool call's arguments being written
                    # (AC-388). Reaching this line already re-armed the idle
                    # deadline above; passed up only as evidence for the
                    # heartbeat, never spoken and never recorded.
                    yield WorkerEvent(kind=event.kind, chars=event.chars)
                elif isinstance(event, Ask):
                    # The child is blocked on a human (AC-234). Yielded and
                    # NOT awaited here: the daemon broadcasts it, the answer
                    # arrives through `POST /api/answer` and goes back to the
                    # child out of band, and this same loop picks the stream
                    # up again when the child does. A brain that awaited the
                    # answer itself would have to hold the route's reply path
                    # open across a wait with no bound.
                    self._host.note_question(chat_id, event.qid)
                    yield AskHuman(event)
                elif isinstance(event, Failed):
                    failure = event.message
                    break
                elif isinstance(event, AgentEnd):
                    break

            if state.aborted:
                # Barge-in took the child away mid-answer. Nothing is replayed
                # and nothing is announced: the daemon has already abandoned
                # this turn and is listening to whatever interrupted it (AC-43).
                return
            if failure is None:
                break

            print(f"[butler] turn {ctx.turn_id}: harness attempt {attempt} "
                  f"failed: {failure}")
            # Once, never a loop -- and only while nothing has been said. Half
            # an answer already in the room followed by a replay is the whole
            # answer spoken one and a half times.
            #
            # A SILENCE TIMEOUT is not replayed (D-34). The replay exists for a
            # transient blip, and a child that accepted the prompt and then
            # said nothing for the whole budget is not evidence of one -- while
            # replaying it costs a SECOND full budget of silence before the
            # room hears anything at all. At 600 s that is twenty
            # minutes of the owner waiting, which is worse than the wrong
            # answer this branch is trying to avoid.
            if attempt == 1 and spoke == 0 and not timed_out:
                # A DEAD session reference is the one failure that also has to
                # change what the next child is pointed at, or the replay
                # walks straight back into it (AC-35).
                self._forget_a_dead_session(failure)
                respawn = True
                continue
            async for intent in self._give_up(
                    chat_id, ctx, f"the replay failed too: {failure}"):
                yield intent
            return

        # The prompt is over at its terminal event, so the child owes nothing
        # and may be HELD for the next question (AC-59). Marked HERE rather
        # than after the `Done`, because the daemon breaks out of this
        # generator on `Done` and never resumes it: anything written after it
        # runs at the garbage collector's convenience, and this flag decides
        # whether the turn's `finally` disposes.
        state.completed = True

        # `rstrip`, NOT `strip`. The LEADING whitespace here is the block
        # separator `speech.drain_sentences` carried onto the leftover, and it
        # is what puts the last block of a reply on its own line. Stripping it
        # welded the final list, fence or table onto the paragraph before it
        # with a joining space (2026-09-10). A whitespace-only buffer rstrips
        # to "" on its own, so `if tail:` below needs no guard beside it.
        tail = buffer.rstrip()
        if tail:
            # The last sentence, when the harness stopped without a terminator
            # after it.
            spoke += 1
            # RECORDED as well as spoken. `answered` used to be fed only by
            # the TextDelta branch above, so every piece that reached the room
            # through this line or the recovery below was heard, drawn live,
            # and then missing from `chats.py` -- see the block comment on
            # `answered` where it is declared.
            answered.append(tail)
            yield Say(tail, tier="conversation")
        if not spoke and turn_texts:
            # AC-47: the measured provider that streams NOTHING. Guarded on
            # having said nothing at all, because `turn_end` carries the whole
            # message on every provider -- streaming ones included -- so an
            # unguarded recovery repeats the entire answer.
            #
            # A paragraph break between turns, as the streamed path gets from
            # the adapter's own deltas (ports.TextDelta): each turn is the
            # text on one side of a tool call. A space here was the no-stream
            # half of the glue in docs/e2e-journeys.md.
            for sentence in speech.split_sentences("\n\n".join(turn_texts)):
                spoke += 1
                answered.append(sentence)
                yield Say(sentence, tier="conversation")
        if not spoke:
            print(f"[butler] turn {ctx.turn_id}: the harness turn produced no "
                  f"text at all")
            yield Say(dialogue.line(self.NOTHING_CAME_BACK, ctx.language),
                      tier="conversation")
        # THE CHILD IS IDLE, as of exactly now (docs/filing-and-pacing.md
        # 1.6). Set HERE, at the prompt's own terminal event, rather than in
        # `turn()`'s `finally` below: that frame also runs when a survived
        # disconnect (`017f8a1`) has walked away from the socket but the
        # turn itself is still mid-generation, and marking idle there would
        # be marking a BUSY child idle the moment its owner merely stopped
        # watching (section 7 risk #2). This line only runs once the prompt
        # has actually ended.
        self._host.note_idle(chat_id)
        # What the agent actually answered, one record per turn: the
        # transcript is a conversation, and a conversation is turns (4.4c).
        state.kept = True
        self._host.record(chat_id, "assistant", " ".join(answered).strip())
        # NO `mark_outcome(chat_id, "done")` here, and that absence is
        # deliberate rather than an oversight (D-13 caught the first draft
        # doing it and the suite stayed green without it): AC-59 holds this
        # conversation's child for the whole conversation, so `status_for`
        # answers "done" from the child's own PRESENCE before it ever reads
        # `last_outcome` -- a write nothing reads is a claim, not a
        # contract, and `Action.writes`'s rule is why it goes rather than
        # stays "just in case".
        yield Done("conversation")

    def _forget_a_dead_session(self, failure: str) -> bool:
        """Discard the session reference, and ONLY on the harness's own words.

        A stored reference that outlived what it points at answers nothing for
        as long as nobody notices -- eight of the predecessor's chats died this
        way and one of them was silent for two weeks (4.4c, AC-35). Clearing it
        costs the agent's context and keeps Butler's record, and the turn is
        then replayed once by the loop above, so the room hears an answer
        rather than a failure the owner has to diagnose.

        The gate is LITERAL for the opposite defect: a rule that cleared on any
        failure would throw a conversation away because a provider blipped.
        """
        if not chats_mod.names_a_missing_session(failure):
            return False
        self._host.forget_session_ref()
        return True

    async def _give_up(self, chat_id: str, ctx: TurnContext, why: str
                       ) -> AsyncIterator[Intent]:
        """The turn is over and it failed: one spoken line, one `Done`.

        The harness's own reason goes to the LOG and to Butler's record. It
        is a stderr tail or a provider's JSON, and reading that into the room
        is not an answer -- but the room must hear that something went wrong,
        because silence is the one wrong ending (AC-47, AC-48, D-1), and the
        record must hold it too, or the transcript shows a turn the agent
        simply never answered (4.4c).

        The child goes, and it goes BEFORE the words. A failed prompt leaves a
        child in a state Butler cannot describe -- it may still be working, it
        may owe a terminal event nobody read -- and holding that into the next
        question is how a leftover ending becomes the next turn's answer
        (AC-59). Before the words rather than after, because the daemon stops
        reading at `Done` and anything past it runs at the garbage collector's
        convenience.
        """
        print(f"[butler] turn {ctx.turn_id}: {why}")
        # What the ROOM heard, not the reason: the flat chat file holds
        # messages, and the harness's own stderr tail is not one. A turn the
        # transcript shows as unanswered would be a worse lie than a short
        # one (4.4c).
        said = dialogue.line(self.FAILED, ctx.language)
        # On record, so a stop that races this failure writes nothing more
        # (AC-394). Looked up by chat: this method is handed no turn state,
        # and the only turn it can be ending is this conversation's own.
        running = self._turns.get(chat_id)
        if running is not None:
            running.kept = True
        self._host.record(chat_id, "assistant", said)
        # THE OTHER terminal outcome (docs/parallel-agents.md phase 7 item 1).
        # `_refuse` never reaches here -- the cap refusal is not a turn any
        # agent saw -- so this and `_answer`'s "done" are the only two writers.
        self._host.mark_outcome(chat_id, "error")
        # THE CHILD IS IDLE too, for `note_idle`'s own reason -- this is the
        # OTHER of the two places a turn ends (docs/filing-and-pacing.md
        # 1.6). The `dispose()` two lines below pops it again immediately in
        # THIS method, because a failed turn drops its child outright rather
        # than holding it (AC-59) -- but the mark is set here rather than
        # skipped, so the rule stays "the two places a turn ends" and not
        # "the two places, except when it also fails."
        self._host.note_idle(chat_id)
        # THIS turn's conversation and never "the" child (AC-241): a failed
        # turn in one conversation must not take another's agent down, and
        # that agent may be mid-edit. `chat_id` is the parameter above,
        # never a shared instance field -- there is no other conversation to
        # confuse it with.
        await self._host.dispose(chat_id)
        yield Say(said, tier="conversation")
        yield Done("conversation")

    # -- the human's half of an `Ask` ------------------------------------- #
    async def answer_question(self, qid: str, answer: Answer) -> bool:
        """Route one answer back to the dialog that raised it (AC-234).

        False means this child does not hold that question any more, and the
        ROUTE turns that into a 410 naming which of the four reasons (AC-250).
        It is never softened into a hopeful write: the harness drops an
        unknown id silently, so a forwarded late answer leaves the owner
        believing they approved something the agent already defaulted on.

        **IT GOES TO THE CHILD THAT ASKED, found by `qid`** (AC-241). With one
        child "the" child was the right answer for free; with ten, an answer
        written into whichever child happened to be held is a `select` resolved
        in another project's repository. `_Host` knows which child holds a
        question because the question is registered against it.

        The open-question entry is dropped either way. A question the child has
        let go of must not go on suspending the idle deadline, whichever side
        let it go first.
        """
        harness = self._host.child_for_question(qid)
        self._host.close_question(qid)
        if harness is None:
            return False
        return await harness.answer(qid, answer)

    def forget_question(self, qid: str) -> None:
        """Stop waiting on `qid` without answering it.

        The daemon's path for a question that closed for a reason that is not
        an answer -- the harness's own deadline passing, or the conversation
        ending. It sends the child NOTHING, which is AC-234's one absolute:
        Butler never auto-answers, because a resolved default is
        byte-identical to the owner having said no.
        """
        self._host.close_question(qid)

    async def abort(self, turn_id: int) -> None:
        """Barge-in, and only for the turn that is actually running (AC-43).

        An abort keyed by session rather than by turn would cancel the reply to
        the utterance that caused the barge-in, which is the one turn the owner
        is waiting for. An abort for a turn that has already ended is a no-op:
        barge-in races completion by nature (D-1).

        ABORT AND THEN DISPOSE, in that order, and the DISPOSE survives AC-59
        deliberately. A held child would be the cheaper barge-in -- pi settles
        an abort in 1.51 s and stays usable [MEASURED 2026-08-17] -- but the
        prompt it settles still emits its terminal event, and with the stream
        held across turns that ending is queued where the NEXT turn reads. The
        alternative is a caller-side settle loop draining the leftovers, which
        is the machinery the phase-7 simplification deleted and which cannot
        run here anyway: `_answer` is suspended on this same stream, and a
        second reader on one async generator raises. So barge-in costs one
        respawn on the next question, which is what every turn cost until
        AC-59.

        The abort still comes FIRST, and that is the divergence from the
        reference implementation, which tree-kills its child. claude's
        transcript lives in claude's own store and survives a kill; pi is
        mid-write to a session FILE Butler wants intact for the next turn, so
        it gets the graceful stop and the teardown second.

        Disposing HERE rather than leaving it to the turn's `finally` is D-18:
        barge-in cancels the reply task, and an async generator closed by
        garbage collection is a child process whose death is scheduled by the
        collector.

        WHICH CONVERSATION owns `turn_id` is found by SCANNING `self._turns`
        rather than by a second reverse-index dict (AC-241, D-18): the `Brain`
        port contract (`ports.py`) hands this method a bare turn id and
        nothing else, so a chat id has to be recovered from turn id alone,
        and `Hub._turn_seq` mints turn ids from one monotonic counter for the
        whole daemon -- a linear scan over at most `harness_agents_max` (1 to
        50) live conversations is cheaper than a second dict this method
        would otherwise have to keep in sync with `self._turns` on every
        write.
        """
        chat_id = None
        state = None
        for cid, running in self._turns.items():
            if running.turn_id == turn_id:
                chat_id = cid
                state = running
                break
        if chat_id is None:
            return
        state.aborted = True
        # THE INTERRUPTED CONVERSATION'S child, never "the" child (AC-241,
        # AC-242). Nine other agents may be running and none of them was
        # interrupted; reading a scalar here is how the wake word becomes a
        # kill switch for every one of them.
        held = self._host.child(chat_id)
        if held is not None:
            await held.harness.abort()
            await self._host.dispose(chat_id)

    async def warm(self) -> None:
        """Nothing to warm: no child exists until a turn asks for one.

        Kept because `dialogue.Dialogue.warm` calls both halves and boot calls
        that unconditionally. Spawning at BOOT would move the 1.5-2.3 s off
        the first question, but it would also start a full-capability coding
        agent in the owner's chosen project on a daemon nobody has spoken to
        yet -- and a restart is not a conversation. The first question pays it
        once; the reflex line covers it (AC-59, D-10).
        """

    @property
    def info(self) -> dict:
        return {"engine": "harness"}


class _HarnessSessions:
    """The conversation methods both harness brains hand to `_Host`.

    A base class rather than two copies, and it exists because there are two
    real users of it -- `HarnessBrain` and `HarnessOnlyBrain` (AC-66). The
    daemon reaches every one of these by CAPABILITY (`HAS_SESSIONS`), so a
    second brain that forgot one would declare a capability the hub then
    called into nothing.
    """

    host: _Host
    converse: "HarnessConverse"

    async def new_conversation(self, project: str | None = None,
                               focus: bool = True,
                               origin: dict | None = None) -> str:
        return await self.host.new_conversation(project, focus=focus,
                                                origin=origin)

    # docs/chat-agent-picker.md AP-1, AP-2: the agent of a conversation nobody
    # has spoken in yet. The ChatStore owns the rule; this is the capability.
    def choose_agent(self, chat_id: str, harness: str) -> dict:
        return self.host._chats.choose_agent(chat_id, harness)

    def is_agent_fixed(self, chat_id: str) -> bool:
        return self.host._chats.is_agent_fixed(chat_id)

    # docs/chat-model.md CM-1: the conversation's own model.
    def set_model(self, chat_id: str, model: str) -> dict:
        return self.host._chats.set_model(chat_id, model)

    # The question a coding agent asked, and the two ways it closes (AC-234).
    # Here rather than on `HarnessBrain` alone because `HarnessOnlyBrain` runs
    # the same `HarnessConverse` and its child raises the same dialogs, and a
    # capability declared on one of two brains is the name test AC-44 forbids
    # wearing a different hat.
    async def answer_question(self, qid: str, answer: Answer) -> bool:
        return await self.converse.answer_question(qid, answer)

    def forget_question(self, qid: str) -> None:
        self.converse.forget_question(qid)

    def record_note(self, chat_id: str, text: str) -> None:
        """One line of Butler's own into a NAMED conversation (AC-234, 9.4).

        What a closed question leaves behind: "Butler asked X, you answered
        Y". Nothing about a PENDING question is ever written -- every child
        is in a kill-on-close job object (`daemon/childproc.py`), so a
        persisted question is one whose only possible answerer is already
        dead and the next reader would trust it.

        **THE CONVERSATION IS AN ARGUMENT** (AC-241). `Hub.answer_question`
        has held `question["chat"]` since the question was raised and threw it
        away here, so the line landed in whatever `ChatStore.current()` named
        at the moment the owner answered -- and answering a question minutes
        later, after starting another conversation, is the ordinary case.

        Recorded as the ASSISTANT, because Butler is who says it. The role
        set is the flat chat file's and gaining a third one would be a schema
        change for one sentence.
        """
        self.host.record(chat_id, "assistant", text)

    def show(self, chat_id: str, role: str, entry: dict) -> None:
        self.host.show(chat_id, role, entry)

    def session_state(self) -> dict:
        return self.host.session_state()

    def session_transcript(self, limit: int | None = None) -> dict:
        return self.host.session_transcript(limit=limit)

    def session_list(self, limit: int | None = None,
                     belongs=None) -> list[dict]:
        return self.host.session_list(limit=limit, belongs=belongs)

    def session_search(self, query: str, limit: int | None = None,
                       belongs=None) -> list[dict]:
        return self.host.session_search(query, limit=limit, belongs=belongs)

    def session_rewind(self, message_id: str) -> dict | None:
        return self.host.session_rewind(message_id)

    def session_chat(self, chat_id: str,
                     limit: int | None = None) -> dict | None:
        return self.host.session_chat(chat_id, limit=limit)

    def session_open(self, chat_id: str,
                     limit: int | None = None) -> dict | None:
        return self.host.session_open(chat_id, limit=limit)

    async def session_delete(self, chat_id: str) -> bool:
        return await self.host.session_delete(chat_id)

    async def dispose(self) -> None:
        """Drop the held child, for the daemon's own teardown (AC-62).

        `butlerd._restart_cleanup` already looks for this method by name on
        the brain and has since the restart endpoint landed; it found nothing
        to call while a child lived for one message. With a child held between
        turns (AC-59) there is one to call: `os.execv` replaces the image but
        keeps the process handles, so a coding agent left running would
        survive into the new image with nobody holding it -- the same leak the
        kokoro sidecar taught, one seam over (AC-36, D-24).

        EVERY child, not one (AC-241): with a child per conversation there are
        up to `harness_agents_max` of them and a teardown that dropped only the
        current conversation's would leave the rest running with nobody holding
        their handles.
        """
        await self.host.dispose_all()


@ports.register(ports.BRAINS, "harness")
class HarnessBrain(_HarnessSessions, dialogue.Dialogue):
    """Router in front, a coding-agent harness behind (4.4 mode 3).

    Selected by the `brain_mode` setting. It runs the coding agent at FULL
    capability, `bash` included, in every permission mode -- the owner
    retired AC-29's spoken grant on 2026-08-18, so a `dispatch` verdict
    reaches this child with nothing between it and the room but the router's
    own decision and the configured project list (AC-31).
    """

    ENGINE = "harness"
    # This brain can CARRY OUT dispatched work itself, so an `AskConfirm` the
    # router raised is handed straight back to it (plan.md 4.10). A CAPABILITY
    # the hub reads, never a name it tests: the endpoint brain and the fake
    # have no agent to do the work and keep the Phase 1 refusal, and the next
    # brain that drives a harness sets this flag rather than editing hub.py.
    CAN_PROCEED = True
    # Two capabilities, declared rather than tested for by name (the rule
    # AC-35's `native_resume` set): the router needs the one `TargetResolver`
    # the daemon builds, and the host needs the two callables that decide what
    # child this turn gets. Resolvers, no store: an adapter holding the
    # settings store could read anything in it, and the next adapter author
    # would.
    NEEDS_TARGETS = True
    NEEDS_HARNESS = True
    # This brain holds a CONVERSATION that outlives the daemon (AC-35), so the
    # daemon's session handling applies to it: the spoken "new conversation"
    # and the session line the page renders. A capability the hub reads, never
    # a name it tests -- the endpoint brain has no session store and no
    # context to lose.
    HAS_SESSIONS = True

    def __init__(self, resolver, harness_for, spec_for, chats, cap_for,
                 client: llm.Client | None = None,
                 project_limit_for=_default_project_limit,
                 project_grace_for=_default_project_grace_minutes,
                 stop_for=None):
        self.host = _Host(harness_for, spec_for, chats, cap_for,
                          project_limit_for=project_limit_for,
                          project_grace_for=project_grace_for,
                          stop_for=stop_for)
        self.converse = HarnessConverse(self.host)
        super().__init__(router=dialogue.Router(resolver, client=client),
                         converse=self.converse)

    # The conversation methods -- `new_conversation`,
    # `session_state`, `session_transcript`, `dispose` -- come from
    # `_HarnessSessions`, shared with `HarnessOnlyBrain` below.

    def proceed(self, text: str, ctx: TurnContext,
                instruction: str | None = None) -> AsyncIterator[Intent]:
        """The utterance the daemon has already ruled on, past the router.

        This is the ONLY path by which dispatched work reaches the coding
        agent: the router turns a `dispatch` verdict into `AskConfirm`, never
        into an action, and `hub._on_ask_confirm` hands it back here. Routing
        it a second time would ask the router a question it has just answered,
        and the second verdict is not guaranteed to be the first -- the owner
        would ask for work and get a conversation about it (plan.md 4.10).

        `instruction` is the router's cleaned reading of `text` and it is what
        the child is PROMPTED with (AC-73, AC-28). Two arguments rather than
        one substituted for the other, because they answer different
        questions: `text` is what the owner said and is what the record keeps
        (D-20), and `instruction` is what they meant. None means the router
        did not rewrite, and then the prompt is the utterance.
        """
        return self.converse.turn(text, ctx, instruction=instruction)


@ports.register(ports.BRAINS, "harness_only")
class HarnessOnlyBrain(_HarnessSessions):
    """The coding agent, with NO router in front of it (AC-67, 4.4 mode 3b).

    The owner's third mode. `HarnessBrain` is the router deciding what each
    utterance is and sending most of them on; this one sends EVERYTHING on.
    It exists because the router is a small model classifying one sentence,
    and two of its five routes -- `answer` and `clarify` -- are answered by
    that model itself and never reach the agent. AC-65 gave it the
    conversation, which is the fix for most of the damage; this mode is for
    the owner who wants none of it, and it is the difference between "Butler
    with a coding agent behind it" and "a coding agent Butler speaks for".

    It is NOT registered as `HarnessConverse` alone, and the difference
    matters: `EndpointConverse` and `HarnessConverse` are deliberately
    unregistered because a Brain that answers everything it is given would
    silently drop the routing the design rests on. Here dropping the routing
    is the DECLARED behaviour, chosen by a setting the owner sees, which is
    the opposite of silent.

    `NEEDS_TARGETS` is absent rather than False, and that is the honest
    declaration: with no router this brain asks no connection for anything, so
    `router_model`, `router_reasoning` and `router_max_tokens` do nothing in
    this mode. The settings page greys the Brain stage to say so.

    `ctx.history` is ignored, deliberately. pi resumes its entire session file
    (AC-59), so handing it Butler's three-exchange window would repeat the
    last three turns to a model that already has all of them.

    What still answers WITHOUT the agent is the three utterances the daemon
    handles itself -- a turn mode, a new conversation, going to a project --
    because `hub._on_final` matches those before any brain is called. That is
    what stops "new conversation" from being sent to pi as a question.

    The cost, stated because the owner pays it on every utterance: "what time
    is it" is now a full agent turn. With a child held that is no longer a
    1.5-2.3 s spawn, but it is still the harness's own model -- measured at
    1.55 s for a bridged cloud model and about 27 s for a local 4B on the same
    question [MEASURED, docs/harness-mode.md]. This mode is only pleasant with
    a fast `harness_model`.
    """

    ENGINE = "harness_only"
    NEEDS_HARNESS = True
    HAS_SESSIONS = True
    # CAN_PROCEED is absent on purpose rather than False: it gates
    # `hub._on_ask_confirm`, and an `AskConfirm` can only come from a router
    # verdict. There is no router here, so that intent can never arrive and a
    # flag claiming to handle it would be describing a path with no caller.

    def __init__(self, harness_for, spec_for, chats, cap_for,
                 project_limit_for=_default_project_limit,
                 project_grace_for=_default_project_grace_minutes,
                 stop_for=None):
        self.host = _Host(harness_for, spec_for, chats, cap_for,
                          project_limit_for=project_limit_for,
                          project_grace_for=project_grace_for,
                          stop_for=stop_for)
        self.converse = HarnessConverse(self.host)

    def turn(self, text: str, ctx: TurnContext) -> AsyncIterator[Intent]:
        return self.converse.turn(text, ctx)

    async def abort(self, turn_id: int) -> None:
        await self.converse.abort(turn_id)

    async def warm(self) -> None:
        await self.converse.warm()

    @property
    def info(self) -> dict:
        return {"engine": self.ENGINE}
