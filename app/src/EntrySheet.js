import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {
  deleteHook,
  deleteSkill,
  moveSkill,
  openInEditor,
  readSkill,
  saveHook,
  saveSkill,
} from './api';
import PushButton from './PushButton';
import { C, S, mono } from './theme';

// The skills and hooks panels' editor. One sheet for both because they are the
// same errand twice: pick a scope, fill in two or three fields, save it, and
// have the panel that opened you read itself again.
//
// It is a modal rather than an inline form for the reason the pad editor is
// one: the panel is 344px of column and a body field wants more than that, and
// a form that pushes the list it belongs to off screen has taken the place of
// the thing you were comparing against.
//
// Skill names follow the server's own rule, checked here so a typo is caught
// before the round trip. Hooks have no name; their event is the closest thing
// and Claude Code decides what those are, so it is a free field with the ones
// that exist offered beside it.
const SKILL_NAME_RE = /^[a-z][a-z0-9-]{0,63}$/;
const SKILL_NAME_HELP =
  'lowercase letters, digits and dashes, starting with a letter';

// Every Claude Code hook event, in the order they happen rather than in
// alphabetical order, which would put PostCompact eleven rows from PreCompact.
// The descriptions are the tool's own -- the names alone are a quiz: Stop and
// StopFailure and SubagentStop are guesses apart, PostToolUse and
// PostToolUseFailure and PostToolBatch are three different moments, and
// PreCompact says nothing about when compaction happens.
//
// Offered, not enforced. This list is a snapshot of a tool that keeps growing
// one, and a field that refuses a name it has not heard of is worse than one
// that lets you type it.
const EVENTS = [
  ['SessionStart', 'a session begins or resumes'],
  ['Setup', 'claude --init-only, or --init / --maintenance in -p mode: one-time preparation in CI or scripts'],
  ['UserPromptSubmit', 'you submit a prompt, before Claude processes it'],
  ['UserPromptExpansion', 'a typed command expands into a prompt, before it reaches Claude — can block the expansion'],
  ['PreToolUse', 'before a tool call executes — can block it'],
  ['PermissionRequest', 'a tool call needs a permission decision'],
  ['PermissionDenied', 'auto mode denied a tool call. hookSpecificOutput.retry: true tells the model it may retry — ignored when the classifier produced no verdict'],
  ['PostToolUse', 'after a tool call succeeds'],
  ['PostToolUseFailure', 'after a tool call fails'],
  ['PostToolBatch', 'after a batch of parallel tool calls resolves, before the next model call'],
  ['Notification', 'Claude Code sends a notification'],
  ['MessageDisplay', 'while assistant message text is displayed'],
  ['SubagentStart', 'a subagent is spawned'],
  ['SubagentStop', 'a subagent finishes'],
  ['TaskCreated', 'a task is being created via TaskCreate'],
  ['TaskCompleted', 'a task is being marked completed'],
  ['Stop', 'Claude finishes responding'],
  ['StopFailure', 'the turn ends on an API error'],
  ['TeammateIdle', 'an agent team teammate is about to go idle'],
  ['InstructionsLoaded', 'a CLAUDE.md or .claude/rules/*.md is loaded — at session start, and lazily during one'],
  ['ConfigChange', 'a configuration file changes during a session'],
  ['CwdChanged', 'the working directory changes, e.g. Claude runs cd — for direnv and the like'],
  ['DirectoryAdded', 'a directory is added mid-session via /add-dir or register_repo_root'],
  ['FileChanged', 'a watched file changes on disk — the matcher says which filenames to watch'],
  ['WorktreeCreate', 'a worktree is being created (--worktree, isolation: worktree, a background session) — replaces default git behaviour'],
  ['WorktreeRemove', 'a worktree is being removed at session exit, when a subagent finishes, or when a background session is deleted'],
  ['PreCompact', 'before context compaction'],
  ['PostCompact', 'after context compaction completes'],
  ['PreModelSwitch', 'before a requested model switch is applied — can block it'],
  ['PostModelSwitch', "after the session's model changes, including changes Claude Code makes itself"],
  ['Elicitation', 'an MCP server requests user input during a tool call'],
  ['ElicitationResult', 'a user answered an MCP elicitation, before the response goes back to the server'],
  ['SessionEnd', 'a session terminates'],
];
// Only two of the four scopes can be written. `plugin` belongs to something
// installed -- editing one in place would be undone by its next update without
// saying so -- and hooks add `local`, which is the same checkout's settings
// kept out of git.
const SKILL_SCOPES = [
  ['project', 'project'],
  ['user', 'global'],
];
const HOOK_SCOPES = [
  ['project', 'project'],
  ['local', 'project · local'],
  ['user', 'global'],
];

// props:
//   kind     'skill' | 'hook'
//   row      the catalog row being edited, or null to create a new one
//   base     string    -- api base url
//   cwd      string    -- which checkout project/local scope means
//   projects array     -- the repos this machine knows about, for "move it to
//                         that one" -- the server will only accept one of these
//   onClose  () => void
//   onSaved  () => void -- the panel should re-read /catalog
export default function EntrySheet({ kind, row, base, cwd, projects = [], onClose, onSaved }) {
  const skill = kind === 'skill';
  const editing = !!row;
  // A plugin's skill is somebody else's file. It opens here to be read, to be
  // opened in an editor, and to be copied somewhere it becomes yours -- never
  // to be saved over, which its next update would undo without saying so.
  const locked = skill && row?.scope === 'plugin';
  const [toCwd, setToCwd] = useState(cwd);

  const [scope, setScope] = useState(row?.scope || 'project');
  const [name, setName] = useState(row?.name || '');
  const [description, setDescription] = useState(row?.description || '');
  const [body, setBody] = useState('');
  const [event, setEvent] = useState(row?.event || 'Stop');
  const [matcher, setMatcher] = useState(row?.matcher || '');
  const [command, setCommand] = useState(row?.command || '');
  const [loading, setLoading] = useState(skill && editing);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [sure, setSure] = useState(false);

  // /catalog carries a skill's name and description but not its instructions:
  // shipping every body in the list would make it many times larger for a
  // field almost nobody is looking at. So the edit is a second call.
  useEffect(() => {
    if (!skill || !editing) return;
    let live = true;
    readSkill(base, row.scope, row.name, cwd, row.path)
      .then((d) => {
        if (!live) return;
        setDescription(d.description || '');
        setBody(d.body || '');
      })
      .catch((e) => live && setErr(e.message || String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [base, cwd, skill, editing, row?.scope, row?.name]);

  const nameOk = !skill || SKILL_NAME_RE.test(name);
  const filled = skill ? !!name && !!body.trim() : !!event.trim() && !!command.trim();
  const canSave = nameOk && filled && !busy && !loading;

  async function save() {
    if (!canSave) return;
    setErr('');
    setBusy(true);
    try {
      if (skill) {
        await saveSkill(base, {
          scope, name, description, body, cwd,
          // only an editor that opened this exact skill may overwrite it --
          // otherwise typing a name someone else used silently replaces it
          replace: editing && row.scope === scope && row.name === name,
        });
      } else {
        await saveHook(base, {
          scope, cwd, event: event.trim(), matcher, command,
          // the row's own address in its settings file; absent, this appends
          ...(editing && row.scope === scope ? { gi: row.gi, hi: row.hi } : {}),
        });
      }
      onSaved();
    } catch (e) {
      setErr(e.message || String(e));
      setBusy(false);
    }
  }

  async function remove() {
    if (busy || !editing) return;
    if (!sure) return setSure(true);   // one tap arms, the next does it
    setErr('');
    setBusy(true);
    try {
      if (skill) await deleteSkill(base, row.scope, row.name, cwd);
      else await deleteHook(base, row.scope, row.event, row.gi, row.hi, cwd);
      onSaved();
    } catch (e) {
      setErr(e.message || String(e));
      setBusy(false);
      setSure(false);
    }
  }

  const scopes = skill ? SKILL_SCOPES : HOOK_SCOPES;
  // changing `where` on something that already exists is a move, and the key
  // says which it is rather than leaving you to find out
  const moving = skill && editing && (locked || scope !== row.scope ||
    (scope === 'project' && toCwd !== cwd));

  async function move() {
    if (busy) return;
    setErr('');
    setBusy(true);
    try {
      await moveSkill(base, {
        name: row.name, scope: row.scope, path: row.path,
        to: scope, cwd, to_cwd: toCwd,
      });
      onSaved();
    } catch (e) {
      setErr(e.message || String(e));
      setBusy(false);
    }
  }

  function open() {
    setErr('');
    openInEditor(base, row.path).catch((e) => setErr(e.message || String(e)));
  }

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>
            {locked
              ? `${row.name} — a plugin's skill`
              : editing
              ? `edit ${skill ? 'skill' : 'hook'}`
              : `new ${skill ? 'skill' : 'hook'}`}
          </Text>
          {locked && (
            <Text style={styles.hint}>
              it belongs to something installed, so it is read-only here — open it, or
              copy it somewhere it is yours
            </Text>
          )}

          <ScrollView
            style={styles.bodyScroll}
            contentContainerStyle={styles.bodyCol}
            keyboardShouldPersistTaps="handled">
            <Text style={styles.label}>
              where{locked ? <Text style={styles.hint}> — copy it to</Text> : null}
            </Text>
            <View style={styles.chips}>
              {scopes.map(([key, word]) => (
                <Text
                  key={key}
                  accessibilityRole="button"
                  accessibilityState={{ selected: scope === key }}
                  onPress={() => setScope(key)}
                  style={[styles.chip, scope === key && styles.chipOn]}>
                  {word}
                </Text>
              ))}
            </View>
            {/* which project, when there is more than one to mean. The server
                takes a directory here and nowhere else, so it takes one off
                this list rather than out of a request. */}
            {skill && scope === 'project' && projects.length > 1 && (
              <View style={styles.chips}>
                {projects.map((p) => (
                  <Text
                    key={p.path}
                    accessibilityRole="button"
                    accessibilityState={{ selected: toCwd === p.path }}
                    onPress={() => setToCwd(p.path)}
                    style={[styles.chip, toCwd === p.path && styles.chipOn]}>
                    {p.name}
                  </Text>
                ))}
              </View>
            )}

            {loading && <ActivityIndicator size="small" color={C.faint} />}

            {skill ? (
              <>
                <Text style={styles.label}>name</Text>
                <TextInput
                  style={styles.input}
                  value={name}
                  onChangeText={setName}
                  editable={!locked}
                  placeholder="my-skill"
                  placeholderTextColor={C.faint}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                {!nameOk && !!name && <Text style={styles.rule}>{SKILL_NAME_HELP}</Text>}

                <Text style={styles.label}>
                  description <Text style={styles.hint}>— what makes Claude reach for it</Text>
                </Text>
                <TextInput
                  style={[styles.input, styles.tall]}
                  value={description}
                  onChangeText={setDescription}
                  editable={!locked}
                  multiline
                  placeholder="Use when the user asks to…"
                  placeholderTextColor={C.faint}
                />

                <Text style={styles.label}>instructions</Text>
                <TextInput
                  style={[styles.input, styles.taller, mono]}
                  value={body}
                  onChangeText={setBody}
                  editable={!locked}
                  multiline
                  // a JS string, not an HTML entity: RN draws the placeholder
                  // as text and &#10; came out as the five characters it is
                  placeholder={'# my-skill\n\nDo the thing, like this.'}
                  placeholderTextColor={C.faint}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </>
            ) : (
              <>
                <Text style={styles.label}>
                  event <Text style={styles.hint}>— type to narrow it</Text>
                </Text>
                <TextInput
                  style={styles.input}
                  value={event}
                  onChangeText={setEvent}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                {/* The list narrows as you type and disappears once what you
                    have typed is exactly one of them -- an autocomplete still
                    offering the answer you have already given is just a list
                    in the way of the next field. */}
                {(() => {
                  const find = event.trim().toLowerCase();
                  const exact = EVENTS.some(([e]) => e.toLowerCase() === find);
                  // name first, and only fall through to the descriptions when
                  // nothing is named that: at 33 events a single letter matches
                  // most of the prose, which is a list rather than a narrowing
                  const named = EVENTS.filter(([e]) => e.toLowerCase().includes(find));
                  const hits = named.length
                    ? named
                    : EVENTS.filter(([, what]) => what.toLowerCase().includes(find));
                  if (exact || !hits.length) return null;
                  return (
                    <ScrollView style={styles.events} keyboardShouldPersistTaps="handled">
                      {hits.map(([e, what]) => (
                        <Pressable
                          key={e}
                          accessibilityRole="button"
                          accessibilityLabel={`${e} — ${what}`}
                          onPress={() => setEvent(e)}
                          style={styles.eventRow}>
                          <Text style={styles.eventName}>{e}</Text>
                          <Text style={styles.eventWhat}>{what}</Text>
                        </Pressable>
                      ))}
                    </ScrollView>
                  );
                })()}

                <Text style={styles.label}>
                  matcher <Text style={styles.hint}>(optional — a tool name, or blank for all)</Text>
                </Text>
                <TextInput
                  style={styles.input}
                  value={matcher}
                  onChangeText={setMatcher}
                  placeholder="Bash"
                  placeholderTextColor={C.faint}
                  autoCapitalize="none"
                  autoCorrect={false}
                  // the matcher belongs to the group, not to the one hook, so
                  // the server refuses to move an existing hook between groups
                  editable={!editing}
                />
                {editing && (
                  <Text style={styles.hint}>
                    the matcher is shared with the other hooks in its group — delete this
                    one and add it back to move it
                  </Text>
                )}

                <Text style={styles.label}>command</Text>
                <TextInput
                  style={[styles.input, styles.tall, mono]}
                  value={command}
                  onChangeText={setCommand}
                  multiline
                  placeholder="afplay /System/Library/Sounds/Glass.aiff"
                  placeholderTextColor={C.faint}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </>
            )}

            {!!err && <Text style={styles.error}>{err}</Text>}
          </ScrollView>

          {/* the editor runs where the agents do, not where you are looking:
              the app may be a tablet and the files are over there */}
          {skill && editing && (
            <PushButton
              label="open the folder in the editor"
              onPress={open}
              disabled={busy}
              style={styles.wide}
            />
          )}

          <View style={styles.row}>
            <PushButton label="cancel" onPress={onClose} disabled={busy} style={styles.btn} />
            {editing && !locked && (
              <PushButton
                label={sure ? 'really delete' : 'delete'}
                colour={C.bad}
                lit={sure}
                onPress={remove}
                disabled={busy}
                style={styles.btn}
              />
            )}
            <PushButton
              label={busy ? '' : moving ? (locked ? 'copy here' : 'move here') : 'save'}
              colour={C.accentText}
              lit
              onPress={moving ? move : save}
              disabled={moving ? busy : !canSave}
              style={styles.btn}>
              {busy && <ActivityIndicator size="small" color={C.text} />}
            </PushButton>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  card: {
    width: 520,
    maxWidth: '92%',
    maxHeight: '88%',
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    padding: S.pad,
    gap: 8,
  },
  title: { color: C.text, fontSize: 16, fontWeight: '600', marginBottom: 4 },
  bodyScroll: { flexGrow: 0, flexShrink: 1 },
  bodyCol: { gap: 8 },
  label: { color: C.dim, fontSize: 12 },
  hint: { color: C.faint, fontSize: 11 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: {
    color: C.faint,
    fontSize: 11,
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 6,
  },
  chipOn: { color: C.text, borderColor: C.accentText },
  events: {
    maxHeight: 240,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    backgroundColor: C.bg,
  },
  eventRow: { paddingHorizontal: 10, paddingVertical: 7, gap: 2 },
  eventName: { color: C.text, fontSize: 13, fontWeight: '600' },
  eventWhat: { color: C.faint, fontSize: 11 },
  input: {
    backgroundColor: C.raised,
    color: C.text,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: S.radius,
    padding: 8,
    fontSize: 13,
    minHeight: S.hit,
  },
  tall: { minHeight: 72, textAlignVertical: 'top' },
  taller: { minHeight: 200, textAlignVertical: 'top' },
  rule: { color: C.warn, fontSize: 11 },
  error: { color: C.bad, fontSize: 12 },
  row: { flexDirection: 'row', gap: 8, marginTop: 8 },
  wide: { minHeight: S.hit, marginTop: 8 },
  btn: { flex: 1, minHeight: S.hit },
});
