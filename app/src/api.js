import Constants from 'expo-constants';
import { Platform } from 'react-native';

export const PORT = 8765;

// Expo Go loads this bundle from the Mac over the network, so the Mac's
// address is already known here -- there is nothing for anyone to type into
// an iPad. On the web build the page came from the same machine.
export function defaultHost() {
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    return window.location.hostname || 'localhost';
  }
  const uri =
    Constants.expoConfig?.hostUri ||
    Constants.expoGoConfig?.debuggerHost ||
    Constants.manifest2?.extra?.expoGo?.debuggerHost ||
    '';
  return uri.split(':')[0] || 'localhost';
}

export const baseFor = (host) => `http://${host}:${PORT}`;

async function ask(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res;
}

export const getJSON = async (base, path) =>
  (await ask(`${base}${path}`)).json();

export const getText = async (base, path) => (await ask(`${base}${path}`)).text();

export const post = async (base, path, body) =>
  (await ask(`${base}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })).text();

// The agent operations, named rather than spelled out at each call site --
// a component should ask for what it wants, not remember a path.
export const listAgents = async (base) =>
  (await getJSON(base, '/agents')).agents || [];

export const listMcps = async (base, cwd) =>
  (await getJSON(base, `/mcps${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`)).mcps || [];

// Health is a separate call from the list on purpose: the list is a file read
// and answers instantly, the health check starts every configured server and
// takes about nine seconds. The rail draws on the first and fills in the
// second when it lands.
export const mcpHealth = (base, cwd) =>
  getJSON(base, `/mcp/health?cwd=${encodeURIComponent(cwd || '')}`);

// login opens a browser and cannot be awaited -- its result turns up in the
// next health check, which is the thing you are already watching.
export const mcpDo = (base, verb, body) => post(base, `/mcp/${verb}`, body);

// A wire, not a composer: these bytes go to the pane as typed. The control
// sequences ride in the same string a character does -- \x03 is Ctrl-C.
export const sendKeys = (base, terminal_id, keys) =>
  post(base, '/keys', { terminal_id, keys });

// Hands the pane to a real terminal on the machine running midiAI. The
// in-app one is a picture with a keyboard on it; this is the thing itself.
// Which pane the in-app terminal is looking at now. A read: tmux owns the
// active pane and shares it between clients, so the app follows rather than
// steers -- moving it from here would move somebody else's cursor.
export const ptyWhere = (base, pane) =>
  getJSON(base, `/pty/where?pane=${encodeURIComponent(pane || '')}`);

export const openTerminal = (base, terminal_id) =>
  post(base, '/terminal/open', { terminal_id });

export const getPr = (base, cwd, number) =>
  getJSON(base, `/pr?cwd=${encodeURIComponent(cwd || '')}&number=${Number(number)}`);

export const listPrs = (base, cwd) =>
  getJSON(base, `/prs?cwd=${encodeURIComponent(cwd || '')}`);

export const reviewPr = (base, cwd, number, action, body = '') =>
  post(base, '/pr/review', { cwd, number, action, body });

// A workflow is addressed by name, never by path -- the server derives
// `.github/workflows/<name>.yml` itself and rejects anything a `..` could ride
// in on, so there is no path on this wire for the app to get wrong.
export const listWorkflows = (base, cwd) =>
  getJSON(base, `/workflows?cwd=${encodeURIComponent(cwd || '')}`);

export const getWorkflow = (base, cwd, name) =>
  getJSON(base, `/workflow?cwd=${encodeURIComponent(cwd || '')}&name=${encodeURIComponent(name || '')}`);

// `replace` is the editor saying it opened this one: without it the server
// answers 409 rather than overwriting a pipeline someone is relying on.
export const saveWorkflow = (base, cwd, name, body, replace = false) =>
  post(base, '/workflow', { cwd, name, body, replace });

// The files that govern a pull request are addressed by a key from a fixed
// table the server owns -- tighter even than a workflow's name, since there is
// no open-ended part at all.
export const listGoverns = (base, cwd) =>
  getJSON(base, `/governs?cwd=${encodeURIComponent(cwd || '')}`);

export const getGovern = (base, cwd, key) =>
  getJSON(base, `/govern?cwd=${encodeURIComponent(cwd || '')}&key=${encodeURIComponent(key || '')}`);

export const saveGovern = (base, cwd, key, body, replace = false) =>
  post(base, '/govern', { cwd, key, body, replace });

// Only the state -- which are ticked, which were added, which starters were
// struck out. The list itself ships with the app.
export const getGuardrails = (base, cwd) =>
  getJSON(base, `/guardrails?cwd=${encodeURIComponent(cwd || '')}`);

export const saveGuardrails = (base, cwd, state) =>
  post(base, '/guardrails', { cwd, ...state });

// A saved checklist, for use in another checkout. The items travel; the ticks
// do not -- whether a guardrail is in force is a fact about one repo.
export const getGuardrailTemplates = (base) =>
  getJSON(base, '/guardrail-templates');

// bindings: the checklist's own entry/exit events, `{phase: {entry:[...], exit:[...]}}`
// -- carried alongside the items so a template reproduces the tool-agnostic
// layer too, not just which guardrails exist.
export const saveGuardrailTemplate = (base, name, items, phases, bindings, replace = false) =>
  post(base, '/guardrail-template', { name, items, phases, bindings, replace });

export const dropGuardrailTemplate = (base, name) =>
  post(base, '/guardrail-template/delete', { name });

// Enforcement: a guardrail can carry a script or an agent-review brief that
// checks it mechanically, kept in the repo under .guardrails/ and compiled by
// guardrails.py. This is a separate store from getGuardrails/saveGuardrails
// (that one is the checklist itself, in ~/.midiai/guardrails.json) -- status
// here is manifest + last results + local approvals + hook state, all at once,
// the same "one call, several things read together" idea as getWork.
export const getEnforce = (base, cwd) =>
  getJSON(base, `/guardrails/enforce?cwd=${encodeURIComponent(cwd || '')}`);

export const compileRail = (base, cwd, item) =>
  post(base, '/guardrails/compile', { cwd, item }).then((t) => JSON.parse(t));

export const getRail = (base, cwd, id) =>
  post(base, '/guardrails/rail', { cwd, id }).then((t) => JSON.parse(t));

// sha is the digest getRail handed back with the text that was shown: the
// server approves exactly that, and refuses if the file has moved since
export const approveRail = (base, cwd, id, sha) =>
  post(base, '/guardrails/approve', { cwd, id, sha }).then((t) => JSON.parse(t));

export const runRails = (base, cwd, { ids, phase } = {}) =>
  post(base, '/guardrails/run', {
    cwd,
    ...(ids ? { ids } : {}),
    ...(phase ? { phase } : {}),
  }).then((t) => JSON.parse(t));

export const setRailBlocking = (base, cwd, id, blocking) =>
  post(base, '/guardrails/blocking', { cwd, id, blocking: !!blocking }).then((t) => JSON.parse(t));

// A phase's gate (entry or exit) bound to whichever events should run it --
// `events` is the whole list for that (phase, gate), not one added or removed.
export const setBinding = (base, cwd, phase, gate, events) =>
  post(base, '/guardrails/binding', { cwd, phase, gate, events }).then((t) => JSON.parse(t));

// Which gate a single rail belongs to -- entry or exit of its phase.
export const setRailGate = (base, cwd, id, gate) =>
  post(base, '/guardrails/gate', { cwd, id, gate }).then((t) => JSON.parse(t));

export const setGuardrailHooks = (base, cwd, install) =>
  post(base, '/guardrails/hooks', { cwd, install: !!install }).then((t) => JSON.parse(t));

export const getTests = (base, cwd, path = []) =>
  getJSON(
    base,
    `/tests?cwd=${encodeURIComponent(cwd || '')}&path=${encodeURIComponent(
      (path || []).join('/')
    )}`
  );

export const runTests = (base, cwd, path = [], file = '') =>
  post(base, '/tests/run', { cwd, path, file });

export const stopTests = (base, cwd) => post(base, '/tests/stop', { cwd });

export const createAgent = (base, cwd, name) =>
  post(base, '/agents', name ? { cwd, name } : { cwd });

export const renameAgent = (base, terminal_id, name) =>
  post(base, '/agents/rename', { terminal_id, name });

export const closeAgent = (base, terminal_id) =>
  post(base, '/agents/close', { terminal_id });

// terminal_id omitted means "wherever the Push is pointed", which is the same
// target a pad fires into -- one idea of the current session, not two.
// replace: the composer is showing the agent's input line back and this is
// that line edited, so the server empties it before typing. Without it the
// edit lands on top of the original.
export const promptAgent = (base, text, submit, terminal_id, replace) =>
  post(base, '/prompt', {
    text, submit, replace: !!replace,
    ...(terminal_id ? { terminal_id } : {}),
  });

// A subagent type's definition: which file the next dispatch of it is built
// from, and the model that file pins. A running subagent's model is fixed;
// setAgentModel changes the definition, so it applies from the next one.
export const getAgentDef = (base, type, cwd) =>
  getJSON(base, `/agent-def?type=${encodeURIComponent(type)}&cwd=${encodeURIComponent(cwd || '')}`);
export const setAgentModel = async (base, type, cwd, model) =>
  JSON.parse(await post(base, '/agent-def/model', { type, cwd, model }));

// The up-next queue, held by the server so it drains whichever agent you are
// looking at. setQueue replaces the whole list: edit, reorder and remove are
// all just a new list.
// { items, playing, next }: paused is the server's default, so a queue holds
// until play (drain when free) or send next (the head, once)
export const getQueueState = (base, terminal_id) =>
  getJSON(base, `/queue?terminal_id=${encodeURIComponent(terminal_id)}`);
export const playQueue = async (base, terminal_id, playing) =>
  JSON.parse(await post(base, '/queue/play', { terminal_id, playing }));
export const sendQueueNext = async (base, terminal_id) =>
  JSON.parse(await post(base, '/queue/next', { terminal_id }));
export const addToQueue = async (base, terminal_id, text) =>
  JSON.parse(await post(base, '/queue/add', { terminal_id, text })).items || [];
export const setQueue = async (base, terminal_id, items) =>
  JSON.parse(await post(base, '/queue', { terminal_id, items })).items || [];

// Hold-to-talk. The mic is the machine running the agents, not the tablet:
// Expo Go has no speech recognition to call, and the useful mic is the one by
// the Push. stop returns the words rather than sending them -- editing them
// first is the whole point.
export const startRecording = (base) => post(base, '/record/start', {});

export const stopRecording = async (base) =>
  JSON.parse(await post(base, '/record/stop', {})).text || '';

// A pty carries text, so an image cannot be typed into one -- but a path can,
// and the agent reads the file itself. Returns where it landed on the machine
// running the agents.
export const pasteImage = async (base, data) =>
  JSON.parse(await post(base, '/paste', { data })).path;

export const makeWorktree = (base, cwd, branch, name) =>
  post(base, '/worktree', { cwd, branch, ...(name ? { name } : {}) });

// Projects: the repos worth listing, each with its worktrees already attached,
// which is one call rather than one per repo. The server remembers them --
// running an agent somewhere adds it, and only `forgetProject` takes one away.
export const listProjects = async (base) =>
  (await getJSON(base, '/projects')).projects || [];

// Any path inside the repo will do; the server stores the main checkout.
export const addProject = (base, path) => post(base, '/projects', { path });

// Forgets the entry. Nothing on disk is touched, and an agent running there
// puts it straight back.
export const forgetProject = (base, path) => post(base, '/projects/remove', { path });

// Worktrees of one repo. `/projects` carries these already, attached to the
// project they belong to, which is how the rail reads them -- this is the bare
// per-repo form, kept because the route is the older and simpler contract and
// the server still answers it.
export const listWorktrees = async (base, cwd) =>
  (await getJSON(base, `/worktrees${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`))
    .worktrees || [];

export const openWorktree = (base, cwd, path) =>
  post(base, '/worktrees/open', { cwd, path });

// Every local branch of a repo, each saying which worktree already has it out
// -- git will not check one out twice, so `at` is the reason a switch would be
// refused, known before it is offered.
export const listBranches = async (base, cwd) =>
  (await getJSON(base, `/branches${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`))
    .branches || [];

// `path` is the worktree, not the repo. git's own refusals -- a dirty tree, a
// branch out somewhere else -- come back as the message.
export const switchBranch = (base, path, branch) =>
  post(base, '/worktrees/switch', { path, branch });

// force is about uncommitted changes only -- the server refuses either way
// while an agent is living in it, and that refusal is not overridable.
export const removeWorktree = (base, cwd, path, force) =>
  post(base, '/worktrees/remove', { cwd, path, force: !!force });

// What an agent working in `cwd` can reach: its skills and its hooks, each
// tagged with the scope it came from (user / project / local / plugin). Read
// off disk by the server, because the files are on the machine the agents run
// on and this app may be a tablet on the other side of the room.
export const listCatalog = async (base, cwd) =>
  getJSON(base, `/catalog${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`);

// A skill's frontmatter and its instructions. /catalog carries the first two
// fields for every skill; the body is a second call because shipping every
// one of them would make that list many times larger.
// `path` is only read for plugin scope, whose location the server cannot
// derive from a scope and a name -- it checks the path really is inside
// ~/.claude/plugins before it opens it.
export const readSkill = async (base, scope, name, cwd, path) =>
  getJSON(
    base,
    `/skill?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}` +
      (cwd ? `&cwd=${encodeURIComponent(cwd)}` : '') +
      (path ? `&path=${encodeURIComponent(path)}` : '')
  );

// Create or replace. The server derives the path from scope + name -- there is
// no path in this call, which is what makes it safe on a --lan server.
export const saveSkill = (base, fields) => post(base, '/skill', fields);

export const deleteSkill = (base, scope, name, cwd) =>
  post(base, '/skill/delete', { scope, name, cwd });

// Global to a project, or the other way. A plugin's skill is copied instead --
// the server decides that, not this call.
export const moveSkill = (base, fields) => post(base, '/skill/move', fields);

// Open a folder in the editor on the machine the agents run on, which is where
// the files are. The app may be a tablet.
export const openInEditor = (base, path) => post(base, '/open', { path });

// `gi`/`hi` are the hook's address in its settings file, straight off the
// catalog row. Without them this appends a new hook instead.
//
// `type` picks which of the five hook shapes this is, and the server keeps
// only that type's own fields -- anything else sent is dropped rather than
// written, because settings.json decides how the whole tool behaves and is no
// place to forward unknown data into. `name` becomes the entry's
// `statusMessage`, which Claude Code shows while the hook runs; `description`
// is kept beside the app's own files, since the schema has no home for it and
// inventing a key that a stricter future version might reject is not a trade
// worth making for a note.
export const saveHook = (base, fields) => post(base, '/hook', fields);

export const deleteHook = (base, scope, event, gi, hi, cwd) =>
  post(base, '/hook/delete', { scope, event, gi, hi, cwd });

// The rules tab: what an agent working in `cwd` reads before anything else,
// and where midiAI can write more of it. `list` carries every always-on file
// slot for the three scopes (global/project/local) plus every rule file, so
// the tab can offer "create" for a slot that is not on disk yet.
export const listRules = (base, cwd) =>
  getJSON(base, `/rules?cwd=${encodeURIComponent(cwd || '')}`);

// A rule or an always-on file's raw text -- frontmatter and heading included,
// same shape read/write agree on. The route answers {path, text}.
export const readRule = async (base, cwd, path) =>
  (await getJSON(
    base,
    `/rules/read?cwd=${encodeURIComponent(cwd || '')}&path=${encodeURIComponent(path)}`
  )).text || '';

// Create or replace a rule. The server derives the path from scope + title
// for a new one; editing an existing rule keeps its path (`path` in fields).
// Writes return the fresh /rules payload, same shape as listRules.
export const writeRule = async (base, fields) =>
  JSON.parse(await post(base, '/rules/write', fields));

// The whole text of an always-on file (CLAUDE.md, CLAUDE.local.md,
// AGENTS.md, project .claude/CLAUDE.md) -- creating one is just writing to
// a slot that was listed with `exists: false`.
export const writeRuleFile = async (base, cwd, path, text) =>
  JSON.parse(await post(base, '/rules/file', { cwd, path, text }));

// Rules only -- CLAUDE.md/AGENTS.md are never deleted, only emptied via
// writeRuleFile.
export const deleteRule = async (base, cwd, path) =>
  JSON.parse(await post(base, '/rules/delete', { cwd, path }));

// Between the global and project rules directories of the same checkout.
export const moveRule = async (base, cwd, path, to) =>
  JSON.parse(await post(base, '/rules/move', { cwd, path, to }));

// Every file an agent in `cwd` actually loads, in load order -- managed
// policy, user, project ancestors, AGENTS.md when it applies, the rules
// directories -- with warnings for anything over 200 lines, a broken
// @import, or a rule AGENTS.md doubles up.
export const effectiveRules = (base, cwd) =>
  getJSON(base, `/rules/effective?cwd=${encodeURIComponent(cwd || '')}`);

// Hands the same start-loaded files to an isolated model and asks what looks
// like a conflict, a duplicate, something vague, or stale. Can take a
// minute -- there is no polling here, the call just runs long.
export const reviewRules = async (base, cwd) =>
  JSON.parse(await post(base, '/rules/review', { cwd }));

// The folders on the machine running the agents. A picker on the tablet would
// browse the tablet, which is not where the repos are.
export const listDirs = (base, path) =>
  getJSON(base, `/dirs${path ? `?path=${encodeURIComponent(path)}` : ''}`);

// Opens the real Finder chooser on that same machine and resolves once it is
// dismissed -- a long request by design. null means the user cancelled.
// prompt: the line the dialog shows, so it says what the folder is for.
export const chooseDir = async (base, start, prompt) => {
  const q = [start && `start=${encodeURIComponent(start)}`, prompt && `prompt=${encodeURIComponent(prompt)}`]
    .filter(Boolean).join('&');
  return (await getJSON(base, `/choose-dir${q ? `?${q}` : ''}`)).path;
};

// The working tree as a person works it: what is staged, what is not, what
// git would refuse to commit, the stashes, and the commit graph. One call --
// six git invocations that always get read together are one screen's worth of
// state, not six polls.
// The focused agent's whole conversation, from its transcript. The pane only
// ever holds a screenful; `since` is how many turns the app already has.
// sub: a subagent of that agent, read from the log beside its parent's.
export const getHistory = (base, tid, since = 0, sub = '') =>
  getJSON(base, `/history?tid=${encodeURIComponent(tid || '')}&since=${since}`
    + (sub ? `&sub=${encodeURIComponent(sub)}` : ''));

// One line saying what a prompt asked for -- the conversation's pinned header.
export const summarizePrompt = async (base, text) =>
  JSON.parse(await post(base, '/summarize-prompt', { text })).summary;

// Files changed and not committed in a checkout -- the GIT tab's count.
// {count, git}: git is false for a project folder with no repository yet.
export const getDirty = (base, cwd) =>
  getJSON(base, `/work/dirty?cwd=${encodeURIComponent(cwd || '')}`);

// `git init` for a project folder that has none, optionally committing what
// is already there. Only works on a folder the project list holds.
export const initRepo = async (base, cwd, branch, commit) =>
  JSON.parse(await post(base, '/work/init', { cwd, branch, commit }));

export const getWork = (base, cwd) =>
  getJSON(base, `/work?cwd=${encodeURIComponent(cwd || '')}`);

// A patch, as text, for the diff parser the PR review already uses. Either one
// file's changes (staged or not) or a whole commit's -- the same viewer draws
// both, because they are the same question asked of different ranges.
export const getWorkDiff = (base, cwd, { file = '', staged = false, sha = '' } = {}) =>
  getText(
    base,
    `/work/diff?cwd=${encodeURIComponent(cwd || '')}` +
      (sha ? `&sha=${encodeURIComponent(sha)}` : '') +
      (file ? `&file=${encodeURIComponent(file)}` : '') +
      (staged ? '&staged=1' : '')
  );

// Every write is a verb from a table the server owns, never a command line the
// app composes -- paths ride after a `--` and nothing here reaches a shell.
// git's own refusal comes back as the message, which says it better than a
// code of ours would.
// A commit message drafted by the model from the change: the staged diff,
// or every change when nothing is staged (scope says which). Drafted only.
export const draftCommit = async (base, cwd) =>
  JSON.parse(await post(base, '/work/ai-message', { cwd }));

export const doWork = (base, cwd, verb, fields = {}) =>
  post(base, '/work/do', { cwd, verb, ...fields });

// Where a commit came from: its guardrail note (the in-toto statement, or
// null), the agent session behind its `Agent-Session` trailer (enriched from
// the memory DB, with `live_tid` when that agent is still around), and the
// spec its `Spec` trailer names. Any of the three can be null -- most commits
// have none of them -- and the route itself may not exist yet on an older
// server, which callers read as "nothing to show" rather than an error.
export const getCommitMeta = (base, cwd, sha) =>
  getJSON(
    base,
    `/work/commit-meta?cwd=${encodeURIComponent(cwd || '')}&sha=${encodeURIComponent(sha || '')}`
  );

// Spec-first "why": a spec is a file under docs/specs/, written once and
// never overwritten -- a title collision gets `-2`, `-3` instead. terminal_id
// makes it that agent's active spec as the same call; tell also queues the
// agent a nudge to go read it (the existing queue add, worded for this).
export const createSpec = async (base, cwd, { title, why, done, terminal_id, tell } = {}) =>
  JSON.parse(await post(base, '/specs', { cwd, title, why, done, terminal_id, tell }));

export const listSpecs = async (base, cwd) =>
  (await getJSON(base, `/specs?cwd=${encodeURIComponent(cwd || '')}`)).rows || [];

export const readSpec = (base, cwd, path) =>
  getJSON(
    base,
    `/specs/read?cwd=${encodeURIComponent(cwd || '')}&path=${encodeURIComponent(path || '')}`
  );

// Which spec (if any) is active for one agent session -- what the
// prepare-commit-msg hook stamps its next commit with.
export const getActiveSpec = (base, terminal_id) =>
  getJSON(base, `/specs/active?terminal_id=${encodeURIComponent(terminal_id || '')}`);

export const setActiveSpec = async (base, terminal_id, path) =>
  JSON.parse(await post(base, '/specs/active', { terminal_id, path }));

// midiAI's own memory: what an agent decided, tried and learned, mined from
// transcripts and (where imported) claude-mem. `cwd` scopes a call to that
// checkout's project; omitted, it reads every project. GET and JSON, like the
// rest of this file -- the store is read-only from here.
export const searchMemory = (base, q, cwd) =>
  getJSON(
    base,
    `/memory/search?q=${encodeURIComponent(q || '')}` +
      (cwd ? `&cwd=${encodeURIComponent(cwd)}` : '')
  );

export const recentMemory = (base, cwd) =>
  getJSON(base, `/memory/recent${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`);

export const getMemoryEntry = (base, id) =>
  getJSON(base, `/memory/entry?id=${encodeURIComponent(id)}`);

export const memoryStats = (base) => getJSON(base, '/memory/stats');

// Claude access: how agents midiAI starts authenticate, plus the default
// model/effort new ones start with. The API key itself lives in the macOS
// Keychain and is never returned -- `key.hint` is the only trace of it that
// ever rides this wire.
export const getClaudeSettings = (base) => getJSON(base, '/settings/claude');

// Any subset of {mode, model, effort, bedrock, vertex} -- the server merges
// it into what it already has and hands back the same shape getClaudeSettings
// reads, so a patch and a read look identical to the caller.
export const setClaudeSettings = async (base, patch) =>
  JSON.parse(await post(base, '/settings/claude', patch));

// Checked against Anthropic before it is saved to the Keychain. A rejection
// is a 400 with a plain-text reason, which post()'s ask() already folds into
// e.message -- there is nothing special to unwrap here on failure.
export const saveClaudeKey = async (base, key) =>
  JSON.parse(await post(base, '/settings/claude/key', { key }));

export const deleteClaudeKey = async (base) =>
  JSON.parse(await post(base, '/settings/claude/key/delete', {}));

// Opens Terminal.app on the Mac running `claude auth login` -- the same "it
// happens on the Mac" idea as chooseDir's Finder dialog. There is nothing to
// await past {ok:true}; the result turns up in the next getClaudeSettings
// read, same as a login elsewhere shows up in the next health check.
// Tab, into a question: the only way off a multi-select question once its
// boxes are ticked (to the next question, or the review screen)
export const agentNext = (base, terminal_id) =>
  post(base, '/agent/next', { terminal_id });

export const claudeLogin = async (base) =>
  JSON.parse(await post(base, '/settings/claude/login', {}));
