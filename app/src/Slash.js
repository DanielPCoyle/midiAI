import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { C, S, mono } from './theme';

// Claude Code's own commands. Hand-kept: nothing the server can read lists
// them, and a stale line here is harmless -- Claude Code answers an unknown
// command itself.
const BUILTIN = [
  ['clear', 'start a fresh conversation'],
  ['compact', 'summarise the conversation to free context'],
  ['context', 'what is using the context window'],
  ['model', 'switch model'],
  ['effort', 'set reasoning effort'],
  ['cost', 'tokens and cost this session'],
  ['usage', 'plan usage limits'],
  ['resume', 'resume an earlier conversation'],
  ['rewind', 'go back to an earlier point'],
  ['review', 'review a pull request'],
  ['security-review', 'security review of pending changes'],
  ['init', 'write a CLAUDE.md for this repo'],
  ['memory', 'edit memory files'],
  ['agents', 'manage subagents'],
  ['mcp', 'manage MCP servers'],
  ['hooks', 'manage hooks'],
  ['permissions', 'allow and deny rules'],
  ['config', 'settings'],
  ['status', 'version, model, account'],
  ['doctor', 'check the installation'],
  ['export', 'export the conversation'],
  ['add-dir', 'add a working directory'],
  ['todos', 'the current todo list'],
  ['help', 'all commands'],
];

// Every slash name an agent here can take: the built-ins, then skills.
// A plugin's skill is typed as plugin:skill, and the plugin is the folder
// after cache/<owner>/ in its path. Keyed by name, because a plugin cached
// from two marketplaces lists each skill twice.
export function slashCommands(skills) {
  const out = new Map(BUILTIN.map(([name, description]) => [name, { name, description, scope: 'built-in' }]));
  for (const s of skills || []) {
    const plugin = s.scope === 'plugin' && (s.path || '').match(/\/plugins\/cache\/[^/]+\/([^/]+)\//);
    const name = plugin ? `${plugin[1]}:${s.name}` : s.name;
    if (!out.has(name)) out.set(name, { name, description: s.description || '', scope: s.scope });
  }
  return [...out.values()];
}

// What the box is asking for: the word after a leading slash, while no
// space has been typed yet. Anything else is not a command being chosen.
export function slashQuery(text) {
  const m = /^\/([^\s]*)$/.exec(text || '');
  return m ? m[1].toLowerCase() : null;
}

// Matches for a query: names starting with it first, then names or
// descriptions containing it. A plugin skill also matches on its bare name,
// since that is what anyone types.
export function slashMatches(all, q, limit = 8) {
  const bare = (n) => n.split(':').pop();
  const starts = all.filter((c) => c.name.startsWith(q) || bare(c.name).startsWith(q));
  const rest = all.filter((c) => !starts.includes(c)
    && (c.name.includes(q) || (c.description || '').toLowerCase().includes(q)));
  return [...starts, ...rest].slice(0, limit);
}

export function SlashMenu({ items, sel, onPick }) {
  if (!items.length) return null;
  return (
    <View style={styles.menu} accessibilityRole="menu">
      {items.map((c, i) => (
        <Pressable
          key={c.name}
          accessibilityRole="menuitem"
          accessibilityLabel={`/${c.name}`}
          onPress={() => onPick(c)}
          style={[styles.row, i === sel && styles.rowOn]}>
          <Text numberOfLines={1} style={styles.name}>/{c.name}</Text>
          <Text numberOfLines={1} style={styles.desc}>{c.description}</Text>
          <Text style={styles.scope}>{c.scope}</Text>
        </Pressable>
      ))}
      <Text style={styles.hint}>↑↓ choose · enter runs · tab fills in to add arguments · esc closes</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  menu: { borderWidth: 1, borderColor: C.edge, borderRadius: S.radius, backgroundColor: C.panel, paddingVertical: 4 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 10, paddingVertical: 6 },
  rowOn: { backgroundColor: C.line },
  name: { color: C.accentText, fontSize: 13, maxWidth: '45%', ...mono },
  desc: { flex: 1, color: C.dim, fontSize: 12 },
  scope: { color: C.faint, fontSize: 10 },
  hint: { color: C.faint, fontSize: 10, paddingHorizontal: 10, paddingTop: 4 },
});
