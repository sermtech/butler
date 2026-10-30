# The agent can find and propose recipes

Owner, 2026-10-08, after asking whether the agent reads saved recipes and
skills ("just the recipe tools. don't change skills."):

Criteria AC-657..AC-670 are RESERVED here until promoted into `plan.md`.

**Status: BUILT 2026-10-08.**

---

## 1. What exists [READ]

- Recipes are saved prompts (`daemon/library.py` `RecipeStore`, `Recipe`:
  id, name, project or `GLOBAL`, prompt), filled by `library.expand`, the ONE
  expansion grammar (AC-448: every door calls it).
- Neither brain sees them: nothing in the prompt, no tool. Since
  `docs/butler-help.md`, `butler_help` reports at most 20 recipe NAMES. A run
  ("run recipe X", Run, `/`) reaches the agent as an ordinary message.
- The agent's library tools (`library.TOOLS`: `task_add`, `reminder_add`,
  `schedule_propose`) go through ONE dispatcher, `Hub.library_tool`, from the
  endpoint brain's `tasks` callable and from pi's loopback
  `POST /api/library/agent`.
- `schedule_propose` writes nothing: the hub keeps ONE proposal per
  conversation in memory (`_proposals`, 10 min), asks it as a question card
  and through the model's reply, and creates only on the owner's yes
  (`_settle_proposal`, spoken `yes`/`no` via `spoken_confirm`, or the card).

Skills are NOT touched (owner's ruling).

---

## 2. Decisions

- **RT-1. `recipe_find(query?)`, read-only.** Answers the recipes of the
  conversation's project and Global (the project from the chat, never the
  model, `task_add`'s rule), matched by every query word against name and
  prompt (case-folded), at most 10, each with its name, where it lives and its
  prompt AS FILLED by `library.expand` with its defaults, cut at 400
  characters. No query lists them all, still at most 10, and says how many
  more. A recipe with a `problem` (a hand edit the parser refuses) is named
  with the problem and no prompt.
- **RT-2. What the agent may do with one.** Follow the filled prompt in this
  same reply, or tell the user how to run it ("say run recipe <name>"). It
  never starts a new chat on its own.
- **RT-3. `recipe_propose(name, prompt)` writes nothing.** The SAME proposal
  machinery as `schedule_propose`, with a `kind`: checked by the recipe rules
  (`RecipeStore`'s name and prompt checks, the placeholder grammar), asked as
  a question card titled "Save this recipe?" and through the model's reply,
  created in the conversation's project only on yes. One proposal per
  conversation of EITHER kind; a newer one replaces an older (the old card
  closes). *Losing option:* a direct `recipe_add` -- a recipe is something
  the owner will run by name for months, so the owner names it.
- **RT-4. Rules, short and static,** in their own block
  (`dialogue.RECIPE_RULES`), sent right after the reminder rules wherever the
  library tools are. [AMENDED 2026-10-08, owner's yes] The trigger is the
  USER's request, the memory rule's shape: before a task with several steps,
  call `recipe_find` and check the skills list "if you have one", use a match
  and say which; offer `recipe_propose` when the same kind of thing is asked
  again. The first wording ("call recipe_find before writing a long prompt")
  was keyed on the agent's own writing and never fired for "check my
  emails". Section 4 is the measurement.
- **RT-5. The guide knows** (`docs/butler-help.md` BH-3): the Recipes feature
  covers both tools; the drift test enforces it.

---

## 3. Criteria (proposed)

- **AC-657** `recipe_find` answers this project's and Global's recipes,
  never another project's.
- **AC-658** Every query word must match name or prompt; no query lists
  all; at most 10, with the count of the rest.
- **AC-659** The prompt shown is `library.expand`'s output (a caller test:
  the dispatcher calls the one grammar), cut at 400 characters.
- **AC-660** A recipe with a problem is named with it, prompt withheld.
- **AC-661** `recipe_propose` writes nothing; the recipe store is unchanged
  until yes.
- **AC-662** Yes (spoken or the card) saves it in the conversation's
  project; no saves nothing; both leave a transcript note.
- **AC-663** A bad name or prompt is refused before anything is asked.
- **AC-664** A recipe proposal replaces a pending schedule proposal in the
  same chat and vice versa; the old card closes.
- **AC-665** Both tools reach the one dispatcher from the endpoint brain and
  from pi's loopback route; the route refuses other tool names.
- **AC-666** The pi extension declares both exactly as the daemon does; both
  join read_only's `--tools`.
- **AC-667** The rules name both tools, stay static, and name no language.
- **AC-668** The guide's Recipes feature covers both tools.
- **AC-669** A recipe saved on yes broadcasts the `library` frame for its
  project.
- **AC-670** Guests are offered neither (unchanged, re-asserted).

---

## 4. Scenario check of RT-4, 2026-10-08 [MEASURED]

`test/manual/eval_butler_help.py ... r_email,r_week,r_status,capital --seed`
on scratch daemons with the desk's setup (harness only, pi on
`glm-5.3-flash`, the owner's SOUL.md and USER.md) and three Global recipes
seeded ("Email check", "My week", "Monday status"). Counted from the pi
session files: did the agent call `recipe_find` before acting?

| wording | "check my emails" / "what does my week look like" | "capital of France" (control) |
|---|---|---|
| two lines inside REMINDER_RULES (before) | 0 of 2 | no call |
| own block, "before a task with several steps" | 0 of 4 (2 runs) | no call |
| own block, "when the user asks you to DO something" (shipped) | 3 of 4 (2 runs) | no call |

Every wording found "Monday status" when the owner NAMED it. Small samples
(n=2 per wording): the direction is clear, the rate is not. Open: the
answers named the skill they used, not the recipe, despite "say which one
you used".
