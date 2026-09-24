import Feather from '@expo/vector-icons/Feather';
import { C } from './theme';

// The one place a meaning becomes a glyph. Call sites say what they mean
// ("agent", "guardrails"), never which Feather name draws it -- so changing
// a glyph is one line here rather than a grep across the app.
const GLYPH = {
  focus: 'terminal',
  guardrails: 'shield',
  ci: 'git-branch',
  usage: 'activity',
  agent: 'cpu',
  project: 'folder',
  worktree: 'corner-down-right',
  mcp: 'zap',
  settings: 'settings',
  prompts: 'grid',
  skills: 'star',
  hooks: 'link',
  rules: 'book-open',
  queue: 'list',
  memory: 'database',
  shell: 'terminal',
  prompt: 'command',
  send: 'send',
  search: 'search',
  scope: 'crosshair',
  new: 'plus',
  rename: 'edit-2',
  compact: 'minimize-2',
  clear: 'slash',
  close: 'x',
  reconnect: 'refresh-cw',
  push: 'sliders',
  more: 'more-vertical',
  trouble: 'alert-triangle',
  split: 'columns',
  down: 'chevron-down',
  right: 'chevron-right',
  up: 'chevron-up',
  ticked: 'check-square',
  unticked: 'square',
};

// Decoration next to a label, not a control of its own -- the Pressable
// around it already carries the accessible name, so this stays unlabelled
// rather than doubling it.
export default function Icon({ name, size = 18, color = C.dim, style }) {
  return <Feather name={GLYPH[name] || name} size={size} color={color} style={style} />;
}

export { GLYPH };
