import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Platform,
  Pressable,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { baseFor, defaultHost, getJSON, PORT, post } from './src/api';
import Inspector from './src/Inspector';
import PadGrid from './src/PadGrid';
import PushButton from './src/PushButton';
import PushMirror from './src/PushMirror';
import { C, KEY, S, SEAT_HEX } from './src/theme';

const SURFACE_MS = 400; // the screen mirror; anything slower reads as laggy
const TARGET_MS = 2500; // which session is selected, and whether push_cc is up

export default function App() {
  const [host, setHost] = useState(defaultHost);
  const [surface, setSurface] = useState({});
  const [target, setTarget] = useState({});
  const [api, setApi] = useState(true); // did mapui.py answer at all
  const [macros, setMacros] = useState([]);
  const [labels, setLabels] = useState([]);
  const [sel, setSel] = useState(null);
  const [moving, setMoving] = useState(null);
  const [armed, setArmed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [ready, setReady] = useState(false);
  const base = baseFor(host);
  const noteAt = useRef(null);

  const say = useCallback((text) => {
    setNote(text);
    clearTimeout(noteAt.current);
    noteAt.current = setTimeout(() => setNote(''), 3000);
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
        if (!live) return;
        setTarget(t || {});
        setApi(true);
      } catch (e) {
        if (!live) return;
        setTarget({});
        setApi(false);
      }
    };
    tick();
    const id = setInterval(tick, TARGET_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [base]);

  // The pads load once, and used to stay a spinner forever if the API happened
  // to be down at that moment. `target` is a fresh object every tick, so this
  // retries on the poll clock and stops the moment they land.
  useEffect(() => {
    if (api && !ready) loadMacros();
  }, [api, ready, target, loadMacros]);

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

  // push_cc holds the MIDI and USB handles, so reconnecting means restarting
  // it -- mapui.py already does that, and answers with why the Push is or is
  // not talking rather than just whether it is.
  const reconnect = useCallback(async () => {
    setBusy(true);
    say('restarting push_cc…');
    try {
      const state = JSON.parse(await post(base, '/relaunch', {}));
      setTarget((prev) => ({ ...prev, ...state }));
      setApi(true);
      say(state.why || 'restarted');
      loadMacros();
    } catch (e) {
      setApi(false);
      say(`no answer from ${host}:${PORT}`);
    } finally {
      setBusy(false);
    }
  }, [base, host, loadMacros, say]);

  // the live wire: this types into a real session, so it is only ever reached
  // from something you deliberately aimed at -- an armed tap, or a card's key
  const firePad = useCallback(
    (i) =>
      post(base, '/fire', { index: i })
        .then((r) => say(r))
        .catch(() => say('nothing there, or no session selected')),
    [base, say]
  );

  const editPad = useCallback((i) => {
    setSel(i);
    setEditing(true);
  }, []);

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
      if (armed) firePad(i);
    },
    [armed, firePad, labels, macros, moving, putMacros, say]
  );

  // dropping a pad on another swaps them, the same trade the Push's own move
  // mode makes -- an empty pad has nothing to give, so dragging one would only
  // teleport the pad you aimed at
  const dropPad = useCallback(
    (from, to) => {
      if (!macros[from]) return say('that pad is empty');
      const next = macros.slice();
      [next[from], next[to]] = [next[to], next[from]];
      putMacros(next, labels);
      setSel(to);
    },
    [labels, macros, putMacros, say]
  );

  const savePad = useCallback(
    (fields) => {
      if (sel === null) return say('pick a pad first');
      const next = macros.slice();
      next[sel] = fields.text.trim() ? fields : null;
      putMacros(next, labels);
      setEditing(false);
    },
    [labels, macros, say, sel, putMacros]
  );

  const clearPad = useCallback(() => {
    if (sel === null) return say('pick a pad first');
    const next = macros.slice();
    next[sel] = null;
    putMacros(next, labels);
    setEditing(false);
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

  // Three states, not two. A surface file left on disk by a push_cc that has
  // since died would keep an "is it live" light green forever, so the answer
  // comes from push_state -- which also knows the difference between the
  // process being down, the Push being off USB, and it sitting in Live mode.
  const live = api && !!target.ok;
  const why = api ? target.why || 'waiting for push_cc' : `no answer from ${host}:${PORT}`;
  const dot = live ? SEAT_HEX.idle : api ? SEAT_HEX.blocked : C.faint;
  const seat = target.name ? `${target.name} · ${target.status || '?'}` : 'no session';

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" />
      <View style={styles.header}>
        <Text style={styles.title}>midiAI</Text>

        <View style={styles.status}>
          <View style={[styles.dot, { backgroundColor: dot }]} />
          <Text style={[styles.why, !live && styles.whyBad]} numberOfLines={1}>
            {why}
          </Text>
          <Text style={styles.seat} numberOfLines={1}>
            {seat}
          </Text>
        </View>

        <PushButton
          label={armed ? 'tap fires the pad' : 'tap selects only'}
          colour={SEAT_HEX.blocked}
          lit={armed}
          onPress={() => setArmed((a) => !a)}
          style={styles.key}
        />
        <PushButton
          label={moving === null ? 'move' : moving < 0 ? 'pick one up' : 'tap where it goes'}
          colour={SEAT_HEX.idle}
          lit={moving !== null}
          onPress={() => {
            setMoving(moving === null ? -1 : null);
            say(moving === null ? 'tap a pad to pick it up' : 'move off');
          }}
          style={styles.key}
        />

        {sel !== null && (
          <PushButton
            label={`edit pad ${36 + sel}`}
            colour={C.accentText}
            lit={editing}
            onPress={() => setEditing(true)}
            style={styles.key}
          />
        )}

        <View style={styles.spacer} />

        <PushButton
          label="reconnect"
          colour={C.accentText}
          lit={api && !live}
          disabled={busy || !api}
          onPress={reconnect}
          style={styles.key}>
          {busy ? <ActivityIndicator size="small" color={C.accentText} /> : null}
        </PushButton>

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
        {ready ? (
          <PadGrid
            macros={macros}
            sel={sel}
            moving={moving !== null && moving >= 0 ? moving : null}
            armed={armed}
            onPress={tapPad}
            onDrop={dropPad}
            onExecute={firePad}
            onEdit={editPad}
          />
        ) : (
          <View style={styles.waiting}>
            <ActivityIndicator color={C.accentText} />
            <Text style={styles.waitingText}>
              no answer from {host}:{PORT} — start it with{' '}
              <Text style={styles.mono}>python3 mapui.py --lan</Text>
            </Text>
          </View>
        )}
      </View>

      {/* the editor is a detour, not a place -- it used to hold a third of the
          screen open whether or not anything was being edited, next to a grid
          that wants every pixel it can get */}
      <Modal
        visible={editing}
        transparent
        animationType="fade"
        supportedOrientations={['portrait', 'landscape']}
        onRequestClose={() => setEditing(false)}>
        <View style={styles.scrim}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setEditing(false)} />
          <View style={styles.sheet}>
            <View style={styles.sheetHead}>
              <Text style={styles.sheetTitle}>Pad {sel === null ? '' : 36 + sel}</Text>
              <View style={styles.spacer} />
              <PushButton
                label="close"
                onPress={() => setEditing(false)}
                style={styles.key}
              />
            </View>
            <Inspector
              index={sel}
              pad={sel === null ? null : macros[sel] || null}
              labels={labels}
              onSave={savePad}
              onClear={clearPad}
              onAddLabel={addLabel}
              onDelLabel={delLabel}
            />
          </View>
        </View>
      </Modal>

      {/* one place for every transient message -- it used to be a reserved
          strip in the header, furthest from the pads and the editor that
          produce them */}
      {!!note && (
        <View style={styles.toastWrap}>
          <Text style={styles.toast}>{note}</Text>
        </View>
      )}
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
  status: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    height: S.control,
    paddingHorizontal: 12,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: KEY.edge,
    backgroundColor: KEY.face,
    maxWidth: 380,
  },
  dot: { width: 9, height: 9, borderRadius: 5 },
  why: { color: C.dim, fontSize: 12, flexShrink: 1 },
  whyBad: { color: C.bad },
  seat: { color: C.faint, fontSize: 12, flexShrink: 1 },
  spacer: { flex: 1 },
  // the hardware's buttons are square-shouldered and short; only the height
  // differs from the ones flanking the display
  key: { height: S.control, minHeight: S.control, minWidth: 96 },
  hostLabel: { color: C.faint, fontSize: 12 },
  host: {
    width: 150,
    height: S.control,
    color: C.text,
    backgroundColor: KEY.face,
    borderWidth: 1,
    borderColor: KEY.edge,
    borderRadius: 6,
    paddingHorizontal: 10,
    fontSize: 13,
  },
  mirror: { paddingHorizontal: S.pad, paddingBottom: 6 },
  body: { flex: 1, padding: S.pad },
  scrim: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.72)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: S.pad,
  },
  sheet: {
    width: 560,
    maxWidth: '100%',
    maxHeight: '92%',
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    backgroundColor: C.panel,
    overflow: 'hidden',
  },
  sheetHead: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: S.pad,
    paddingTop: S.pad,
  },
  sheetTitle: { color: C.text, fontSize: 16, fontWeight: '600' },
  waiting: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  waitingText: { color: C.dim, fontSize: 13, textAlign: 'center', maxWidth: 420 },
  mono: {
    color: C.accentText,
    ...Platform.select({ ios: { fontFamily: 'Menlo' }, default: {} }),
  },
  toastWrap: {
    position: 'absolute',
    pointerEvents: 'none',
    left: 0,
    right: 0,
    bottom: 20,
    alignItems: 'center',
  },
  toast: {
    color: C.text,
    fontSize: 13,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.edge,
    backgroundColor: C.raised,
    overflow: 'hidden',
  },
});
