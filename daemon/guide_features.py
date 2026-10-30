"""The feature table the agent is told about (docs/butler-help.md BH-3).

ONE row per thing the owner can use. `daemon/guide.py` turns it into the
always-on index and the `butler_help` answers; its `Feature` docstring says
what each field means. Every sentence here describes what HEAD does (BH-6):
labels are the page's own words, phrases are ones the daemon's matchers in
`daemon/settings.py` recognise (AC-653), and a test fails when a settings
group or an agent tool is covered by no row (AC-646, AC-647).
"""
from __future__ import annotations

from daemon.guide import Feature

FEATURES: tuple[Feature, ...] = (
    Feature(
        key="help",
        name="Butler help",
        index="what Butler can do",
        detail=(
            "Call butler_help with no topic for every feature and whether it "
            "is on now, or with a topic for how to use one. The owner can "
            "simply ask what Butler can do. Settings open from the gear "
            "beside Account at the foot of the sidebar: categories on the "
            "left, one page at a time, and a search box (or /) that finds a "
            "setting in any of them. The Voice pages start with the signal "
            "path; press a stage to open it. A change that needs a restart "
            "is listed in a bar at the foot of Settings, and Butler restarts "
            "by itself to apply it as soon as nothing is running."),
        tools=("butler_help",),
    ),
    Feature(
        key="chats",
        name="Chats and projects",
        index="find old chats; start fresh; switch project",
        detail=(
            "The sidebar (Ctrl+B) lists chats under their project; the plus "
            "beside Projects adds a folder as a project (any existing folder "
            "on this machine, given as a full path, but not a whole drive or "
            "the home folder itself). New chat at the top "
            "of the sidebar, or inside a project, starts one there. Search, "
            "under it, finds old chats by title and content. Say \"new "
            "conversation\" to start over, or \"go to project butler\" (any "
            "project's name) to open that project's newest chat. With "
            "Settings, Access, In-page terminals on (off by default, owner "
            "only), Open terminal under a project opens a shell in its "
            "folder in one tap; type claude, pi or opencode there; "
            "dictation types into a focused terminal. Chats exist "
            "only in the Chat + coding agent and Coding agent only modes."),
        say=(("spoken_new_conversation", "new conversation"),
             ("spoken_project_request", "go to project butler")),
    ),
    Feature(
        key="messages",
        name="Message actions",
        index="redo or reword a turn; copy or replay",
        detail=(
            "Small icons under each message (hover names them). Under the "
            "owner's message: Rewind drops it and everything after it and "
            "puts the text back in the box to say differently; the original "
            "is kept as a copy in the sidebar, and files the agent changed "
            "are not undone. Copy copies it. Under Butler's answer: Copy, "
            "Try again (sends the same question again) and Read aloud. "
            "Rewind needs a Brain mode with a coding agent."),
    ),
    Feature(
        key="compose",
        name="Composer",
        index="type, dictate, attach files or photos",
        detail=(
            "Type in the message box and press Enter; typing is a full peer "
            "of speaking. While Butler hears you the words appear inside the "
            "box. The microphone button in the box dictates: press, speak, "
            "press again and the words are inserted, nothing is sent. Send "
            "becomes Stop while Butler is busy (Esc too). The + offers Upload "
            "a file or Take a photo, and files can be dropped on the chat. "
            "Images go to the model that answers, which must be able to "
            "see; PDF and Word files are read as "
            "text on this machine first (Settings, Brain, Reading documents). "
            "Size limit: Attachment ceiling. While an agent works, a row at "
            "the end of the chat says what it is doing, with Stop; a "
            "question it asks sits on top of the box, and 1, 2, 3 pick an "
            "option. A lost link or a problem shows as one line above the "
            "box; Details opens Diagnostics."),
    ),
    Feature(
        key="memory",
        name="Memory and identity",
        index="remember or forget facts; SOUL.md, USER.md",
        detail=(
            "Say \"remember that my sister is called Ana\" (any fact after "
            "\"remember that\") and Butler saves it in the owner's words and reads it back; \"forget that\" deletes the "
            "newest note. The agent saves facts with memory_write (private "
            "keeps one out of the always-shown list) and searches notes and "
            "past conversations with memory_search. Settings, Identity holds "
            "Who Butler is (SOUL.md) and Who you are (USER.md)."),
        groups=("identity",),
        tools=("memory_search", "memory_write"),
        say=(("spoken_remember", "remember that my sister is called Ana"),
             ("spoken_forget", "forget that")),
    ),
    Feature(
        key="tasks",
        name="Tasks and reminders",
        index="note a to-do; be reminded aloud at a time",
        detail=(
            "Tasks belong to a project: Library, Tasks tab, the Add a task "
            "box, or say \"add a task buy milk\", \"what are my tasks\", "
            "then \"mark task two done\". A reminder is spoken aloud at its time and runs no "
            "agent: say \"remind me in 10 minutes to call mom\" or \"remind me "
            "to call mom at 5 pm\", or Library, Automations, New reminder. "
            "The agent adds them with task_add and reminder_add; every task "
            "it adds goes to the main project's list, tagged with the project "
            "it is about and why, and never twice while open."),
        tools=("task_add", "reminder_add"),
        say=(("spoken_task_add", "add a task buy milk"),
             ("spoken_task_list", "what are my tasks"),
             ("spoken_task_done", "mark task two done"),
             ("spoken_remind", "remind me in 10 minutes to call mom"),
             ("spoken_remind", "remind me to call mom at 5 pm")),
    ),
    Feature(
        key="recipes",
        name="Recipes",
        index="save a prompt; rerun it by name or with /",
        detail=(
            "A recipe is a saved prompt, for this project or Global: Library "
            "(sidebar), Recipes tab, New recipe. Run starts it in a new "
            "chat. Typing / at the start of the message box lists recipes "
            "and fills one in without sending. Say \"run recipe\" and its "
            "name, e.g. \"run recipe morning briefing\". The agent can look "
            "recipes up with recipe_find and follow one, and can propose "
            "saving a prompt with recipe_propose; it is saved only when the "
            "owner says \"yes\"."),
        say=(("spoken_run_request", "run recipe morning briefing"),),
        state=("recipes",),
        tools=("recipe_find", "recipe_propose"),
    ),
    Feature(
        key="automations",
        name="Automations",
        index="a recipe on a schedule, mail or event",
        detail=(
            "Library, Automations tab, New automation runs a recipe Daily, "
            "Weekdays, Weekly, Monthly or by cron, each run in its own chat; "
            "its Starts field can instead pick a trigger (Settings, Triggers: "
            "a mailbox, a calendar link or a webhook). Heartbeat runs a "
            "checklist every few minutes. Quiet hours (Settings, Quiet "
            "hours: on or off, Starts, Ends) hold unasked speech and "
            "heartbeats overnight; the owner's own messages, typed, spoken "
            "or from the phone, are always answered at any hour. Account, "
            "Tasks and automations (or a card's Past runs) opens every "
            "project's automations and tasks in the chat's place, the "
            "sidebar still there: filters, select several to turn on, off "
            "or run now, each one's past runs beside the list, and who "
            "added each task and when; J and K move, X selects, R runs "
            "now. The agent may propose one with "
            "schedule_propose; it is created only when the owner says "
            "\"yes\"."),
        groups=("triggers", "quiet"),
        tools=("schedule_propose",),
        say=(("spoken_confirm", "yes"),),
        state=("automations", "quiet"),
    ),
    Feature(
        key="notify",
        name="Notifications",
        index="results to the phone; chat via Telegram",
        detail=(
            "Settings, Notifications adds a channel (Telegram bot or "
            "WhatsApp) with Add channel and checks it with send test. A "
            "finished automation sends its result to the channels ticked in "
            "its Notify field; Default channel is pre-ticked. A Telegram "
            "channel with Inbound set to true lets the owner talk to Butler "
            "from that chat; /new starts a fresh chat."),
        groups=("notify",),
        state=("channels",),
    ),
    Feature(
        key="whatsapp",
        name="WhatsApp",
        index="read, search, send; talk to Butler from the phone",
        detail=(
            "Settings, WhatsApp, Link WhatsApp shows a code to scan in "
            "WhatsApp. Butler may read (off by default) lets the agent list, "
            "read and search chats; Butler may send lets it send, and the "
            "first message to each person waits for the owner's approval. "
            "Messages the owner writes in their own WhatsApp chat are turns "
            "for Butler, answered by voice note and text by default; /new "
            "starts fresh. "
            "Butler answers can let Butler reply to listed people, without "
            "tools."),
        groups=("whatsapp",),
        tools=("whatsapp_list_chats", "whatsapp_read_chat",
               "whatsapp_search_messages", "whatsapp_list_contacts",
               "whatsapp_send"),
        state=("whatsapp",),
    ),
    Feature(
        key="devices",
        name="Phone and app",
        index="a phone or tablet; pairing; the desktop app",
        detail=(
            "Settings, Devices, Set up phone access forwards this PC's "
            "Tailscale address, then scan the code with the phone; only "
            "devices on the owner's Tailscale network can reach it. The "
            "desktop app's tray menu has Open Butler, Phone link, Start with "
            "Windows, Restart Butler and Quit Butler. Only this PC, the "
            "owner's Tailscale login (Settings, Access, Tailscale owners; "
            "blank means this PC's) and paired devices can open Butler. "
            "Settings, Access, Pair a device shows a six-digit code and a "
            "QR code, good once for five minutes; Remove there unpairs a "
            "device at once. Open on the local network also allows this "
            "PC's network address, after a restart."),
        groups=("devices", "access"),
        state=("devices",),
    ),
    Feature(
        key="files",
        name="Files and showing",
        index="show an image, chart or table in chat",
        detail=(
            "The side panel's Files tab (Ctrl+Shift+E) lists the "
            "project's files and previews one under the list, and Copy path "
            "copies its full path; a file dropped on the panel (it says "
            "where while you drag) or on a folder is uploaded there, never "
            "over an existing one. The agent shows a file in the chat with "
            "show_file, or numbers, tables and charts with show_ui; each "
            "shows as a card, and a sound plays in its own small player. "
            "Showing needs a Brain mode with a coding agent; Chat only "
            "cannot show."),
        tools=("show_file", "show_ui"),
    ),
    Feature(
        key="voice",
        name="Voice",
        index="stop a reply, long talks, language",
        detail=(
            "Tap the voice pill at the top right to start listening, then "
            "say \"hey butler\"; tap it again to stop a reply. The arrow "
            "beside it has the switches (Voice options). Two wake words, "
            "\"hey butler\" and \"hey jarvis\", each have a setting under "
            "Listening: Off, Conversation on screen (talk to the chat the "
            "device shows) or New conversation (start a new chat in that "
            "chat's project first; Chat only has no chats, so it talks to "
            "the screen). Hey butler starts on screen and hey jarvis off; "
            "one must stay on. Turn taking has three modes: Pause (the "
            "default) answers when you pause, Pause length sets how long; "
            "Smart listens for a finished sentence; Long form waits for an "
            "end phrase. Say \"long form\" to talk at length and end with "
            "\"over to you\", \"pause mode\" for Pause, \"smart turn\" "
            "for Smart. Settings: Ambient context keeps a few seconds before "
            "the wake word; Language, Automatic answers in the language "
            "spoken; Speech sets voice and speed."),
        groups=("listen", "stt", "turn", "tts"),
        say=(("spoken_mode_request", "long form"),
             ("spoken_mode_request", "pause mode"),
             ("spoken_mode_request", "smart turn")),
        state=("listening", "speak", "ambient"),
    ),
    Feature(
        key="voice_options",
        name="Voice options",
        index="wake word, replies aloud, room memory",
        detail=(
            "The arrow beside the voice pill at the top right opens three "
            "switches: Wake word (listen for the wake words on this "
            "device's microphone), Read replies aloud (Butler speaks each "
            "reply as it streams), and Room memory (keeps the last seconds "
            "of the room for the next turn; \"Recording room\" shows in "
            "the header while it records). Each applies on every device."),
        state=("listening", "speak", "ambient"),
    ),
    Feature(
        key="account",
        name="Account menu",
        index="settings, theme, shortcuts, diagnostics, restart",
        detail=(
            "Account, at the foot of the sidebar, opens Settings, Appearance "
            "(Dark, Light, Auto), Tasks and automations, Keyboard shortcuts "
            "(every key, including each project's), Diagnostics (link, "
            "engines, wake score, recent errors; Details on an error line "
            "opens it too) and Restart Butler, which asks first. The gear "
            "beside Account opens Settings in one press."),
    ),
    Feature(
        key="panel",
        name="Side panel",
        index="files and library beside the chat",
        detail=(
            "The panel button at the top right opens one side panel with two "
            "tabs: Files (the project's files, read only, Ctrl+Shift+E) and "
            "Library (recipes, automations, tasks); Library in the sidebar "
            "opens it on that tab. On a phone it rises from the bottom."),
    ),
    Feature(
        key="desk",
        name="Desk keys",
        index="dictate into any app; open an agent in a folder",
        detail=(
            "Settings, Listening, Desk hotkeys (needs a restart to turn on): "
            "hold numpad star, speak, release, and the words are pasted into "
            "the window in front. In the sidebar, a project's three-dot menu "
            "has Open claude here, Open pi here and Open opencode here, each "
            "a terminal running that agent in the project folder on the desk "
            "PC; claude runs with permissions skipped. Set keys binds a key "
            "to each; Account, Keyboard shortcuts lists them."),
    ),
    Feature(
        key="brain",
        name="Brain and agent",
        index="models; chats on pi, Claude Code or opencode",
        detail=(
            "Settings, Brain, Brain mode: Chat only talks and nothing else; "
            "Chat + coding agent sends real work to a coding agent; Coding "
            "agent only sends everything to it; a change restarts Butler by "
            "itself "
            "once nothing is running. "
            "A chat runs on one of the agents switched on in Settings, "
            "Coding agent, Chat agents (only pi by default; Claude Code and "
            "opencode can be added). A new chat starts "
            "on the default (Settings, Coding agent, Default coding agent); "
            "while it is still "
            "empty the agent chip in the message box changes it, and the "
            "small arrow beside New chat starts one on a chosen agent. Once "
            "something is said the chat keeps that agent, shown beside its "
            "title. Claude Code and opencode chats run "
            "with permissions skipped and without Butler's memory tools. "
            "Each chat has its own model: pick it on the chip in the message "
            "box (agent . model), or say \"use sonnet\" or \"back to the "
            "default model\"; it applies from the next reply. Settings, "
            "Default model is only what a chat with no model of its own "
            "uses. Agent "
            "tools: Read only lets "
            "the agent read files only; Full adds commands, edits and "
            "writes."),
        groups=("brain", "harness"),
        state=("brain_mode", "harness_tools"),
    ),
    Feature(
        key="look",
        name="Appearance",
        index="theme, text size, voice effect",
        detail=(
            "Settings, Appearance: Style, Theme (dark, light, "
            "or auto to follow the device), Voice effect (Lantern, the "
            "default, lights the message box's border: blue while Butler "
            "hears, amber while it thinks or speaks), Wave placement (only "
            "for the lattice effect), "
            "Reading face (the face of Butler's replies; Style default keeps "
            "the style's own) and Text size. The Account menu switches "
            "Dark, Light or Auto in one press. Changes apply at once on every "
            "device."),
        groups=("look",),
    ),
    Feature(
        key="style",
        name="Style",
        index="Lantern or Caption",
        detail=(
            "Style, first in Settings, Appearance: "
            "Lantern (the default) is warm, with Butler's replies in a "
            "book-like serif, and the message box's border lights while "
            "Butler hears you, with your words inside the box; Caption is "
            "cool, with one plain typeface throughout, shows your words as a "
            "large caption above the box and a small waveform on the box's "
            "top edge. Both keep the same controls and the same meaning "
            "of colour: blue while Butler listens, amber while it thinks "
            "and speaks, red for a problem. Applies at once on every "
            "device."),
    ),
)

#: Settings groups no feature covers, each with the reason (AC-646).
EXCLUDED_GROUPS: dict[str, str] = {}
