import React, { useEffect, useRef, useState } from 'react';
import { PanResponder, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { MaterialIcons } from '@expo/vector-icons';
import { getQueueState, playQueue, sendQueueNext, setQueue } from './api';
import { C, S } from './theme';

// An agent's up-next queue, as the server holds it. Polled, because the
// server drains it on its own. Changes are optimistic: the list moves when
// you move it, and the server's answer replaces it.
// ponytail: each caller polls on its own (the composer and the right-hand
// panel) -- a shared store if the extra request every 2s ever matters.
export function useQueue(base, tid) {
  const [queue, setQ] = useState([]);
  const [err, setErr] = useState('');
  const [flow, setFlow] = useState({ playing: false, next: false, waiting: '' });
  const take = (d) => {
    setQ(d.items || []);
    setFlow({ playing: !!d.playing, next: !!d.next, waiting: d.waiting || '' });
  };
  useEffect(() => {
    setQ([]);
    setFlow({ playing: false, next: false, waiting: '' });
    if (!base || !tid) return undefined;   // a subagent has no pane to queue into
    let live = true;
    const pull = () => getQueueState(base, tid).then((d) => live && take(d)).catch(() => {});
    pull();
    const timer = setInterval(pull, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [base, tid]);
  const change = (next) => {
    setQ(next);
    setErr('');
    setQueue(base, tid, next).then(setQ).catch((e) => setErr(String((e && e.message) || e)));
  };
  // paused by default: nothing leaves until play, or send next for one
  const ctl = {
    ...flow,
    setPlaying: (on) => {
      setFlow((f) => ({ ...f, playing: on }));
      playQueue(base, tid, on).then(take).catch((e) => setErr(String((e && e.message) || e)));
    },
    sendNext: () => {
      setFlow((f) => ({ ...f, next: true }));
      sendQueueNext(base, tid).then(take).catch((e) => setErr(String((e && e.message) || e)));
    },
  };
  return [queue, change, setQ, err, ctl];
}

// A grip that reports a vertical drag. PanResponder, so no gesture library;
// it refuses to hand the touch back mid-drag, or the list would scroll away
// with the row you are holding.
// Exported: the guardrail checklist reorders with the same grip.
export function DragHandle({ onStart, onMove, onEnd, label = 'drag to reorder' }) {
  const cb = useRef();
  cb.current = { onStart, onMove, onEnd };
  const pan = useRef(PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onMoveShouldSetPanResponder: () => true,
    onPanResponderTerminationRequest: () => false,
    onPanResponderGrant: () => cb.current.onStart(),
    onPanResponderMove: (_, g) => cb.current.onMove(g.dy),
    onPanResponderRelease: (_, g) => cb.current.onEnd(g.dy),
    onPanResponderTerminate: () => cb.current.onEnd(null),
  })).current;
  return (
    <View {...pan.panHandlers} accessibilityLabel={label} style={styles.handle}>
      <MaterialIcons name="drag-indicator" size={18} color={C.dim} />
    </View>
  );
}

// The queue, Spotify-style: what goes next is on top. Drag a row by its grip
// to move it, or use the arrows (the top one plays it next); tap a message
// to edit it in place (saved when you leave the box); x drops it.
export function QueuePanel({ queue, onChange, err, ctl }) {
  const [editing, setEditing] = useState({});   // id -> text being typed
  const [draft, setDraft] = useState('');
  // ponytail: the id is made here and the whole list is sent, like every other
  // edit -- the server's clean_queue keeps any id it is given.
  const add = () => {
    if (!draft.trim()) return;
    onChange([...queue, { id: `own-${Date.now().toString(36)}`, text: draft }]);
    setDraft('');
  };
  // Rows are as tall as their text, so where a drag lands is worked out from
  // each row's measured box, not from a fixed row height.
  const boxes = useRef({});                      // id -> { y, h }
  const [drag, setDrag] = useState(null);        // { id, dy }
  const others = (id) => queue.filter((q) => q.id !== id && boxes.current[q.id]);
  // the index the dragged row would take: how many other rows' middles sit
  // above its own middle
  const landing = (id, dy) => {
    const me = boxes.current[id];
    if (!me) return 0;
    const mid = me.y + me.h / 2 + dy;
    return others(id).filter((q) => boxes.current[q.id].y + boxes.current[q.id].h / 2 < mid).length;
  };
  const dropLine = () => {
    if (!drag) return null;
    const rest = others(drag.id);
    const to = landing(drag.id, drag.dy);
    if (!rest.length) return null;
    const b = boxes.current[(rest[to] || rest[rest.length - 1]).id];
    return to < rest.length ? b.y - 5 : b.y + b.h + 3;
  };
  const line = dropLine();
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
    <ScrollView
      contentContainerStyle={styles.list}
      keyboardShouldPersistTaps="handled"
      scrollEnabled={!drag}>
      {queue.length === 0 ? (
        <Text style={styles.none}>
          nothing queued — send to a busy agent, or add one below, and it waits
          here, editable, until the agent is free
        </Text>
      ) : (
        <View style={styles.headRow}>
          <Text style={styles.note}>
            {!ctl ? 'top goes first, when the agent is idle'
              : ctl.playing ? 'playing — top goes when the agent is free'
              : ctl.next ? 'sending the top one when the agent is free'
              : 'paused — nothing sends until you play or send next'}
            {!!ctl?.waiting && (ctl.playing || ctl.next) && (
              <Text style={styles.why}>{`\nwaiting: ${ctl.waiting}`}</Text>
            )}
          </Text>
          <Text accessibilityRole="button" onPress={() => onChange([])} style={styles.clear}>
            clear
          </Text>
        </View>
      )}
      {!!ctl && queue.length > 0 && (
        <View style={styles.flowRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={ctl.playing ? 'pause the queue' : 'play the queue'}
            onPress={() => ctl.setPlaying(!ctl.playing)}
            style={[styles.flowBtn, ctl.playing && styles.flowBtnOn]}>
            <MaterialIcons name={ctl.playing ? 'pause' : 'play-arrow'} size={16} color={C.accentText} />
            <Text style={styles.flowText}>{ctl.playing ? 'pause' : 'play'}</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="send the next queued message"
            disabled={ctl.playing || ctl.next}
            onPress={ctl.sendNext}
            style={[styles.flowBtn, (ctl.playing || ctl.next) && styles.keyOff]}>
            <MaterialIcons name="skip-next" size={16} color={C.accentText} />
            <Text style={styles.flowText}>send next</Text>
          </Pressable>
        </View>
      )}
      {!!err && <Text style={styles.err}>{err}</Text>}
      {line !== null && <View pointerEvents="none" style={[styles.dropLine, { top: line }]} />}
      {queue.map((q, i) => (
        <View
          key={q.id}
          onLayout={(e) => {
            const { y, height } = e.nativeEvent.layout;
            boxes.current[q.id] = { y, h: height };
          }}
          style={[
            styles.row,
            drag?.id === q.id && [styles.rowLifted, { transform: [{ translateY: drag.dy }] }],
          ]}>
          <View style={styles.rowHead}>
            <DragHandle
              onStart={() => setDrag({ id: q.id, dy: 0 })}
              onMove={(dy) => setDrag({ id: q.id, dy })}
              onEnd={(dy) => {
                setDrag(null);
                const from = queue.findIndex((x) => x.id === q.id);
                if (dy === null || from < 0) return;   // cancelled, or it already went
                const to = landing(q.id, dy);
                if (to !== from) move(from, to);
              }}
            />
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
      <View style={styles.addRow}>
        <TextInput
          style={[styles.text, styles.addInput]}
          value={draft}
          onChangeText={setDraft}
          placeholder="a message for later"
          placeholderTextColor={C.faint}
          multiline
          accessibilityLabel="message to add to the queue"
          // web: Enter adds, Shift+Enter is a newline -- same as the composer
          onKeyPress={Platform.OS === 'web' ? (e) => {
            const ev = e.nativeEvent;
            if (ev.key !== 'Enter' || ev.shiftKey || ev.isComposing) return;
            e.preventDefault();
            add();
          } : undefined}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Add to queue"
          disabled={!draft.trim()}
          onPress={add}
          style={[styles.addBtn, !draft.trim() && styles.keyOff]}>
          <MaterialIcons name="playlist-add" size={16} color={C.accentText} />
          <Text style={styles.addLabel}>Add to queue</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  list: { padding: 12, gap: 8 },
  none: { color: C.faint, fontSize: 12, lineHeight: 18 },
  headRow: { flexDirection: 'row', alignItems: 'center' },
  note: { flex: 1, color: '#e0a03c', fontSize: 11 },
  why: { color: C.dim },
  clear: { color: C.faint, fontSize: 11, padding: 4 },
  err: { color: C.bad, fontSize: 12 },
  row: { gap: 4, borderWidth: 1, borderColor: C.line, borderRadius: S.radius, padding: 6, backgroundColor: C.panel },
  rowLifted: { zIndex: 10, borderColor: C.accentText, opacity: 0.92 },
  handle: { paddingRight: 4, cursor: 'grab', userSelect: 'none', touchAction: 'none' },
  dropLine: { position: 'absolute', left: 12, right: 12, height: 2, borderRadius: 1, backgroundColor: C.accentText, zIndex: 20 },
  rowHead: { flexDirection: 'row', alignItems: 'center' },
  num: { color: C.faint, fontSize: 11 },
  spacer: { flex: 1 },
  text: { color: C.text, fontSize: 13, lineHeight: 19, padding: 6, borderRadius: S.radius, backgroundColor: C.bg },
  key: { padding: 4 },
  keyOff: { opacity: 0.3 },
  addRow: { gap: 6, marginTop: 4 },
  flowRow: { flexDirection: 'row', gap: 8 },
  flowBtn: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingVertical: 5, paddingHorizontal: 10, borderRadius: S.radius, borderWidth: 1, borderColor: C.line },
  flowBtnOn: { borderColor: C.accentText },
  flowText: { color: C.accentText, fontSize: 12 },
  addInput: { borderWidth: 1, borderColor: C.line, minHeight: 38 },
  addBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 7, borderRadius: S.radius, borderWidth: 1, borderColor: C.accentText },
  addLabel: { color: C.accentText, fontSize: 12, fontWeight: '600' },
});
