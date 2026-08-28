import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { baseFor, defaultHost, getJSON, PORT, post } from './src/api';
import Pads from './src/Pads';
import Pane from './src/Pane';
import PushButton from './src/PushButton';
import PushMirror from './src/PushMirror';
import Rail from './src/Rail';
import { C, KEY, S, SEAT_HEX } from './src/theme';

const SURFACE_MS = 400; // the mirror and the views both; anything slower lags
const TARGET_MS = 2500; // which session is selected, and whether push_cc is up
const EMPTY = Array(64).fill(null);

export default function App() {
  const [host, setHost] = useState(defaultHost);
  const [surface, setSurface] = useState({});
  const [target, setTarget] = useState({});
  const [api, setApi] = useState(true);
  const [pages, setPages] = useState([]);
  const [labels, setLabels] = useState([]);
  const [ownPage, setOwnPage] = useState(0);
  const [sel, setSel] = useState(null);
  const [moving, setMoving] = useState(null);
  const [armed, setArmed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [grid, setGrid] = useState(false);
  const [mirror, setMirror] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [ready, setReady] = useState(false);
  const base = baseFor(host);
  const noteAt = useRef(null);

  const total = Math.max(surface.pages ?? pages.length, 1);
  const page = Math.min(surface.page ?? ownPage, total - 1);
  const macros = pages[page] || EMPTY;
  const onward = page + 1 < total || macros.some(Boolean);
  const opts = surface.opts || [];
  const asking = opts.length > 0;
  const views = surface.views || [];
  const view = views[surface.view] || '';
  const data = surface.data || {};
  const cols = surface.cols || [];

  const say = useCallback((text) => {
    setNote(text);
    clearTimeout(noteAt.current);
    noteAt.current = setTimeout(() => setNote(''), 3000);
  }, []);

  // The surface poll would show a new or closed session within 400ms on its
  // own. Asking straight away is the difference between a button that visibly
  // worked and one you press twice because you are not sure it did.
  const refresh = useCallback(async () => {
    try {
      setSurface((await getJSON(base, '/surface')) || {});
    } catch (e) {
      // the next tick will pick it up; a failed refresh is not worth saying
    }
  }, [base]);

  const loadMacros = useCallback(async () => {
    try {
      const d = await getJSON(base, '/macros');
      setPages(d.pages || [d.pads || []]);
      setLabels(d.labels || []);
      setOwnPage(d.page || 0);
      setReady(true);
    } catch (e) {
      setReady(false);
    }
  }, [base]);

  useEffect(() => {
    loadMacros();
  }, [loadMacros]);

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

  // the pads load once, and used to stay a spinner if the API happened to be
  // down at that moment; `surface` is a fresh object each tick, so this retries
  // on the poll clock and stops the moment they land
  useEffect(() => {
    if (api && !ready) loadMacros();
  }, [api, ready, surface, loadMacros]);

  const putMacros = useCallback(
    async (pads, labs) => {
      const next = Array.from({ length: Math.max(pages.length, page + 1) }, (_, i) =>
        i === page ? pads : pages[i] || EMPTY
      );
      setPages(next);
      setLabels(labs);
      try {
        await post(base, '/macros', { labels: labs, pages: next });
        say('saved — live on the Push');
      } catch (e) {
        say('save failed');
      }
    },
    [base, page, pages, say]
  );

  const press = useCallback(
    (what) => post(base, '/press', what).catch(() => say('the Push did not answer')),
    [base, say]
  );

  const firePad = useCallback(
    (i) =>
      post(base, '/fire', { index: i, page })
        .then((r) => say(r))
        .catch(() => say('nothing there, or no session selected')),
    [base, page, say]
  );

  const editPad = useCallback((i) => {
    setSel(i);
    setEditing(true);
  }, []);

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
      setSel((was) => (was === i ? null : i));
      if (armed) firePad(i);
    },
    [armed, firePad, labels, macros, moving, putMacros]
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
      const pads = macros.map((m) => (m && m.tag === gone ? { ...m, tag: null } : m));
      putMacros(pads, labels.filter((_, k) => k !== i));
    },
    [labels, macros, putMacros]
  );

  const live = api && !!target.ok;
  const why = api ? target.why || 'waiting for push_cc' : `no answer from ${host}:${PORT}`;
  const dot = live ? SEAT_HEX.idle : api ? SEAT_HEX.blocked : C.faint;

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" />

      <View style={styles.header}>
        <Text style={styles.brand}>midiAI</Text>
        <View style={styles.status}>
          <View style={[styles.dot, { backgroundColor: dot }]} />
          <Text style={[styles.why, !live && styles.whyBad]} numberOfLines={1}>
            {why}
          </Text>
        </View>

        {!mirror && (
          <View style={styles.tabs}>
            {views.map((name, i) => {
              const rgb = (surface.colours || [])[i];
              const hue = rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : C.accentText;
              const on = i === surface.view;
              const modes = (surface.modes || [])[i] || 1;
              const at = (surface.at || [])[i] || 0;
              return (
                <Text
                  key={name}
                  onPress={() => press({ tab: i })}
                  style={[
                    styles.tab,
                    { color: hue, borderBottomColor: on ? hue : 'transparent' },
                    on && styles.tabOn,
                  ]}>
                  {/* always, not only when active: the tab would change
                      width as you moved between them and the row would
                      shuffle under your finger */}
                  {modes > 1 ? `${name} ${at + 1}/${modes}` : name}
                </Text>
              );
            })}
          </View>
        )}

        <View style={styles.spacer} />

        <PushButton
          label={armed ? 'tap fires the pad' : 'tap selects only'}
          colour={SEAT_HEX.blocked}
          lit={armed}
          onPress={() => setArmed((a) => !a)}
          style={styles.key}
        />
        <PushButton
          label="Push mirror"
          colour={C.accentText}
          lit={mirror}
          onPress={() => setMirror((m) => !m)}
          style={styles.key}
        />
        <PushButton
          label="reconnect"
          colour={C.accentText}
          lit={api && !live}
          disabled={busy || !api}
          onPress={reconnect}
          style={styles.key}>
          {busy ? <ActivityIndicator size="small" color={C.accentText} /> : null}
        </PushButton>
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

      {mirror ? (
        <View style={styles.mirror}>
          <PushMirror
            base={base}
            surface={surface}
            macros={macros}
            onTab={(i) => press({ tab: i })}
            onSeat={(i) => press({ seat: i })}
          />
        </View>
      ) : (
        <View style={styles.body}>
          <Rail
            cols={cols}
            current={surface.current}
            onSeat={(i) => press({ seat: i })}
            base={base}
            onChanged={refresh}
          />
          <View style={styles.centre}>
            <Pane
              data={data}
              opts={opts}
              cols={cols}
              current={surface.current}
              onAnswer={(k) => press({ answer: k })}
              base={base}
              onSent={refresh}
            />
          </View>
          {!(asking && data.kind === 'focus') &&
            (ready ? (
              <Pads
                kind={data.kind}
                data={data}
                opts={opts}
                onAnswer={(k) => press({ answer: k })}
                macros={macros}
                labels={labels}
                sel={sel}
                moving={moving !== null && moving >= 0 ? moving : null}
                armed={armed}
                grid={grid}
                onGrid={setGrid}
                editing={editing && sel !== null}
                page={page}
                total={total}
                onward={onward}
                onPage={(d) => press({ page: d })}
                onPress={tapPad}
                onDrop={dropPad}
                onExecute={firePad}
                onEdit={editPad}
                onSave={savePad}
                onClear={clearPad}
                onAddLabel={addLabel}
                onDelLabel={delLabel}
                onClose={() => setEditing(false)}
              />
            ) : (
              <View style={styles.waiting}>
                <ActivityIndicator color={C.accentText} />
                <Text style={styles.waitingText}>
                  no answer from {host}:{PORT}
                </Text>
              </View>
            ))}
        </View>
      )}

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
    height: 56,
    flexDirection: 'row',
    alignItems: 'center',
    gap: S.gap,
    paddingHorizontal: S.pad,
    borderBottomWidth: 1,
    borderBottomColor: C.line,
  },
  brand: { color: C.text, fontSize: 16, fontWeight: '600' },
  status: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    height: 32,
    paddingHorizontal: 12,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: KEY.edge,
    backgroundColor: KEY.face,
    maxWidth: 300,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  why: { color: C.dim, fontSize: 12, flexShrink: 1 },
  whyBad: { color: C.bad },
  tabs: { flexDirection: 'row', alignItems: 'stretch', alignSelf: 'stretch', gap: 18, marginLeft: 4 },
  tab: {
    fontSize: 13,
    borderBottomWidth: 2,
    textAlignVertical: 'center',
    paddingTop: 20,
    paddingHorizontal: 2,
  },
  tabOn: { fontWeight: '600' },
  spacer: { flex: 1 },
  key: { height: S.control, minHeight: S.control, minWidth: 96 },
  host: {
    width: 130,
    height: S.control,
    color: C.text,
    backgroundColor: KEY.face,
    borderWidth: 1,
    borderColor: KEY.edge,
    borderRadius: 6,
    paddingHorizontal: 10,
    fontSize: 13,
  },
  body: { flex: 1, flexDirection: 'row' },
  centre: { flex: 1, minWidth: 0 },
  mirror: { flex: 1, padding: S.pad },
  waiting: {
    width: 344,
    borderLeftWidth: 1,
    borderLeftColor: C.line,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  waitingText: { color: C.dim, fontSize: 13, textAlign: 'center' },
  toastWrap: { position: 'absolute', left: 0, right: 0, bottom: 20, alignItems: 'center', pointerEvents: 'none' },
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
