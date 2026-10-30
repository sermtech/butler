"""Who Butler is and who it is talking to, resolved per turn for the brains
that are shown it.

docs/identity-memory.md 4.2 and 11 (AC-420..AC-429). The owner's ruling of
2026-09-26: two FILES in the persona folder (`paths.Paths.persona` -- the
data root, or the records folder on the legacy layout), Hermes's and
OpenClaw's names for them:

* `SOUL.md` -- who Butler is.
* `USER.md` -- who the owner is.

Each starts with an HTML comment of instructions for the OWNER. Every HTML
comment is stripped before anything is sent, so guidance costs nothing per
turn, and a body holding nothing but headings (the template's `# Soul`) sends
nothing, so a fresh install's prompts are byte-identical to HEAD's (AC-422).

`PersonaFile` owns one file: created from its template ONLY IF ABSENT
(AC-420), re-read only when its mtime or size changed (AC-425), written by
the settings page with its instruction comment kept (AC-426), and REFUSED
when over its cap -- at write time by `Settings.validate`, at read time for a
hand edit, where the last good content stays in use and the reason is logged
and served as the setting's `problem` (AC-424).

`Persona` hands out two bound methods, one per path, each a resolver of the
same shape as `connections.SettingsResolver.resolve` and
`butlerd.harness_spec_resolver`:

* `blocks` -- the labelled system blocks the CONVERSATION model is shown,
  after `REPLY_SYSTEM` and before history: SOUL, then USER (AC-423), then the
  Memory rules when the brain has the memory tools (AC-438), then the memory
  index (AC-382), each only when it has content.
* `append_file` -- the PATH of a rendered file the HARNESS child is started
  with (`--append-system-prompt <path>`, AC-373). Content-addressed, so the
  path changes exactly when the content does, and the path is a
  `HarnessSpec` field: AC-60's one comparison then replaces a held child on
  its next turn (AC-374), never mid-turn.

The ROUTER is never handed either (4.4): it answers short questions itself on
the model chosen for speed, and a persona in front of a JSON classifier costs
every reflex turn tokens for a reader least able to use them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import dialogue, guide, settings, trace

#: What a rendered harness file is called: `append-<sha12>.md`.
PREFIX = "append-"

#: The heading each template's body starts with. A body holding only
#: headings says nothing about anybody and is sent as nothing (AC-421).
HEADINGS = {"soul": "# Soul", "user": "# User"}

# The instruction comment at the top of each template, for the OWNER and
# never sent (AC-421). It names NO language (AC-428, language.md D2): Butler
# adds no language instruction, and a template is Butler adding text.
GUIDES = {
    "soul": """SOUL.md -- who Butler is.
Butler reads this file in every conversation, in every project folder.
Everything inside this comment is guidance for you and is never sent.

Write here:
- Butler's name and personality.
- How it SPEAKS: spoken replies are heard, not read, so short and plain.
- How it WORKS: files, code and documents follow the project's own rules.
- What it may do without asking, and what it must ask about first.
- Boundaries: what it never does.

A project's own AGENTS.md is read after this file and wins for that
project's work.
Keep it under {cap} characters, not counting this comment. A longer file
is refused and the last version that fit stays in use.""",
    "user": """USER.md -- who you are.
Butler reads this file in every conversation, in every project folder.
Everything inside this comment is guidance for you and is never sent.

Write here:
- Your name, how to address you, and your roles.
- How you like answers: length, tone, level of detail.
- Standing priorities and preferences.
- People and places Butler should know.

Things you tell Butler with "remember that ..." go to its memory notes,
not here.
Keep it under {cap} characters, not counting this comment. A longer file
is refused and the last version that fit stays in use.""",
}

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")


def template(key: str, cap: int) -> str:
    """The file a fresh install gets: the instruction comment, then the
    heading, then nothing."""
    return f"<!--\n{GUIDES[key].format(cap=int(cap))}\n-->\n\n{HEADINGS[key]}\n"


def split_header(text: str) -> tuple[str, str]:
    """`(instruction comment, body)`. The header is a comment at the very top
    of the file; an unterminated one is no header at all, so its text stays in
    the body where the owner can see it rather than vanishing."""
    stripped = text.lstrip()
    if stripped.startswith("<!--"):
        end = stripped.find("-->")
        if end != -1:
            return stripped[:end + 3], stripped[end + 3:]
    return "", text


def sendable(body: str) -> str:
    """What a model is shown of a body: every HTML comment removed, ends
    trimmed, and "" when what is left is nothing but headings (AC-421)."""
    text = _COMMENT.sub("", body).strip()
    if all(not line.strip() or _HEADING.match(line)
           for line in text.splitlines()):
        return ""
    return text


def _decode(raw: bytes) -> str:
    """UTF-8, with Notepad's CRLF folded, so a hand edit and a page edit
    measure the same length."""
    return raw.decode("utf-8").replace("\r\n", "\n")


class PersonaFile:
    """One of the two files: its template, its cached reading, its writes."""

    def __init__(self, key: str, path: Path, cap: int):
        self.key = key
        self.path = Path(path)
        self.cap = int(cap)
        self._stamp: tuple | None = None
        self._read = False
        self._body = ""        # the body as it is on disk, for the page
        self._good = ""        # the last content that fit: what is SENT
        self._problem = ""     # why the file on disk is not what is sent

    def ensure(self) -> bool:
        """Create the file from its template ONLY IF ABSENT (AC-420). True
        when it was created. Exclusive create, so a file that appeared in the
        meantime is never overwritten either."""
        if self.path.exists():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "xb") as f:
                f.write(template(self.key, self.cap).encode("utf-8"))
        except FileExistsError:
            return False
        return True

    def body(self) -> str:
        self._refresh()
        return self._body

    def effective(self) -> str:
        self._refresh()
        return self._good

    def problem(self) -> str:
        self._refresh()
        return self._problem

    def _refresh(self) -> None:
        """Re-read only when the stamp moved (AC-425). NEVER RAISES for a bad
        file: a persona that cannot be read is a log line and a notice, never
        a failed turn (D-1)."""
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        if self._read and stamp == self._stamp:
            return
        self._read, self._stamp = True, stamp
        if stamp is None:
            # Deleting the file is the owner's act and means "nothing".
            self._body = self._good = self._problem = ""
            return
        name = self.path.name
        try:
            text = _decode(self.path.read_bytes())
        except (OSError, UnicodeDecodeError) as e:
            self._body = ""
            self._refuse(f"{name} could not be read ({e})")
            return
        body = split_header(text)[1].strip()
        self._body = body
        if len(body) > self.cap:
            self._refuse(f"{name} is {len(body)} characters, over its "
                         f"{self.cap} limit")
            return
        self._good = sendable(body)
        self._problem = ""

    def _refuse(self, why: str) -> None:
        kept = ("the last version that fit stays in use" if self._good
                else "nothing from it is sent")
        self._problem = f"{why}, so Butler is not using this version; {kept}."
        print(f"[butler] {self._problem} ({self.path})", flush=True)

    def write(self, body: str) -> None:
        """Replace the body, KEEPING the file's instruction comment (AC-426).
        A file that does not exist yet gets its template's comment; a file
        whose comment the owner deleted stays without one. The cap was
        checked by `Settings.validate` before this is called."""
        header = split_header(template(self.key, self.cap))[0]
        if self.path.exists():
            try:
                header = split_header(_decode(self.path.read_bytes()))[0]
            except UnicodeDecodeError:
                pass    # unreadable: the template's comment replaces it
        body = body.strip()
        parts = [p for p in (header, body) if p]
        _atomic_write(self.path, "\n\n".join(parts) + "\n" if parts else "")
        st = self.path.stat()
        self._read, self._stamp = True, (st.st_mtime_ns, st.st_size)
        self._body, self._good, self._problem = body, sendable(body), ""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def files_for(folder: Path) -> dict[str, PersonaFile]:
    """Both files in `folder`, named and capped by the settings schema, which
    is the one place either is spelled (`Setting.file`, `Setting.maximum`)."""
    return {s.key: PersonaFile(s.key, Path(folder) / s.file, s.maximum)
            for s in settings.SCHEMA if s.file}


def soul_file(folder: Path) -> PersonaFile:
    """SOUL.md ALONE, for a WhatsApp guest's turn (docs/messaging.md MS-14b,
    AC-536): the guest is shown who Butler is and never who the owner is, so
    this builds no handle on the other file at all."""
    spec = settings.BY_KEY["soul"]
    return PersonaFile(spec.key, Path(folder) / spec.file, spec.maximum)


def split_identity(text: str) -> dict[str, str]:
    """The retired `identity` value, split by 4.1's two headings (AC-427):
    under "About Butler" is SOUL, under "About you" is USER, text before any
    heading is SOUL, and any other heading stays with the section it is in."""
    parts: dict[str, list[str]] = {"soul": [], "user": []}
    current = "soul"
    for line in text.replace("\r\n", "\n").split("\n"):
        m = _HEADING.match(line)
        title = m.group(1).lower() if m else ""
        if "about butler" in title:
            current = "soul"
            continue
        if "about you" in title:
            current = "user"
            continue
        parts[current].append(line)
    return {k: "\n".join(v).strip() for k, v in parts.items()}


def migrate_identity(settings_path: Path,
                     files: dict[str, PersonaFile]) -> tuple[list, list]:
    """Move the retired `identity` setting into the files, ONCE (AC-427).

    Reads the settings FILE rather than a store, the `projects` and
    `terminal_cwd` precedent: the key is gone from the schema, so a `Settings`
    would have dropped it. Each part goes only into a file that sends nothing
    yet (absent, or template-only after `ensure`); a part whose file already
    says something is LEFT, logged, and the key is kept so no text is lost.
    When every part found a home the key is removed from the file, so this
    cannot repeat after the owner empties a file on purpose. Run BEFORE the
    `Settings` store is built, so it never sees the key. Answers
    `(placed, left)` file names.
    """
    path = Path(settings_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return [], []
    old = raw.get("identity") if isinstance(raw, dict) else None
    if not isinstance(old, str) or not old.strip():
        return [], []
    placed, left = [], []
    for key, text in split_identity(old).items():
        if not text:
            continue
        target = files[key]
        if target.effective() or target.problem():
            left.append(target.path.name)
            continue
        target.write(f"{HEADINGS[key]}\n\n{text}")
        placed.append(target.path.name)
    if placed:
        print(f"[butler] the retired identity setting was moved into "
              f"{placed} (docs/identity-memory.md 11.4)", flush=True)
    if left:
        print(f"[butler] the retired identity setting was NOT moved into "
              f"{left}: those files already have content. The old text stays "
              f"in {path} under 'identity'; copy what you want by hand.",
              flush=True)
        return placed, left
    del raw["identity"]
    # The same shape `Settings._write` writes, so the store reads it as its
    # own on the next line of `build_hub`.
    _atomic_write(path, json.dumps(raw, indent=1, sort_keys=True))
    return placed, left


class Persona:
    """SOUL.md, USER.md and the memory index, rendered for both paths."""

    def __init__(self, files: dict[str, PersonaFile], memory,
                 prompt_dir: Path, rules: bool = False,
                 projects=lambda: []):
        self._soul = files["soul"]
        self._user = files["user"]
        self._memory = memory
        self._dir = Path(prompt_dir)
        # The Memory rules block (docs/identity-memory.md 12.5, AC-438): True
        # exactly when the brain this persona serves HAS the memory tools --
        # `build_hub` decides, because only it knows whether the endpoint
        # brain got the store or the harness got a loopback URL. Rules for a
        # tool that is not there would teach a model to call nothing.
        self._rules = bool(rules)
        # () -> [(name, folder, is_main)]: the Projects block
        # (docs/todo-list.md AC-675), read per resolve so a row added in the
        # pane reaches the next request.
        self._projects = projects

    def blocks(self) -> tuple[str, ...]:
        """The labelled system blocks the conversation model is shown, in
        order, or () when there is nothing to show -- which is the shipped
        case and must add NOTHING to the request (AC-422)."""
        soul = self._soul.effective()
        user = self._user.effective()
        index = self._memory.index()
        # AC-385: the per-resolve size, into the voice trace, so phase 5 can
        # report a median and a maximum from real use rather than an
        # estimate. One line per resolve: per turn on the endpoint path, per
        # spec check on the harness path.
        rules = dialogue.MEMORY_RULES if self._rules else ""
        # The Tasks rules go wherever the Memory rules go: `task_add` is
        # offered exactly where the memory tools are (docs/automations.md
        # AU-9). Their own block, right after, so each is found whole.
        tasks = dialogue.TASK_RULES if self._rules else ""
        # And every project, right after them (docs/todo-list.md AC-675):
        # the names `about` takes and the folders an agent may work in.
        projects = (dialogue.projects_note(self._projects()) if self._rules
                    else "")
        # And the reminder and schedule rules beside them: those tools ride
        # with task_add (docs/proactive.md PR-20, PR-21).
        reminders = dialogue.REMINDER_RULES if self._rules else ""
        # And the recipe rules, beside them (docs/recipe-tools.md RT-4).
        recipes = dialogue.RECIPE_RULES if self._rules else ""
        # And what Butler itself can do (docs/butler-help.md BH-1, AC-641):
        # `butler_help` is offered exactly where these tools are. STATIC, so
        # it costs no respawn and no cold prefix between turns (AC-643).
        features = guide.block() if self._rules else ""
        trace.trace("persona", soul_chars=len(soul), user_chars=len(user),
                    rules_chars=len(rules) + len(tasks) + len(projects)
                    + len(reminders)
                    + len(recipes) + len(features),
                    index_chars=len(index))
        # SOUL, USER, then the rules -- AFTER the owner's files, which is what
        # lets SOUL.md say otherwise and the rules say so (ruling 5) -- then
        # the index (AC-423, AC-438).
        blocks = (dialogue.soul_note(soul), dialogue.user_note(user), rules,
                  tasks, projects, reminders, recipes, features,
                  dialogue.memory_note(index))
        return tuple(b for b in blocks if b)

    def append_file(self) -> str | None:
        """The rendered file's path, or None when there is nothing to append.

        None puts NO flag on the argv (AC-422): a flag naming an empty file
        would still add a section to the child's system prompt. The file is
        written only if absent -- same content, same name, same bytes -- so a
        per-turn call costs one hash and one `exists`.
        """
        blocks = self.blocks()
        if not blocks:
            return None
        body = ("\n\n".join(blocks) + "\n").encode("utf-8")
        path = self._dir / (PREFIX + hashlib.sha256(body).hexdigest()[:12]
                            + ".md")
        if not path.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        return str(path)

    def prune(self) -> list[str]:
        """Delete every rendered file. Called at BOOT only, when no child is
        alive and so no live spec names any of them; the next spec re-renders
        what it needs. Returns what was removed, for the boot log."""
        if not self._dir.is_dir():
            return []
        gone = []
        for path in sorted(self._dir.glob(PREFIX + "*.md")):
            path.unlink()
            gone.append(path.name)
        return gone
