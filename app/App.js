import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { baseFor, defaultHost, getJSON, post } from './src/api';
import Inspector from './src/Inspector';
import PadGrid from './src/PadGrid';
import PushMirror from './src/PushMirror';
import { C, S, SEAT_HEX } from './src/theme';

const SURFACE_MS = 400; // the screen mirror; anything slower reads as laggy
const TARGET_MS = 2500; // which session is selected, and whether push_cc is up

export default function App() {
  const [host, setHost] = useState(defaultHost);
  const [surface, setSurface] = useState({});
  const [target, setTarget] = useState({});
  const [macros, setMacros] = useState([]);
  const [labels, setLabels] = useState([]);
  const [sel, setSel] = useState(null);
  const [moving, setMoving] = useState(null);
  const [armed, setArmed] = useState(false);
  const [note, setNote] = useState('');
  const [ready, setReady] = useState(false);
  const base = baseFor(host);
  const noteAt = useRef(null);

  const say = useCallback((text) => {
    setNote(text);
    clearTimeout(noteAt.current);
    noteAt.current = setTimeout(() => setNote(''), 2500);
  }, []);

  const loadMacros = useCallback(async () => {
    try {
      const d = await getJSON(base, '/macros');
      setMacros(d.pads || []);
      setLabels(d.labels || []);
      setReady(true);
    } catch (e) {
      setReady(false);
    }
  }, [base]);

  useEffect(() => {
    loadMacros();
  }, [loadMacros]);

  // Two clocks, not one: the mirror has to keep up with a screen that changes
  // several times a second, and the rest of it does not.
  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const s = await getJSON(base, '/surface');
        if (live) setSurface(s || {});
      } catch (e) {
        if (live) setSurface({});
      }
    };
    tick();
    const id = setInterval(tick, SURFACE_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [base]);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const t = await getJSON(base, '/target');
        if (live) setTarget(t || {});
      } catch (e) {
        if (live) setTarget({});
      }
    };
    tick();
    const id = setInterval(tick, TARGET_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [base]);

  const putMacros = useCallback(
    async (pads, labs) => {
      setMacros(pads);
      setLabels(labs);
      try {
        await post(base, '/macros', { labels: labs, pads });
        say('saved — live on the Push');
      } catch (e) {
        say('save failed');
      }
    },
    [base, say]
  );

  const press = useCallback(
    (what) => post(base, '/press', what).catch(() => say('the Push did not answer')),
    [base, say]
  );

  // One tap, three meanings, in the order that cannot surprise you: a pad in
  // your hand lands, an armed pad fires, and otherwise you are just picking
  // one to edit.
  const tapPad = useCallback(
    (i) => {
      if (moving !== null) {
        if (i !== moving) {
          const next = macros.slice();
          [next[i], next[moving]] = [next[moving], next[i]];
          putMacros(next, labels);
        }
        setMoving(null);
        setSel(i);
        return;
      }
      setSel(i);
      if (armed) {
        post(base, '/fire', { index: i })
          .then((r) => say(r))
          .catch(() => say('nothing there, or no session selected'));
      }
    },
    [armed, base, labels, macros, moving, putMacros, say]
  );

  const savePad = useCallback(
    (fields) => {
      if (sel === null) return say('pick a pad first');
      const next = macros.slice();
      next[sel] = fields.text.trim() ? fields : null;
      putMacros(next, labels);
    },
    [labels, macros, say, sel, putMacros]
  );

  const clearPad = useCallback(() => {
    if (sel === null) return say('pick a pad first');
    const next = macros.slice();
    next[sel] = null;
    putMacros(next, labels);
  }, [labels, macros, say, sel, putMacros]);

  const addLabel = useCallback(
    (entry) => {
      if (labels.some((l) => l.name === entry.name)) return say('that name is taken');
      putMacros(macros, labels.concat([entry]));
    },
    [labels, macros, say, putMacros]
  );

  const delLabel = useCallback(
    (i) => {
      const gone = labels[i].name;
      // no orphan tags: a pad pointing at a label that no longer exists would
      // keep a colour nothing can explain
      const pads = macros.map((m) => (m && m.tag === gone ? { ...m, tag: null } : m));
      putMacros(pads, labels.filter((_, k) => k !== i));
    },
    [labels, macros, putMacros]
  );

  const live = !!surface.views;
  const seat = target.name ? `${target.name} · ${target.status || '?'}` : 'no session';

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" />
      <View style={styles.header}>
        <Text style={styles.title}>midiAI</Text>
        <View style={[styles.dot, { backgroundColor: live ? SEAT_HEX.idle : C.faint }]} />
        <Text style={styles.seat}>{seat}</Text>

        <Pressable
          onPress={() => setArmed((a) => !a)}
          style={[styles.toggle, armed && styles.toggleOn]}>
          <Text style={[styles.toggleText, armed && styles.toggleTextOn]}>
            {armed ? 'tap fires the pad' : 'tap selects only'}
          </Text>
        </Pressable>
        <Pressable
          onPress={() => {
            setMoving(moving === null ? -1 : null);
            say(moving === null ? 'tap a pad to pick it up' : 'move off');
          }}
          style={[styles.toggle, moving !== null && styles.toggleMove]}>
          <Text style={[styles.toggleText, moving !== null && styles.toggleTextOn]}>
            {moving === null ? 'move' : moving < 0 ? 'pick one up' : 'tap where it goes'}
          </Text>
        </Pressable>

        <View style={styles.spacer} />
        <Text style={styles.hostLabel}>push_cc at</Text>
        <TextInput
          value={host}
          onChangeText={setHost}
          autoCapitalize="none"
          autoCorrect={false}
          style={styles.host}
          placeholder="host or ip"
          placeholderTextColor={C.faint}
        />
        <Text style={[styles.note, note ? styles.noteOn : null]}>{note}</Text>
      </View>

      <View style={styles.mirror}>
        <PushMirror
          base={base}
          surface={surface}
          onTab={(i) => press({ tab: i })}
          onSeat={(i) => press({ seat: i })}
        />
      </View>

      <View style={styles.body}>
        <View style={styles.gridWrap}>
          {ready ? (
            <PadGrid
              macros={macros}
              sel={sel}
              moving={moving !== null && moving >= 0 ? moving : null}
              onPress={tapPad}
            />
          ) : (
            <View style={styles.waiting}>
              <ActivityIndicator color={C.accentText} />
              <Text style={styles.waitingText}>
                no answer from {host}:8765 — start it with{' '}
                <Text style={styles.mono}>python3 mapui.py --lan</Text>
              </Text>
            </View>
          )}
        </View>
        <View style={styles.side}>
          <Inspector
            index={sel}
            pad={sel === null ? null : macros[sel] || null}
            labels={labels}
            note={note}
            onSave={savePad}
            onClear={clearPad}
            onAddLabel={addLabel}
            onDelLabel={delLabel}
          />
        </View>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: S.gap,
    paddingHorizontal: S.pad,
    paddingVertical: 10,
  },
  title: { color: C.text, fontSize: 17, fontWeight: '600' },
  dot: { width: 9, height: 9, borderRadius: 5, marginLeft: 6 },
  seat: { color: C.dim, fontSize: 13 },
  spacer: { flex: 1 },
  toggle: {
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    paddingHorizontal: 12,
    height: 34,
    justifyContent: 'center',
    backgroundColor: C.panel,
  },
  toggleOn: { borderColor: '#7a3a3a', backgroundColor: '#241a1a' },
  toggleMove: { borderColor: '#3a7a4a', backgroundColor: '#182417' },
  toggleText: { color: C.faint, fontSize: 12 },
  toggleTextOn: { color: C.text },
  hostLabel: { color: C.faint, fontSize: 12 },
  host: {
    width: 150,
    height: 34,
    color: C.text,
    backgroundColor: C.panel,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    paddingHorizontal: 10,
    fontSize: 13,
  },
  note: { color: 'transparent', fontSize: 12, width: 190 },
  noteOn: { color: C.good },
  mirror: { paddingHorizontal: S.pad, paddingBottom: 6 },
  body: { flex: 1, flexDirection: 'row', padding: S.pad, gap: S.pad },
  gridWrap: { flex: 1 },
  side: { width: 380 },
  waiting: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  waitingText: { color: C.dim, fontSize: 13, textAlign: 'center', maxWidth: 420 },
  mono: {
    color: C.accentText,
    ...Platform.select({ ios: { fontFamily: 'Menlo' }, default: {} }),
  },
});
