import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';

import { baseFor, defaultHost, getJSON, PORT, post } from './src/api';
import Pads from './src/Pads';
import Pane from './src/Pane';
import PushButton from './src/PushButton';
import PushMirror from './src/PushMirror';
import Rail from './src/Rail';
import { BREAK, C, KEY, S, SEAT_HEX } from './src/theme';

const SURFACE_MS = 400; // the mirror and the views both; anything slower lags
const TARGET_MS = 2500; // which session is selected, and whether push_cc is up
// Nobody is watching a hidden tab, and an idle agent is not producing
// anything to watch either -- push_cc.py's `stamp` is `_frame_stamp`,
// written only when a frame is actually redrawn, so it sitting still for a
// while is an exact idle signal, not a heuristic. Both cases back off; the
// idle cap stays close to SURFACE_MS on purpose -- a press on the Push
// should still show up in about a second, not three (MIDI-016).
const SURFACE_HIDDEN_MS = 5000;
const SURFACE_IDLE_MS = 1200;
const SURFACE_IDLE_AFTER_MS = 10000;
const TARGET_HIDDEN_MS = 5000;
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
  const [editing, setEditing] = useState(false);
  const [grid, setGrid] = useState(false);
  const [mirror, setMirror] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [ready, setReady] = useState(false);
  // Which view and mode the pane draws from used to be a read of wherever
  // the Push happened to be pointed -- one "current" view, hardware
  // included, so two clients fought over it and every tab click waited on a
  // round trip. Now it is the app's own state; `followPush` decides whether
  // that state is this browser's to drive or a mirror of the Push's.
  const [followPush, setFollowPush] = useState(true);
  const [localView, setLocalView] = useState(0);
  const [localModes, setLocalModes] = useState({});
  // subagents has no mode of its own on the wire -- it is the same
  // `sessions` view's second mode, drawn as a tab of its own. Kept apart
  // from `localModes` (below) rather than folded into it because that map
  // is re-seeded from the Push every poll while following, and there is no
  // press that tells the hardware to sit on mode 1 -- seeding it back would
  // undo the tab you just picked within half a second.
  const [sessionsMode, setSessionsMode] = useState(0);
  // The session rail is a fixed 268px the header and pads panel cannot both
  // afford below 1200 -- rather than shrink it into uselessness it moves
  // behind this toggle, since the pane header already names the current
  // session and the rail's only remaining job is picking a different one.
  const [railOpen, setRailOpen] = useState(false);
  const base = baseFor(host);
  const noteAt = useRef(null);
  // Idle detection for the surface poll (MIDI-016): the last stamp seen and
  // when it last actually changed, plus the last time a person did anything
  // in this tab. Refs, not state -- these feed a setTimeout's own delay
  // calculation between renders and have no business causing one.
  const surfaceStampRef = useRef(null);
  const surfaceStampAtRef = useRef(Date.now());
  const lastActivityAtRef = useRef(Date.now());
  const { width } = useWindowDimensions();
  const wide = width >= BREAK.wide;
  // <=, not <: at exactly 820 the app is in exactly the same bind as 819 --
  // the single-column body and the last of the header's optional controls
  // (Push mirror) both need to have already given way by here, not one
  // pixel later.
  const narrow = width <= BREAK.mid;
  // The prompt library is 19 always-on items competing with the one thing
  // you're actually reading -- collapsed is the resting state everywhere,
  // freeing that width for the transcript. Only at `wide` is there room to
  // read one and glance at the other at the same time, so only there does
  // it default open. Re-evaluated whenever the breakpoint is crossed, not
  // held forever -- short of that, whichever way it was last toggled stands.
  const [padsOpen, setPadsOpen] = useState(wide);

  const total = Math.max(surface.pages ?? pages.length, 1);
  const page = Math.min(surface.page ?? ownPage, total - 1);
  const macros = pages[page] || EMPTY;
  const onward = page + 1 < total || macros.some(Boolean);
  const opts = surface.opts || [];
  const asking = opts.length > 0;
  const views = surface.views || [];
  const cols = surface.cols || [];

  const viewIdx = localView;
  const viewName = views[viewIdx] || '';
  // focus's second mode is the macro/prompt grid on the Push strip -- there
  // is no such thing to switch to here, the right-hand pads panel is always
  // on screen, so this view is pinned to mode 0 regardless of what the
  // hardware or a stale follow-mirror happens to be sitting on. sessions'
  // second mode is the opposite case -- a real place worth being (the
  // subagents tab), just one this app can only ever pick locally.
  const mode =
    viewName === 'focus' ? 0
      : viewName === 'sessions' ? sessionsMode
      : localModes[viewIdx] ?? 0;
  // views_data is a parallel change landing on the server; until it does,
  // there is only surface.data -- whatever the hardware itself is showing,
  // which is the fallback rather than the source now.
  const viewData = surface.views_data && surface.views_data[viewName];
  const data = (viewData && viewData[mode]) || surface.data || {};

  // The view name is a wire contract (VIEWS in push_cc.py, a views_data key)
  // -- only the word drawn on the tab changes here, never the key anything
  // is looked up by.
  const TAB_LABEL = { sessions: 'agents' };
  const sessionsIdx = views.indexOf('sessions');
  // tests, prs and the subagents split of sessions are the tabs worth a
  // glance without a click for a single-repo user -- usually zero, and
  // "zero" is itself the useful fact. focus and usage have no one number
  // that sums them up, so they stay bare.
  const testsCount = (surface.views_data?.tests?.[0]?.items || []).length;
  const prsCount = (surface.views_data?.prs?.[0]?.rows || []).length;
  const subsCount = (surface.views_data?.sessions?.[1]?.rows || []).length;
  const TAB_COUNT = { tests: testsCount, prs: prsCount };
  const filled = macros.filter(Boolean).length;

  // Two different facts were being read as one status. Whether push_cc
  // answers and is running is the thing a red dot and a lit reconnect
  // button are for -- reconnect restarts the process. Whether a Push is on
  // the end of the USB cable is a separate fact the launcher explicitly
  // supports being false: headless is normal, not a fault, and no amount of
  // pressing reconnect plugs in a cable that isn't there.
  const reachable = api && !!target.running;
  const headless = reachable && !target.ok;
  // Headless, the only thing that ever moves push_cc's own view is this
  // app's `press({tab})` -- there's no hardware to press its buttons, so
  // "following the Push" would just mean following the app's own presses
  // in a loop. `followPush` still holds whatever the user last set it to
  // (plugging a Push back in mid-session should restore it, not reset it),
  // but it drives nothing while headless: this is the one flag the follow
  // effects and every tab press actually check.
  const effectiveFollow = followPush && !headless;

  // While following, local state is a mirror rather than a second copy of
  // the truth: every poll re-seeds it from the Push. Flip follow off (or go
  // headless) and these two effects simply stop firing -- local state
  // freezes right where it was and the app is on its own from there.
  useEffect(() => {
    if (effectiveFollow && surface.view != null) setLocalView(surface.view);
  }, [effectiveFollow, surface.view]);
  useEffect(() => {
    if (!effectiveFollow) return;
    const at = surface.at || [];
    if (at.length) setLocalModes(Object.fromEntries(at.map((m, i) => [i, m])));
  }, [effectiveFollow, surface.at]);
  // A resize past the breakpoint puts the panel back to its default for the
  // new width; short of that this leaves whatever the toggle or the composer
  // last set alone, rather than fighting every render.
  useEffect(() => {
    setPadsOpen(wide);
  }, [wide]);

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

  // A flat 400ms whether or not anyone was looking used to mean 666 polls in
  // under 5 minutes of testing, same rate hidden, idle, or watched. This is
  // a setTimeout loop rather than setInterval so the delay can change from
  // one tick to the next -- hidden drops to a heartbeat, and visible-but-
  // idle backs off (capped, see SURFACE_IDLE_MS above); anything that isn't
  // that -- a stamp change, becoming visible, a person doing something --
  // collapses straight back to SURFACE_MS on the very next tick (MIDI-016).
  useEffect(() => {
    let live = true;
    let timer = null;

    const clearTimer = () => {
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const nextDelay = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        return SURFACE_HIDDEN_MS;
      }
      const quietFor = Date.now() - Math.max(surfaceStampAtRef.current, lastActivityAtRef.current);
      return quietFor >= SURFACE_IDLE_AFTER_MS ? SURFACE_IDLE_MS : SURFACE_MS;
    };

    const tick = async () => {
      clearTimer();
      try {
        const s = await getJSON(base, '/surface');
        if (!live) return;
        if (s && s.stamp !== surfaceStampRef.current) {
          surfaceStampRef.current = s.stamp;
          surfaceStampAtRef.current = Date.now();
        }
        setSurface(s || {});
      } catch (e) {
        if (live) setSurface({});
      }
      if (live) timer = setTimeout(tick, nextDelay());
    };

    // Hidden's heartbeat is worth abandoning the moment the tab is visible
    // again rather than waiting out whatever's left of its 5s -- a press on
    // the Push shouldn't have to wait for a timer that was set for nobody
    // watching.
    const onVisibility = () => {
      if (document.visibilityState !== 'hidden') tick();
    };
    // Same reasoning, triggered by the person instead of the tab: typing,
    // clicking, tapping a pad all mean somebody is here now, so a backed-off
    // wait isn't worth finishing either.
    const onActivity = () => {
      const wasBackedOff = Date.now() - surfaceStampAtRef.current >= SURFACE_IDLE_AFTER_MS;
      lastActivityAtRef.current = Date.now();
      if (wasBackedOff) tick();
    };

    tick();
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibility);
      document.addEventListener('pointerdown', onActivity);
      document.addEventListener('keydown', onActivity);
    }
    return () => {
      live = false;
      clearTimer();
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility);
        document.removeEventListener('pointerdown', onActivity);
        document.removeEventListener('keydown', onActivity);
      }
    };
  }, [base]);

  // /target only needs the hidden-tab half of the same treatment -- its
  // visible cadence (2.5s: which session is selected, whether push_cc is up
  // at all) is fine as is, see MIDI-016.
  useEffect(() => {
    let live = true;
    let timer = null;

    const nextDelay = () =>
      typeof document !== 'undefined' && document.visibilityState === 'hidden'
        ? TARGET_HIDDEN_MS
        : TARGET_MS;

    const tick = async () => {
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
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
      if (live) timer = setTimeout(tick, nextDelay());
    };

    const onVisibility = () => {
      if (document.visibilityState !== 'hidden') tick();
    };

    tick();
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibility);
    }
    return () => {
      live = false;
      if (timer != null) clearTimeout(timer);
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility);
      }
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
        .catch(() => say('nothing there, or no agent selected')),
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

  // A tap always selects, never fires -- the pad popover's own `run` is the
  // one way to fire from here now. Arming was a global modifier that changed
  // what every tap meant with no way to tell which mode you were in without
  // checking the header; deleting it means a tap can only ever mean one thing.
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
    },
    [labels, macros, moving, putMacros]
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

  const why = api ? target.why || 'waiting for push_cc' : `no answer from ${host}:${PORT}`;
  const dot = reachable ? SEAT_HEX.idle : api ? SEAT_HEX.blocked : C.faint;
  // The header's own tightness, not the body's: below `wide` the rail toggle
  // joins the row and the whole thing no longer fits at any width the body
  // still calls two-pane. Tying the wrap to `narrow` (the body's boundary,
  // 300px further down) left this whole middle stretch overflowing off the
  // edge with nothing to catch it -- wrapping here costs nothing extra once
  // it triggers, so it triggers as soon as the row can no longer promise it.
  const headerTight = !wide;

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" />

      <View style={[styles.header, headerTight && styles.headerWrap]}>
        <Text style={styles.brand}>midiAI</Text>
        <View style={styles.status}>
          <View style={[styles.dot, { backgroundColor: dot }]} />
          {/* no numberOfLines: this used to crop mid-sentence with no way to
              read the rest. The pill wraps to a second line instead now --
              room for the whole thing beats a truncation nobody can undo. */}
          <Text style={[styles.why, !reachable && styles.whyBad]}>{why}</Text>
        </View>
        {/* Headless is the launcher's own supported mode, not a fault -- the
            red dot and banner above are for push_cc being unreachable, and
            this is the separate, quiet fact that no Push is on the cable. */}
        {headless && (
          <View style={styles.headless}>
            <Text style={styles.headlessText}>headless</Text>
          </View>
        )}

        {!mirror && (
          <View style={styles.tabs}>
            {/* Every mode a tab could be sitting in used to hang off it as an
                "N/M" counter -- a number nobody could act on without pressing
                the tab to find out what the other one was. Gone now except
                for the three counts below, which are the ones you can act on
                without a click: usage's modes are their own pressable row
                inside the pane, and focus's mode is pinned off above. */}
            {views.map((name, i) => {
              const rgb = (surface.colours || [])[i];
              const hue = rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : C.accentText;
              // sessions carries two tabs now (agents, subagents) sharing
              // one view index -- this one reads "on" only for its own mode,
              // so picking the other doesn't light both.
              const on = i === viewIdx && (name !== 'sessions' || sessionsMode === 0);
              return (
                <Tab
                  key={name}
                  label={TAB_LABEL[name] || name}
                  count={TAB_COUNT[name]}
                  hue={hue}
                  on={on}
                  onPress={() => {
                    // instant, always -- following or not, the tab you just
                    // hit is the one you see. Following additionally tells
                    // the Push to move there, same as it always did.
                    setLocalView(i);
                    if (name === 'sessions') setSessionsMode(0);
                    if (effectiveFollow) press({ tab: i });
                  }}
                />
              );
            })}
            {/* A synthetic tab: no hardware equivalent, and driving push_cc's
                own mode-cycle from here would be fragile for a button that
                only ever needs to land on one specific mode. Following
                presses the sessions tab either way -- same as the real
                "agents" tab above -- and the app draws whichever of the two
                was actually picked, which can drift from the hardware's own
                mode while following. That's fine; see sessionsMode above. */}
            {sessionsIdx >= 0 && (
              <Tab
                key="subagents"
                label="subagents"
                count={subsCount}
                hue={(() => {
                  const rgb = (surface.colours || [])[sessionsIdx];
                  return rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : C.accentText;
                })()}
                on={viewIdx === sessionsIdx && sessionsMode === 1}
                onPress={() => {
                  setLocalView(sessionsIdx);
                  setSessionsMode(1);
                  if (effectiveFollow) press({ tab: sessionsIdx });
                }}
              />
            )}
          </View>
        )}

        <View style={styles.spacer} />

        {/* Below 1200 the rail moves behind this and the pane header already
            names the current agent, so there is always an answer to "who
            am I looking at" without it. */}
        {!wide && (
          <PushButton
            label="agents"
            colour={C.accentText}
            lit={railOpen}
            onPress={() => setRailOpen((o) => !o)}
            style={styles.key}
          />
        )}
        {/* Headless, there's no hardware for this to mean anything about --
            push_cc's view only ever moves because this app pressed it, so
            "follow" would be the app following its own presses. The stored
            value is untouched underneath (see effectiveFollow above), so a
            Push plugged in mid-session brings this back exactly as it was
            left, rather than resetting to "following". */}
        {!headless && (
          <PushButton
            // On (the default) is the behaviour this app always had: your tab
            // is the Push's tab. Off, the two are independent -- the mirror
            // still drives the hardware regardless, because mirroring it is
            // the one thing the mirror is for.
            label={followPush ? 'following the Push' : 'independent view'}
            colour={C.accentText}
            lit={followPush}
            onPress={() => setFollowPush((f) => !f)}
            style={styles.key}
          />
        )}
        {/* reconnect is the one control that must never be the thing that
            clips -- it's precisely what you reach for when push_cc has died,
            so it and the rail toggle above it come before anything optional. */}
        <PushButton
          label="reconnect"
          colour={C.accentText}
          lit={api && !reachable}
          disabled={busy || !api}
          onPress={reconnect}
          style={styles.key}>
          {busy ? <ActivityIndicator size="small" color={C.accentText} /> : null}
        </PushButton>
        {/* Push mirror gives way second, once the row is genuinely narrow --
            it is optional in a way reconnect is not, but it survives the
            middle widths the host field already vacated by then. */}
        {!narrow && (
          <PushButton
            label="Push mirror"
            colour={C.accentText}
            lit={mirror}
            onPress={() => setMirror((m) => !m)}
            style={styles.key}
          />
        )}
        {/* a debug affordance, not a phone's business -- the first thing to
            give way, well above the width anyone is typing a hostname at */}
        {wide && (
          <TextInput
            value={host}
            onChangeText={setHost}
            autoCapitalize="none"
            autoCorrect={false}
            style={styles.host}
            placeholder="host or ip"
            placeholderTextColor={C.faint}
          />
        )}
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
      ) : narrow ? (
        // The rail and the pads panel are both fixed-width furniture (268
        // and 344-528) that cannot shrink to fit a column this narrow -- so
        // rather than let them overlap the pane, as they did, the whole
        // thing becomes one scrollable column and the pads panel keeps its
        // own width behind a sideways scroll scoped to just that block,
        // which is not the page scrolling sideways.
        <ScrollView contentContainerStyle={styles.bodyNarrow}>
          <Pane
            data={data}
            opts={opts}
            cols={cols}
            current={surface.current}
            onAnswer={(k) => press({ answer: k })}
            base={base}
            onSent={refresh}
            mode={mode}
            onMode={(i) => setLocalModes((prev) => ({ ...prev, [viewIdx]: i }))}
            reachable={reachable}
            onComposerFocus={() => setPadsOpen(true)}
            padsOpen={padsOpen}
            padsCount={filled}
            onTogglePads={() => setPadsOpen((o) => !o)}
          />
          {!(asking && data.kind === 'focus') && (padsOpen || asking) &&
            (ready ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                <Pads
                  kind={data.kind}
                  data={data}
                  opts={opts}
                  onAnswer={(k) => press({ answer: k })}
                  macros={macros}
                  labels={labels}
                  sel={sel}
                  moving={moving !== null && moving >= 0 ? moving : null}
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
              </ScrollView>
            ) : (
              <View style={styles.waitingNarrow}>
                <ActivityIndicator color={C.accentText} />
                <Text style={styles.waitingText}>
                  no answer from {host}:{PORT}
                </Text>
              </View>
            ))}
        </ScrollView>
      ) : (
        <View style={styles.body}>
          {wide && (
            <Rail
              cols={cols}
              current={surface.current}
              onSeat={(i) => press({ seat: i })}
              base={base}
              onChanged={refresh}
            />
          )}
          <View style={styles.centre}>
            <Pane
              data={data}
              opts={opts}
              cols={cols}
              current={surface.current}
              onAnswer={(k) => press({ answer: k })}
              base={base}
              onSent={refresh}
              mode={mode}
              onMode={(i) => setLocalModes((prev) => ({ ...prev, [viewIdx]: i }))}
              reachable={reachable}
              onComposerFocus={() => setPadsOpen(true)}
            padsOpen={padsOpen}
            padsCount={filled}
            onTogglePads={() => setPadsOpen((o) => !o)}
            />
          </View>
          {!(asking && data.kind === 'focus') && (padsOpen || asking) &&
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

      {/* The rail as a drawer rather than a column below 1200 -- a Modal so
          it overlays instead of competing with the pane and pads for a row
          that cannot fit all three. */}
      {!wide && (
        <Modal
          visible={railOpen}
          transparent
          animationType="fade"
          onRequestClose={() => setRailOpen(false)}>
          <Pressable style={styles.railBackdrop} onPress={() => setRailOpen(false)} />
          <View style={styles.railDrawer}>
            <Rail
              cols={cols}
              current={surface.current}
              onSeat={(i) => {
                press({ seat: i });
                setRailOpen(false);
              }}
              base={base}
              onChanged={refresh}
            />
          </View>
        </Modal>
      )}

      {!!note && (
        <View style={styles.toastWrap}>
          <Text style={styles.toast}>{note}</Text>
        </View>
      )}
    </SafeAreaView>
  );
}

// One tab, drawn the same whether it comes straight off `views` or is the
// synthetic subagents split of `sessions` -- the caller decides the label,
// the count and what "on" means, this just draws it.
//
// The touch target is a Pressable wrapping the Text, not the Text itself.
// `styles.tabs` puts 18px of `gap` between tabs for looks, and a bare Text's
// hit box stops at its own edge -- so half of every gap was dead, and a
// click there landed on the row's own View with nowhere to send a /press
// (MIDI-012). `tabHit` pads the Pressable out by half that gap and pulls it
// back in with an equal negative margin, so the tappable box grows to meet
// the neighbour while the drawn box never moves.
//
// The accessible name lives on the Pressable, not the two Text nodes inside
// it -- nested Text used to leave assistive tech (and the QA recorder)
// landing on whichever node the pointer was actually over, sometimes just
// the count on its own ("· 0", nothing to say what it counted) (MIDI-015).
// The inner Text is aria-hidden so that name isn't read out a second time.
function Tab({ label, count, hue, on, onPress }) {
  const has = count != null && count > 0;
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={count != null ? `${label} · ${count}` : label}
      accessibilityState={{ selected: on }}
      onPress={onPress}
      style={styles.tabHit}>
      <Text
        aria-hidden
        importantForAccessibility="no-hide-descendants"
        style={[
          styles.tab,
          { color: hue, borderBottomColor: on ? hue : 'transparent' },
          on && styles.tabOn,
        ]}>
        {label}
        {count != null && (
          <Text style={[styles.tabCount, has && { color: hue, fontWeight: '700' }]}>
            {' '}
            · {count}
          </Text>
        )}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    gap: S.gap,
    paddingHorizontal: S.pad,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: C.line,
  },
  // Below `wide` there are eleven-odd controls and nowhere to put them in one
  // line -- wrapping is the whole fix, and it costs nothing at a width where
  // everything already fit on one row.
  headerWrap: { flexWrap: 'wrap', rowGap: 8 },
  brand: { color: C.text, fontSize: 16, fontWeight: '600' },
  status: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: 32,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: KEY.edge,
    backgroundColor: KEY.face,
    maxWidth: 440,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  // no numberOfLines -- a clipped "why" used to be unreadable and
  // unrecoverable; wrapping inside the pill's own maxWidth is room enough.
  why: { color: C.dim, fontSize: 12, flexShrink: 1 },
  whyBad: { color: C.bad },
  // Quiet on purpose: headless is a supported way to run, not the red
  // banner above it. Same pill shape, no colour that reads as trouble.
  headless: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: KEY.edge,
    backgroundColor: KEY.face,
  },
  headlessText: { color: C.faint, fontSize: 11, letterSpacing: 0.3 },
  tabs: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'stretch', alignSelf: 'stretch', gap: 18, marginLeft: 4 },
  // Half of `tabs`' own 18px gap, padded on and pulled back off -- see the
  // comment on Tab above. Purely a hit-area move: nothing here is visible.
  tabHit: { paddingHorizontal: 9, marginHorizontal: -9 },
  tab: {
    fontSize: 13,
    borderBottomWidth: 2,
    textAlignVertical: 'center',
    paddingTop: 20,
    paddingHorizontal: 2,
  },
  tabOn: { fontWeight: '600' },
  // A zero here is a settled fact, not a warning, so it stays this quiet by
  // default. But quiet was all it ever was -- the tester hit an empty tab
  // twice in the same 21 seconds with the count on screen the whole time,
  // because a faint "0" and a faint "3" read the same at a glance (MIDI-014).
  // Whenever there's something behind the tab, Tab above lifts this to the
  // tab's own hue and bold -- a glance now tells "nothing" from "something"
  // without reading the digit, and zero still never shouts.
  tabCount: { color: C.faint, fontSize: 12 },
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
  // One column below 820: the pane sizes to its own content instead of
  // fighting a flex:1 chain it no longer sits inside, and the page's own
  // scroll -- not a nested one -- carries you from the transcript down
  // through the composer to the pads.
  bodyNarrow: { flexGrow: 1 },
  waiting: {
    width: 344,
    borderLeftWidth: 1,
    borderLeftColor: C.line,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  waitingNarrow: {
    borderTopWidth: 1,
    borderTopColor: C.line,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    paddingVertical: 32,
  },
  waitingText: { color: C.dim, fontSize: 13, textAlign: 'center' },
  // The rail as a drawer: a dimmed backdrop the width of the screen, and the
  // rail itself pinned to the left edge at its own fixed width -- it never
  // had to learn to be anything else, only to sit somewhere that isn't a
  // column fighting the pane and pads for room.
  // pointerEvents: 'auto' on both -- RN's own transparent Modal wraps its
  // content in a chain of pointer-events:none containers on web (so a modal
  // with nothing painted over a given pixel doesn't eat clicks meant for the
  // page under it), and pointer-events inherits. Without asserting it back
  // on, neither the backdrop nor anything inside the drawer is clickable,
  // silently -- the close and every seat in the rail just eat the tap.
  railBackdrop: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.5)',
    pointerEvents: 'auto',
  },
  railDrawer: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    width: 268,
    backgroundColor: C.bg,
    borderRightWidth: 1,
    borderRightColor: C.line,
    pointerEvents: 'auto',
  },
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
