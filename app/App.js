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

import {
  listPrs,
  getDirty, addToQueue, baseFor, defaultHost, getJSON, listCatalog, listProjects, PORT, post } from './src/api';
import EntrySheet from './src/EntrySheet';
import Icon from './src/Icon';
import NoAgent from './src/NoAgent';
import Pads from './src/Pads';
import { useQueue } from './src/Queue';
import Pane, { SignalBars, fillHue } from './src/Pane';
import { inside } from './src/Projects';
import PushButton from './src/PushButton';
import PushMirror from './src/PushMirror';
import Rail from './src/Rail';
import Settings from './src/Settings';
import { BREAK, C, KEY, S, SEAT_HEX, mono } from './src/theme';

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
  const [editing, setEditing] = useState(false);
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
  // The session rail is a fixed 268px the header and pads panel cannot both
  // afford below 1200 -- rather than shrink it into uselessness it moves
  // behind this toggle, since the pane header already names the current
  // session and the rail's only remaining job is picking a different one.
  const [railOpen, setRailOpen] = useState(false);
  // reconnect, the mirror and the host field, behind one ⋮ -- see the header.
  const [menu, setMenu] = useState(false);
  // null | 'claude' | 'mcps' -- which section of the Settings modal is open,
  // or closed entirely. The gear opens 'claude'; the rail's footer MCPs row
  // opens 'mcps' directly via onOpenSettings, same modal either way.
  const [settings, setSettings] = useState(null);
  // { project, path, label, main } | null -- a worktree picked in the rail
  // that has nobody living in it. The pane draws NoAgent for it instead of a
  // transcript there is none of.
  const [pick, setPick] = useState(null);
  // Which panel the right-hand column is showing: the prompt library, the
  // skills this agent can reach, or its hooks. One key in the pane's title row
  // opens the menu that picks between them.
  const [panel, setPanel] = useState('prompts');
  const [catalog, setCatalog] = useState({ skills: [], hooks: [] });
  // The repos, for the one line under the focus title that says which checkout
  // and which branch you are typing into. Projects.js reads the same route for
  // the rail; it is fetched twice rather than lifted because the rail lives in
  // a Modal below `wide` and is simply not mounted there, so a list held only
  // by the rail would be missing exactly when the title line still is not.
  const [projects, setProjects] = useState([]);
  // { kind: 'skills'|'hooks', row } | null -- the skill or hook being edited,
  // or created when `row` is null.
  const [entry, setEntry] = useState(null);
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
  // The prompt library is 19 always-on items competing with the one thing you
  // are actually reading. Collapsed is the resting state at every width now --
  // it is a 56px strip of keys rather than nothing, so it costs a tap to open
  // and never costs 344px to ignore. It used to default open above `wide` on
  // the grounds that there was room for both; there was, and the transcript
  // still wanted it more.
  const [padsOpen, setPadsOpen] = useState(false);
  // Focusing the composer opens the library -- until you shut it yourself.
  // Without this every tap back into the input undid the close.
  const padsShut = useRef(false);
  const collapsePads = () => {
    padsShut.current = true;
    setPadsOpen(false);
  };
  const composerFocus = () => padsShut.current || setPadsOpen(true);

  const total = Math.max(surface.pages ?? pages.length, 1);
  const page = Math.min(surface.page ?? ownPage, total - 1);
  const macros = pages[page] || EMPTY;
  const opts = surface.opts || [];
  const asking = opts.length > 0;
  // These are product navigation, not a capability probe. Keeping the stable
  // contract visible while push_cc reconnects also lets the app-owned Git and
  // test endpoints continue to work in headless/server-only use.
  const views = surface.views || ['focus', 'sessions', 'tests', 'prs', 'usage'];
  const cols = surface.cols || [];
  // Every pad fires into "whichever session the Push is pointed at" -- with no
  // agent anywhere there is no such session, and the server answers a fire with
  // 409 "no session selected on the Push". A panel whose every button is a
  // guaranteed error is not worth the width.
  const anyAgent = cols.some(Boolean);
  // The focused agent's subagents. Both of `sessions`' modes carry the same
  // rows; mode 0 is the one that exists whatever the hardware is sitting on.
  const subs = surface.views_data?.sessions?.[0]?.rows || [];
  const here = cols[surface.current] || null;
  // Which worktree the focused agent is sitting in, and therefore which repo
  // and which branch. Longest match, same rule Projects uses for its own rows.
  const place = (() => {
    // The focused agent's cwd if there is one, otherwise the worktree picked
    // in the rail. A repo's tests and its git state are facts about the
    // checkout, not about who happens to be sitting in it -- reading them
    // needed an agent only because this was the one thing that answered
    // "which checkout", and it only ever asked the agents.
    // A pick wins: it is what you just chose to look at, and the tab badges
    // already read it (scopePath) -- with the agent winning here instead,
    // GIT's badge described the picked folder while its pane showed the
    // agent's repo underneath it.
    const from = pick?.path || here?.cwd;
    if (!from) return null;
    let best = null;
    for (const p of projects)
      for (const w of p.worktrees || []) {
        if (!inside(from, w.path)) continue;
        if (!best || w.path.length > best.w.path.length) best = { p, w };
      }
    if (!best) return null;
    const { p, w } = best;
    return {
      repo: p.name,
      root: p.path,
      branch: w.detached ? (w.head || '').slice(0, 7) : w.branch || '',
      // the worktree itself, so the rail can ask "is this agent in here too"
      path: w.path,
    };
  })();
  // What "here" means to the AGENTS filter: the worktree you picked in the
  // rail if you picked one, otherwise the one the focused agent is living in.
  const scopePath = pick?.path || place?.path || '';
  // A pick only stands while it is still empty: the moment an agent turns up
  // in that worktree the transcript is the better thing to be looking at, and
  // it clears itself rather than needing an effect to notice.
  const picked =
    pick && !cols.some((c) => c && inside(c.cwd, pick.path)) ? pick : null;


  const viewIdx = localView;
  const viewName = views[viewIdx] || '';
  // focus's second mode is the macro/prompt grid on the Push strip -- there
  // is no such thing to switch to here, the right-hand prompt panel is always
  // on screen, so this view is pinned to mode 0 regardless of what the
  // hardware or a stale follow-mirror happens to be sitting on.
  const mode = viewName === 'focus' ? 0 : localModes[viewIdx] ?? 0;
  // views_data is a parallel change landing on the server; until it does,
  // there is only surface.data -- whatever the hardware itself is showing,
  // which is the fallback rather than the source now.
  const viewData = surface.views_data && surface.views_data[viewName];
  const wireData = (viewData && viewData[mode]) || surface.data || {};
  const data = wireData.kind ? wireData : { kind: viewName };

  // The view name is a wire contract (VIEWS in push_cc.py, a views_data key)
  // -- only the word drawn on the tab changes here, never the key anything
  // is looked up by.
  // The view name is the wire contract; only the word drawn changes here.
  // Both of these are wider than the key underneath them. `prs` reads GIT
  // because pull requests are one of four things on it -- what has not left
  // this machine yet, what is waiting on a person, what the push set running,
  // and what runs before a commit is allowed to leave. It read CI/CD while
  // the first of those did not exist; a working tree is not CI, so the wider
  // word had to get wider still. `tests` reads GUARDRAILS because the view is
  // everything standing between a change and main, not the test files it
  // lists today.
  const TAB_LABEL = { prs: 'git', tests: 'guardrails' };

  // `sessions` and its subagents split are no longer tabs: the rail's AGENTS
  // group is a better answer to "who is running" than a tab that had to be
  // clicked to find out, and it is on screen the whole time. The view itself
  // is untouched on the wire -- the Push still has it.
  // usage is not a tab either: it is the indicator at the right of the bar,
  // so how full the context is can be read from anywhere in the app
  const TAB_HIDE = new Set(['sessions', 'usage']);
  // tests and git are the tabs worth a glance without a click for a
  // single-repo user -- usually zero, and "zero" is itself the useful fact.
  // focus and usage have no one number that sums them up, so they stay bare.
  const testsCount = (surface.views_data?.tests?.[0]?.items || []).length;
  // ...but that one is read off the Push's own focused agent. With a
  // worktree picked in the rail the pane below is about a different
  // checkout, and a badge counting the other one is worse than no badge.
  //
  // GIT counts what has not been committed yet -- files changed, staged or
  // not, untracked included -- in whichever checkout is in view, picked or
  // focused. It used to count open PRs, which is a fact about the remote;
  // the number worth a glance is the one you are about to lose or ship.
  const [dirty, setDirty] = useState(null);
  useEffect(() => {
    setDirty(null);
    if (!base || !scopePath) return undefined;
    let live = true;
    const pull = () => getDirty(base, scopePath).then((d) => live && setDirty(d)).catch(() => live && setDirty(null));
    pull();
    const timer = setInterval(pull, 5000);
    return () => { live = false; clearInterval(timer); };
  }, [base, scopePath]);
  // ...and its open pull requests, for the same checkout. Once a minute: it
  // is a `gh` call, a second or so against GitHub, not a local git status.
  const [openPrs, setOpenPrs] = useState(null);
  useEffect(() => {
    setOpenPrs(null);
    // only once the checkout is known to be a repo: before that answer it
    // would ask twice, and a folder with no repo has no pull requests
    if (!base || !scopePath || dirty?.git !== true) return undefined;
    let live = true;
    const pull = () => listPrs(base, scopePath)
      .then((d) => live && setOpenPrs(d.err ? null : (d.rows || []).length))
      .catch(() => live && setOpenPrs(null));
    pull();
    const timer = setInterval(pull, 60000);
    return () => { live = false; clearInterval(timer); };
  }, [base, scopePath, dirty?.git]);
  const TAB_COUNT = {
    ...(picked ? {} : { tests: testsCount }),
    ...(dirty?.git ? { prs: dirty.count } : {}),
  };
  // GIT reads "changed | open pull requests"; the second number only once known
  const TAB_COUNT2 = { prs: dirty?.git ? openPrs : null };
  // a project folder with no repo yet: GIT wears a warning, and opening it
  // is where `git init` is offered
  const TAB_WARN = { prs: dirty?.git === false };
  // FOCUS reads "active | blocked | passive" across every agent, each number
  // in its seat hue. Passive is idle, done (idle + unseen) and unknown alike.
  const live = cols.filter(Boolean);
  const nStatus = (...st) => live.filter((c) => st.includes(c.status)).length;
  const TAB_PARTS = live.length ? {
    focus: [
      { n: nStatus('working'), hue: SEAT_HEX.working, word: 'active' },
      { n: nStatus('blocked'), hue: SEAT_HEX.blocked, word: 'blocked' },
      { n: live.length - nStatus('working', 'blocked'), hue: SEAT_HEX.idle, word: 'passive or complete' },
    ],
  } : {};
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

  // Skills and hooks are files on disk, not surface state -- they change when
  // somebody edits a settings file, which is not something worth polling for.
  // Read once per agent you land on, and again whenever a rail operation says
  // something changed.
  const hereCwd = here?.cwd || '';
  // the current agent's up-next queue, for the right-hand column's queue tab
  const [queue, changeQueue, , queueErr, queueCtl] = useQueue(base, here?.tid);
  useEffect(() => {
    let live = true;
    listCatalog(base, hereCwd)
      .then((d) => live && setCatalog({ skills: d.skills || [], hooks: d.hooks || [] }))
      .catch(() => live && setCatalog({ skills: [], hooks: [] }));
    listProjects(base)
      .then((rows) => live && setProjects(rows))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [base, hereCwd]);

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
    (i, renderedText) => {
      const prompt = macros[i];
      if (!prompt || !here?.tid) return Promise.resolve(say('nothing there, or no agent selected'));
      // into the up-next queue, where it can be read, edited and reordered;
      // the queue sends it once the agent is free
      return addToQueue(base, here.tid, renderedText || prompt.text)
        .then((q) => say(`queued · ${q.length} up next`))
        .catch(() => say('nothing there, or no agent selected'));
    },
    [base, here?.tid, macros, say]
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

  // A tap always selects, never fires -- the pad popover's own `run` is the
  // one way to fire from here now. Arming was a global modifier that changed
  // what every tap meant with no way to tell which mode you were in without
  // checking the header; deleting it means a tap can only ever mean one thing.
  // The swap branch that used to live here armed from the pad grid's drag,
  // and nothing ever set it again once the grid went -- `moving` was already
  // dead state at HEAD, never assigned anything but null. Swapping pads is
  // the Push's own move mode now.
  const tapPad = useCallback((i) => setSel((was) => (was === i ? null : i)), []);

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
  // Built here rather than up with `picked`: `refresh` is a useCallback
  // declared further down, and reading it above its own declaration is a
  // temporal-dead-zone throw that takes the whole app blank on first render.
  // One call for "show me this panel" -- picking one from the menu opens the
  // column if it was shut, and picking `hide` shuts it without changing which
  // panel it will come back on.
  const showPanel = (which, open) => {
    setPanel(which);
    padsShut.current = !open;
    setPadsOpen(open);
  };
  // The catalog is files on disk, so nothing tells the app they changed but
  // the app having changed them.
  const reloadCatalog = useCallback(() => {
    listCatalog(base, hereCwd)
      .then((d) => setCatalog({ skills: d.skills || [], hooks: d.hooks || [] }))
      .catch(() => {});
  }, [base, hereCwd]);

  // ＋ prompt: the first empty pad on this page, opened in the editor. There
  // is no "create" for a pad -- there are sixty-four of them and they are
  // always there, so making one is filling one in.
  const newPad = useCallback(() => {
    const free = macros.findIndex((m) => !m);
    if (free < 0) return say('every pad on this page is full');
    setSel(free);
    setEditing(true);
  }, [macros, say]);

  // Every pad fires into "whichever session the Push is pointed at" -- with
  // no agent anywhere there is no such session, and the server answers a fire
  // with 409. A panel whose every button is a guaranteed error is not worth
  // the width, collapsed or open.
  //
  // And it is focus's panel, not the app's: guardrails, CI/CD and usage are
  // read, not typed into, so a column of things to say to an agent is 56px of
  // furniture with nothing to fire at on any of them. A question on the glass
  // takes it too -- the answer pads are what that moment is for.
  const showPads = !picked && anyAgent && viewName === 'focus' && !asking;

  const panelCounts = {
    prompts: filled,
    skills: catalog.skills.length,
    hooks: catalog.hooks.length,
    queue: queue.length,
  };

  // An empty worktree has no transcript to show and no one to send to, which
  // is what NoAgent answers -- so it stands in for `focus` alone. Tests and
  // git describe the checkout itself and are worth reading before anyone is
  // living in it; they used to be replaced by an offer to start an agent,
  // which made picking a repo to look at it impossible.
  const noAgent =
    picked && viewName === 'focus' ? (
      <NoAgent pick={picked} base={base} onClose={() => setPick(null)} onChanged={refresh} />
    ) : null;

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" />

      {/* One band where there were two. A header naming the app and a repo bar
          naming the checkout were 83px of chrome saying two things that belong
          on one line -- and the second of them existed only because the first
          had no room left. The checkout is a chip here rather than a band of
          its own: it is a fact about what you are looking at, the same size as
          the rest of them. */}
      <View style={[styles.band, headerTight && styles.bandWrap]}>
        <View style={[styles.dot, { backgroundColor: dot }]} />
        <Text style={styles.brand}>midiAI</Text>

        {/* Spend is account-wide, so usage gets no checkout chip -- a repo name
            over it would be claiming those were that repo's tokens. */}
        {viewName !== 'usage' &&
          (place ? (
            <View style={styles.chip}>
              <Text style={styles.chipRepo}>{place.repo}</Text>
              {!!place.branch && <Text style={styles.chipBranch}>{place.branch}</Text>}
            </View>
          ) : (
            <Text style={styles.chipNone}>no checkout — pick a worktree, or focus an agent</Text>
          ))}

        {!mirror && (
          <View style={styles.tabs}>
            {/* Every mode a tab could be sitting in used to hang off it as an
                "N/M" counter -- a number nobody could act on without pressing
                the tab to find out what the other one was. Gone now except
                for the counts below, which are the ones you can act on
                without a click. */}
            {views.map((name, i) => {
              // mapped, not filtered: `i` is the wire index a /press carries,
              // and a filtered array would renumber it.
              if (TAB_HIDE.has(name)) return null;
              const rgb = (surface.colours || [])[i];
              const hue = rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : C.accentText;
              return (
                <Tab
                  key={name}
                  label={TAB_LABEL[name] || name}
                  count={TAB_COUNT[name]}
                  count2={TAB_COUNT2[name]}
                  warn={TAB_WARN[name]}
                  parts={TAB_PARTS[name]}
                  hue={hue}
                  on={i === viewIdx}
                  onPress={() => {
                    // instant, always -- following or not, the tab you just
                    // hit is the one you see. Following additionally tells
                    // the Push to move there, same as it always did.
                    setLocalView(i);
                    if (effectiveFollow) press({ tab: i });
                  }}
                />
              );
            })}
          </View>
        )}

        <View style={styles.spacer} />

        {/* USAGE, apart from the tabs: the plan's session and weekly limits,
            each as phone bars and a percentage, on every view. Pressing it
            still opens the usage view, like the tab it was. */}
        {(() => {
          const ui = views.indexOf('usage');
          if (ui < 0) return null;
          const bars = surface.views_data?.usage?.[0]?.bars || [];
          const pick = (re) => bars.find((b) => re.test(b.label || ''));
          const shown = [['session', pick(/session/i)], ['week', pick(/week/i)]]
            .filter(([, b]) => b);
          const pct = (b) => Math.round(Math.max(0, Math.min(1, Number(b.used) || 0)) * 100);
          const on = ui === viewIdx;
          return (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={shown.length
                ? `usage · ${shown.map(([w, b]) => `${w} ${pct(b)}%`).join(', ')}`
                : 'usage'}
              accessibilityState={{ selected: on }}
              onPress={() => {
                setLocalView(ui);
                if (effectiveFollow) press({ tab: ui });
              }}
              style={[styles.usageKey, on && styles.usageKeyOn]}>
              {!shown.length && (
                <Text style={[styles.usageWord, on && styles.usageWordOn]}>usage</Text>
              )}
              {shown.map(([word, b], k) => (
                <View key={word} style={[styles.usagePart, k > 0 && styles.usagePartNext]}>
                  <Text style={[styles.usageWord, on && styles.usageWordOn]}>{word}</Text>
                  <SignalBars frac={pct(b) / 100} size={14} />
                  <Text style={[styles.usagePct, { color: fillHue(pct(b) / 100) }]}>{pct(b)}%</Text>
                </View>
              ))}
            </Pressable>
          );
        })()}

        {/* The status pill's whole content was this sentence -- why push_cc is
            not answering. A red dot that cannot say why is worse than no light,
            so the sentence outlived the pill; the dot beside the brand is the
            rest of it. */}
        {!reachable && (
          <Text style={styles.whyBad} numberOfLines={2}>
            {why}
          </Text>
        )}

        {/* Below 1200 the rail moves behind this and the pane header already
            names the current agent, so there is always an answer to "who am I
            looking at" without it. */}
        {!wide && (
          <PushButton
            label="agents"
            colour={C.accentText}
            lit={railOpen}
            onPress={() => setRailOpen((o) => !o)}
            style={styles.key}
          />
        )}
        {/* Headless is the launcher's own supported mode, not a fault, and
            there is no hardware for "follow" to mean anything about. The
            stored value is untouched underneath, so a Push plugged in
            mid-session brings this back exactly as it was left. */}
        {headless ? (
          <Text style={styles.headlessText}>headless</Text>
        ) : (
          <PushButton
            label={followPush ? 'following the Push' : 'independent view'}
            colour={C.accentText}
            lit={followPush}
            onPress={() => setFollowPush((f) => !f)}
            style={styles.key}
          />
        )}
        {!!place && (
          <Text style={styles.pathText} numberOfLines={1}>
            {place.path.replace(HOME_RE, '~')}
          </Text>
        )}
        {/* Claude access and MCPs, one gear rather than buried in the ⋮ menu --
            it is a destination (a modal with its own sections), not one more
            item in a list of one-shot actions. */}
        <PushButton
          accessibilityLabel="settings"
          colour={C.accentText}
          lit={!!settings}
          onPress={() => setSettings('claude')}
          style={styles.dots}>
          <Icon name="settings" size={16} color={settings ? C.text : C.dim} />
        </PushButton>
        {/* reconnect, the mirror and the host field behind one key, so the
            three things you reach for when something is wrong are all there at
            every width and the row keeps what you read rather than what you
            press. */}
        <PushButton
          label="⋮"
          accessibilityLabel="more controls"
          colour={C.accentText}
          lit={menu || (api && !reachable)}
          onPress={() => setMenu(true)}
          style={styles.dots}
        />
      </View>

      <Modal
        visible={menu}
        transparent
        animationType="fade"
        onRequestClose={() => setMenu(false)}>
        <Pressable style={styles.menuBackdrop} onPress={() => setMenu(false)} />
        {/* pinned to the corner rather than measured against the ⋮: the button
            is always in that corner, so there is nothing for a measurement to
            tell us that the corner does not. */}
        <View style={styles.menu}>
          <Pressable
            accessibilityRole="button"
            disabled={busy || !api}
            onPress={() => {
              setMenu(false);
              reconnect();
            }}
            style={styles.menuRow}>
            <Text
              style={[
                styles.menuText,
                api && !reachable && styles.menuHot,
                (busy || !api) && styles.menuOff,
              ]}>
              reconnect
            </Text>
            {busy && <ActivityIndicator size="small" color={C.accentText} />}
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ selected: mirror }}
            onPress={() => {
              setMenu(false);
              setMirror((m) => !m);
            }}
            style={styles.menuRow}>
            <Text style={[styles.menuText, mirror && styles.menuHot]}>Push mirror</Text>
            {mirror && <Text style={styles.menuTick}>✓</Text>}
          </Pressable>
          {/* stays open while you type: this is the one row that is not a
              press, and closing on every keystroke would be unusable */}
          <View style={styles.menuField}>
            <Text style={styles.menuLabel}>host</Text>
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
        </View>
      </Modal>

      {mirror ? (
        <View style={styles.mirror}>
          <PushMirror
            base={base}
            surface={surface}
            macros={macros}
            onTab={(i) => press({ tab: i })}
            onSeat={(i) => press({ seat: i })}
            press={press}
          />
        </View>
      ) : narrow ? (
        // The rail and the pads panel are both fixed-width furniture the
        // column cannot shrink to fit, so below `mid` the whole thing is one
        // scrollable column and the pads keep their own width behind a
        // sideways scroll scoped to that block.
        <ScrollView contentContainerStyle={styles.bodyNarrow}>
          {noAgent || (
            <Pane
                data={data}
                opts={opts}
                question={surface.question}
                cols={cols}
                current={surface.current}
                onSeat={(i) => press({ seat: i })}
                onAnswer={(k, text) => press({ answer: k, ...(text ? { answer_text: text } : {}) })}
                base={base}
                onSent={refresh}
                mode={mode}
                onMode={(i) => setLocalModes((prev) => ({ ...prev, [viewIdx]: i }))}
                reachable={reachable}
                onComposerFocus={composerFocus}
                onQueue={() => showPanel('queue', true)}
                skills={catalog.skills}
                place={place}
                subs={subs}
                onSub={(i) => press({ sub: i })}
              />
          )}
          {showPads &&
            (ready ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                <Pads
                base={base}
                cwd={hereCwd}
                kind={data.kind}
                data={data}
                opts={opts}
                onAnswer={(k, text) => press({ answer: k, ...(text ? { answer_text: text } : {}) })}
                macros={macros}
                labels={labels}
                promptRoot={place?.root || ''}
                promptPath={place?.path || ''}
                panel={panel}
                onPanel={showPanel}
                catalog={catalog}
                onEntry={(kind, row, scope) => setEntry({ kind, row, scope })}
                onNewPad={newPad}
                sel={sel}
                editing={editing && sel !== null}
                onPress={tapPad}
                onExecute={firePad}
                onEdit={editPad}
                onSave={savePad}
                onClear={clearPad}
                onAddLabel={addLabel}
                onDelLabel={delLabel}
                onClose={() => setEditing(false)}
                onCollapse={collapsePads}
                queue={queue}
                onQueueChange={changeQueue}
                queueErr={queueErr}
                queueCtl={queueCtl}
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
              onPick={setPick}
              subs={subs}
              scopePath={scopePath}
              base={base}
              onChanged={refresh}
              onOpenSettings={(s) => setSettings(s)}
            />
          )}
          <View style={styles.centre}>
            {noAgent || (
              <Pane
                data={data}
                opts={opts}
                question={surface.question}
                cols={cols}
                current={surface.current}
                onSeat={(i) => press({ seat: i })}
                onAnswer={(k, text) => press({ answer: k, ...(text ? { answer_text: text } : {}) })}
                base={base}
                onSent={refresh}
                mode={mode}
                onMode={(i) => setLocalModes((prev) => ({ ...prev, [viewIdx]: i }))}
                reachable={reachable}
                onComposerFocus={composerFocus}
                onQueue={() => showPanel('queue', true)}
                skills={catalog.skills}
                place={place}
                subs={subs}
                onSub={(i) => press({ sub: i })}
              />
            )}
          </View>
          {/* The prompt library used to hold 344px open whether or not you
              were looking at it -- a third of the screen next to the one thing
              you were actually reading. Collapsed it is a 56px strip of keys:
              still on screen, still one tap from open, and the transcript gets
              the width back. */}
          {showPads &&
            (!ready ? (
              <View style={styles.waiting}>
                <ActivityIndicator color={C.accentText} />
                <Text style={styles.waitingText}>
                  no answer from {host}:{PORT}
                </Text>
              </View>
            ) : padsOpen ? (
              <Pads
                base={base}
                cwd={hereCwd}
                kind={data.kind}
                data={data}
                opts={opts}
                onAnswer={(k, text) => press({ answer: k, ...(text ? { answer_text: text } : {}) })}
                macros={macros}
                labels={labels}
                promptRoot={place?.root || ''}
                promptPath={place?.path || ''}
                panel={panel}
                onPanel={showPanel}
                catalog={catalog}
                onEntry={(kind, row, scope) => setEntry({ kind, row, scope })}
                onNewPad={newPad}
                sel={sel}
                editing={editing && sel !== null}
                onPress={tapPad}
                onExecute={firePad}
                onEdit={editPad}
                onSave={savePad}
                onClear={clearPad}
                onAddLabel={addLabel}
                onDelLabel={delLabel}
                onClose={() => setEditing(false)}
                onCollapse={collapsePads}
                queue={queue}
                onQueueChange={changeQueue}
                queueErr={queueErr}
                queueCtl={queueCtl}
              />
            ) : (
              <View style={styles.strip}>
                {PANELS.map(([key, word]) => (
                  <Pressable
                    key={key}
                    accessibilityRole="button"
                    accessibilityLabel={panelCounts[key] == null ? word : `${word} · ${panelCounts[key]}`}
                    accessibilityState={{ selected: panel === key }}
                    onPress={() => showPanel(key, true)}
                    style={[styles.stripKey, panel === key && styles.stripKeyOn]}>
                    <Icon
                      name={key}
                      size={20}
                      color={panel === key ? C.accentText : C.faint}
                    />
                    {/* A zero is a settled fact, not a warning, so it stays
                        off the strip entirely -- a faint "0" and a faint "3"
                        read the same at a glance (MIDI-014). */}
                    {panelCounts[key] > 0 && (
                      <View style={styles.stripBadge}>
                        <Text style={styles.stripBadgeText}>{panelCounts[key]}</Text>
                      </View>
                    )}
                  </Pressable>
                ))}
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
              onPick={(p) => {
                setPick(p);
                if (p) setRailOpen(false);
              }}
              subs={subs}
              scopePath={scopePath}
              base={base}
              onChanged={refresh}
              onOpenSettings={(s) => {
                setSettings(s);
                setRailOpen(false);   // the drawer closes for any other destination too
              }}
            />
          </View>
        </Modal>
      )}

      {entry && (
        <EntrySheet
          kind={entry.kind === 'skills' ? 'skill' : 'hook'}
          row={entry.row}
          initialScope={entry.scope}
          base={base}
          cwd={hereCwd}
          projects={projects}
          labels={labels}
          onClose={() => setEntry(null)}
          onSaved={() => {
            setEntry(null);
            reloadCatalog();
          }}
        />
      )}

      <Settings
        visible={!!settings}
        section={settings}
        onSection={setSettings}
        onClose={() => setSettings(null)}
        base={base}
        cwd={place?.path}
        // every running agent's checkout, as the sidebar's MCP list read them:
        // project- and local-scoped servers only show up for the cwd they are in
        cwds={[...new Set(cols.filter(Boolean).map((c) => c.cwd).filter(Boolean))]}
      />

      {!!note && (
        <View style={styles.toastWrap}>
          <Text style={styles.toast}>{note}</Text>
        </View>
      )}
    </SafeAreaView>
  );
}

// Paths read from home, the way they are said out loud.
const HOME_RE = /^\/Users\/[^/]+/;

// The panels the collapsed strip offers, in the order they sit in it.
// The word is the accessible name; the strip itself draws only the glyph.
const PANELS = [
  ['prompts', 'prompts'],
  ['skills', 'skills'],
  ['hooks', 'hooks'],
  ['queue', 'up next'],
  ['memory', 'memory'],
];

// One tab. The caller decides the label, the count and what "on" means; this
// just draws it. Uppercasing is a style on the drawn Text rather than on the
// string, so the accessible name stays the word as written.
//
// The accessible name lives on the Pressable, not the two Text nodes inside
// it -- nested Text used to leave assistive tech (and the QA recorder)
// landing on whichever node the pointer was actually over, sometimes just
// the count on its own ("· 0", nothing to say what it counted) (MIDI-015).
// The inner Text is aria-hidden so that name isn't read out a second time.
function Tab({ label, count, count2, warn, parts, hue, on, onPress }) {
  const has = (count != null && count > 0) || (count2 != null && count2 > 0);
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={parts ? `${label} · ${parts.map((p) => `${p.n} ${p.word}`).join(', ')}`
        : warn ? `${label} · no repository yet`
        : count2 != null ? `${label} · ${count} changed, ${count2} open pull request${count2 === 1 ? '' : 's'}`
        : count != null ? `${label} · ${count}` : label}
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
        {warn && <Text style={styles.tabWarn}> ⚠</Text>}
        {parts && (
          <Text style={styles.tabCount}>
            {' · '}
            {parts.map((p, i) => (
              <Text key={p.word}>
                {i > 0 && ' | '}
                <Text style={p.n > 0 && { color: p.hue, fontWeight: '700' }}>{p.n}</Text>
              </Text>
            ))}
          </Text>
        )}
        {count != null && (
          <Text style={[styles.tabCount, has && { color: hue, fontWeight: '700' }]}>
            {' '}
            · {count}
            {count2 != null && ` | ${count2}`}
          </Text>
        )}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  spacer: { flex: 1 },
  brand: { color: C.text, fontSize: 16, fontWeight: '600' },
  dot: { width: 8, height: 8, borderRadius: 4 },
  // Quiet on purpose: headless is a supported way to run, not a fault.
  headlessText: { color: C.faint, fontSize: 11, letterSpacing: 0.3 },
  whyBad: { color: C.bad, fontSize: 12, flexShrink: 1, maxWidth: 320 },
  pathText: { color: C.edge, fontSize: 10, flexShrink: 1, ...mono },
  key: { height: S.control, minHeight: S.control, minWidth: 96 },
  dots: { height: S.control, minHeight: S.control, minWidth: S.control, paddingHorizontal: 0 },
  // `tabs` puts 18px of gap between tabs for looks, and a bare Text's hit box
  // stops at its own edge -- so half of every gap was dead, and a click there
  // landed on the row's own View with nowhere to send a /press (MIDI-012).
  tabs: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'stretch',
    alignSelf: 'stretch',
    gap: 18,
    marginLeft: 4,
  },
  // Half of `tabs`' own gap, padded on and pulled back off. Purely a hit-area
  // move: nothing here is visible.
  tabHit: { paddingHorizontal: 9, marginHorizontal: -9 },
  usageKey: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 8, paddingVertical: 4, borderRadius: S.radius, borderWidth: 1, borderColor: C.line },
  usageKeyOn: { borderColor: C.accentText },
  usageWord: { color: C.dim, fontSize: 11, fontWeight: '600', letterSpacing: 1, textTransform: 'uppercase' },
  usageWordOn: { color: C.text },
  usagePct: { fontSize: 11, fontWeight: '700' },
  usagePart: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  usagePartNext: { borderLeftWidth: 1, borderLeftColor: C.line, paddingLeft: 8 },
  tab: {
    fontSize: 13,
    // uppercase and tracked out: the tabs are the one row of chrome that has
    // to read as chrome, not as words in the pane below it
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    borderBottomWidth: 2,
    textAlignVertical: 'center',
    paddingTop: 18,
    paddingHorizontal: 2,
  },
  tabOn: { fontWeight: '600' },
  // A zero here is a settled fact, not a warning, so it stays this quiet by
  // default -- but quiet was all it ever was, and a faint "0" and a faint "3"
  // read the same at a glance (MIDI-014). Tab lifts it to the tab's own hue
  // and bold whenever there is something behind the tab.
  tabCount: { color: C.faint, fontSize: 12 },
  tabWarn: { color: '#e0a03c', fontWeight: '700' },
  // One row, wrapping rather than truncating: at a phone width the lenses drop
  // to a second line, which costs 40px once instead of hiding a control.
  band: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: C.line,
    backgroundColor: C.panel,
  },
  // Below `wide` there are more controls than fit in one line, and wrapping is
  // the whole fix -- it costs nothing at a width where everything already fit.
  bandWrap: { flexWrap: 'wrap', rowGap: 8 },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    height: 32,
    paddingHorizontal: 10,
    borderRadius: 7,
    borderWidth: 1,
    borderColor: KEY.edge,
    backgroundColor: KEY.face,
  },
  chipRepo: { color: C.text, fontSize: 12, fontWeight: '600' },
  chipBranch: { color: C.accentText, fontSize: 11, ...mono },
  chipNone: { color: C.faint, fontSize: 11 },
  menuBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' },
  menu: {
    position: 'absolute',
    top: 58,
    right: 12,
    width: 250,
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    paddingVertical: 6,
  },
  menuRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: S.hit,
    paddingHorizontal: 14,
  },
  menuText: { color: C.text, fontSize: 14, flex: 1 },
  menuHot: { color: C.accentText },
  menuOff: { color: C.edge },
  menuTick: { color: C.accentText, fontSize: 13, fontWeight: '700' },
  menuField: {
    gap: 6,
    paddingHorizontal: 14,
    paddingTop: 8,
    paddingBottom: 4,
    borderTopWidth: 1,
    borderTopColor: C.line,
    marginTop: 4,
  },
  menuLabel: { color: C.faint, fontSize: 11 },
  host: {
    // full width of the menu now, not the 130px it took in the header row
    alignSelf: 'stretch',
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
  // One column below `mid`: the pane sizes to its own content and the page's
  // own scroll carries you from the transcript down through the composer.
  bodyNarrow: { flexGrow: 1 },
  // 56, not 344. The library is still on screen and still one tap from open;
  // what it stops doing is holding a third of the width for a list you are
  // not reading.
  strip: {
    width: 56,
    borderLeftWidth: 1,
    borderLeftColor: C.line,
    backgroundColor: C.panel,
    alignItems: 'center',
    gap: 8,
    paddingVertical: S.pad,
  },
  stripKey: {
    width: 44,
    height: 44,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stripKeyOn: { backgroundColor: C.raised, borderWidth: 1, borderColor: C.edge },
  stripBadge: {
    position: 'absolute',
    top: 1,
    right: 1,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    borderRadius: 8,
    backgroundColor: C.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stripBadgeText: { color: C.text, fontSize: 10, fontWeight: '700' },
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
  // One column below 820: the pane sizes to its own content instead of
  // fighting a flex:1 chain it no longer sits inside, and the page's own
  // scroll -- not a nested one -- carries you from the transcript down
  // through the composer to the pads.
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
