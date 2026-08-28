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
export const promptAgent = (base, text, submit, terminal_id) =>
  post(base, '/prompt', { text, submit, ...(terminal_id ? { terminal_id } : {}) });

export const makeWorktree = (base, cwd, branch) =>
  post(base, '/worktree', { cwd, branch });

// Worktrees. `cwd` says which repo to ask about; the server defaults it to
// whichever session the Push is pointed at when omitted.
export const listWorktrees = async (base, cwd) =>
  (await getJSON(base, `/worktrees${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`))
    .worktrees || [];

export const openWorktree = (base, cwd, path) =>
  post(base, '/worktrees/open', { cwd, path });

// force is about uncommitted changes only -- the server refuses either way
// while an agent is living in it, and that refusal is not overridable.
export const removeWorktree = (base, cwd, path, force) =>
  post(base, '/worktrees/remove', { cwd, path, force: !!force });

// The folders on the machine running the agents. A picker on the tablet would
// browse the tablet, which is not where the repos are.
export const listDirs = (base, path) =>
  getJSON(base, `/dirs${path ? `?path=${encodeURIComponent(path)}` : ''}`);
