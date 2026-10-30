# Choosing a chat's agent when it starts (BUILT, option C)

Written 2026-10-29 against HEAD `438a79c`; the owner chose option C ("C is
fine") and it was built the same day. AC-774..AC-779 are in `plan.md`.
On a chat whose agent is not the setting's, BOTH model chips hide (they
open one popover that sets the setting's model). Spec-local decision ids are AP-n. Mock:
`web/mocks/chat-agent-picker.html` (options A, B, C).

## 0. The owner's request [OWNER]

2026-10-29: "i wanted both plain chats that can run on claude opencode or pi
like an earlier prototype And terminals on webui." Terminals are reachable from the
page; a chat's agent is not: it can only be changed in Settings, Harness,
Coding agent, which sets EVERY new chat. "give me specs and a plan, and a
mock html to decide first."

## 1. What is true today [READ 2026-10-29]

- Three chat agents are registered: `pi`, `claude`, `opencode`
  (`ports.HARNESSES`); labels in `butlerd.AGENT_LABELS`.
- A chat records its agent at its FIRST SPAWN (`chats.py:796`) from
  `butlerd.chat_harness(settings, chat)`: the record's `harness`, else the
  `harness` setting. So the agent is effectively fixed by whatever the
  setting says when the first message is sent.
- `POST /api/session/new {"project"}` births a chat (`butlerd.py:3202`); it
  takes no agent.
- Chat payloads already carry `agent` and `agent_label`; the header chip
  `#chatAgent` shows the label.
- The composer's "Agent" chip (`#agentBtn`) is the `harness_model` quick
  setting: it picks a MODEL of the setting's agent, not the agent.
- `harness_model` applies only to a chat whose agent equals the setting's;
  a chat on another agent runs that agent's default model.
- The desk runs `brain_mode: harness_only`; in `endpoint` mode no agent
  answers a chat at all.

## 2. The shape (common to every option)

- **AP-1 Birth with an agent.** `POST /api/session/new` accepts an optional
  `"agent"`. It must name a registered harness (else 400, nothing minted).
  The chat record gets `harness` AT BIRTH, so `chat_harness` returns it from
  the first message on. Absent means today's behaviour (the setting).
- **AP-2 Change while empty (options B and C).** `POST
  /api/chats/{cid}/agent {"agent"}` sets the agent of a chat that has never
  spawned (no turn, no session reference). Once the first message has been
  sent the answer is 409 naming why: an agent's conversation cannot move to
  another agent.
- **AP-3 The daemon serves the choices.** The session and chat payloads carry
  `agents: [{"name", "label"}]` (the registry, in a stable order) and
  `agent_default` (the setting). The page names no agent and no setting
  (AC-41): it renders whatever the daemon lists.
- **AP-4 Only where an agent answers.** In `endpoint` brain mode the picker is
  not shown (the payload says `agents: []`).
- **AP-5 Who.** Anyone past the gate who can start a chat can choose its
  agent (owner or paired device): a chat is content, not a standing channel
  (H2's line), and all three agents already run with the same posture.
- **AP-6 The model follows the agent.** The composer's model chip lists the
  CHAT's agent's models. Today's rule stays: `harness_model` applies when the
  chat's agent equals the setting's; otherwise the agent's default model.

## 3. The options (the mock shows each)

- **A. Pick at New chat.** The sidebar's New chat and each project's "+ New
  chat" open a menu: pi, Claude Code, opencode (default marked). Uses AP-1
  only. One extra click every time.
- **B. Chip on the empty chat.** New chat starts on the default at once; while
  the chat is empty the composer shows an agent chip that changes it (AP-2);
  after the first message the agent shows only as the header label. Zero
  extra clicks for the common case.
- **C. Both (recommended).** B everywhere, plus a chevron on the New chat
  buttons that opens A's menu for when you know up front.

## 4. Criteria (AC-774..AC-779 reserved; final wording after the choice)

- **AC-774** `POST /api/session/new` with an `agent` births a chat whose
  record holds it; the first turn spawns that agent (spawn argv); an unknown
  agent is a 400 and no chat is minted.
- **AC-775** (B, C) Changing an empty chat's agent works; after the first
  turn it is a 409 and the record is unchanged.
- **AC-776** The payloads carry `agents` and `agent_default`; in `endpoint`
  mode `agents` is empty and the page shows no picker.
- **AC-777** The page renders the picker from the payload only (grep gate:
  no agent or setting name in `web/`), textContent only.
- **AC-778** The model chip lists the chosen agent's models.
- **AC-779** `daemon/guide_features.py` says how to start a chat on Claude
  Code or opencode.

## 5. Build plan

1. Daemon: AP-1..AP-4 with red tests (`test/test_chat_agent.py`), mutation
   check on the 409 and the unknown-agent refusal. [opus: per-chat state is
   where a wrong agent goes silently to the wrong child]
2. Page: the chosen option against the payload; grep tests; a real-browser
   scratch run that starts a Claude Code chat from the page and sees the
   header label. [sonnet against the tests; opus review]
3. Guide row, `plan.md`, this spec's status; rollout to the three machines
   (restart; the laptop's old PID first).

About half a day of work for C, less for A or B alone.

## 6. Open decisions [OWNER]

- **AP-D1 Which option: A, B or C** (mock).
- **AP-D2 Voice.** Should "new chat with Claude" (or "with opencode") start a
  chat on that agent? Today the spoken "new conversation" uses the setting.
- **AP-D3 A model per chat.** Should the model chip set the model for THIS
  chat only (like the agent), instead of the setting? Today it is the
  setting.
- **AP-D4 Scheduled and triggered chats** (automations) keep using the
  setting, or does an automation name its agent?
