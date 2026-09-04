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
export const saveHook = (base, fields) => post(base, '/hook', fields);

export const deleteHook = (base, scope, event, gi, hi, cwd) =>
  post(base, '/hook/delete', { scope, event, gi, hi, cwd });

// The folders on the machine running the agents. A picker on the tablet would
// browse the tablet, which is not where the repos are.
export const listDirs = (base, path) =>
  getJSON(base, `/dirs${path ? `?path=${encodeURIComponent(path)}` : ''}`);

// Opens the real Finder chooser on that same machine and resolves once it is
// dismissed -- a long request by design. null means the user cancelled.
export const chooseDir = async (base, start) =>
  (await getJSON(base, `/choose-dir${start ? `?start=${encodeURIComponent(start)}` : ''}`)).path;
