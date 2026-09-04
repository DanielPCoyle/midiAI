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
import { deleteHook, deleteSkill, readSkill, saveHook, saveSkill } from './api';
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

// Claude Code's own event names. Offered, not enforced: the list grows with
// the tool and a field that refuses a new one is worse than a field that lets
// you type it.
const EVENTS = [
  'PreToolUse',
  'PostToolUse',
  'UserPromptSubmit',
  'Notification',
  'Stop',
  'SubagentStop',
  'SessionStart',
  'SessionEnd',
  'PreCompact',
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
//   onClose  () => void
//   onSaved  () => void -- the panel should re-read /catalog
export default function EntrySheet({ kind, row, base, cwd, onClose, onSaved }) {
  const skill = kind === 'skill';
  const editing = !!row;

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
    readSkill(base, row.scope, row.name, cwd)
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

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>
            {editing ? `edit ${skill ? 'skill' : 'hook'}` : `new ${skill ? 'skill' : 'hook'}`}
          </Text>

          <ScrollView
            style={styles.bodyScroll}
            contentContainerStyle={styles.bodyCol}
            keyboardShouldPersistTaps="handled">
            <Text style={styles.label}>where</Text>
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

            {loading && <ActivityIndicator size="small" color={C.faint} />}

            {skill ? (
              <>
                <Text style={styles.label}>name</Text>
                <TextInput
                  style={styles.input}
                  value={name}
                  onChangeText={setName}
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
                  multiline
                  placeholder="Use when the user asks to…"
                  placeholderTextColor={C.faint}
                />

                <Text style={styles.label}>instructions</Text>
                <TextInput
                  style={[styles.input, styles.taller, mono]}
                  value={body}
                  onChangeText={setBody}
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
                <Text style={styles.label}>event</Text>
                <TextInput
                  style={styles.input}
                  value={event}
                  onChangeText={setEvent}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <View style={styles.chips}>
                  {EVENTS.map((e) => (
                    <Text
                      key={e}
                      accessibilityRole="button"
                      onPress={() => setEvent(e)}
                      style={[styles.chip, event === e && styles.chipOn]}>
                      {e}
                    </Text>
                  ))}
                </View>

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

          <View style={styles.row}>
            <PushButton label="cancel" onPress={onClose} disabled={busy} style={styles.btn} />
            {editing && (
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
              label={busy ? '' : 'save'}
              colour={C.accentText}
              lit
              onPress={save}
              disabled={!canSave}
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
  btn: { flex: 1, minHeight: S.hit },
});
