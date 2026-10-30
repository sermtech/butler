"""D-13 mutation harness for ambient: AC-68, AC-69 and above all AC-70.

Each entry breaks ONE rule, then asks two questions with pytest EXIT CODES and
nothing else -- a previous session matched the word "FAILED" in pytest's
output, pytest colours that word, and every mutation was reported as
surviving:

  A: do the NAMED tests fail?           expected exit 1
  B: does everything else still pass?   expected exit 0

Fourteen rules. The first two are the SAFETY property, rows 2-8 are what keeps
it cheap and honest, and rows 9-13 are the same safety property one seam
further in -- what the BRAINS do with the field now that they read it.

**What was overheard must not become what was said (AC-70).** Two ways to
break it and both are one line: merge the room's words into the utterance the
brain is prompted with, or append them to the rolling transcript. The second
is the worse one, because a leak into `hub.history` outlives the turn that
caused it -- every later utterance is then routed as though the owner had
said a thing that was said to somebody else in the room.

**A clip belongs to ONE turn.** The request is fired without await, so it
races the turn it names. Drop the ownership check and a late clip lands on
whatever turn is current; drop the turn KEY on the read and a clip stored for
a turn that never used it is inherited by the next one. Those are different
mutations with different witnesses, and only the second needs a turn that
ended without a reply.

**Off is off, and empty is free (AC-68).** The window ships at 0, and a
daemon that stored a clip anyway is holding a room the owner told it not to.
The empty-clip short circuit is the other half: without it every non-ambient
turn pays a thread hop to decode nothing, and no output anywhere differs.

**A model must be able to tell the room from the request (AC-70, rows 9-13).**
The brains read `ctx.overheard` and wrap it in a labelled block; the mutation
that matters most is making the two indistinguishable, which is one string
concatenation in `brain_harness`. The other four are the same merge in
endpoint mode, an empty overheard that adds a section anyway (AC-68: with
ambient off the request must be what it always was), the ROUTER being shown
the room -- its verdict's summary reaches a coding agent with no label on it
-- and the room reaching `chats.py`, where it never expires.

**The decode has to happen, has to be bounded, and must never be fatal.**
`_overheard` returning "" is the whole feature quietly absent; a missing
ceiling lets one request make the daemon hold arbitrary memory; and a decode
failure that escapes takes the turn with it, which is D-1 -- the context is
what a failure here may cost, never the answer.

Run: .venv\\Scripts\\python.exe -X utf8 test\\manual\\mutate_ambient.py [index]

A whole pass is about fifteen minutes, because every mutation runs the fast
tier once. An INDEX -- the number this script prints in brackets -- runs one
of them, which is what re-checking a single rule after a fix needs.

`__pycache__` is deleted around every run, and this is not tidiness: a
same-length mutation written and reverted inside one filesystem timestamp
second is served from the bytecode cache, which is how a whole suite once ran
green against source the reader could see was correct (test/AGENTS.md). The
anchor count is checked BEFORE anything is written, so a mutation that lands
nowhere reports as an error rather than as a survivor, and every restore is
re-run and must come back exit 0.

[EXECUTED 2026-08-19: all nine OK -- aimed=1, rest=0, restored=0. Mutation 5
came back BAD on the first pass, with rest=1, and the harness was right: two
more ambient tests are honest witnesses of that rule and were not named. They
are named now, and it is the failure mode this file's own rule about naming
BOTH witnesses exists for.]

[EXECUTED 2026-08-19, rows 9-13 when they were added: all five OK --
aimed=1, rest=0, restored=0.]
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"

AMBIENT = "test/test_ambient.py::"
CONVERSATION = "test/test_conversation.py::"
PROMPT = "test/test_ambient_prompt.py::"

# (file, anchor, replacement, *tests that must go red)
MUTATIONS = [
    # AC-70: the room's words merged into the owner's utterance.
    (
        "daemon/hub.py",
        "            stream = self.brain.turn(text, ctx)",
        "            stream = self.brain.turn(\n"
        "                (text + ' ' + ctx.overheard).strip(), ctx)",
        AMBIENT + "test_what_was_overheard_never_becomes_what_was_said",
        AMBIENT + "test_the_backfill_is_decoded_and_reaches_the_turn_as_its"
                  "_own_field",
    ),
    # AC-70: the room's words in the rolling transcript, where they outlive
    # the turn. Guarded on there BEING something overheard, so the mutation
    # is aimed rather than coupled -- an unconditional append would put an
    # empty entry in every turn of every other suite.
    (
        "daemon/hub.py",
        "            stream = self.brain.turn(text, ctx)",
        "            if ctx.overheard:\n"
        "                self.history.append(('user', ctx.overheard))\n"
        "            stream = self.brain.turn(text, ctx)",
        AMBIENT + "test_what_was_overheard_never_becomes_what_was_said",
        CONVERSATION + "test_a_back_fill_belongs_to_ONE_turn_and_never_reaches"
                       "_a_later_one",
    ),
    # D-6: a clip stored against a turn no session owns.
    (
        "daemon/hub.py",
        "        for s in list(self.sessions):\n"
        "            if s.owns(turn):",
        "        for s in list(self.sessions):\n"
        "            if True:",
        AMBIENT + "test_a_clip_for_a_turn_the_session_has_moved_past_is"
                  "_discarded",
    ),
    # The read is keyed by TURN. Without it a clip its own turn never used is
    # inherited by the next question.
    (
        "daemon/hub.py",
        "        pcm = self.ambient if self.ambient_turn == turn else b\"\"",
        "        pcm = self.ambient",
        AMBIENT + "test_a_clip_whose_own_turn_never_used_it_is_not_inherited",
    ),
    # AC-68: off means the DAEMON stores nothing, not that it trusts the page.
    (
        "daemon/hub.py",
        "        if self._ambient_seconds() <= 0:",
        "        if False:",
        AMBIENT + "test_nothing_is_stored_while_the_window_is_off",
    ),
    # AC-68: no clip means no work. Without the short circuit every turn in
    # the daemon decodes an empty buffer and nothing observable differs.
    # THREE honest witnesses and all three are named (test/AGENTS.md): the
    # decode-count one is the aimed test, and the other two redden because a
    # recognizer asked to transcribe nothing still answers -- which is what
    # makes "there was no clip" and "the clip was empty" the same turn.
    (
        "daemon/hub.py",
        "        if not pcm:\n            return \"\"\n",
        "",
        AMBIENT + "test_with_ambient_off_the_turn_does_no_ambient_work_at_all",
        AMBIENT + "test_a_clip_whose_own_turn_never_used_it_is_not_inherited",
        CONVERSATION + "test_a_back_fill_belongs_to_ONE_turn_and_never_reaches"
                       "_a_later_one",
    ),
    # D-1: a decode failure that escapes takes the turn with it.
    (
        "daemon/hub.py",
        "        try:\n"
        "            text = (await self.asr.transcribe(pcm, self.asr_config)"
        " or \"\")\n"
        "        except Exception as e:                            "
        "# noqa: BLE001\n"
        "            print(f\"[butler] turn {turn}: the ambient back-fill "
        "would not \"\n"
        "                  f\"decode ({len(pcm)} bytes): {e!r} -- answering "
        "without it\")\n"
        "            return \"\"\n",
        "        text = (await self.asr.transcribe(pcm, self.asr_config)"
        " or \"\")\n",
        AMBIENT + "test_a_backfill_that_will_not_decode_costs_the_context_not"
                  "_the_turn",
    ),
    # AC-69: the decode itself. The whole feature, quietly absent.
    (
        "daemon/hub.py",
        "                              overheard=await self._overheard(s, turn))",
        "                              overheard=\"\")",
        AMBIENT + "test_the_backfill_is_decoded_and_reaches_the_turn_as_its"
                  "_own_field",
        CONVERSATION + "test_a_back_fill_belongs_to_ONE_turn_and_never_reaches"
                       "_a_later_one",
    ),
    # The ceiling on what one request may make the daemon hold.
    (
        "daemon/butlerd.py",
        "        if len(pcm) > AMBIENT_MAX_BYTES:",
        "        if False:",
        AMBIENT + "test_a_body_bigger_than_the_biggest_window_is_refused",
    ),
    # -- AC-70 in the BRAINS, added when they started reading the field ----- #
    # The one that matters most: the room and the request as one run of text
    # in the prompt handed to a CODING AGENT. Nothing downstream can then tell
    # a remark made to somebody else from an instruction.
    (
        "daemon/adapters/brain_harness.py",
        "                    f\"{note}\\n\\nRequest: {prompt}\" if note "
        "else prompt, ctx):",
        "                    f\"{ctx.overheard} {prompt}\".strip(), ctx):",
        PROMPT + "test_the_coding_agent_is_prompted_with_a_labelled_block",
        PROMPT + "test_a_dispatched_turn_carries_the_context_and_the_cleaned"
                 "_request",
    ),
    # The same merge one mode over: the conversation model asked to answer the
    # room and the utterance as one sentence.
    (
        "daemon/adapters/brain_endpoint.py",
        "        note = dialogue.overheard_note(ctx)\n"
        "        if note:\n"
        "            messages.append({\"role\": \"system\", \"content\": note})\n"
        "        messages.append({\"role\": \"user\", \"content\": text})",
        "        messages.append({\"role\": \"user\",\n"
        "                         \"content\": f\"{ctx.overheard} {text}\""
        ".strip()})",
        PROMPT + "test_the_conversation_model_is_told_which_half_is_which",
    ),
    # AC-68: an empty overheard adds a section anyway, so every request in
    # endpoint mode changes shape for a feature nobody switched on. TWO honest
    # witnesses and both are named: the ambient one, and the history test that
    # asserts the exact role sequence -- which is what makes this rule cheap
    # to hold, since any test asserting a message list is a witness to it.
    (
        "daemon/adapters/brain_endpoint.py",
        "        note = dialogue.overheard_note(ctx)\n"
        "        if note:\n"
        "            messages.append({\"role\": \"system\", \"content\": note})",
        "        messages.append({\"role\": \"system\",\n"
        "                         \"content\": dialogue.overheard_note(ctx)})",
        PROMPT + "test_an_empty_overheard_leaves_the_endpoint_request"
                 "_untouched",
        "test/test_brain_endpoint.py::"
        "test_the_conversation_is_carried_when_there_is_one",
    ),
    # The ROUTER is blind to the room ON PURPOSE (AC-70, AC-73): its dispatch
    # verdict becomes the summary a coding agent is prompted with, and that
    # summary carries no label. This mutation is the shortest path in the
    # daemon from a stray remark to an action.
    (
        "daemon/dialogue.py",
        "        messages.append({\"role\": \"user\", \"content\": text})",
        "        messages.append({\"role\": \"user\",\n"
        "                         \"content\": f\"{ctx.overheard}\\n{text}\""
        ".strip()})",
        PROMPT + "test_the_router_is_never_told_what_the_room_said",
    ),
    # AC-70's third negative at the durable seam: the room filed as the
    # owner's words in `chats.py`, where -- unlike the rolling transcript --
    # it never expires (D-20).
    (
        "daemon/adapters/brain_harness.py",
        "            self._host.record(\"user\", text)",
        "            self._host.record(\"user\", "
        "f\"{ctx.overheard} {text}\".strip())",
        PROMPT + "test_the_room_never_enters_butlers_own_record",
    ),
]

# The sixth brain-side rule is NOT a row here, and the reason is the harness's
# own question B. Wrapping the HARNESS prompt unconditionally -- the empty
# overheard that costs something -- reddens every test in the tier that
# asserts what a child was prompted with, so "does everything else still pass"
# cannot be answered with the aimed test deselected. That breadth is the
# evidence rather than a problem with the mutation: with ambient off the
# prompt must be the string it always was, and nineteen tests already say so.
# EXECUTED by hand 2026-08-19, exit codes only -- mutated: tier exit 1 with 19
# tests red across test_ambient_prompt, test_brain_harness, test_chats,
# test_conversation, test_project_switch and test_router_summary; restored:
# tier exit 0.


def clear_pycache() -> None:
    for d in REPO.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def run(args: list[str]) -> int:
    clear_pycache()
    return subprocess.run([str(PY), "-X", "utf8", "-m", "pytest", *args],
                          cwd=REPO, capture_output=True).returncode


def main(only: int | None = None) -> int:
    """`only` is the index of ONE mutation, because a whole pass is fifteen
    minutes and re-checking a single rule after a fix should not be."""
    failures = 0
    for index, (path, anchor, replacement, *targets) in enumerate(MUTATIONS):
        if only is not None and index != only:
            continue
        p = REPO / path
        original = p.read_text(encoding="utf-8")
        found = original.count(anchor)
        if found != 1:
            print(f"ERROR anchor appears {found} times in {path}; a mutation "
                  f"that lands nowhere would report as a survivor")
            failures += 1
            continue
        p.write_text(original.replace(anchor, replacement), encoding="utf-8")
        try:
            aimed = run([*targets, "-q"])
            deselect = [a for t in targets for a in ("--deselect", t)]
            rest = run(["test/", "-q", "-m", "not slow and not needs_gpu",
                        *deselect])
        finally:
            p.write_text(original, encoding="utf-8")
        restored = run([*targets, "-q"])
        ok = (aimed == 1 and rest == 0 and restored == 0)
        failures += 0 if ok else 1
        print(f"{'OK  ' if ok else 'BAD '} [{index}] {path}: aimed={aimed} (want 1) "
              f"rest={rest} (want 0) restored={restored} (want 0)  <- "
              f"{', '.join(t.split('::')[-1] for t in targets)}")
    clear_pycache()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else None))
