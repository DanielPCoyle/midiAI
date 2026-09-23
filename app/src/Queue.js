import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { MaterialIcons } from '@expo/vector-icons';
import { getQueue, setQueue } from './api';
import { C, S } from './theme';

// An agent's up-next queue, as the server holds it. Polled, because the
// server drains it on its own. Changes are optimistic: the list moves when
// you move it, and the server's answer replaces it.
// ponytail: each caller polls on its own (the composer and the right-hand
// panel) -- a shared store if the extra request every 2s ever matters.
export function useQueue(base, tid) {
  const [queue, setQ] = useState([]);
  const [err, setErr] = useState('');
  useEffect(() => {
    setQ([]);
    if (!base || !tid) return undefined;   // a subagent has no pane to queue into
    let live = true;
    const pull = () => getQueue(base, tid).then((q) => live && setQ(q)).catch(() => {});
    pull();
    const timer = setInterval(pull, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [base, tid]);
  const change = (next) => {
    setQ(next);
    setErr('');
    setQueue(base, tid, next).then(setQ).catch((e) => setErr(String((e && e.message) || e)));
  };
  return [queue, change, setQ, err];
}

// The queue, Spotify-style: what goes next is on top. Tap a message to edit
// it in place (saved when you leave the box), arrows to move it, the top
// arrow to play it next, x to drop it.
// ponytail: arrows, not drag -- a drag handle needs a gesture library or a
// PanResponder; add one if reordering long queues gets tedious.
export function QueuePanel({ queue, onChange, err }) {
  const [editing, setEditing] = useState({});   // id -> text being typed
  const move = (i, to) => {
    const next = [...queue];
    const [item] = next.splice(i, 1);
    next.splice(Math.max(0, Math.min(to, next.length)), 0, item);
    onChange(next);
  };
  const commit = (id) => {
    const text = editing[id];
    setEditing(({ [id]: _, ...rest }) => rest);
    if (text === undefined) return;
    onChange(text.trim()
      ? queue.map((q) => (q.id === id ? { ...q, text } : q))
      : queue.filter((q) => q.id !== id));   // emptied is removed
  };
  const key = (label, icon, onPress, off) => (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={off}
      onPress={onPress}
      style={[styles.key, off && styles.keyOff]}>
      <MaterialIcons name={icon} size={16} color={C.dim} />
    </Pressable>
  );
  return (
    <ScrollView contentContainerStyle={styles.list} keyboardShouldPersistTaps="handled">
      {queue.length === 0 ? (
        <Text style={styles.none}>
          nothing queued — send to a busy agent and it waits here, editable, until
          the agent is free
        </Text>
      ) : (
        <View style={styles.headRow}>
          <Text style={styles.note}>top goes first, when the agent is idle</Text>
          <Text accessibilityRole="button" onPress={() => onChange([])} style={styles.clear}>
            clear
          </Text>
        </View>
      )}
      {!!err && <Text style={styles.err}>{err}</Text>}
      {queue.map((q, i) => (
        <View key={q.id} style={styles.row}>
          <View style={styles.rowHead}>
            <Text style={styles.num}>{i + 1}</Text>
            <View style={styles.spacer} />
            {key('play next', 'vertical-align-top', () => move(i, 0), i === 0)}
            {key('move up', 'arrow-upward', () => move(i, i - 1), i === 0)}
            {key('move down', 'arrow-downward', () => move(i, i + 1), i === queue.length - 1)}
            {key('remove', 'close', () => onChange(queue.filter((x) => x.id !== q.id)))}
          </View>
          <TextInput
            style={styles.text}
            value={editing[q.id] ?? q.text}
            onChangeText={(t) => setEditing((e) => ({ ...e, [q.id]: t }))}
            onBlur={() => commit(q.id)}
            multiline
            accessibilityLabel={`queued message ${i + 1}`}
          />
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  list: { padding: 12, gap: 8 },
  none: { color: C.faint, fontSize: 12, lineHeight: 18 },
  headRow: { flexDirection: 'row', alignItems: 'center' },
  note: { flex: 1, color: '#e0a03c', fontSize: 11 },
  clear: { color: C.faint, fontSize: 11, padding: 4 },
  err: { color: C.bad, fontSize: 12 },
  row: { gap: 4, borderWidth: 1, borderColor: C.line, borderRadius: S.radius, padding: 6 },
  rowHead: { flexDirection: 'row', alignItems: 'center' },
  num: { color: C.faint, fontSize: 11 },
  spacer: { flex: 1 },
  text: { color: C.text, fontSize: 13, lineHeight: 19, padding: 6, borderRadius: S.radius, backgroundColor: C.bg },
  key: { padding: 4 },
  keyOff: { opacity: 0.3 },
});
