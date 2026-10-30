# Several agents in one project (PROPOSAL)

Written 2026-10-08 against HEAD `8dbb1a4`. Nothing here is accepted:
AC-564..AC-585 are RESERVED for this spec. Spec-local decision ids are PS-n.
Evidence: `.references/parallel-same-project-map.md` (every place the
one-per-project rule is enforced, with path:line). Provenance tags as
`docs/proactive.md`.

## 0. The owner's decisions (2026-10-08) [OWNER]

- "Enable multiple parallel agents even in the same project please."
- On isolation: "Give user options by new agent. As a whole, same branch is
  fine as they won't be coding or editing most of the time. No worktree by
  default, unless user wants to."
- This reopens `docs/filing-and-pacing.md` 8's "only allow 1 per project for
  now", which already said "raising it later should be a NUMBER".

## 1. What exists [READ, map sections 1-5]

- The rule is ALREADY a number: setting `project_agent_limit` (1..10,
  default 1, `daemon/settings.py`). `_Host._enforce_project_limit` and
  `plan()` in `daemon/adapters/brain_harness.py` count busy children per
  folder against it; children, running turns and pi session files are
  already per CHAT. Test fixtures already run 10 chats in one folder. The
  setting's help text ("enforced at 1 either way") is stale.
- What assumes 1 and breaks above it:
  1. `gateNewChat` (`web/app.js`) shows "this will STOP the running agent"
     whenever any agent in the project is busy, even with a free slot.
  2. `POST /api/project/{id}/stop` (`post_project_stop`, `butlerd.py`)
     stops EVERY busy agent in the project.
  3. `_annotate_busy` (`butlerd.py`) marks a project busy while slots remain.
  4. D1=A disposes the longest-idle held child when a new chat needs the
     slot; at N > 1 a third chat can evict an idle agent the user meant to
     keep.
  5. Automations and heartbeats wait for a busy project (AC-460: a run
     never overlaps the owner); at N > 1 "busy" no longer means "full", so
     a scheduled run can start beside the owner's agent in the same tree.
- The real risk is the shared folder: files, the git index and checkout,
  and build/test state are shared, and Butler isolates none of it.
  `handoff.md` records one agent's `git checkout` wiping a sibling's
  uncommitted edits [READ]. agentnet's `src/agentnet/worktree.py` is the
  proven per-task worktree pattern (sibling folder, branch, merge only on a
  green verifier).

## 2. Decisions

- **PS-1 Default limit 4** [DECIDED]. `project_agent_limit` defaults to 4
  (agentnet measured four parallel agents optimal on this hardware and eight
  degraded [MEASURED, agentnet skill notes]); the setting stays 1..10 and
  the help text is corrected. Existing installs keep a saved value.
- **PS-2 Shared folder by default** [OWNER]. A new chat in a project runs in
  the project folder on the current branch, beside any other agent there.
- **PS-3 A worktree per new agent, on request** [OWNER: "options by new
  agent"]. Starting a new chat in a project that already has a held or
  busy agent offers one choice: "Same folder" (default) or "Own copy (git
  worktree)". Only for a git repository with at least one commit; elsewhere
  the option is shown disabled with the reason. By voice the default
  applies; "in its own copy" in the request picks the worktree.
- **PS-4 The worktree** follows agentnet's shape: a sibling folder
  `<project>.butler-worktrees\<chat-id>`, branch `butler/<chat-id>` cut from
  the project's current HEAD, created at the chat's birth, the chat's cwd
  for life. It is listed in the chat's header ("own copy on butler/...").
- **PS-5 Finishing a worktree chat** never merges by itself (Butler has no
  verifier). The chat gets three actions: "Merge into <branch>" (allowed
  only when the project folder's tree is clean; `git merge --no-ff`; a
  conflict aborts the merge, keeps the branch and says which files),
  "Keep branch" (removes the worktree folder, keeps the branch), "Discard"
  (asks, then removes worktree and branch). Deleting the chat asks which.
- **PS-6 Same-folder awareness** [DECIDED]. When two or more agents are
  live in one folder, each new prompt sent to a harness child there carries
  one fixed line naming the others ("Another agent is working in this
  folder (chat <title>). Do not run git checkout, reset, stash or clean;
  avoid editing files it may be editing."). It informs; it does not lock.
  The page shows "2 agents in this folder" on the project row.
- **PS-7 The breakages, fixed** [DECIDED]:
  1. The new-chat confirm appears only when the limit is REACHED; below it
     the chat just starts (with the PS-3 choice when another agent exists).
  2. "Stop project" becomes "Stop all agents in this project" with the
     count in its confirm; each chat keeps its own Stop.
  3. The project row shows busy as "2 of 4"; full only at the limit.
  4. D1=A evicts an idle held child only when the limit is reached, never a
     busy one, and never a worktree chat's child (its folder is its own).
  5. Automations and heartbeats need a FREE SLOT, and additionally never
     start while an OWNER turn is running in that folder (AC-460's promise
     kept as "never beside the owner's live turn"); a run started in a
     worktree-less project carries the PS-6 line.
- **PS-8 Voice and desk** are unchanged: speech follows the chat on screen
  (AC-238); other agents' replies are written, not spoken.

## 3. Criteria (AC-564..AC-585 reserved)

- **AC-564 (limit).** Default 4, range 1..10, help text true; a fifth busy
  agent in a project at limit 4 is refused with the existing CapReached
  wording. **Recoverable.**
- **AC-565 (no false confirm).** With 1 of 4 busy, a new chat in that
  project starts with no stop confirm; at 4 of 4 the confirm appears.
  *Fails as:* the user stopping an agent they meant to keep. **NOT
  recoverable.**
- **AC-566 (busy count).** The project row reports busy and limit ("2 of
  4"); full only at the limit. **Recoverable.**
- **AC-567 (stop all).** The project stop names how many agents it stops;
  a chat's own Stop stops only that chat. **NOT recoverable** for the
  stopped turns.
- **AC-568 (eviction).** An idle child is disposed only at the limit; a
  busy one never; a worktree chat's never. **Recoverable.**
- **AC-569 (scheduled runs).** An automation or heartbeat starts only with a
  free slot and never while an owner turn runs in that folder; otherwise it
  waits within grace as today. **Recoverable.**
- **AC-570 (choice offered).** A new chat in a project with another live
  agent offers Same folder / Own copy; Own copy is disabled with its reason
  outside a git repo with a commit; voice defaults to same folder and "in
  its own copy" picks the worktree. **Recoverable.**
- **AC-571 (worktree birth).** Own copy creates `<project>.butler-worktrees\
  <chat-id>` on branch `butler/<chat-id>` from HEAD, and the chat's agent
  runs there for life; the header names the branch. **Recoverable.**
- **AC-572 (merge).** Merge runs only on a clean project tree, `--no-ff`;
  a conflict aborts, keeps the branch and lists the files; nothing merges
  by itself. *Fails as:* a half-merged project. **NOT recoverable.**
- **AC-573 (keep and discard).** Keep removes the folder and keeps the
  branch; Discard asks, then removes both; deleting the chat asks which.
  **NOT recoverable** for Discard.
- **AC-574 (awareness line).** With two live agents in one folder, each
  prompt to a child there carries the fixed line naming the others; with
  one, it does not. **Recoverable.**
- **AC-575 (voice).** Speech follows the chat on screen; other agents'
  answers are written, not spoken (AC-238 unchanged). **Recoverable.**
- AC-576..AC-585 are left for what the build finds.

## 4. Brief plan

Red-green per phase; the AC-565 and AC-572 mutations are mandatory (D-13).

| phase | builds | criteria |
|---|---|---|
| S0 | mock: new-chat choice, "2 of 4" row, worktree chat header and its three actions; owner approves the look | -- |
| S1 | limit default 4 + help; busy count; no false confirm; stop-all wording; eviction rule | AC-564..568 |
| S2 | scheduled runs need a free slot and never beside an owner turn | AC-569 |
| S3 | awareness line for shared folders | AC-574 |
| S4 | worktree option: birth, header, merge / keep / discard (reuse agentnet's `worktree.py` logic, not its process) | AC-570..573 |
| S5 | voice wording and the desk check | AC-575 |

S1-S3 are small daemon and page changes; S4 is the only new machinery. Full
suite after S1 (session identity and busy are shared paths) and after S4.

## 5. Open for the owner

- **O-1.** Default limit 4: fine for the laptop as well? (Each agent is a pi
  or claude process; the laptop has less memory than the desk.)
- **O-2.** Should an automation be allowed to start beside the owner's
  agent when a slot is free? PS-7.5 says no while an owner turn is RUNNING,
  yes when the owner's agent is only held idle.
