import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import PushButton from './PushButton';
import { C, S } from './theme';
import { listWorktrees, openWorktree, removeWorktree } from './api';

// SessionSheet's sibling, not a new idea: same modal shape, same busy-guard,
// same "the server's own sentence is the error message" handling -- just
// listing one repo's worktrees instead of running one agent's lifecycle.
//
// props:
//   visible    bool                 -- modal open/closed
//   base       string               -- api base url
//   cwd        string | undefined   -- which repo to list; server defaults it
//                                       to wherever the Push is pointed when omitted
//   onClose    () => void           -- dismiss
//   onChanged  () => void           -- fired after a successful open or remove;
//                                       caller should refetch whatever it shows
export default function Worktrees({ visible, base, cwd, onClose, onChanged }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  // path of the one row currently offered "remove anyway" -- only the dirty
  // 409 earns this, so any other attempt clears it rather than leaving a
  // stale force-button pointed at whatever failed last.
  const [forceRow, setForceRow] = useState(null);

  // re-fetch every time the sheet opens, same reasoning as SessionSheet's
  // re-seed effect: a stale list from the last repo must not show through.
  useEffect(() => {
    if (!visible) return;
    setErr('');
    setForceRow(null);
    load();
  }, [visible, base, cwd]);

  async function load() {
    setLoading(true);
    setErr('');
    try {
      setRows(await listWorktrees(base, cwd));
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function open(path) {
    if (busy) return;
    setErr('');
    setBusy(true);
    try {
      await openWorktree(base, cwd, path);
      onChanged && onChanged();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doRemove(row, force) {
    if (busy) return;
    setErr('');
    setForceRow(null);
    setBusy(true);
    try {
      await removeWorktree(base, cwd, row.path, force);
      onChanged && onChanged();
      await load(); // the removed (or pruned) row should vanish from this list too
    } catch (e) {
      const msg = e.message || String(e);
      setErr(msg);
      // post() throws "<status> <body>", and the body is one of the server's
      // own sentences -- "uncommitted changes in <path>: ..." or
      // "agent <name> is running in <path>". Only the first is retryable:
      // the server refuses --force outright while an agent lives there, by
      // design, so no force button is ever offered for that one.
      if (msg.includes('uncommitted')) setForceRow(row.path);
    } finally {
      setBusy(false);
    }
  }

  function confirmRemove(row) {
    if (busy) return;
    const label = row.detached ? (row.head || '').slice(0, 7) : row.branch;
    Alert.alert(
      'remove worktree',
      `remove ${label} at ${row.path}?`,
      [
        { text: 'remove', style: 'destructive', onPress: () => doRemove(row, false) },
        { text: 'cancel', style: 'cancel' },
      ],
    );
  }

  const forceRowData = forceRow ? rows.find((r) => r.path === forceRow) : null;

  return (
    <Modal visible={!!visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>worktrees</Text>

          {loading && <ActivityIndicator size="small" color={C.text} style={styles.spinner} />}

          {!loading && (
            <ScrollView style={styles.list} contentContainerStyle={styles.listContent}>
              {rows.length === 0 && <Text style={styles.empty}>no worktrees</Text>}
              {rows.map((row) => (
                <View key={row.path} style={styles.wtRow}>
                  <View style={styles.wtText}>
                    <Text style={styles.branch} numberOfLines={1}>
                      {row.detached ? (row.head || '').slice(0, 7) : row.branch}
                      {row.main && <Text style={styles.mainTag}>  main</Text>}
                      {!row.exists && <Text style={styles.orphanTag}>  directory gone</Text>}
                    </Text>
                    <Text style={styles.path} numberOfLines={1}>{row.path}</Text>
                  </View>
                  <View style={styles.wtActions}>
                    {row.exists && (
                      <PushButton
                        label="open"
                        onPress={() => open(row.path)}
                        disabled={busy}
                        style={styles.smallBtn}
                      />
                    )}
                    {/* the server refuses the main checkout with a 409 every
                        time -- a button that always fails has no reason to
                        be here */}
                    {!row.main && (
                      <PushButton
                        label="remove"
                        colour={C.bad}
                        onPress={() => confirmRemove(row)}
                        disabled={busy}
                        style={styles.smallBtn}
                      />
                    )}
                  </View>
                </View>
              ))}
            </ScrollView>
          )}

          {!!err && <Text style={styles.error}>{err}</Text>}
          {!!forceRowData && (
            <PushButton
              label="remove anyway"
              colour={C.bad}
              onPress={() => doRemove(forceRowData, true)}
              disabled={busy}
              style={styles.btn}
            />
          )}

          <View style={styles.footer}>
            <PushButton label="close" onPress={onClose} disabled={busy} style={styles.btn}>
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
    width: 420,
    maxWidth: '90%',
    maxHeight: '80%',
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    padding: S.pad,
    gap: 8,
  },
  title: { color: C.text, fontSize: 16, fontWeight: '600', marginBottom: 4 },
  spinner: { marginVertical: 16 },
  list: { maxHeight: 320 },
  listContent: { gap: 6 },
  empty: { color: C.faint, fontSize: 12, paddingVertical: 8 },
  wtRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: C.line,
  },
  wtText: { flex: 1, gap: 2 },
  branch: { color: C.text, fontSize: 14, fontWeight: '600' },
  mainTag: { color: C.dim, fontSize: 12, fontWeight: '400' },
  orphanTag: { color: C.warn, fontSize: 12, fontWeight: '400' },
  path: { color: C.faint, fontSize: 11 },
  wtActions: { flexDirection: 'row', gap: 6 },
  smallBtn: { minWidth: 64, minHeight: S.hit, paddingHorizontal: 8 },
  error: { color: C.bad, fontSize: 12 },
  footer: { flexDirection: 'row', gap: 8, marginTop: 8 },
  btn: { flex: 1, minHeight: S.hit },
});
