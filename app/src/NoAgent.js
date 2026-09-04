import { useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { openWorktree, removeWorktree } from './api';
import PushButton from './PushButton';
import { C, S } from './theme';

// What the pane shows when the worktree you picked has nobody in it.
//
// Tapping an empty worktree in the rail used to start a claude on the spot,
// which is a lot to happen from one tap on a list -- and once an agent was
// closed there was nothing left on screen saying what you were looking at,
// only the same "no agent selected" you get having picked nothing at all.
// This is that difference drawn: the worktree is named, and the three things
// worth doing to an empty one are here rather than behind the ⋮ you just
// closed.
//
// props:
//   pick       { project, path, label, main }  -- the worktree, from Projects
//   base       string
//   onClose    () => void   -- dismiss: stop showing this, pick nothing
//   onChanged  () => void   -- an agent was started, or the worktree removed
export default function NoAgent({ pick, base, onClose, onChanged }) {
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  // Only the dirty-tree refusal earns a second button. The server refuses
  // outright while an agent is living there and will not be forced past that
  // one, by design -- so nothing offers to.
  const [force, setForce] = useState(false);

  const run = (what, fn) => {
    if (busy) return;
    setErr('');
    setForce(false);
    setBusy(what);
    fn()
      .then(() => onChanged && onChanged())
      .catch((e) => {
        const msg = e.message || String(e);
        setErr(msg);
        // post() throws "<status> <body>", and the body is the server's own
        // sentence: "uncommitted changes in <path>: ...". That one is worth
        // offering past, once you have read it.
        if (msg.includes('uncommitted')) setForce(true);
      })
      .finally(() => setBusy(''));
  };

  const remove = (hard) =>
    run('rm', async () => {
      await removeWorktree(base, pick.project, pick.path, hard);
      onClose();
    });

  return (
    <View style={styles.wrap}>
      <View style={styles.card}>
        <Text style={styles.head}>no active agent</Text>
        <Text style={styles.name} numberOfLines={1}>
          {pick.label}
        </Text>
        <Text style={styles.path} numberOfLines={2}>
          {pick.path}
        </Text>

        <View style={styles.keys}>
          <PushButton
            label="＋ new agent"
            colour={C.accentText}
            lit
            disabled={!!busy}
            onPress={() =>
              run('new', () => openWorktree(base, pick.project, pick.path))
            }
            style={styles.key}
          />
          <PushButton
            label="close"
            disabled={!!busy}
            onPress={onClose}
            style={styles.key}
          />
          {/* the main checkout cannot be removed -- the server answers 409
              every time, so there is no button for it here either */}
          {!pick.main && (
            <PushButton
              label="close and delete worktree"
              colour={C.bad}
              disabled={!!busy}
              onPress={() => remove(false)}
              style={styles.key}
            />
          )}
          {force && (
            <PushButton
              label="delete it anyway — there are uncommitted changes"
              colour={C.bad}
              lit
              disabled={!!busy}
              onPress={() => remove(true)}
              style={styles.key}
            />
          )}
        </View>

        {!!busy && <ActivityIndicator size="small" color={C.faint} />}
        {/* the server's own sentence -- "uncommitted changes in <path>: ..."
            says more than a rewording of it would, and it is what the force
            key above is asking you to have read */}
        {!!err && <Text style={styles.err}>{err}</Text>}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: S.pad },
  card: {
    width: 420,
    maxWidth: '100%',
    gap: 6,
    alignItems: 'center',
    padding: S.pad,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    backgroundColor: C.panel,
  },
  head: { color: C.dim, fontSize: 13, letterSpacing: 0.6 },
  name: { color: C.text, fontSize: 18, fontWeight: '600' },
  path: { color: C.faint, fontSize: 11, textAlign: 'center' },
  keys: { alignSelf: 'stretch', gap: 8, marginTop: 10 },
  key: { minHeight: S.hit },
  err: { color: C.bad, fontSize: 12, textAlign: 'center' },
});
