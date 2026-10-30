# Chat agents and terminals are switched on in Settings (BUILT)

Written 2026-10-29 against HEAD `6834c1f`; approved with SW-D1..SW-D3 as
written ("yes") and built the same day. AC-790..AC-798 are in `plan.md`.
As built, SW-4 holds BY CONSTRUCTION rather than by a refused write: the
agents a chat may use are `chat_agents` PLUS the default agent
(`butlerd.allowed_agents`), so no settings file can make the default a
switched-off agent and no boot is refused over it. Spec-local decision ids are SW-n.

## 0. The owner's request [OWNER]

2026-10-29: "the claude code and opencode and pi as chat options should be
settings on the backend. default, only pi available. Also, terminals should
be settings options to enable on settings page, and not available by
default. Also, terminals should only have a shell option, as user can enter
any of the harnesses, to diminish clicking/tapping."

## 1. What is true today [READ 2026-10-29]

- Every registered agent with a label (pi, Claude Code, opencode) is offered
  (`butlerd.offered_agents`); the chat chip's Agent section and the New chat
  chevrons appear whenever more than one is offered (`hasAgentChoice`).
- In-page terminals are always on for the owner; "Open terminal" opens a
  menu of four (claude, pi, opencode, shell) (`terminals.py` agents list,
  `web/terminal.js` picker).
- The project row's click still opens an EXTERNAL window with an agent
  (D9, `TERMINAL_ARGV`): untouched by this spec.

## 2. The rules

### Chat agents

- **SW-1 A setting lists the chat agents on offer.** `chat_agents` (kind
  list, choices from the registry's labelled agents, default `["pi"]`), in
  the Harness group. `offered_agents` returns only those.
- **SW-2 One agent means no picker.** With only pi on, the chip shows just
  the model (`pi . Default`) with no Agent section, and the New chat
  chevrons are gone: the page already hides them when fewer than two agents
  are offered.
- **SW-3 The routes enforce it.** `POST /api/session/new {agent}` and `POST
  /api/chats/{cid}/agent` refuse an agent that is not on (400 naming the
  setting); nothing is minted or changed.
- **SW-4 The default agent is always on.** `harness` must be one of
  `chat_agents`; the settings write refuses a list without it (400 naming
  both), so the default can never point at a switched-off agent.
- **SW-5 Existing chats keep their agent.** Turning an agent off stops NEW
  chats on it; a chat already on Claude Code keeps working (its record says
  so). Nothing is stranded or silently moved.

### Terminals

- **SW-6 A setting switches terminals on.** `terminals_enabled` (bool,
  default `false`), in the Access group (Safety), owner-only (joins
  `access.OWNER_KEYS`: a terminal is a shell).
- **SW-7 Off means off.** While off, every terminal route and the terminal
  socket answer 404 "terminals are off" (the page then hides the feature, as
  it already does on 404), no host is started, and turning it off ENDS every
  open terminal.
- **SW-8 A terminal is a shell.** The in-page terminal runs only the plain
  shell in the project's folder; the agent choice is gone from the API and
  the page. "Open terminal" opens it in one tap; the owner types `claude`,
  `pi` or `opencode` there. (The external-window click, D9, keeps its agent
  table.)

## 3. Criteria (AC-790..AC-799 reserved)

- **AC-790** A fresh install offers only pi: no Agent section, no chevron.
- **AC-791** `chat_agents` lists the offer; the routes refuse an agent not in
  it (400), nothing minted or changed.
- **AC-792** A `chat_agents` without the `harness` agent is refused (400).
- **AC-793** A chat on an agent later switched off keeps running on it.
- **AC-794** `terminals_enabled` defaults false; while false every terminal
  route and the socket answer 404 and no host process starts.
- **AC-795** Turning terminals off ends every open terminal (shell PIDs
  gone).
- **AC-796** `terminals_enabled` is owner-only: a paired device's write is
  refused.
- **AC-797** `POST /api/terminals` takes no agent and spawns the shell;
  "Open terminal" opens it with one tap (no picker in `web/`).
- **AC-798** `daemon/guide_features.py` says both switches live in Settings.

## 4. Brief plan

1. Daemon (opus: the gate and the settings rules): the two settings, SW-3
   and SW-4 checks, SW-7 (404 when off, end all on the switch-off, through
   the settings-apply hook), SW-8 (shell-only spawn), owner key. Red tests
   first; mutation checks on the refusals and the off switch.
2. Page (sonnet against tests): drop the terminal agent picker (one-tap
   "Open terminal"); confirm the chip and chevrons hide with one agent.
3. Docs, guide row, `plan.md`; rollout. ON ROLLOUT, each machine's live
   behaviour changes: Claude Code / opencode options and terminals vanish
   until switched on in Settings. Your desk is already on pi, so nothing
   breaks; turn on what you want there.

About half a day.

## 5. Decisions taken by default (change any before the build)

- **SW-D1** Existing chats on a switched-off agent keep working (SW-5),
  rather than being refused.
- **SW-D2** Turning terminals off ends the open ones (SW-7), rather than
  leaving them until a restart.
- **SW-D3** `terminals_enabled` is owner-only (SW-6); `chat_agents` is not
  (choosing an agent is not a standing channel).
