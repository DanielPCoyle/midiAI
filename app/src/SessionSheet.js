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
import PushButton from './PushButton';
import { C, S } from './theme';
import { createAgent, renameAgent, closeAgent, makeWorktree, listDirs } from './api';

// The same rule mapui.py enforces server-side -- checked here too so a typo
// is caught before the round trip rather than after a 400 comes back.
const NAME_RE = /^[a-z][a-z0-9_-]{0,31}$/;
const NAME_HELP = 'lowercase letters, digits, _ or - only, must start with a letter, 32 chars max';

// Folders come from the server, not the tablet: a file picker here would
// browse the iPad, and the repos are on the machine running the agents.
//
// The path field and the list are one control, not two. Typing jumps, tapping
// walks, and either way the field IS the answer -- there is no separate
// "use this folder", because the folder you are looking at is the one you get.
function FolderPick({ base, value, onChange }) {
  const [listing, setListing] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const load = async (path) => {
    setLoading(true);
    setErr('');
    try {
      setListing(await listDirs(base, path));
    } catch (e) {
      setListing(null);
      setErr(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(value || undefined);
    // only on open: after that, navigation drives it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const go = (path) => {
    onChange(path);
    load(path);
  };

  // a repo is nearly always what you mean, so they sort first
  const entries = [...(listing?.entries || [])].sort(
    (a, b) => (b.git ? 1 : 0) - (a.git ? 1 : 0) || a.name.localeCompare(b.name)
  );

  return (
    <View style={styles.pick}>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChange}
        onSubmitEditing={() => load(value)}
        placeholder="/path/to/project"
        placeholderTextColor={C.faint}
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="go"
      />
      <View style={styles.pickHead}>
        <Text style={styles.hint} numberOfLines={1}>
          {listing?.git ? 'a repo · ' : ''}
          {entries.length} folder{entries.length === 1 ? '' : 's'}
        </Text>
        {loading && <ActivityIndicator size="small" color={C.faint} />}
      </View>
      {!!err && <Text style={styles.error}>{err}</Text>}
      <ScrollView style={styles.pickList} keyboardShouldPersistTaps="handled">
        {!!listing?.parent && (
          <Pressable style={styles.pickRow} onPress={() => go(listing.parent)}>
            <Text style={styles.pickUp}>..</Text>
          </Pressable>
        )}
        {entries.map((e) => (
          <Pressable key={e.path} style={styles.pickRow} onPress={() => go(e.path)}>
            <Text style={[styles.pickName, e.git && styles.pickRepo]} numberOfLines={1}>
              {e.name}
            </Text>
            {/* an agent already lives here; starting a second is allowed but
                worth knowing before you do it */}
            {e.busy && <Text style={styles.pickTag}>in use</Text>}
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
}

const TITLE = {
  new: 'new session',
  rename: 'rename',
  close: 'close session',
  worktree: 'new worktree',
};

function nameValid(mode, value) {
  if (mode === 'new') return value === '' || NAME_RE.test(value); // blank is fine, server names it
  if (mode === 'rename') return NAME_RE.test(value);
  return true; // close and worktree don't gate on this field
}

// One modal, four errands, picked by `mode` -- keeping the name rule, the
// error readout, and the in-flight guard written once rather than four times.
//
// props:
//   visible  bool                              -- modal open/closed
//   mode     'new' | 'rename' | 'close' | 'worktree'
//   base     string                            -- api base url, passed straight to api.js
//   tid      string | null                     -- terminal id; required for rename and close
//   name     string                            -- current agent name; prefills rename, labels close
//   cwd      string | undefined                -- default cwd for new/worktree, blank if none passed in
//   onClose  () => void                        -- dismiss without doing anything
//   onDone   () => void                        -- fired after the api call succeeds; caller should
//                                                  dismiss (visible=false) and refetch /agents
export default function SessionSheet({ visible, mode, base, tid, name, cwd, onClose, onDone }) {
  const [cwdField, setCwdField] = useState('');
  const [nameField, setNameField] = useState('');
  const [branchField, setBranchField] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  // re-seed the form every time the sheet opens, or a rename typed for one
  // seat would still be sitting in the field when it opens again for another.
  useEffect(() => {
    if (!visible) return;
    setCwdField(cwd || '');
    setNameField(mode === 'rename' ? name || '' : '');
    setBranchField('');
    setBusy(false);
    setErr('');
  }, [visible, mode, cwd, name]);

  const cwdOk = mode !== 'new' && mode !== 'worktree' ? true : cwdField.trim().length > 0;
  const nameOk = nameValid(mode, nameField);
  const branchOk = mode !== 'worktree' ? true : NAME_RE.test(branchField);
  const canConfirm = cwdOk && nameOk && branchOk && !busy;

  async function confirm() {
    if (!canConfirm) return;
    setErr('');
    setBusy(true);
    try {
      if (mode === 'new') await createAgent(base, cwdField.trim(), nameField.trim() || undefined);
      else if (mode === 'rename') await renameAgent(base, tid, nameField.trim());
      else if (mode === 'close') await closeAgent(base, tid);
      else if (mode === 'worktree') await makeWorktree(base, cwdField.trim(), branchField.trim());
      onDone();
    } catch (e) {
      setErr(e.message || String(e)); // api.js's post() already reads "<status> <body>" off the server
      setBusy(false);
    }
  }

  return (
    <Modal visible={!!visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>{TITLE[mode] || mode}</Text>

          {mode === 'new' && (
            <>
              <Text style={styles.label}>folder</Text>
              <FolderPick base={base} value={cwdField} onChange={setCwdField} />
              <Text style={styles.label}>name <Text style={styles.hint}>(optional)</Text></Text>
              <TextInput
                style={styles.input}
                value={nameField}
                onChangeText={setNameField}
                placeholder="auto"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {!nameOk && <Text style={styles.rule}>{NAME_HELP}</Text>}
            </>
          )}

          {mode === 'rename' && (
            <>
              <Text style={styles.label}>name</Text>
              <TextInput
                style={styles.input}
                value={nameField}
                onChangeText={setNameField}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {!nameOk && <Text style={styles.rule}>{NAME_HELP}</Text>}
            </>
          )}

          {mode === 'close' && (
            <Text style={styles.confirmText}>
              close <Text style={styles.confirmName}>{name || tid}</Text>? this ends the running agent.
            </Text>
          )}

          {mode === 'worktree' && (
            <>
              <Text style={styles.label}>branches from</Text>
              <TextInput
                style={styles.input}
                value={cwdField}
                onChangeText={setCwdField}
                placeholder="/path/to/project"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <Text style={styles.label}>new branch name</Text>
              <TextInput
                style={styles.input}
                value={branchField}
                onChangeText={setBranchField}
                placeholder="feature-name"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {!branchOk && branchField.length > 0 && <Text style={styles.rule}>{NAME_HELP}</Text>}
            </>
          )}

          {!!err && <Text style={styles.error}>{err}</Text>}

          <View style={styles.row}>
            <PushButton label="cancel" onPress={onClose} disabled={busy} style={styles.btn} />
            <PushButton
              label={busy ? '' : mode === 'close' ? 'close' : 'confirm'}
              colour={mode === 'close' ? C.bad : C.accentText}
              lit
              onPress={confirm}
              disabled={!canConfirm}
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
    width: 340,
    maxWidth: '90%',
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    padding: S.pad,
    gap: 8,
  },
  title: { color: C.text, fontSize: 16, fontWeight: '600', marginBottom: 4 },
  label: { color: C.dim, fontSize: 12 },
  hint: { color: C.faint, fontSize: 11 },
  input: {
    backgroundColor: C.raised,
    color: C.text,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: S.radius,
    padding: 8,
    fontSize: 14,
    minHeight: S.hit,
  },
  pick: { gap: 6 },
  pickHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  pickList: {
    maxHeight: 190,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    backgroundColor: C.bg,
  },
  pickRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 10,
    minHeight: 34,
  },
  pickName: { color: C.dim, fontSize: 13, flexShrink: 1 },
  pickRepo: { color: C.text, fontWeight: '600' },
  pickUp: { color: C.faint, fontSize: 13 },
  pickTag: { color: C.faint, fontSize: 10 },
  rule: { color: C.warn, fontSize: 11 },
  error: { color: C.bad, fontSize: 12 },
  confirmText: { color: C.text, fontSize: 14, lineHeight: 20 },
  confirmName: { fontWeight: '600' },
  row: { flexDirection: 'row', gap: 8, marginTop: 8 },
  btn: { flex: 1, minHeight: S.hit },
});
