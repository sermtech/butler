# A model per chat (BUILT)

Written 2026-10-29 against HEAD `6e146d7`; approved ("go": CM-D1 daemon-wide
recents, CM-D2 "Default model", CM-D3 no mock) and built the same day.
AC-780..AC-788 are in `plan.md`; AC-789 stays reserved. In the harness
modes the settings `answers` pointer is now null (no setting answers; the
chat chip does), so the old "who answers" pill hides. Spec-local decision ids are CM-n. UX consulted with a Fable
subagent on the owner's request (2026-10-29) [REVIEW]; an earlier prototype is the
reference the owner named.

## 0. The owner's request [OWNER]

2026-10-29: "i want a model choice per chat. simple, like we have on e
projects an earlier prototype. consult with fable subagent for sota ux and ui so logic
and workflows make sense and easy, simple."

## 1. What is true today [READ 2026-10-29]

- The model is ONE global setting, `harness_model` (quick chip "Agent",
  favourites list, `daemon/settings.py`). It reaches a child only on its
  spawn argv, read per message in `butlerd.harness_spec_resolver`
  (`butlerd.py:2066`), and only when the chat's agent equals the `harness`
  setting; otherwise the agent's own default.
- On a chat whose agent is not the setting's, both model chips are hidden
  (`docs/chat-agent-picker.md`, AC-778): there is no way to pick a model for a
  Claude Code or opencode chat from the page.
- A changed spawn spec already replaces a held child on its NEXT turn (AC-60),
  and pi, Claude Code and opencode each resume the same session under a
  different model. So changing a model mid-chat needs no new machinery.
- Each agent lists its own models (`list_models`): pi ~532, Claude Code its
  aliases (haiku, sonnet, opus), opencode ~840.
- An earlier prototype (the reference): each chat stores its own model or none; none
  shows "Default (<resolved name>)"; picking in the composer sets the viewed
  chat only; Settings holds only the default; the server resolves what the
  default is (`GET /api/chat/default-models`).

## 2. The rule (one)

- **CM-1 A chat has its own model, or none.** The chat record gains `model`
  (a string, or empty = follow the agent's default). Picking a model in a
  chat sets THIS chat only. It never changes `harness_model`, which becomes
  what it really is: the default model for new chats on the default agent,
  changed only in Settings ("Default model").
- **CM-2 The spawn reads the chat first.** `harness_spec_resolver`: the
  chat's own `model` if set, else `harness_model` when the chat's agent is
  the setting's, else none (the agent's own default). Any agent, so Claude
  Code and opencode chats get a model too.
- **CM-3 It can change at any time, from the next turn.** Unlike the agent,
  the model is free for the life of the chat. A change during a running turn
  leaves that turn alone; the next turn spawns with the new model (AC-60).
  The chip shows the new value at once.
- **CM-4 Switching agent on an empty chat clears the model.** A pi model name
  means nothing to Claude Code; the chip then shows the new agent's default.
- **CM-5 A model that disappeared stays.** Kept on the chat and shown "(not
  listed)"; the next turn's spawn failure is spoken as any harness failure
  is. No silent fallback.

## 3. The control

- **CM-6 One composer chip: agent and model together.** It replaces today's
  separate agent chip and the two model chips in the chat composer (the
  Brain chip stays). Label: `pi · Default (deepseek-v4)`, `pi · sonnet`,
  `Claude Code · Default` (Claude Code's built-in default cannot be named);
  on a phone the parenthesis drops (`pi · Default`).
- **CM-7 One popover (a bottom sheet on a phone), two sections.**
  - Agent: the three agents while the chat is empty (radio); once started, a
    read-only line "pi -- this chat stays on pi".
  - Model: "Default (X)" first, then Favourites (the existing shared
    favourites, starred), Recent (the last 5 models picked on this agent),
    then "All models" folded, with a search box that filters as you type.
    "Use default" clears the chat's own model.
- **CM-8 The daemon serves everything the page shows.** The chat documents
  gain `model` (own or empty) and `model_default` (the resolved default name
  for the chat's agent, empty when unnameable). `GET /api/agents/{agent}/
  models` serves `{default, favourites, recent, models}` from the agent's
  cached `list_models`. The page names no model, agent or setting (AC-41).
- **CM-9 Header and New chat stay.** The header label shows the agent (as
  today); the chevron beside New chat still picks the agent; a new chat
  starts with no own model.

## 4. Voice

- **CM-10 Two spoken intents.** "use sonnet" / "switch to opus" / "use
  deepseek on this chat" sets this chat's model; "back to the default model"
  clears it. Matched against favourites first, then the agent's catalogue by
  substring. One match: "Sonnet from the next reply." None: "I don't have a
  model called X on pi." Several: "Several match X; say deepseek chat or
  deepseek reasoner." No "a faster model": nothing ranks models by speed.
  BUILT 2026-10-29 (`settings.spoken_model_request` / `match_models`,
  `Hub._model_turn`, `test/test_chat_model_voice.py`). As built: an exact
  name beats a substring, and a substring must start at a word ("seek" is
  not deepseek); a bare "use X" / "switch to X" naming no model falls
  through to the agent as work, while "change the model to X", "use the X
  model" and "... on this chat" refuse it aloud; a bare name that is a
  configured project stays navigation. No frame is sent: open pages show
  the change on their next read of the chat.

## 5. Criteria (AC-780..AC-789 reserved)

- **AC-780** `POST /api/chats/{cid}/model {"model"}` stores the chat's model;
  the next spawn's argv carries it even when the chat's agent is not the
  setting's; `{"model": ""}` clears it.
- **AC-781** Setting a chat's model leaves `harness_model` unchanged and does
  not change another chat's next spawn.
- **AC-782** A change during a running turn: that turn's child keeps its
  argv; the next turn spawns with the new model on the same session.
- **AC-783** Changing an empty chat's agent clears its model; `model_default`
  names the new agent's default.
- **AC-784** The models route serves default, favourites, recent and the
  catalogue per agent; a pick lands in `recent`.
- **AC-785** One composer chip shows `agent · model` from the payload; the
  separate agent chip and model chips are gone from the chat composer; grep
  gate: no model, agent or setting name in `web/`.
- **AC-786** A model missing from the catalogue is shown "(not listed)" and
  is not replaced.
- **AC-787** "use <model>" and "back to the default model" set and clear the
  current chat's model and say so; ambiguous and unknown names are answered,
  not guessed.
- **AC-788** `daemon/guide_features.py` explains the per-chat model.

## 6. Build plan

1. Daemon (opus: per-chat state reaching the wrong child is silent): the
   record field, CM-2 in the resolver, the set route, CM-4, the models route
   with recents, payload fields. Red tests first, mutation checks on CM-2 and
   AC-781.
2. Voice intents (opus): the matcher beside the existing spoken commands,
   with its answers.
3. Page (sonnet against the tests; opus review): the merged chip and the
   sheet, search, favourites, recents; real-Chrome scratch run on desk and
   phone widths; screenshots read.
4. Guide row, `plan.md`, this spec's status; rollout to the three machines.

About a day. A mock first is optional (an earlier prototype's chip and the approved
`chat-agent-picker.html` already fix the look).

## 6a. Not built (deliberately)

A "make this the default" toggle; a model per project; speed or cost tiers;
a model row in the header; model choice for automations (they follow the
setting, AP-D4); a separate models page.

## 7. Open decisions [OWNER]

- **CM-D1** Recents: daemon-wide per agent (simple, recommended) or per
  device?
- **CM-D2** Should the Settings label change from "Harness model" to
  "Default model" (recommended: yes, it is what it is now)?
- **CM-D3** A mock before building, or straight to build?
