/**
 * Butler's memory tools for pi: memory_search and memory_write -- and, since
 * docs/automations.md phase 3 (AU-9, AC-454), task_add, offered exactly where
 * the memory tools are; and beside it reminder_add and schedule_propose
 * (docs/proactive.md PR-20, PR-21), plus the per-prompt clock line they read,
 * and recipe_find and recipe_propose (docs/recipe-tools.md RT-1, RT-3).
 *
 * docs/identity-memory.md 12.6 (AC-440, AC-441). Loaded by the daemon with
 *   pi --extension <this file> --butler-url https://127.0.0.1:<port>
 * and never installed into the owner's pi config, which belongs to every pi
 * they run by hand.
 *
 * NO LOGIC LIVES HERE. Both tools POST to the daemon, which owns the one
 * implementation (daemon/memory.py: the scan, the cap, the supersede rule,
 * the search, and the text a model is shown). A second copy of the scan in
 * this file would drift from the first (D-18).
 *
 * The names and parameters below mirror daemon/memory.py TOOLS and
 * daemon/library.py TOOLS; tests in test/test_memory_v2.py and
 * test/test_library_voice.py hold them together, because this file cannot
 * import those.
 *
 * task_add sends the CONVERSATION this child serves (--butler-chat, set by
 * the daemon at spawn) and never where to store it: the daemon files every
 * agent task on the MAIN list (docs/todo-list.md AC-671). `about` only tags
 * it with a project name from the Projects block.
 *
 * LOOPBACK ONLY, and refused loudly otherwise: the daemon's certificate is
 * self-signed, so this file skips certificate verification -- which is only
 * sound for a connection that never leaves the machine.
 *
 * THE WHATSAPP TOOLS (docs/messaging.md MS-8, MS-18b; AC-508, AC-562) mirror
 * daemon/whatsapp.py READ_TOOLS and SEND_TOOLS (test/test_whatsapp_read.py
 * holds them together). All five are registered, because pi's flags are not
 * readable until `session_start`; there, every WhatsApp tool the daemon did
 * NOT name in the `whatsapp` query of --butler-url is deactivated. The daemon
 * puts that list in the URL from the settings, so a switch is a new spawn
 * spec and a new child (AC-60). Reads POST to /api/whatsapp/read/<tool>;
 * whatsapp_send uploads through the loopback send door. For a file THIS
 * TOOL reads the bytes (the door never opens a path a caller names) and
 * sends the real path it read as `source` with the conversation as `chat`,
 * which the daemon checks against that conversation's project folder and
 * the data root before anything is sent.
 */

import * as fs from "node:fs";
import * as http from "node:http";
import * as https from "node:https";
import * as path from "node:path";

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

/**
 * Every local route also needs this header (docs/messaging.md 6.5 RV-1,
 * AC-586; daemon/hub.py LOCAL_HEADER): a web page cannot send it
 * cross-site, this extension can.
 */
const LOCAL_HEADER = { "x-butler-local": "1" };

function daemonUrl(raw: unknown): URL {
  if (typeof raw !== "string" || !raw) {
    throw new Error(
      "Butler's memory tools need --butler-url; this pi was not started by Butler.",
    );
  }
  const url = new URL(raw);
  if (!LOOPBACK.has(url.hostname)) {
    throw new Error(
      `--butler-url ${raw} is not a loopback address; Butler's memory is served to this machine only.`,
    );
  }
  return url;
}

function post(base: URL, route: string, body: unknown, signal?: AbortSignal): Promise<any> {
  const url = new URL(route, base);
  const lib = url.protocol === "https:" ? https : http;
  const data = JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = lib.request(
      url,
      {
        method: "POST",
        headers: {
          ...LOCAL_HEADER,
          "content-type": "application/json",
          "content-length": Buffer.byteLength(data),
        },
        // Loopback only (daemonUrl refuses anything else): the daemon's
        // certificate is self-signed for its LAN name, not for 127.0.0.1.
        rejectUnauthorized: false,
        signal,
      },
      (res) => {
        let text = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => (text += chunk));
        res.on("end", () => {
          let parsed: any;
          try {
            parsed = JSON.parse(text);
          } catch {
            reject(new Error(`Butler answered ${res.statusCode}: ${text.slice(0, 200)}`));
            return;
          }
          if (res.statusCode !== 200) {
            reject(new Error(parsed?.error || `Butler answered ${res.statusCode}`));
            return;
          }
          resolve(parsed);
        });
      },
    );
    req.on("error", reject);
    req.end(data);
  });
}

/**
 * One request whose every status is an ANSWER, not an error: the send door's
 * 409 and 403 are what the model must be told (the daemon renders `text`).
 */
function send(base: URL, route: string, body: Buffer, contentType: string,
              signal?: AbortSignal): Promise<{ status: number; body: any }> {
  const url = new URL(route, base);
  const lib = url.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const req = lib.request(
      url,
      {
        method: "POST",
        headers: { ...LOCAL_HEADER, "content-type": contentType, "content-length": body.length },
        rejectUnauthorized: false,   // loopback only, as post()
        signal,
      },
      (res) => {
        let raw = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => (raw += chunk));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode ?? 0, body: JSON.parse(raw) });
          } catch {
            reject(new Error(`Butler answered ${res.statusCode}: ${raw.slice(0, 200)}`));
          }
        });
      },
    );
    req.on("error", reject);
    req.end(body);
  });
}

/** A multipart body: string fields, then the ONE file in `files`. */
function multipart(fields: Record<string, string>, name: string, data: Buffer) {
  const boundary = `butler${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  const parts: Buffer[] = [];
  for (const [key, value] of Object.entries(fields)) {
    parts.push(Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="${key}"\r\n\r\n${value}\r\n`, "utf8"));
  }
  const safe = name.replace(/["\r\n]/g, "_");
  parts.push(Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="${safe}"\r\n`
    + "Content-Type: application/octet-stream\r\n\r\n", "utf8"));
  parts.push(data, Buffer.from(`\r\n--${boundary}--\r\n`, "utf8"));
  return { body: Buffer.concat(parts), type: `multipart/form-data; boundary=${boundary}` };
}

/** The send door's own cap (daemon/whatsapp.py MAX_FILE_BYTES). */
const MAX_FILE_BYTES = 100 * 1024 * 1024;

const WHATSAPP_TOOLS = [
  "whatsapp_list_chats",
  "whatsapp_read_chat",
  "whatsapp_search_messages",
  "whatsapp_list_contacts",
  "whatsapp_send",
];

/** The WhatsApp tools the daemon named on --butler-url (`?whatsapp=a,b`). */
function whatsappAllowed(raw: unknown): Set<string> {
  if (typeof raw !== "string" || !raw) return new Set();
  const list = new URL(raw).searchParams.get("whatsapp");
  return new Set(list ? list.split(",") : []);
}

function text(value: string) {
  return { content: [{ type: "text" as const, text: value }], details: undefined };
}

export default function butlerMemory(pi: any): void {
  pi.registerFlag("butler-url", {
    description: "Butler daemon's loopback address, for the memory tools",
    type: "string",
  });

  pi.registerFlag("butler-chat", {
    description: "The Butler conversation this pi serves, for the library tools",
    type: "string",
  });

  // AC-508, AC-562: a WhatsApp tool the daemon did not name is not offered.
  pi.on("session_start", async () => {
    const allowed = whatsappAllowed(pi.getFlag("butler-url"));
    pi.setActiveTools(pi.getActiveTools().filter(
      (name: string) => !WHATSAPP_TOOLS.includes(name) || allowed.has(name)));
  });

  const readTool = (name: string, label: string, description: string,
                    properties: any, required: string[]) => pi.registerTool({
    name,
    label,
    description,
    parameters: { type: "object", properties, required },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const base = daemonUrl(pi.getFlag("butler-url"));
      const answer = await post(base, `/api/whatsapp/read/${name}`, params ?? {}, signal);
      return text(answer.text);
    },
  });

  readTool("whatsapp_list_chats", "List WhatsApp chats",
    "List the user's WhatsApp conversations, most recently active first. Use to find the exact name of a chat before reading it.",
    {
      groups_only: { type: "boolean", description: "Only list group chats." },
      limit: { type: "integer", minimum: 1, maximum: 200, description: "How many chats to list (default 30)." },
    }, []);

  readTool("whatsapp_read_chat", "Read WhatsApp chat",
    "Read recent messages from one WhatsApp conversation, newest first. Accepts a contact name, a group name, or a phone number.",
    {
      chat: { type: "string", description: "Contact name, group name, or phone number." },
      count: { type: "integer", minimum: 1, maximum: 200, description: "How many messages (default 30)." },
    }, ["chat"]);

  readTool("whatsapp_search_messages", "Search WhatsApp messages",
    "Search the text of stored WhatsApp messages across every conversation.",
    {
      query: { type: "string", description: "Text to look for." },
      limit: { type: "integer", minimum: 1, maximum: 200, description: "Maximum hits (default 30)." },
    }, ["query"]);

  readTool("whatsapp_list_contacts", "List WhatsApp contacts",
    "Look up the user's WhatsApp contacts, optionally filtered by name. Use to confirm exactly who a name refers to before messaging them.",
    {
      query: { type: "string", description: "Filter by name or number." },
      limit: { type: "integer", minimum: 1, maximum: 200, description: "Maximum contacts (default 50)." },
    }, []);

  pi.registerTool({
    name: "whatsapp_send",
    label: "Send WhatsApp",
    description:
      "Send a WhatsApp message, or one file, from the user's own number. Use recipient \"me\" for the user's own chat. For ANYONE ELSE, ASK THE USER FIRST, naming the recipient and the exact text or file, unless they have just told you to send exactly this. The first message to a person is held and Butler itself asks the user to approve it; the answer says whether it was sent. Never send in bulk, and never write to someone who has not written to the user first unless the user says so. Always tell the user whether it was sent.",
    parameters: {
      type: "object",
      properties: {
        recipient: { type: "string", description: "\"me\", or a saved contact or group: name, phone number or chat id." },
        text: { type: "string", description: "The message. Required unless file is given." },
        file: { type: "string", description: "A file to send, inside this project's folder or Butler's data root. Its type decides how it appears (inline image, video or audio, else a document). At most 100 MB." },
        caption: { type: "string", description: "Caption for the file." },
      },
      required: ["recipient"],
    },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const base = daemonUrl(pi.getFlag("butler-url"));
      // The conversation goes with EVERY send and `confirm` with none: the
      // daemon treats a send naming its chat as a tool's, and a held first
      // send becomes ITS question to the owner in that chat (RV-2, AC-588).
      const chat = pi.getFlag("butler-chat");
      if (typeof chat !== "string" || !chat) {
        throw new Error("whatsapp_send needs --butler-chat; this pi was not started by Butler.");
      }
      if (!params?.file) {
        const json = Buffer.from(JSON.stringify(
          { recipient: params?.recipient, text: params?.text, chat }), "utf8");
        const answer = await send(base, "/api/whatsapp/send", json, "application/json", signal);
        return text(answer.body.text);
      }
      // The REAL path, so the daemon judges where the bytes come from, not
      // the name a link gives them (AC-562). Size before reading a byte.
      const real = fs.realpathSync(path.resolve(String(params.file)));
      const size = fs.statSync(real).size;
      if (size > MAX_FILE_BYTES) {
        return text(`NOT SENT. ${path.basename(real)} is ${size} bytes; the limit is ${MAX_FILE_BYTES}.`);
      }
      const fields: Record<string, string> = {
        recipient: String(params.recipient ?? ""),
        chat,
        source: real,
      };
      if (params.text) fields.text = String(params.text);
      if (params.caption) fields.caption = String(params.caption);
      const form = multipart(fields, path.basename(real), fs.readFileSync(real));
      const answer = await send(base, "/api/whatsapp/send", form.body, form.type, signal);
      return text(answer.body.text);
    },
  });

  pi.registerTool({
    name: "memory_search",
    label: "Memory search",
    description:
      "Search Butler's saved notes and past conversations. Use before answering anything personal or about past decisions.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: "Words to look for." },
        limit: {
          type: "integer",
          minimum: 1,
          maximum: 10,
          description: "Most hits of each kind (default 5).",
        },
      },
      required: ["query"],
    },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const base = daemonUrl(pi.getFlag("butler-url"));
      const answer = await post(base, "/api/memory/search", params, signal);
      return text(answer.text);
    },
  });

  pi.registerTool({
    name: "memory_write",
    label: "Memory write",
    description:
      "Save ONE durable fact about the user's life (a person, preference, decision, date or commitment), under 300 characters. Writing an existing title replaces that note.",
    parameters: {
      type: "object",
      properties: {
        title: { type: "string", description: "A short topic, reused to correct it." },
        text: { type: "string", description: "The fact, one or two sentences." },
        private: {
          type: "boolean",
          description: "True keeps it out of the always-shown list.",
        },
      },
      required: ["title", "text"],
    },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const base = daemonUrl(pi.getFlag("butler-url"));
      const answer = await post(base, "/api/memory/write", params, signal);
      return text(answer.text);
    },
  });

  pi.registerTool({
    name: "task_add",
    label: "Add task",
    description:
      "Add ONE to-do to the owner's main list: something to do later with no set time. Under 200 characters, one line.",
    parameters: {
      type: "object",
      properties: {
        text: { type: "string", description: "The task, in a few words." },
        about: { type: "string", description: "The name of the project it concerns, from the Projects list. Leave out for this conversation's project." },
        why: { type: "string", description: "Why it matters, a few words." },
      },
      required: ["text"],
    },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const base = daemonUrl(pi.getFlag("butler-url"));
      const chat = pi.getFlag("butler-chat");
      if (typeof chat !== "string" || !chat) {
        throw new Error("task_add needs --butler-chat; this pi was not started by Butler.");
      }
      const answer = await post(base, "/api/tasks/agent",
                                { text: params?.text, about: params?.about,
                                  why: params?.why ?? "", chat }, signal);
      return text(answer.text);
    },
  });

  // reminder_add and schedule_propose (docs/proactive.md PR-20, PR-21):
  // the same shape as task_add, through /api/library/agent, which hands the
  // call to the daemon's one dispatcher. schedule_propose writes nothing;
  // the daemon asks the user and only their yes creates the schedule.
  const libraryTool = (tool: { name: string; label: string; description: string;
                                properties: any; required: string[] }) => pi.registerTool({
    name: tool.name,
    label: tool.label,
    description: tool.description,
    parameters: { type: "object", properties: tool.properties, required: tool.required },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const name = tool.name;
      const base = daemonUrl(pi.getFlag("butler-url"));
      const chat = pi.getFlag("butler-chat");
      if (typeof chat !== "string" || !chat) {
        throw new Error(`${name} needs --butler-chat; this pi was not started by Butler.`);
      }
      const answer = await post(base, "/api/library/agent",
                                { tool: name, args: params ?? {}, chat }, signal);
      return text(answer.text);
    },
  });

  libraryTool({
    name: "reminder_add",
    label: "Set reminder",
    description:
      "Set ONE reminder Butler says aloud at a time the user asked for. Under 200 characters. No agent runs when it fires.",
    properties: {
      text: { type: "string", description: "What to remind them of, in their own words." },
      when: { type: "string", description: "A local date and time, YYYY-MM-DDTHH:MM, worked out from the 'Local time now' line; or a 5-field cron for a repeating one." },
    },
    required: ["text", "when"],
  });

  libraryTool({
    name: "schedule_propose",
    label: "Propose schedule",
    description:
      "PROPOSE running a prompt on a schedule in this conversation's project. Creates nothing: Butler asks the user, and only their yes creates it.",
    properties: {
      name: { type: "string", description: "A short name, under 60 characters." },
      cron: { type: "string", description: "5-field cron in local time, e.g. '0 9 * * 1-5' for weekdays at 9:00." },
      prompt: { type: "string", description: "The instruction each run is given, complete on its own." },
    },
    required: ["name", "cron", "prompt"],
  });

  // recipe_find and recipe_propose (docs/recipe-tools.md RT-1, RT-3): the
  // same route and dispatcher. recipe_find only reads; recipe_propose saves
  // nothing -- the daemon asks the user and only their yes saves it.
  libraryTool({
    name: "recipe_find",
    label: "Find recipes",
    description:
      "Find the user's saved recipes (saved prompts) for this conversation's project and Global. Every query word must match the name or prompt; no query lists them. Reads only.",
    properties: {
      query: { type: "string", description: "Words to look for; leave it out to list them." },
    },
    required: [],
  });

  libraryTool({
    name: "recipe_propose",
    label: "Propose recipe",
    description:
      "PROPOSE saving a prompt as a recipe in this conversation's project, to rerun by name. Saves nothing: Butler asks the user, and only their yes saves it.",
    properties: {
      name: { type: "string", description: "A short name, under 60 characters." },
      prompt: { type: "string", description: "The prompt to save, complete on its own." },
    },
    required: ["name", "prompt"],
  });

  // show_file and show_ui (docs/rich-chat.md RC-1, RC-2): the libraryTool
  // shape, through /api/artifacts/show. The PATH goes as the model wrote it:
  // the daemon resolves it against the conversation's project folder (this
  // child's working folder) and judges the REAL path with the same guard as
  // the WhatsApp send (RC-11), so nothing here reads or checks a file.
  const showTool = (tool: { name: string; label: string; description: string;
                             properties: any; required: string[] }) => pi.registerTool({
    name: tool.name,
    label: tool.label,
    description: tool.description,
    parameters: { type: "object", properties: tool.properties, required: tool.required },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const name = tool.name;
      const base = daemonUrl(pi.getFlag("butler-url"));
      const chat = pi.getFlag("butler-chat");
      if (typeof chat !== "string" || !chat) {
        throw new Error(`${name} needs --butler-chat; this pi was not started by Butler.`);
      }
      const answer = await post(base, "/api/artifacts/show",
                                { tool: name, args: params ?? {}, chat }, signal);
      return text(answer.text);
    },
  });

  showTool({
    name: "show_file",
    label: "Show file",
    description:
      "Show the user ONE file in the chat: an image, chart, audio or video plays inline; text, markdown, code and CSV are shown as text or a table; any other file appears as a download link. The file must be inside this project's folder or Butler's data root. Use it after you make or find something worth seeing. Your spoken or written answer must still make sense without it.",
    properties: {
      path: { type: "string", description: "The file, relative to the project folder or absolute." },
      caption: { type: "string", description: "One short line shown under it." },
    },
    required: ["path"],
  });

  showTool({
    name: "show_ui",
    label: "Show layout",
    description:
      "Show the user a small layout in the chat: headline numbers, tables, lists, bar or line charts, grouped in cards. Blocks: {type:'Card', title, blocks:[...]}, {type:'Text', markdown}, {type:'Stat', label, value, unit?, delta?}, {type:'Table', columns:[text], rows:[[cell]]}, {type:'List', items:[text]}, {type:'BarChart'|'LineChart', title?, labels:[text], series:[{name, values:[number]}]} with one value per label. An invalid layout is refused with the reason; fix it and call again. Your answer must still make sense without it.",
    properties: {
      title: { type: "string", description: "What this shows." },
      blocks: { type: "array", items: { type: "object" }, description: "The blocks, top to bottom." },
    },
    required: ["title", "blocks"],
  });

  // butler_help (docs/butler-help.md BH-2): what Butler itself can do and
  // what is on now. The daemon reads the state per call and writes the
  // text; nothing is decided here.
  pi.registerTool({
    name: "butler_help",
    label: "Butler help",
    description:
      "Ask what Butler (this app) can do: with no topic, every feature and what is on now; with a topic, how the user uses that feature, its limits and its settings. Use it before suggesting a feature, and when the user asks what Butler can do.",
    parameters: {
      type: "object",
      properties: {
        topic: { type: "string", description: "A feature key or name from the Butler features list; leave out for all of them." },
      },
      required: [],
    },
    async execute(_id: string, params: any, signal?: AbortSignal) {
      const base = daemonUrl(pi.getFlag("butler-url"));
      const answer = await post(base, "/api/guide/agent", { topic: params?.topic }, signal);
      return text(answer.text);
    },
  });

  // The brain's clock (AC-491): one line AFTER each prompt, the shape of
  // daemon/dialogue.py clock_note. A message per prompt rather than text in
  // the system prompt, which would change every minute and cost the cached
  // prefix over the whole conversation.
  pi.on("before_agent_start", async () => ({
    message: { customType: "butler-clock", content: clockNote(new Date()), display: false },
  }));
}

const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

/** "Local time now: Wednesday 2026-10-07 14:05 (UTC-04:00)." */
function clockNote(now: Date): string {
  const two = (n: number) => String(n).padStart(2, "0");
  const off = -now.getTimezoneOffset();
  const sign = off < 0 ? "-" : "+";
  const abs = Math.abs(off);
  return `Local time now: ${DAY_NAMES[now.getDay()]} ${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())} `
    + `${two(now.getHours())}:${two(now.getMinutes())} (UTC${sign}${two(Math.floor(abs / 60))}:${two(abs % 60)}).`;
}
