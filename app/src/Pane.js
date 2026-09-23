import React, { useEffect, useRef, useState } from 'react';
import {
  Image,
  Linking,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';

// The three answers to "what should the right-hand column hold". The key in
// the title row names whichever is showing; the menu behind it names them all.
const PANELS = [
  ['prompts', 'prompts'],
  ['skills', 'skills'],
  ['hooks', 'hooks'],
];
const PANEL_NAME = Object.fromEntries(PANELS);
import * as ImagePicker from 'expo-image-picker';
import { MaterialIcons } from '@expo/vector-icons';
import {
  doWork,
  dropGuardrailTemplate,
  getGovern,
  getGuardrails,
  getGuardrailTemplates,
  getPr,
  getTests,
  getWork,
  getWorkDiff,
  getWorkflow,
  listBranches,
  listGoverns,
  listPrs,
  listWorkflows,
  openTerminal,
  ptyWhere,
  saveGovern,
  saveGuardrails,
  saveGuardrailTemplate,
  saveWorkflow,
  switchBranch,
  pasteImage,
  promptAgent,
  reviewPr,
  runTests,
  sendKeys,
  startRecording,
  stopRecording,
  stopTests,
} from './api';
import { GOVERN_STARTERS, TEMPLATES, WORKFLOW_NAME_RE } from './workflows';
import { PHASES, STARTER } from './guardrails';
import reflow from './reflow';
// web only -- xterm needs a DOM, and the native build keeps the scraped view
// rather than being shown an empty box
const Term = Platform.OS === 'web' ? require('./Term').default : null;
import { Menu, MenuButton } from './Menu';
import PushButton from './PushButton';
import { ANSWER_HEX, BREAK, C, S, SEAT_HEX, seatHue, seatWord, mono } from './theme';

// Below BREAK.mid the app is one scrollable column (App.js's own doing),
// so a pane built to fill a flex:1 slot -- transcript included -- would
// collapse to nothing inside it. Every top-level view checks this once and
// swaps flex:1 for auto height, letting the page's single scroll carry the
// whole thing rather than nesting a scroll inside a scroll that has no
// bound to scroll within.
function useNarrow() {
  return useWindowDimensions().width < BREAK.mid;
}

// The rail goes behind a toggle below BREAK.wide, which is a wider band than
// narrow: at 1000 the app is still two columns but the agent list is already
// a drawer. Anything the rail was the only home for has to know that.
function useRailHidden() {
  return useWindowDimensions().width < BREAK.wide;
}

// xterm paints its own chrome, so it is handed the app's colours rather than
// arriving with a black box and a white cursor in the middle of a dark panel.
const TERM_THEME = {
  background: '#0b0b0e',
  foreground: '#d6d6de',
  cursor: '#3cd05a',
  selectionBackground: '#2a3a55',
};

const TEST_HEX = { pass: '#3cd05a', fail: '#e03c3c', run: '#f0c828', '': C.edge };
const CI_HEX = { pass: '#3cd05a', fail: '#e03c3c', pending: '#e0d02c', none: C.edge };

// The centre pane: the active view, drawn at sizes a person reads from a desk
// rather than scaled up off a 960x160 strip meant to be read from a keyboard.
//
// New props, for the composer on the focus view (the contract to wire from
// App.js against):
//   base       - string, server base url (api.baseFor(host)). Required for
//                the composer to send anything; omit it and Send/Send ↵
//                still render but every send rejects, same as any other bad
//                host.
//   onSent     - optional (): void, called after a send lands. The composer
//                clears itself either way it can tell the send worked; this
//                is for the app to refresh sooner than its next poll, not
//                for the composer's own state.
//
// And for the view/mode state App.js now owns rather than reading off the
// Push:
//   mode       - number, which mode of the current view is showing (only
//                the views with more than one care -- today, just usage).
//   onMode     - (i) => void, set that mode. Local to the app; there is no
//                server call, because there is nothing on the Push to move.
//   reachable  - bool, whether push_cc itself answers, as opposed to
//                whether this particular view happens to have data yet.
//                Only the former earns the "waiting for push_cc" text --
//                conflating the two was the dead-tab bug.
//   onComposerFocus - optional (): void, called when the composer's own text
//                box takes focus. The prompt library collapses by default
//                now (App.js), and focusing the box to type is as clear a
//                sign you're about to dispatch as the explicit toggle is --
//                this is how that reaches App.js, which owns the panel.
export default function Pane({
  data,
  opts,
  cols,
  current,
  onSeat,
  onAnswer,
  base,
  onSent,
  mode,
  onMode,
  reachable = true,
  onComposerFocus,
  place,
  subs,
}) {
  const kind = (data || {}).kind;
  // A question takes the glass only where the glass was already about this
  // session. Walk to tests or prs with one pending and the Push keeps drawing
  // tests -- the pads are what answer from wherever you are, and here that is
  // the rail.
  if (opts && opts.length && (!kind || kind === 'focus')) {
    return <Question opts={opts} onAnswer={onAnswer} />;
  }
  if (kind === 'focus')
    return (
      <Focus
        info={data.info}
        seat={data.sub ? null : cols?.[current] || null}
        sub={data.sub}
        cols={cols}
        current={current}
        onSeat={onSeat}
        base={base}
        onSent={onSent}
        onComposerFocus={onComposerFocus}
        place={place}
        subs={subs}
      />
    );
  if (kind === 'sessions') return <Sessions cols={cols} current={current} />;
  if (kind === 'subs') return <Subs data={data} />;
  if (kind === 'tests')
    return <Guardrails data={data} base={base} cwd={place?.path || cols?.[current]?.cwd} />;
  if (kind === 'prs')
    return <Git data={data} base={base} cwd={place?.path || cols?.[current]?.cwd} place={place} />;
  if (kind === 'usage') return <Usage data={data} mode={mode} onMode={onMode} />;
  // Unrecognised or absent data used to say "waiting for push_cc" no matter
  // why it was absent -- including the one mode (focus 2/2) that was simply
  // never modelled server-side, which read as an outage while push_cc ran
  // fine. Now that fallback is reserved for the case it actually describes.
  return (
    <Empty what={reachable ? 'nothing to show for this view yet' : 'waiting for push_cc'} />
  );
}

function Empty({ what }) {
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyText}>{what}</Text>
    </View>
  );
}

function Question({ opts, onAnswer }) {
  const [custom, setCustom] = useState('');
  const customAt = opts.findIndex(([, label]) => /^type something\.?$/i.test(label.trim()));
  const chatAt = opts.findIndex(([, label]) => /^chat about this\.?$/i.test(label.trim()));
  return (
    <ScrollView contentContainerStyle={[styles.body, styles.asking]}>
      <Text style={styles.askHead}>ASKING</Text>
      <Text style={styles.askText}>{opts.length} ways to answer</Text>
      <View style={styles.opts}>
        {opts.map(([num, label], k) => {
          if (k === customAt || k === chatAt) return null;
          const hue = ANSWER_HEX[k % ANSWER_HEX.length];
          return (
            <PushButton
              key={k}
              colour={hue}
              lit
              onPress={() => onAnswer(k)}
              style={[styles.opt, { borderColor: hue }]}>
              <View style={styles.optRow}>
                <Text style={[styles.optNum, { backgroundColor: hue }]}>{num}</Text>
                <Text style={styles.optLabel}>{label}</Text>
              </View>
            </PushButton>
          );
        })}
        {customAt >= 0 && (
          <>
            <View style={styles.orDivider}>
              <View style={styles.orRule} />
              <Text style={styles.orText}>OR</Text>
              <View style={styles.orRule} />
            </View>
            <View style={[styles.customAnswer, { borderColor: ANSWER_HEX[customAt % ANSWER_HEX.length] }]}>
              <TextInput
                value={custom}
                onChangeText={setCustom}
                placeholder="Type your answer"
                placeholderTextColor={C.faint}
                style={styles.customAnswerInput}
                multiline
                autoFocus
              />
              <PushButton
                label="send"
                colour={ANSWER_HEX[customAt % ANSWER_HEX.length]}
                lit={!!custom.trim()}
                disabled={!custom.trim()}
                onPress={() => onAnswer(customAt, custom.trim())}
                style={styles.customAnswerSend}
              />
            </View>
          </>
        )}
        {chatAt >= 0 && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Chat about this"
            onPress={() => onAnswer(chatAt)}
            style={styles.chatAbout}>
            <Text style={styles.chatAboutText}>Chat about this</Text>
          </Pressable>
        )}
      </View>
    </ScrollView>
  );
}

function Focus({
  info,
  seat,
  sub,
  cols = [],
  current,
  onSeat,
  base,
  onSent,
  onComposerFocus,
  place,
  subs = [],
}) {
  // The focus view has two things to show about one agent: what it is saying,
  // and what it has dispatched. A sub-tab rather than a second view, because
  // both are this agent -- switching views to see its subagents means leaving
  // the agent to look at it.
  const [tab, setTab] = useState('pretty');
  const [receipt, setReceipt] = useState(null);
  const [typing, setTyping] = useState(false);
  const [opened, setOpened] = useState('');
  // A real terminal where there can be one. Everything below it -- the
  // capture, the key box, the pad row -- exists only for the build that
  // cannot have this, and is drawn only there.
  const live = !!Term && !!seat?.tid;

  // Follow the terminal. tmux keeps the active pane per window and shares it
  // between clients, so the app cannot steer it without moving the cursor in
  // whatever else is attached -- clicking is tmux's business. What the app can
  // do is notice: when the pane under the app's own client turns out to be a
  // different agent's, the focus moves to that agent, and the banner, CI/CD
  // and guardrails follow it because they all read the focused checkout.
  useEffect(() => {
    if (!live || tab !== 'terminal' || !base || !seat?.tid) return undefined;
    let watching = true;
    const look = () =>
      ptyWhere(base, seat.tid)
        .then(({ pane }) => {
          if (!watching || !pane || pane === seat.tid) return;
          const at = (cols || []).findIndex((col) => col && col.tid === pane);
          if (at >= 0 && at !== current) onSeat?.(at);
        })
        .catch(() => {});
    const timer = setInterval(look, 1500);
    return () => { watching = false; clearInterval(timer); };
  }, [live, tab, base, seat?.tid, cols, current, onSeat]);
  // Straight to the pane, not through the composer: no echo of our own, no
  // submit decision, nothing kept. The pane's next scrape is the feedback.
  //
  // One request at a time, and everything typed while one is in flight rides
  // the next. A fetch per key looked fine and was not: they are concurrent,
  // so they land in whatever order the network settles on, and typing
  // "echo terminal-is-live" put "ech toremnial-is-live" in the pane. Typing
  // is a stream, and a stream has exactly one order.
  // Same checkout, not merely the same repo: a worktree is the branch and the
  // files, and two agents on different branches of one project are not
  // working on the same thing at all.
  const mates = (cols || [])
    .map((col, at) => ({ col, at }))
    .filter(({ col }) => col && seat?.cwd && col.cwd === seat.cwd);

  const waiting = useRef('');
  const inFlight = useRef(false);
  const drain = () => {
    if (inFlight.current || !waiting.current || !seat?.tid) return;
    const batch = waiting.current;
    waiting.current = '';
    inFlight.current = true;
    sendKeys(base, seat.tid, batch)
      .catch(() => {})
      .finally(() => { inFlight.current = false; drain(); });
  };
  const key = (bytes) => {
    waiting.current += bytes;
    drain();
  };
  const [sentPrompts, setSentPrompts] = useState([]);
  const [clearArmed, setClearArmed] = useState(false);
  const [contextPick, setContextPick] = useState(null);
  const clearTimer = useRef(null);
  // Tapped compact, and the pane has not said "Compacting" yet. Bridges the
  // second or two before the scrape shows it; the timeout is only a backstop.
  const [compactAsked, setCompactAsked] = useState(false);
  const compactingNow = (info?.lines || []).some((l) => /Compacting conversation/.test(l));
  useEffect(() => {
    if (compactingNow) setCompactAsked(false);
  }, [compactingNow]);
  useEffect(() => {
    if (!compactAsked) return undefined;
    const timer = setTimeout(() => setCompactAsked(false), 20000);
    return () => clearTimeout(timer);
  }, [compactAsked]);
  const narrow = useNarrow();
  const railHidden = useRailHidden();
  useEffect(() => {
    setSentPrompts([]);
    setReceipt(null);
    setClearArmed(false);
    setCompactAsked(false);
  }, [info?.tid]);
  useEffect(() => () => clearTimeout(clearTimer.current), []);
  // every hook above this line: React counts them per render, and an early
  // return placed among them means "no agent selected" renders fewer than the
  // next render does -- which is a hard crash, not a warning, the moment an
  // agent arrives. Symptom: a blank app and "Rendered more hooks than during
  // the previous render".
  if (!info) return <Empty what="no agent selected" />;
  const lines = info.lines || [];
  const { turns: chat, doing } = chatFromTerminal(lines, info.tldr || [], sentPrompts);
  const contextPct = Math.round(Math.max(0, Math.min(1, Number(info.context || 0))) * 100);
  // The rail card and these selectors read the same polled seat record. The
  // focus payload still provides a fallback for subagents and older servers.
  // "working" is the agent's own status, with act as the fallback for a
  // subagent view that has no seat behind it. Not derived from whether text
  // has stopped arriving: a long tool call looks exactly like a finished
  // answer from out here, and guessing wrong either way is worse than asking.
  const working = (seat?.status || info.status) === 'working' || (!!info.act && !!doing.length);
  const currentModel = seat?.model || info.model || '';
  const currentEffort = seat?.effort || info.effort || '';
  const runContextCommand = (command) =>
    promptAgent(base, command, true, info.tid).then(() => onSent && onSent()).catch(() => {});
  const clearContext = () => {
    if (clearArmed) {
      clearTimeout(clearTimer.current);
      setClearArmed(false);
      runContextCommand('/clear');
      return;
    }
    setClearArmed(true);
    clearTimeout(clearTimer.current);
    clearTimer.current = setTimeout(() => setClearArmed(false), 3000);
  };
  // Status used to be said twice within 200px: once here, once on this same
  // agent's rail card. The rail is the list you scan, so it's the right home
  // for it, and this title already names which agent you're looking at.
  //
  // But the rail is a drawer below BREAK.wide, and then the title is the only
  // home there is -- de-duplicating a fact is only right while both copies are
  // on screen. Say it here exactly when the rail cannot.
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      {/* Two agents in one worktree is the normal case here -- it is why the
          rail exists -- but reaching the other one meant leaving this view for
          it. They are the same repo, the same branch and the same files, so
          they belong to each other more than to anything in the rail, and a
          row of them at the top of the pane is the shortest way between two
          halves of one piece of work.
          Only drawn when there IS another: one tab is a label pretending to
          be a control. */}
      {mates.length > 1 && (
        <View style={styles.mates}>
          {mates.map(({ col, at }) => (
            <Pressable
              key={at}
              accessibilityRole="tab"
              accessibilityState={{ selected: at === current }}
              accessibilityLabel={`${col.name} · ${seatWord(col)}`}
              onPress={() => onSeat?.(at)}
              style={[styles.mate, at === current && styles.mateOn]}>
              <View style={[styles.dot, { backgroundColor: seatHue(col) }]} />
              <Text
                numberOfLines={1}
                style={[styles.mateName, at === current && styles.mateNameOn]}>
                {col.name}
              </Text>
            </Pressable>
          ))}
        </View>
      )}

      <View style={styles.title}>
        <Text style={styles.h1}>{info.name}</Text>
        {railHidden && !!info.status && (
          <Text style={[styles.model, { color: SEAT_HEX[info.status] || C.faint }]}>
            {info.status}
          </Text>
        )}
        {!!sub && <Text style={styles.subTag}>subagent</Text>}
        <Text style={styles.model}>{info.model}</Text>
        <View style={styles.spacer} />
        {/* The progress line lives at the foot of the conversation now, beside
            the tool it is inside, where you are already reading. Up here it
            was a truncated sentence in the corner competing with the agent's
            name -- and once it is in both places they disagree the moment one
            of them is a render behind. Still drawn here for the terminal tab,
            which has no conversation to put it under. */}
        {!!info.act && tab !== 'pretty' && (
          <Text style={styles.act} numberOfLines={1}>
            {info.act}
          </Text>
        )}
      </View>

      {/* This line used to name the checkout as well -- repo and branch, because
          two agents in one repo on different branches is the normal case. The
          banner above every pane says both now, so what is left here is the
          one fact that is about this agent rather than its checkout: which of
          its faces you are looking at. */}
      <View style={styles.place}>
        <View style={styles.spacer} />
        {/* far right of the line that already says where you are, because
            which of this agent's two faces you are looking at is the same
            kind of fact */}
        {[
          ['pretty', 'pretty'],
          ['terminal', 'terminal'],
          ['subagents', `subagents · ${subs.length}`],
        ].map(([key, word]) => (
          <Text
            key={key}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === key }}
            onPress={() => setTab(key)}
            style={[styles.subTab, tab === key && styles.subTabOn]}>
            {word}
          </Text>
        ))}
      </View>

      {/* The terminal is the one view that wants every pixel of height it can
          get -- and it carries its own answer to what this panel says: the
          context line and the model are both on the agent's own status line,
          drawn by Claude Code a few rows down. Two copies of one fact, and the
          copy up here is the one costing the terminal four rows. */}
      {tab !== 'subagents' && tab !== 'terminal' && (
        <View style={styles.contextPanel}>
        <View style={styles.contextBar}>
          <Text style={styles.contextLabel}>Context</Text>
          <View style={styles.contextTrack}>
            <View style={[styles.contextFill, { width: `${contextPct}%` }]} />
          </View>
          <Text style={styles.contextPct}>{contextPct}%</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="compact context"
            hitSlop={6}
            onPress={() => {
              setCompactAsked(true);
              runContextCommand('/compact');
            }}
            style={styles.contextAction}>
            <MaterialIcons name="compress" size={17} color={C.dim} />
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={clearArmed ? 'clear context — tap again to confirm' : 'clear context'}
            hitSlop={6}
            onPress={clearContext}
            style={[styles.contextAction, clearArmed && styles.contextActionArmed]}>
            <MaterialIcons name="delete-sweep" size={18} color={clearArmed ? C.bad : C.dim} />
          </Pressable>
        </View>
        <View style={styles.contextSettings}>
          <Text style={styles.contextSettingLabel}>Model</Text>
          <MenuButton
            label={`${currentModel || 'unknown'} ▾`}
            accessibilityLabel={`choose model, currently ${currentModel || 'unknown'}`}
            onOpen={(anchor) => setContextPick({ kind: 'model', anchor })}
            style={styles.contextSelect}
            glyphStyle={styles.contextSelectText}
          />
          <View style={styles.contextSettingRule} />
          <Text style={styles.contextSettingLabel}>Effort</Text>
          <MenuButton
            label={`${currentEffort || 'unknown'} ▾`}
            accessibilityLabel={`choose effort, currently ${currentEffort || 'unknown'}`}
            onOpen={(anchor) => setContextPick({ kind: 'effort', anchor })}
            style={styles.contextSelect}
            glyphStyle={styles.contextSelectText}
          />
        </View>
        </View>
      )}

      {contextPick && (
        <Menu
          anchor={contextPick.anchor}
          head={contextPick.kind === 'model' ? 'model' : 'effort'}
          onClose={() => setContextPick(null)}
          items={(contextPick.kind === 'model'
            ? ['default', 'opus', 'sonnet', 'haiku']
            : ['low', 'medium', 'high', 'xhigh', 'max']
          ).map((value) => ({
            label: `${value}${
              (contextPick.kind === 'model' ? currentModel : currentEffort)?.startsWith(value)
                ? ' · current'
                : ''
            }`,
            onPress: () => runContextCommand(`/${contextPick.kind} ${value}`),
          }))}
        />
      )}

      {tab === 'subagents' && (
        <View style={[styles.transcript, narrow && styles.transcriptNarrow]}>
          <View style={styles.title}>
            <Text style={styles.head}>SUBAGENTS</Text>
          </View>
          <ScrollView contentContainerStyle={styles.rows}>
            {subs.map((r, i) => (
              <View key={i} style={styles.row}>
                <View
                  style={[
                    styles.dot,
                    { backgroundColor: r.running ? '#f0c828' : '#3cd05a' },
                  ]}
                />
                <View style={styles.rowBody}>
                  <Text style={styles.rowName}>{r.label}</Text>
                  <Text style={styles.rowSub}>
                    {r.type} · {r.running ? 'running' : 'returned'}
                  </Text>
                </View>
                {!!r.focused && <Text style={styles.tag}>focused</Text>}
              </View>
            ))}
            {!subs.length && <Empty what="no subagents spawned yet" />}
          </ScrollView>
        </View>
      )}

      {/* Composer stays reachable at every width, so the transcript is what
          gives -- below 820 it stops being a bounded scroll box of its own
          (there is no flex:1 ancestor to bound it inside) and just lays its
          lines into the page, which is one long scroll by then anyway. */}
      {tab === 'pretty' && (
      <View style={[styles.transcript, styles.pretty, narrow && styles.transcriptNarrow]}>
        <ScrollView contentContainerStyle={styles.chat}>
          {chat.map((turn, i) => (
            <View key={`${turn.role}-${i}`} style={turn.role === 'user' ? styles.userTurn : styles.agentTurn}>
              <Text style={styles.speaker}>{turn.role === 'user' ? 'You' : info.name}</Text>
              <Markdown text={turn.text} />
              {turn.role === 'agent' && (
                <View style={styles.turnActions}>
                  {turn.work.length > 0 && (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`show work receipt for ${info.name}'s response`}
                      onPress={() => setReceipt(turn)}
                      style={styles.receiptKey}>
                      <Text style={styles.receiptKeyText}>work receipt · {turn.work.length}</Text>
                    </Pressable>
                  )}
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`show ${info.name}'s response in the terminal`}
                    onPress={() => setTab('terminal')}
                    style={styles.receiptKey}>
                    <Text style={styles.receiptKeyText}>terminal</Text>
                  </Pressable>
                </View>
              )}
            </View>
          ))}
          {(compactAsked || compactingNow) ? (
            <Working act="compacting context…" doing={[]} name={info.name} />
          ) : working && <Working act={info.act} doing={doing} name={info.name} />}
          {!chat.length && !working && !compactAsked && !compactingNow && <Empty what="Send a prompt to begin" />}
        </ScrollView>
      </View>
      )}

      {tab === 'terminal' && (
      <View style={[styles.transcript, narrow && styles.transcriptNarrow]}>
        <View style={styles.title}>
          <Text style={styles.head}>TERMINAL</Text>
          <Text style={[styles.dim, (live || typing) && { color: '#3cd05a' }]}>
            {live ? 'live' : typing ? 'live — keys go to the pane' : 'tap to type'}
          </Text>
          <View style={styles.spacer} />
          {info.scroll > 0 && (
            <Text style={styles.dim}>{info.scroll} back</Text>
          )}
          {/* Everything this view cannot be: colour, a cursor, the mouse,
              scrollback. None of it is fixable here -- capture-pane strips the
              first and polling rules out the second -- and none of it needs
              fixing on a desktop, where tmux is already running and the real
              terminal is one attach away. Web only, because the native iPad
              build is the case that genuinely has no terminal to open. */}
          {Platform.OS === 'web' && !!seat?.tid && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="open this pane in a real terminal"
              onPress={() =>
                openTerminal(base, seat.tid)
                  .then((said) => setOpened(String(said || 'opened').slice(0, 90)))
                  .catch((e) => setOpened(e.message.slice(0, 120)))}
              style={styles.reviewedKey}>
              <Text style={styles.reviewedKeyText}>open in Terminal ↗</Text>
            </Pressable>
          )}
        </View>
        {!!opened && <Text style={styles.caption}>{opened}</Text>}
        {live && (
          <Term base={base} pane={seat.tid} theme={TERM_THEME} />
        )}
        {/* tmux capture-pane has already wrapped every line to the real
            pane's width, not this box's -- laying that text into a narrower
            column wraps it again, raggedly, which is not what the terminal
            being mirrored looks like. Each line stays on the row tmux gave
            it (no wrap, no shrink) and the extra width scrolls sideways
            instead, inside this ScrollView alone -- it never reaches the
            page, which still only scrolls the one way at any size. */}
        {!live && (
        <ScrollView
          contentContainerStyle={styles.lines}
          style={narrow ? styles.linesNarrow : undefined}
          scrollEnabled={!narrow}>
          <ScrollView horizontal style={styles.linesH} contentContainerStyle={styles.linesHContent}>
            {lines.slice(-60).map((line, i) => (
              <Text key={i} style={styles.line}>
                {line}
              </Text>
            ))}
          </ScrollView>
        </ScrollView>
        )}
        {/* The capture is a picture of the pane; this is the keyboard on it.
            A TextInput rather than key handlers on the View because it is the
            one thing that reliably has focus and receives hardware keys on
            both of the places this runs -- a browser and an iPad with a
            keyboard attached. It renders nothing: whatever lands in it is
            forwarded and the value goes straight back to empty, so the pane's
            own echo is the only thing you ever read. */}
        {!live && (
        <TextInput
          value=""
          onChangeText={(t) => t && key(t)}
          onKeyPress={({ nativeEvent }) => {
            const bytes = KEY_BYTES[nativeEvent.key];
            if (bytes) key(bytes);
          }}
          onFocus={() => setTyping(true)}
          onBlur={() => setTyping(false)}
          autoCapitalize="none"
          autoCorrect={false}
          spellCheck={false}
          accessibilityLabel="terminal keyboard — typing goes to the pane"
          style={[styles.termKeys, typing && styles.termKeysOn]}
        />
        )}
        {!live && (
        <View style={styles.termPads}>
          {KEY_PADS.map(([label, bytes]) => (
            <Pressable
              key={label}
              accessibilityRole="button"
              onPress={() => key(bytes)}
              style={styles.termPad}>
              <Text style={styles.termPadText}>{label}</Text>
            </Pressable>
          ))}
        </View>
        )}
      </View>
      )}

      <Modal visible={!!receipt} transparent animationType="fade" onRequestClose={() => setReceipt(null)}>
        <Pressable style={styles.modalBack} onPress={() => setReceipt(null)}>
          <Pressable style={styles.receipt} onPress={() => {}}>
            <View style={styles.title}>
              <Text style={styles.receiptTitle}>Work receipt</Text>
              <View style={styles.spacer} />
              <Text accessibilityRole="button" onPress={() => setReceipt(null)} style={styles.receiptClose}>close</Text>
            </View>
            <ScrollView contentContainerStyle={styles.receiptLines}>
              {(receipt?.work || []).map((line, i) => (
                <Text key={i} style={styles.receiptLine}>{line}</Text>
              ))}
            </ScrollView>
            <Pressable
              accessibilityRole="button"
              onPress={() => { setReceipt(null); setTab('terminal'); }}
              style={styles.terminalKey}>
              <Text style={styles.terminalKeyText}>Open full terminal</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Claude Code's own queue, not ours: typed at the keyboard while the
          agent was busy. Read-only on purpose -- once a message is in there a
          pty cannot reach it, so offering an edit would be a lie. Ours is the
          one in the composer, which never leaves. */}
      {!!(info.queued || []).length && (
        <View style={styles.card}>
          <Text style={styles.head}>QUEUED AT THE KEYBOARD — NOT EDITABLE HERE</Text>
          {info.queued.map((q, i) => (
            <Text key={i} style={styles.queuedLine} numberOfLines={3}>
              {q}
            </Text>
          ))}
        </View>
      )}

      {/* The terminal is a terminal: it has its own line, its own history and
          its own idea of what Enter means, so a second box underneath that
          also sends to the same pane is two prompts for one cursor. It comes
          back with the pretty view, which is the one that needs it. */}
      {tab !== 'terminal' && (
        <Composer
          info={info}
          base={base}
          onSent={onSent}
          onPromptSent={(text) => setSentPrompts((items) => [...items, text])}
          onComposerFocus={onComposerFocus}
        />
      )}
    </View>
  );
}

// A TLDR row is scraped, so it arrives as the markdown the agent wrote:
// leading bullet, **bold** labels, `code` spans. Drawn raw those markers are
// punctuation in the way of the sentence.
// ponytail: bold and code only, which is all a TLDR actually uses -- an
// unmatched or unknown marker just falls through as the text it already is.
const SPAN = /(\*\*[^*]+\*\*|`[^`]+`)/g;

function Rich({ line, style }) {
  return (
    <Text style={[styles.tldrText, style]}>
      {line
        .replace(/^\s*[-*\u2022]\s+/, '')  // the tick is the bullet now
        .split(SPAN)
        .filter(Boolean)
        .map((part, i) => {
          if (part.startsWith('**') && part.endsWith('**'))
            return (
              <Text key={i} style={styles.tldrBold}>
                {part.slice(2, -2)}
              </Text>
            );
          if (part.startsWith('`') && part.endsWith('`'))
            return (
              <Text key={i} style={styles.tldrCode}>
                {part.slice(1, -1)}
              </Text>
            );
          return part;
        })}
    </Text>
  );
}

const TLDR_HEAD = /^[*#\s]*TL;?DR\b/i; // push_cc._TLDR_RE, which picks the summary
const WORK_START = /^(Bash|Read|Write|Edit|Update|Search|Glob|Grep|Task|Web Search|Web Fetch|Skill|mcp[_:])/i;
// ⎿ Tip: hangs under the spinner, not under a tool, and "Ran 5 shell
// commands" is a collapsed tool group -- indented under a prompt, it was
// being read as the end of what you typed.
const CHROME = /^[─━]+$|^\[PONYTAIL\]|^⏵⏵ auto mode|^(esc to interrupt|shift\+tab to cycle|ctrl\+|tokens:|context:)|^⎿\s*Tip:|^(Ran|Read|Searched for|Edited|Wrote|Listed) \d+ [a-z ]+$/i;

function chatFromTerminal(lines, summary, sentPrompts) {
  const turns = [];
  let work = [];
  // Where the summary goes: after whatever turns came before its heading.
  // It is the last TLDR on screen, which mid-turn is the previous answer's --
  // appended at the end it sat under the prompt you had just sent.
  let summaryAt = -1;
  for (let i = 0; i < lines.length; i += 1) {
    const raw = String(lines[i] || '');
    const clean = raw.trim();
    if (TLDR_HEAD.test(clean)) summaryAt = turns.length;
    if (!clean || CHROME.test(clean)) {
      // a blank line inside an answer is the agent's own paragraph break --
      // the one newline in a capture that means what it says
      const open = turns[turns.length - 1];
      if (!clean && open && !work.length) open.rows.push('');
      continue;
    }
    if (clean.startsWith('❯')) {
      const text = clean.slice(1).trim();
      // the bare ❯ is the empty input box under the spinner, not a turn --
      // resetting on it threw away the tool call the agent is inside
      if (!text) continue;
      turns.push({ role: 'user', rows: [text], work: [] });
      work = [];
      continue;
    }
    if (clean.startsWith('⏺')) {
      const text = clean.slice(1).trim();
      const isWork = WORK_START.test(text) || String(lines[i + 1] || '').trim().startsWith('⎿');
      if (isWork) {
        work.push(text);
        continue;
      }
      if (text) turns.push({ role: 'agent', rows: [text], work: [...work] });
      work = [];
      continue;
    }
    // The running tool's ⏺ blinks, so every other scrape draws its header
    // bare and indented -- which read as one more line of the answer above,
    // and the last message flickered between two texts while it worked. A
    // line with tool output hanging under it is a tool call, dot or no dot.
    if (raw.startsWith(' ') && String(lines[i + 1] || '').trim().startsWith('⎿')) {
      work.push(clean);
      continue;
    }
    if (clean.startsWith('⎿') || (work.length && raw.startsWith(' '))) {
      work.push(clean.replace(/^⎿\s*/, ''));
      continue;
    }
    // a wrapped prompt continues indented under its caret, exactly like an
    // answer does -- dropping those rows left a truncated "You" that the sent
    // copy below no longer matched, so one message showed up as two
    const last = turns[turns.length - 1];
    if (last && !work.length && raw.startsWith(' ')) last.rows.push(clean);
  }
  for (const turn of turns) turn.text = reflow(turn.rows);
  // the pane wraps and the transcript does not, so compare on words
  const words = (t) => String(t).split(/\s+/).join(' ').trim();
  for (const text of sentPrompts) {
    if (!turns.some((turn) => turn.role === 'user' && words(turn.text) === words(text)))
      turns.push({ role: 'user', rows: [text], text, work: [] });
  }
  // the TLDR arrives wrapped by the same pane, so its bullets need the same
  // rejoining -- a bullet broken over three rows was three bullets
  const final = reflow(summary.filter((line) => !CHROME.test(String(line).trim())))
    .replace(/^\s*[-*•]\s+/gm, '')
    .trim();
  // No heading on screen means push_cc fell back to "the last ⏺ block",
  // which the turns above already are -- or, mid-turn, the running tool
  // call, which then showed up as a message of its own.
  if (final && summaryAt >= 0) {
    const at = summaryAt;
    const owner = turns.slice(0, at).reverse().find((turn) => turn.role === 'agent');
    // compared bare: the answer keeps its "- " bullets and the summary has
    // them stripped, so a plain includes() never matched and showed it twice
    const bare = (t) => String(t).replace(/^\s*[-*•]\s+/gm, '').split(/\s+/).join(' ').trim();
    if (!owner || !bare(owner.text).includes(bare(final)))
      turns.splice(at, 0, { role: 'agent', rows: [final], text: final, work: [] });
  }
  // Work collected after the last finished answer is the work happening NOW --
  // the tool call the agent is inside. It used to be dropped on the floor
  // because no `⏺` ever came to close it, which is exactly the moment you most
  // want to see it.
  return { turns: turns.slice(-20), doing: work };
}

// What the agent is doing right now, at the bottom of the conversation where
// you are already looking -- not a word in the title bar.
//
// Three facts, in the order they answer "is this thing alive": a dot that
// moves, Claude Code's own progress line (the `✻` line, which already carries
// the elapsed seconds and the token count), and the tool call it is inside.
// The last of those is the only terminal detail this view shows on purpose:
// everything else stays behind the work receipt.
function Working({ act, doing, name }) {
  const [lit, setLit] = useState(true);
  useEffect(() => {
    // a plain interval rather than Animated: it is one dot, it does not need
    // a driver, and this runs identically on the web build and the iPad
    const timer = setInterval(() => setLit((on) => !on), 600);
    return () => clearInterval(timer);
  }, []);
  const now = doing[doing.length - 1] || '';
  return (
    <View style={styles.working}>
      <View style={styles.workingHead}>
        <View style={[styles.workingDot, !lit && styles.workingDotOff]} />
        <Text style={styles.speaker}>{name}</Text>
        <Text numberOfLines={1} style={styles.workingAct}>
          {act || 'working…'}
        </Text>
      </View>
      {!!now && (
        <Text numberOfLines={2} style={styles.workingNow}>{now}</Text>
      )}
      {doing.length > 1 && (
        <Text style={styles.workingMore}>
          and {doing.length - 1} more step{doing.length === 2 ? '' : 's'} in this turn
        </Text>
      )}
    </View>
  );
}

function Markdown({ text }) {
  const rows = String(text || '').split('\n');
  return (
    <View style={styles.markdown}>
      {rows.map((line, i) => {
        if (/^#{1,3}\s/.test(line))
          return <Rich key={i} line={line.replace(/^#{1,3}\s+/, '')} style={styles.mdHead} />;
        // reflow already decided this is a heading; drawing it as body text
        // would put a bare shouty word in the middle of a paragraph run
        if (/^[A-Z][A-Z0-9 ]{1,14}$/.test(line.trim()))
          return <Rich key={i} line={line.trim()} style={styles.mdShout} />;
        if (/^\s*[-*•]\s+/.test(line))
          return <View key={i} style={styles.mdBullet}><Text style={styles.mdBulletMark}>•</Text><Rich line={line.replace(/^\s*[-*•]\s+/, '')} /></View>;
        return <Rich key={i} line={line} />;
      })}
    </View>
  );
}

// The only way to talk to an agent used to be a macro pad -- fixed text,
// picked in advance. This is the other half: whatever you type or say, right
// now.
//
// One Send, and it submits. There used to be two, because staging a prompt in
// the pane and firing it are different acts. That distinction died when this
// box started mirroring the pane's own input line: staging now writes text
// the composer immediately reads back, so the button that did it looked like
// it had done nothing.
function Composer({ info, base, onSent, onPromptSent, onComposerFocus }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState('');

  // Dictation types into the agent's own input line, not into this box, and
  // a macro loads text there too. Both used to be shown beside the composer
  // as a card you could read and not touch, which left the one editable
  // field on the view empty while the words you had just said sat next to it.
  // So adopt that line instead of displaying it.
  //
  // On change, not on every poll: the pane is scraped twice a second and
  // re-seeding from it continuously would fight your typing. And only into a
  // box that is empty or still holds exactly what we last put there, so an
  // edit in progress survives a scrape that comes back different.
  const [rec, setRec] = useState('');   // '' | 'on' | 'busy'
  const [shot, setShot] = useState(false);
  const [armed, setArmed] = useState(false);
  // What attachFile/attach have actually saved so far: the path is what the
  // agent reads (it's what lives in text, below), the url is the whole data
  // URL or local uri -- kept only so the thumbnail has something to draw,
  // never sent anywhere.
  const [attachments, setAttachments] = useState([]);
  const seen = useRef((info.pending || '').trim());
  useEffect(() => {
    const pend = (info.pending || '').trim();
    if (pend === seen.current) return;
    const was = seen.current;
    seen.current = pend;
    setText((t) => (t === '' || t === was ? pend : t));
  }, [info.pending]);

  // a question is answered from the opt rows, not typed over -- two ways to
  // answer the same thing could disagree, so while one is open the composer
  // steps back rather than competing with it.
  if (info.opts && info.opts.length) return null;

  const empty = !text.trim();
  // unknown is not idle: an agent with no session yet has nothing to send to
  const busy = info.status !== 'idle';
  const disabled = empty || sending;

  async function send(submit) {
    setArmed(false);
    setSending(true);
    setErr('');
    try {
      const sent = text.trim();
      await promptAgent(base, text, submit, info.tid, true);
      onPromptSent && onPromptSent(sent);
      setText(''); // only on success -- a failed send keeps what you typed
      setAttachments([]); // their paths just left in that text
      onSent && onSent();
    } catch (e) {
      setErr(String((e && e.message) || e));
    } finally {
      setSending(false);
    }
  }

  // Sending into a working agent hands the text to Claude Code's own queue,
  // and from there a pty cannot reach it again -- it is gone, uneditable,
  // until it runs. So hold it here instead and keep it in the box, where it
  // is still yours: armed, not sent. Whatever the text says when the agent
  // goes idle is what goes, which is what "editable" has to mean.
  useEffect(() => {
    if (!armed || sending || info.status !== 'idle') return;
    if (!text.trim()) {          // emptied while waiting: nothing to send
      setArmed(false);
      return;
    }
    send(true);
  }, [armed, info.status, sending]);

  // An image cannot go down a pty, so what goes into the box is the path it
  // was saved to and the agent reads the file. Sent from a ref-free closure so
  // both the paste listener and the button land in the same place.
  // Takes base64, not a file: the web build has a File to read and iOS has no
  // such object, so the split lives in the two callers and the upload is one.
  // url is the whole data URL (web) or local uri (iOS) -- attachFile/pick
  // already have it in hand before they strip it down to base64, and it is
  // the one thing a path string cannot show you: what you actually attached.
  async function attach(b64, url) {
    if (!b64) return;
    setShot(true);
    setErr('');
    try {
      const path = await pasteImage(base, b64);
      setText((t) => (t ? `${t.trim()} ${path}` : path));
      setAttachments((a) => [...a, { path, url }]);
    } catch (e) {
      setErr(String((e && e.message) || e));
    } finally {
      setShot(false);
    }
  }

  // Removing a thumbnail has to take its path out of the box too, or the
  // strip and the text disagree about what's attached. The path is a single
  // whitespace-delimited token by construction (attach always joins it on
  // with a space), so pulling that one token out is exact -- it can't eat
  // part of a neighbouring path or anything typed in between.
  function removeAttachment(i) {
    const gone = attachments[i];
    setAttachments((a) => a.filter((_, k) => k !== i));
    if (!gone) return;
    setText((t) =>
      t
        .split(/\s+/)
        .filter((tok) => tok !== gone.path)
        .join(' ')
    );
  }

  function attachFile(file) {
    if (!file) return;
    const r = new FileReader();
    r.onerror = () => setErr('could not read that file');
    r.onload = () => {
      const url = String(r.result);
      // strip the data: prefix -- the server wants base64, not a URL
      attach(url.split(',')[1] || '', url);
    };
    r.readAsDataURL(file);
  }

  // Web only, and deliberately: iOS hands React Native no paste event and no
  // clipboard image without a new dependency. On the tablet the button is the
  // whole story; in a browser cmd-V is what anyone actually reaches for.
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof document === 'undefined') return;
    const onPaste = (e) => {
      const item = [...(e.clipboardData?.items || [])].find((i) =>
        i.type?.startsWith('image/'));
      if (item) {
        e.preventDefault();
        attachFile(item.getAsFile());
      }
    };
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [base]);

  // Two pickers because there are two machines. On the web build it is the
  // browser's file dialog -- react-native-web will not render a raw <input>,
  // so it is built and clicked on the DOM rather than hidden in the tree. On
  // the tablet it is the iOS photo library, which is where the screenshot you
  // just took actually is.
  async function pick() {
    if (Platform.OS === 'web') {
      if (typeof document === 'undefined') return;
      const el = document.createElement('input');
      el.type = 'file';
      el.accept = 'image/*';
      el.onchange = () => attachFile(el.files && el.files[0]);
      el.click();
      return;
    }
    try {
      const ok = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!ok.granted) {
        setErr('midiAI needs photo access to attach an image');
        return;
      }
      const res = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        base64: true,   // bytes: a file:// on the tablet means nothing to the Mac
        quality: 1,
      });
      if (!res.canceled) attach(res.assets[0]?.base64, res.assets[0]?.uri);
    } catch (e) {
      setErr(String((e && e.message) || e));
    }
  }

  // Press and hold, exactly like a pad: the mic runs for as long as your
  // finger is down and the words land in the box above, editable, unsent.
  async function talk(on) {
    if (on) {
      setErr('');
      setRec('on');
      try {
        await startRecording(base);
      } catch (e) {
        setRec('');
        setErr(String((e && e.message) || e));
      }
      return;
    }
    if (rec !== 'on') return;          // a release with no start to match
    setRec('busy');
    try {
      const said = await stopRecording(base);
      // added to what is there rather than over it -- a second thought is
      // still a thought, and the box may already hold a dictated first one
      if (said) setText((t) => (t ? `${t.trim()} ${said}` : said));
    } catch (e) {
      setErr(String((e && e.message) || e));
    } finally {
      setRec('');
    }
  }

  return (
    <View style={styles.composer}>
      {/* All a paste used to leave behind was a filename dropped into the
          text -- no confirmation you attached what you meant, or that it
          worked at all. This is that confirmation, one thumbnail per path
          the text carries. */}
      {attachments.length > 0 && (
        <View style={styles.thumbStrip}>
          {attachments.map((a, i) => (
            <View key={`${a.path}-${i}`} style={styles.thumbWrap}>
              <Image source={{ uri: a.url }} style={styles.thumb} />
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`remove ${a.path}`}
                onPress={() => removeAttachment(i)}
                style={styles.thumbX}>
                <MaterialIcons name="close" size={12} color={C.text} />
              </Pressable>
            </View>
          ))}
        </View>
      )}
      {!!info.suggested && (
        <Text style={styles.head}>PROPOSED — A PROMPT PUT THIS THERE</Text>
      )}
      <TextInput
        style={styles.composerInput}
        value={text}
        onChangeText={setText}
        onFocus={onComposerFocus}
        placeholder="type to the agent"
        placeholderTextColor={C.faint}
        multiline
        editable={!sending}
      />
      {!!err && <Text style={styles.err}>{err}</Text>}
      {/* The box mirrors the agent's own input line so dictation can be edited
          before it goes. Nothing said so, though, so text that arrived on its
          own looked like text you had typed -- and a cursor sitting at the end
          of it meant the next thing you typed joined it into one prompt. Said
          only while it is still untouched: the moment you edit, it is yours
          and the line has nothing left to warn about. */}
      {!!text.trim() && text === seen.current && (
        <Text style={styles.mirrored}>
          this is the agent's own input line, not yours yet — edit it, or clear
          it before typing
        </Text>
      )}
      {/* One row of three dispatch actions, icon-only in every state: a word
          next to a glyph was redundant once the glyph itself changed with the
          state, and dropping it is what let the toggle's real label (prompts
          · count, now up in the title) keep breathing room instead of being
          squeezed to a third of a third of the width. The accessible name
          still carries the word -- PushButton falls back to accessibilityLabel
          because these buttons pass children (the icon) instead of label,
          which is the one thing label doubles as when there's nothing else. */}
      <View style={styles.composerRow}>
        <PushButton
          accessibilityLabel={shot ? 'Saving' : 'Attach image'}
          lit={shot}
          colour={shot ? '#e0a03c' : C.faint}
          disabled={shot || sending}
          onPress={pick}
          style={styles.composerBtn}>
          <MaterialIcons
            name={shot ? 'hourglass-empty' : 'image'}
            size={20}
            color={shot ? C.text : C.dim}
          />
        </PushButton>
        <PushButton
          accessibilityLabel={
            rec === 'on' ? 'Listening' : rec === 'busy' ? 'Transcribing' : 'Hold to talk'
          }
          colour="#e03c3c"
          lit={rec === 'on'}
          disabled={sending || rec === 'busy'}
          onPressIn={() => talk(true)}
          onPressOut={() => talk(false)}
          style={styles.composerBtn}>
          <MaterialIcons
            name={rec === 'busy' ? 'graphic-eq' : 'mic'}
            size={20}
            color={rec !== '' ? C.text : C.dim}
          />
        </PushButton>
        <PushButton
          accessibilityLabel={armed ? 'Cancel queue' : busy ? 'Queue' : 'Send'}
          colour={armed ? '#e0a03c' : C.accent}
          lit={armed || !disabled}
          disabled={sending || (!armed && empty)}
          onPress={() => (armed ? setArmed(false) : busy ? setArmed(true) : send(true))}
          style={styles.composerBtn}>
          <MaterialIcons
            name={armed ? 'close' : busy ? 'schedule' : 'send'}
            size={20}
            color={armed || !disabled ? C.text : C.dim}
          />
        </PushButton>
      </View>
      {armed && (
        <Text style={styles.queued}>
          Queued here, not handed over — it goes when {info.name || 'the agent'} is
          idle, and it sends whatever this box says at that moment.
        </Text>
      )}
    </View>
  );
}

// Every agent side by side. The id tail earns its space: two checkouts of
// one repo show the same name, and it is the only thing that tells them apart.
function Sessions({ cols, current }) {
  const narrow = useNarrow();
  const live = (cols || []).map((c, i) => ({ c, i })).filter((x) => x.c);
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>Every agent</Text>
        {/* Used to be set exactly like a sortable column header -- mono,
            dot-separated field names -- and drew two clicks 711ms apart from
            a tester it couldn't answer. Sorting is a feature nobody asked
            for and there's usually one agent on screen anyway; read as a
            caption instead, in the same voice as `note` below (MIDI-013). */}
        <Text style={styles.caption}>repo, status, model, terminal id</Text>
      </View>
      <View style={[styles.cols, narrow && styles.colsNarrow]}>
        {live.map(({ c, i }) => {
          const hue = seatHue(c);
          return (
            <View
              key={i}
              style={[
                styles.col,
                { borderTopColor: hue },
                i === current && styles.colOn,
                narrow && styles.colNarrow,
              ]}>
              <Text style={styles.colName} numberOfLines={2}>
                {c.name}
              </Text>
              <Text style={[styles.rowSub, { color: hue }]}>{seatWord(c)}</Text>
              <Text style={[styles.rowSub, mono]}>{c.model}</Text>
              <Text style={[styles.rowSub, mono]}>{c.effort}</Text>
              <View style={styles.spacer} />
              <Text style={[styles.colId, mono]}>{c.sub}</Text>
              <View style={styles.ctxTrack}>
                <View
                  style={[
                    styles.ctxFill,
                    { width: `${Math.min(100, (c.context || 0) * 100)}%` },
                  ]}
                />
              </View>
            </View>
          );
        })}
        {live.length === 0 && <Empty what="no agents" />}
      </View>
    </View>
  );
}

function Subs({ data }) {
  const narrow = useNarrow();
  const rows = data.rows || [];
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>{data.repo}</Text>
        <Text style={styles.model}>{rows.length} subagents</Text>
      </View>
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {rows.map((r, i) => (
          <View key={i} style={styles.row}>
            <View
              style={[
                styles.dot,
                { backgroundColor: r.running ? '#f0c828' : '#3cd05a' },
              ]}
            />
            <View style={styles.rowBody}>
              <Text style={styles.rowName}>{r.label}</Text>
              <Text style={styles.rowSub}>
                {r.type} · {r.running ? 'running' : 'returned'}
              </Text>
            </View>
            {!!r.focused && <Text style={styles.tag}>focused</Text>}
          </View>
        ))}
        {rows.length === 0 && <Empty what="no subagents spawned yet" />}
      </ScrollView>
    </View>
  );
}

// The checklist's own tabs. Shipped as the six SDLC phases plus the
// cross-cutting one, but a team's lifecycle is theirs -- so they are editable,
// and a template carries them, because an item filed under a phase the target
// repo has never heard of would arrive in no section at all.
//
// Empty means "whatever the app ships": a team that never touches these keeps
// getting new ones as the framework grows, and only taking ownership stops
// that. That is why the panel writes the whole list the first time it is used.
function PhasesPanel({ phases, items, onPhases }) {
  const [name, setName] = useState('');
  const move = (i, by) => {
    const next = [...phases];
    const to = i + by;
    if (to < 0 || to >= next.length) return;
    [next[i], next[to]] = [next[to], next[i]];
    onPhases(next);
  };
  const set = (i, field, v) =>
    onPhases(phases.map((p, n) => (n === i ? { ...p, [field]: v } : p)));
  const add = () => {
    const label = name.trim();
    if (!label) return;
    // a key the items will be filed under, so it is derived once and never
    // shown -- renaming a phase must not orphan everything in it
    const base = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'phase';
    let key = base;
    for (let n = 2; phases.some((p) => p.key === key); n += 1) key = `${base}-${n}`;
    onPhases([...phases, { key, name: label, constrains: '', what: '' }]);
    setName('');
  };
  return (
    <View style={styles.grAdd}>
      <Text style={styles.grLabel}>the phases this checklist is filed under</Text>
      {phases.map((p, i) => {
        const holding = items.filter((it) => it.phase === p.key).length;
        return (
          <View key={p.key} style={styles.grTplRow}>
            <View style={styles.grPhaseEdit}>
              <TextInput
                value={p.name}
                onChangeText={(v) => set(i, 'name', v)}
                placeholder="phase"
                placeholderTextColor={C.faint}
                style={[styles.wfName, styles.grPhaseField]}
              />
              <TextInput
                value={p.constrains}
                onChangeText={(v) => set(i, 'constrains', v)}
                placeholder="constrains…"
                placeholderTextColor={C.faint}
                style={[styles.wfName, styles.grPhaseField]}
              />
            </View>
            <Text style={styles.hookSub}>{holding}</Text>
            <Pressable accessibilityRole="button" accessibilityLabel={`move ${p.name} up`}
              onPress={() => move(i, -1)} style={styles.reviewedKey}>
              <Text style={styles.reviewedKeyText}>↑</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel={`move ${p.name} down`}
              onPress={() => move(i, 1)} style={styles.reviewedKey}>
              <Text style={styles.reviewedKeyText}>↓</Text>
            </Pressable>
            {/* A phase with guardrails in it does not delete. Removing it would
                file them under a tab that no longer exists, and an item you
                cannot see is worse than one you have to move first -- the edit
                form's phase picker is where you move them. */}
            {holding ? (
              <Text style={styles.grWarn}>holds {holding}</Text>
            ) : (
              <Pressable
                accessibilityRole="button"
                onPress={() => onPhases(phases.filter((_, n) => n !== i))}
                style={styles.reviewedKey}>
                <Text style={styles.reviewedKeyText}>Remove</Text>
              </Pressable>
            )}
          </View>
        );
      })}
      <View style={styles.manageRow}>
        <TextInput
          value={name}
          onChangeText={setName}
          placeholder="a phase of our own"
          placeholderTextColor={C.faint}
          style={[styles.wfName, styles.grTplName]}
        />
        <PushButton label="add phase" disabled={!name.trim()} onPress={add} style={styles.manageBtn} />
      </View>
    </View>
  );
}

// The overview half of guardrails: what is meant to be standing between an
// agent and main, and whether it actually is.
//
// Two fields per item and they are not the same field. `implemented` is what
// the control IS -- the claim. `validate` is how you find out whether the
// claim is true, and it is the one that matters: a checklist of assertions
// nobody can check is the thing it is pretending to protect against. So a row
// does not tick until its own validation is on screen; opening it is the
// gesture, and the tick sits next to the instructions for earning it.
//
// The starter list is a framework, not this repo's opinion. Items can be
// struck out and added, and only that difference is ever stored.
// One item's three fields, in the one form that both writes a new guardrail
// and edits an existing one. Two forms would be two places for the phase
// picker to fall out of step with the phase list.
function GuardrailForm({ form, onForm, onSave, onCancel, onRevert }) {
  return (
    <View style={styles.grAdd}>
      <Text style={styles.grLabel}>{form.isNew ? 'a guardrail of our own' : 'editing'}</Text>
      <View style={styles.walk}>
        {form.phases.map((p) => (
          <Text
            key={p.key}
            onPress={() => onForm({ ...form, phase: p.key })}
            style={[styles.walkAt, form.phase === p.key && styles.walkOn]}>
            {p.name.toLowerCase()}
          </Text>
        ))}
      </View>
      <TextInput
        value={form.title}
        onChangeText={(v) => onForm({ ...form, title: v })}
        placeholder="what the guardrail is, in one line"
        placeholderTextColor={C.faint}
        style={styles.wfName}
      />
      <Text style={styles.grLabel}>the guardrail</Text>
      <TextInput
        value={form.implemented}
        onChangeText={(v) => onForm({ ...form, implemented: v })}
        multiline
        placeholder="what it is and how it is implemented"
        placeholderTextColor={C.faint}
        style={[styles.wfName, styles.grField]}
      />
      <Text style={styles.grLabel}>how to validate it</Text>
      <TextInput
        value={form.validate}
        onChangeText={(v) => onForm({ ...form, validate: v })}
        multiline
        placeholder="a command, a file, or a failure to induce deliberately and watch"
        placeholderTextColor={C.faint}
        style={[styles.wfName, styles.grField]}
      />
      <View style={styles.manageRow}>
        <PushButton
          label={form.isNew ? 'add it' : 'save'}
          colour={C.accentText}
          lit
          disabled={!form.title.trim()}
          onPress={onSave}
          style={styles.manageBtn}
        />
        <PushButton label="cancel" onPress={onCancel} style={styles.manageBtn} />
        <View style={styles.spacer} />
        {/* only when this one is a shipped guardrail we have edited -- there
            is nothing to revert a guardrail of our own to */}
        {!!onRevert && <PushButton label="revert to shipped" onPress={onRevert} style={styles.manageBtn} />}
      </View>
    </View>
  );
}

// The overview half of guardrails: what is meant to be standing between an
// agent and main, and whether it actually is.
//
// Two fields per item and they are not the same field. `implemented` is what
// the control IS -- the claim. `validate` is how you find out whether the
// claim is true, and it is the one that matters: a checklist of assertions
// nobody can check is the thing it is pretending to protect against. So a row
// does not tick until its own validation is on screen; opening it is the
// gesture, and the tick sits next to the instructions for earning it.
//
// The starter list is a framework, not this repo's opinion. Every difference
// from it -- struck out, added, or edited -- is what gets stored, and a
// `custom` entry sharing a shipped item's id shadows it. That is what makes
// editing a shipped guardrail possible without shipping a copy of the whole
// list per repo, and what lets "revert to shipped" simply drop the override.
function Overview({ base, cwd }) {
  const narrow = useNarrow();
  const [state, setState] = useState(null);
  const [open, setOpen] = useState('');
  const [phase, setPhase] = useState('');       // '' = all
  const [q, setQ] = useState('');
  const [err, setErr] = useState('');
  const [form, setForm] = useState(null);       // null | the item being written
  const [tabs, setTabs] = useState(false);      // the phase editor
  const [shelf, setShelf] = useState(null);     // saved templates, or null until asked
  const [tplName, setTplName] = useState('');
  const [sure, setSure] = useState('');         // the template `use` is one tap into

  useEffect(() => {
    if (!base || !cwd) return;
    setOpen('');
    setForm(null);
    getGuardrails(base, cwd)
      .then((got) => { setState(got); setErr(''); })
      .catch((e) => { setState({ checked: {}, custom: [], hidden: [] }); setErr(e.message); });
  }, [base, cwd]);

  // Every write is the whole record. Optimistic, because a checkbox that waits
  // for a round trip before it moves feels broken on a tablet -- and a failed
  // save says so in the error line rather than silently disagreeing with disk.
  const put = (next) => {
    setState(next);
    saveGuardrails(base, cwd, next).then(() => setErr('')).catch((e) => setErr(e.message));
  };
  if (!state) return <Empty what="reading the checklist…" />;

  // untouched means "whatever the app ships", so the shipped list is the
  // fallback rather than a copy written on first load
  const phases = state.phases?.length ? state.phases : PHASES;
  const hidden = new Set(state.hidden || []);
  const shipped = new Map(STARTER.map((it) => [it.id, it]));
  const resolved = new Map(shipped);
  for (const own of state.custom || []) resolved.set(own.id, own);   // ours wins
  const items = [...resolved.values()].filter((it) => !hidden.has(it.id));

  // Anything filed under a phase that is no longer in the list still has to
  // draw: a guardrail nobody can see is the failure mode this whole screen is
  // about. Removing a phase that holds items is blocked, but a template or an
  // older record can still arrive carrying one.
  const known = new Set(phases.map((p) => p.key));
  const orphans = [...new Set(items.filter((it) => !known.has(it.phase)).map((it) => it.phase))];
  const sections = [...phases,
                    ...orphans.map((key) => ({ key, name: key, constrains: 'nothing — this phase is gone' }))];

  const needle = q.trim().toLowerCase();
  const hit = (it) =>
    !needle ||
    // the validation text too, deliberately: "which of these mention gitleaks"
    // is the question you actually arrive with
    `${it.title} ${it.implemented} ${it.validate}`.toLowerCase().includes(needle);
  const shown = items.filter((it) => (!phase || it.phase === phase) && hit(it));

  const done = (id) => !!state.checked?.[id];
  const tally = (list) => list.filter((it) => done(it.id)).length;
  const edited = (id) => (state.custom || []).some((c) => c.id === id) && shipped.has(id);

  const toggle = (id) =>
    put({ ...state, checked: { ...state.checked, [id]: !done(id) } });
  const strike = (id) =>
    put({ ...state,
          hidden: [...(state.hidden || []), id],
          // an added guardrail that is struck is simply gone; a shipped one
          // stays strikable-and-restorable because STARTER still has it
          custom: (state.custom || []).filter((c) => c.id !== id) });

  const commit = () => {
    const title = form.title.trim();
    if (!title) return;
    const id = form.isNew ? `own-${Date.now().toString(36)}` : form.id;
    const rest = (state.custom || []).filter((c) => c.id !== id);
    put({ ...state,
          custom: [...rest, { id, phase: form.phase, title,
                              implemented: form.implemented, validate: form.validate }] });
    setForm(null);
    setOpen(id);
  };
  const revert = () => {
    put({ ...state, custom: (state.custom || []).filter((c) => c.id !== form.id) });
    setForm(null);
  };

  const shelve = () =>
    getGuardrailTemplates(base)
      .then((got) => { setShelf(got.rows || []); setErr(''); })
      .catch((e) => { setShelf([]); setErr(e.message); });

  const keep = async (replace = false) => {
    const name = tplName.trim();
    if (!name) return;
    try {
      // the items, not the ticks -- see the note under the list
      await saveGuardrailTemplate(base, name, items, phases, replace);
      setTplName('');
      setErr('');
      shelve();
    } catch (e) { setErr(e.message); }
  };

  // Applying one replaces the item set: starters it leaves out are struck, and
  // everything it carries that differs from what this build ships -- an added
  // guardrail, an edited one, or one from a version of the app that has since
  // retired it -- is kept from the template's own copy. So a template outlives
  // both an edit and a starter being dropped.
  //
  // Ticks survive by id, so re-applying a list you already follow is a no-op
  // on your assessment rather than a reset of it.
  const apply = (tpl) => {
    const ids = new Set(tpl.items.map((i) => i.id));
    const differs = (i) => {
      const ship = shipped.get(i.id);
      return !ship || ['phase', 'title', 'implemented', 'validate'].some((f) => ship[f] !== i[f]);
    };
    put({
      checked: Object.fromEntries(
        Object.entries(state.checked || {}).filter(([id]) => ids.has(id))),
      custom: tpl.items.filter(differs),
      hidden: STARTER.filter((i) => !ids.has(i.id)).map((i) => i.id),
      // a template made before phases were editable carries none; leaving
      // ours alone is right in that case, not blanking them
      phases: tpl.phases?.length ? tpl.phases : state.phases || [],
    });
    setSure('');
    setOpen('');
  };

  return (
    <>
      {!!err && <Text style={styles.err}>{err}</Text>}
      <View style={styles.manageRow}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={styles.walk}>
            <Text
              onPress={() => setPhase('')}
              style={[styles.walkAt, !phase && styles.walkOn]}>
              all {tally(items)}/{items.length}
            </Text>
            {/* empty ones included: a phase you just added and cannot see
                reads as an add that did not work, and a phase whose every
                guardrail you struck out is a fact worth showing too */}
            {sections.map((p) => {
              const mine = items.filter((it) => it.phase === p.key);
              return (
                <Text
                  key={p.key}
                  onPress={() => setPhase(p.key)}
                  style={[styles.walkAt, phase === p.key && styles.walkOn]}>
                  {p.name.toLowerCase()} {tally(mine)}/{mine.length}
                </Text>
              );
            })}
          </View>
        </ScrollView>
      </View>
      <View style={styles.grTrack}>
        <View style={[styles.grFill, { width: `${items.length ? tally(items) / items.length * 100 : 0}%` }]} />
      </View>
      <TextInput
        value={q}
        onChangeText={setQ}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
        accessibilityLabel="search guardrails"
        placeholder="search guardrails, and how to validate them"
        placeholderTextColor={C.faint}
        style={styles.find}
      />
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {sections.filter((p) => shown.some((it) => it.phase === p.key)).map((p) => (
          <View key={p.key} style={styles.grPhase}>
            <View style={styles.grPhaseHead}>
              <Text style={styles.grPhaseName}>{p.name}</Text>
              {/* the phase's one-line job: constrain intent, constrain
                  architecture, constrain actions... */}
              <Text style={styles.grPhaseWhat}>constrains {p.constrains}</Text>
            </View>
            {shown.filter((it) => it.phase === p.key).map((it) => {
              const on = open === it.id;
              const ticked = done(it.id);
              const writing = form && !form.isNew && form.id === it.id;
              return (
                <View key={it.id} style={[styles.grItem, ticked && styles.grItemOn]}>
                  <Pressable
                    style={styles.grRow}
                    accessibilityRole="button"
                    accessibilityState={{ expanded: on }}
                    onPress={() => setOpen(on ? '' : it.id)}>
                    <Pressable
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked: ticked }}
                      accessibilityLabel={it.title}
                      onPress={() => toggle(it.id)}
                      style={[styles.grBox, ticked && styles.grBoxOn]}>
                      <Text style={styles.grTick}>{ticked ? '✓' : ''}</Text>
                    </Pressable>
                    <Text style={[styles.grTitle, ticked && styles.grTitleOn]}>{it.title}</Text>
                    <View style={styles.spacer} />
                    {!shipped.has(it.id) && <Text style={styles.tag}>ours</Text>}
                    {edited(it.id) && <Text style={styles.tag}>edited</Text>}
                    <Text style={styles.grCaret}>{on ? '−' : '+'}</Text>
                  </Pressable>
                  {on && (writing ? (
                    <GuardrailForm
                      form={form}
                      onForm={setForm}
                      onSave={commit}
                      onCancel={() => setForm(null)}
                      onRevert={edited(it.id) ? revert : null}
                    />
                  ) : (
                    <View style={styles.grBody}>
                      <Text style={styles.grLabel}>the guardrail</Text>
                      <Text style={styles.grText}>{it.implemented}</Text>
                      <Text style={styles.grLabel}>how to validate it</Text>
                      <Text style={styles.grText}>{it.validate}</Text>
                      <View style={styles.manageRow}>
                        <Pressable
                          accessibilityRole="button"
                          onPress={() => toggle(it.id)}
                          style={[styles.reviewedKey, ticked && styles.reviewedKeyOn]}>
                          <Text style={styles.reviewedKeyText}>
                            {ticked ? 'In force ✓' : 'Mark in force'}
                          </Text>
                        </Pressable>
                        <Pressable
                          accessibilityRole="button"
                          onPress={() => setForm({ ...it, phases, isNew: false })}
                          style={styles.reviewedKey}>
                          <Text style={styles.reviewedKeyText}>Edit</Text>
                        </Pressable>
                        <View style={styles.spacer} />
                        {/* struck out, not deleted -- a shipped guardrail this
                            repo has decided against is a decision, and it comes
                            back with "restore struck out" below */}
                        <Pressable
                          accessibilityRole="button"
                          onPress={() => strike(it.id)}
                          style={styles.reviewedKey}>
                          <Text style={styles.reviewedKeyText}>Not for us</Text>
                        </Pressable>
                      </View>
                    </View>
                  ))}
                </View>
              );
            })}
          </View>
        ))}
        {shown.length === 0 && (
          <Empty what={needle ? `nothing matches "${q.trim()}"` : 'no guardrails here'} />
        )}
        {form?.isNew ? (
          <GuardrailForm
            form={form}
            onForm={setForm}
            onSave={commit}
            onCancel={() => setForm(null)}
          />
        ) : (
          <View style={styles.manageRow}>
            <PushButton
              label="＋ guardrail"
              onPress={() => setForm({ id: '', phase: phase || phases[0]?.key || 'cross', title: '', implemented: '', validate: '', phases, isNew: true })}
              style={styles.manageBtn}
            />
            {hidden.size > 0 && (
              <PushButton
                label={`restore ${hidden.size} struck out`}
                onPress={() => put({ ...state, hidden: [] })}
                style={styles.manageBtn}
              />
            )}
            <PushButton
              label={tabs ? 'hide phases' : 'phases'}
              lit={tabs}
              onPress={() => setTabs((t) => !t)}
              style={styles.manageBtn}
            />
            <PushButton
              label={shelf ? 'hide templates' : 'templates'}
              lit={!!shelf}
              onPress={() => (shelf ? setShelf(null) : shelve())}
              style={styles.manageBtn}
            />
          </View>
        )}
        {tabs && (
          <PhasesPanel
            phases={phases}
            items={items}
            onPhases={(next) => put({ ...state, phases: next })}
          />
        )}
        {!!shelf && (
          <View style={styles.grAdd}>
            <Text style={styles.grLabel}>checklists saved for any repo</Text>
            {shelf.map((tpl) => {
              const asking = sure === tpl.name;
              // how much of this repo's own list the template does not carry.
              // Struck-out shipped ones come back, so the loss worth naming is
              // our own additions and edits -- those exist nowhere else.
              const losing = (state.custom || []).filter(
                (c) => !tpl.items.some((i) => i.id === c.id)).length;
              return (
                <View key={tpl.name} style={styles.grTplRow}>
                  <View style={styles.hookNames}>
                    <Text style={styles.rowName}>{tpl.name}</Text>
                    <Text style={styles.hookSub}>
                      {tpl.items.length} guardrails
                      {tpl.phases?.length ? ` · ${tpl.phases.length} phases` : ''}
                      {tpl.made ? ` · saved ${new Date(tpl.made * 1000).toLocaleDateString()}` : ''}
                    </Text>
                  </View>
                  <View style={styles.spacer} />
                  {asking ? (
                    <>
                      <Text style={styles.grWarn}>
                        replaces this list{losing ? ` · loses ${losing} of ours` : ''}
                      </Text>
                      <Pressable
                        accessibilityRole="button"
                        onPress={() => apply(tpl)}
                        style={[styles.reviewedKey, styles.reviewedKeyOn]}>
                        <Text style={styles.reviewedKeyText}>Yes, use it</Text>
                      </Pressable>
                      <Pressable
                        accessibilityRole="button"
                        onPress={() => setSure('')}
                        style={styles.reviewedKey}>
                        <Text style={styles.reviewedKeyText}>No</Text>
                      </Pressable>
                    </>
                  ) : (
                    <>
                      <Pressable
                        accessibilityRole="button"
                        onPress={() => setSure(tpl.name)}
                        style={styles.reviewedKey}>
                        <Text style={styles.reviewedKeyText}>Use</Text>
                      </Pressable>
                      <Pressable
                        accessibilityRole="button"
                        onPress={() =>
                          dropGuardrailTemplate(base, tpl.name).then(shelve).catch((e) => setErr(e.message))}
                        style={styles.reviewedKey}>
                        <Text style={styles.reviewedKeyText}>Delete</Text>
                      </Pressable>
                    </>
                  )}
                </View>
              );
            })}
            {shelf.length === 0 && (
              <Text style={styles.hookSub}>
                None saved yet. Save this one and it is available in every
                other project.
              </Text>
            )}
            <View style={styles.manageRow}>
              <TextInput
                value={tplName}
                onChangeText={setTplName}
                placeholder="name this checklist"
                placeholderTextColor={C.faint}
                style={[styles.wfName, styles.grTplName]}
              />
              <PushButton
                label={`save these ${items.length}`}
                colour={C.accentText}
                lit
                disabled={!tplName.trim()}
                onPress={() => keep(shelf.some((t) => t.name === tplName.trim()))}
                style={styles.manageBtn}
              />
            </View>
            <Text style={styles.hookSub}>
              A template carries the guardrails, not the ticks — which ones a
              team holds itself to travels between repos, and whether each is
              actually in force is a fact about one repo that would be a lie
              anywhere else.
            </Text>
          </View>
        )}
      </ScrollView>
      <Text style={styles.note}>
        Autonomy proportional to blast radius: low-risk reversible work runs
        free, and the controls get deterministic where the damage does. Ticks
        are kept per checkout in `~/.midiai/guardrails.json`, not in the repo.
      </Text>
    </>
  );
}

const GUARD_HALVES = ['overview', 'tests'];

function Guardrails({ data, base, cwd }) {
  const narrow = useNarrow();
  const [half, setHalf] = useState(0);
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>guardrails</Text>
        <View style={styles.spacer} />
        <Segmented names={GUARD_HALVES} at={half} onAt={setHalf} />
      </View>
      {half === 0 ? <Overview base={base} cwd={cwd} /> : <Tests data={data} base={base} cwd={cwd} bare />}
    </View>
  );
}

function Tests({ data, base, cwd, bare }) {
  const narrow = useNarrow();
  const [path, setPath] = useState(data.path || []);
  const [live, setLive] = useState(data);
  const [err, setErr] = useState('');
  const load = () => {
    if (!base || !cwd) return;
    getTests(base, cwd, path)
      .then((next) => { setLive(next); setErr(''); })
      .catch((e) => setErr(e.message));
  };
  useEffect(() => {
    setPath([]);
  }, [cwd]);
  useEffect(() => {
    load();
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [base, cwd, path.join('/')]);
  const shown = live || data;
  const items = shown.items || [];
  const act = async (file = '') => {
    setErr('');
    try {
      await runTests(base, cwd, path, file);
      load();
    } catch (e) {
      setErr(e.message);
    }
  };
  const Frame = bare ? React.Fragment : View;
  const frameProps = bare ? {} : { style: [styles.body, narrow && styles.bodyNarrow] };
  return (
    <Frame {...frameProps}>
      <View style={styles.title}>
        <Text style={styles.h1}>tests</Text>
        <Text style={styles.path}>/{path.join('/')}</Text>
        <View style={styles.spacer} />
        {!!shown.running && <Text style={styles.act}>{shown.running}</Text>}
        <Text style={[styles.model, { color: '#3cd05a' }]}>{shown.passed || 0} passed</Text>
        {(shown.failed || 0) > 0 && (
          <Text style={[styles.model, { color: '#e03c3c' }]}>
            {shown.failed} failed
          </Text>
        )}
      </View>
      <View style={styles.manageRow}>
        {path.length > 0 && <PushButton label="back" onPress={() => setPath(path.slice(0, -1))} style={styles.manageBtn} />}
        <PushButton
          label={shown.running ? 'stop' : 'run here'}
          colour={shown.running ? C.bad : '#3cd05a'}
          lit
          onPress={async () => {
            if (shown.running) await stopTests(base, cwd);
            else await act();
            load();
          }}
          style={styles.manageBtn}
        />
        <Text style={styles.caption}>{shown.packages || 0} Vitest/test runner{shown.packages === 1 ? '' : 's'}</Text>
      </View>
      {!!err && <Text style={styles.err}>{err}</Text>}
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {items.map((it, i) => (
          <Pressable
            key={i}
            style={styles.row}
            onPress={() => (it.dir ? setPath([...path, it.name]) : act(it.name))}>
            <View
              style={[styles.chip, { backgroundColor: TEST_HEX[it.state] || C.edge }]}
            />
            <Text style={[styles.rowName, mono]}>
              {it.name}
              {it.dir ? '/' : ''}
            </Text>
            <View style={styles.spacer} />
            <Text style={styles.rowSub}>{it.state || 'not run'}</Text>
          </Pressable>
        ))}
        {items.length === 0 && <Empty what={shown.discovering ? 'finding tests…' : 'no Vitest or test files found here'} />}
      </ScrollView>
      <Text style={styles.note}>
        A directory wears the worst state beneath it — follow red down to the file
        without knowing where it lives.
      </Text>
    </Frame>
  );
}

function parseDiff(raw) {
  const files = [];
  let file = null;
  let oldLine = 0;
  let newLine = 0;
  for (const text of String(raw || '').split('\n')) {
    const start = text.match(/^diff --git a\/(.*?) b\/(.*)$/);
    if (start) {
      file = { path: start[2], lines: [], additions: 0, deletions: 0 };
      files.push(file);
      continue;
    }
    if (!file) continue;
    const hunk = text.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      file.lines.push({ kind: 'hunk', text, old: '', next: '' });
      continue;
    }
    if (text.startsWith('+++ ') || text.startsWith('--- ') || text.startsWith('index ') ||
        text.startsWith('new file ') || text.startsWith('deleted file ') ||
        text.startsWith('similarity ') || text.startsWith('rename ')) {
      file.lines.push({ kind: 'meta', text, old: '', next: '' });
    } else if (text.startsWith('+')) {
      file.lines.push({ kind: 'add', text: text.slice(1), old: '', next: newLine++ });
      file.additions += 1;
    } else if (text.startsWith('-')) {
      file.lines.push({ kind: 'del', text: text.slice(1), old: oldLine++, next: '' });
      file.deletions += 1;
    } else if (text.startsWith(' ')) {
      file.lines.push({ kind: 'same', text: text.slice(1), old: oldLine++, next: newLine++ });
    } else if (text) {
      file.lines.push({ kind: 'meta', text, old: '', next: '' });
    }
  }
  return files;
}

function DiffLine({ line }) {
  return (
    <View style={[styles.diffLine, styles[`diff_${line.kind}`]]}>
      <Text style={styles.diffNumber}>{line.old}</Text>
      <Text style={styles.diffNumber}>{line.next}</Text>
      <Text style={styles.diffMark}>
        {line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '}
      </Text>
      <Text style={styles.diffCode}>{line.text || ' '}</Text>
    </View>
  );
}

// The dot on the list row, spelled out. A red PR is a question -- which check
// -- and reviewing it without the answer meant leaving for a browser.
//
// Only the checks that are still a question get a row: a green run named in
// full says nothing the count does not, and a repo with twenty of them buried
// the PR's own description under a wall of green. What failed, what is still
// running, and a tally for the rest -- each row the link to its own run, so
// the red one is one tap and not a hunt through Actions.
function Checks({ runs }) {
  if (!runs?.length) return null;
  const tally = runs.reduce((n, r) => ({ ...n, [r.state]: (n[r.state] || 0) + 1 }), {});
  return (
    <View style={styles.checks}>
      <Text style={styles.caption}>
        {['fail', 'pending', 'pass'].filter((k) => tally[k]).map((k) => `${tally[k]} ${k}`).join(' · ')}
      </Text>
      {runs.filter((r) => r.state !== 'pass').map((run, i) => (
        <Pressable
          key={i}
          disabled={!run.url}
          onPress={() => Linking.openURL(run.url)}
          style={styles.check}>
          <View style={[styles.chip, { backgroundColor: CI_HEX[run.state] || C.edge }]} />
          <Text numberOfLines={1} style={styles.checkName}>{run.name}</Text>
        </Pressable>
      ))}
    </View>
  );
}

// GIT was already named for the repo's whole state rather than its pull
// requests alone -- these are the rest of that state. Work is what has not
// left this machine yet; pull requests are what is waiting on a person;
// actions are what the push set running; hooks are what runs before a commit
// is even allowed to leave. The tab row lives in each pane's own title row
// rather than a band of its own: a second full-width strip of chrome above
// four panes that each already have a heading is the kind of furniture that
// eats a screen a pixel at a time.
//
// Work leads, and is what GIT now opens on: it is the earliest of the four in
// the life of a change, and the only one whose answer was previously reachable
// only by leaving the app for a terminal.
const GIT_TABS = ['work', 'pull requests', 'actions', 'hooks'];

function GitTabs({ at, onAt }) {
  return (
    <View style={styles.walk}>
      {GIT_TABS.map((name, i) => (
        <Text
          key={name}
          accessibilityRole="tab"
          accessibilityState={{ selected: i === at }}
          onPress={() => onAt(i)}
          style={[styles.walkAt, i === at && styles.walkOn]}>
          {name}
        </Text>
      ))}
    </View>
  );
}

function Git({ data, base, cwd, place }) {
  const [at, setAt] = useState(0);
  const tabs = <GitTabs at={at} onAt={setAt} />;
  // no repo name threaded down any more -- the banner above every pane says
  // it once, and four panes each working it out again was the drift
  if (at === 1) return <Prs data={data} base={base} cwd={cwd} tabs={tabs} />;
  if (at === 2) return <Actions base={base} cwd={cwd} tabs={tabs} />;
  if (at === 3) return <Hooks tabs={tabs} />;
  return <Work base={base} cwd={cwd} tabs={tabs} />;
}

// The first GIT tab, and the only one about work that has not left this
// machine yet. Pull requests, actions and hooks are all questions about a
// server -- what CI said, what a reviewer said, what would have stopped a
// push. This one never leaves the checkout: the working tree, the commits it
// sits on, and the commands that move things between the two.
//
// It is first because it is where work is before it is anywhere else, and
// because the answer to "what am I in the middle of" was previously only
// available by leaving for a terminal.
const WORK_HALVES = ['changes', 'tree'];

// git's own word for what happened to a file, in the app's hues. Untracked is
// deliberately not the green of added: one is a file git is already carrying,
// the other is a file it would happily lose.
const WORK_HEX = {
  modified: '#e0d02c',
  added: '#3cd05a',
  deleted: '#e03c3c',
  renamed: '#4a86d0',
  copied: '#4a86d0',
  typechange: '#e08a2c',
  untracked: '#6b7280',
  conflicted: '#e03c3c',
};

// A row in the file column. The whole row opens the diff; the keys on its
// right act on that one path -- staging file by file is the normal way a
// commit gets built, and a screen that could only stage everything would be
// a screen you still had to leave.
function WorkFile({ row, on, staged, onOpen, onStage, onDiscard }) {
  return (
    <Pressable onPress={onOpen} style={[styles.diffFile, on && styles.diffFileOn]}>
      <View style={[styles.chip, { backgroundColor: WORK_HEX[row.state] || C.edge }]} />
      <Text numberOfLines={1} style={styles.diffFileName}>
        {row.was ? `${row.was} → ${row.path}` : row.path}
      </Text>
      <Text style={styles.wkState}>{row.state}</Text>
      <Text
        accessibilityRole="button"
        accessibilityLabel={`${staged ? 'unstage' : 'stage'} ${row.path}`}
        onPress={onStage}
        style={styles.wkKey}>
        {staged ? 'unstage' : 'stage'}
      </Text>
      {!staged && (
        <Text
          accessibilityRole="button"
          accessibilityLabel={`discard ${row.path}`}
          onPress={onDiscard}
          style={[styles.wkKey, styles.wkDrop]}>
          discard
        </Text>
      )}
    </Pressable>
  );
}

function Work({ base, cwd, tabs }) {
  const narrow = useNarrow();
  const [half, setHalf] = useState(0);
  const [work, setWork] = useState(null);
  // One pick, two shapes: {file, staged} from the changes half, {sha} from the
  // tree. Both end in the same viewer, because a file's changes and a commit's
  // changes are the same question asked of different ranges.
  const [pick, setPick] = useState(null);
  const [diff, setDiff] = useState('');
  const [tick, setTick] = useState(0);
  const [msg, setMsg] = useState('');
  const [amend, setAmend] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [say, setSay] = useState('');
  // Anything that can lose work waits here until it is confirmed. discard and
  // stash drop are the two commands on this screen with no undo -- git keeps
  // no reflog for a change that was never committed.
  const [ask, setAsk] = useState(null);
  const [branches, setBranches] = useState(null);
  const [named, setNamed] = useState('');

  const load = () => {
    if (!base || !cwd) return Promise.resolve();
    return getWork(base, cwd)
      .then((next) => { setWork(next); setErr(''); })
      .catch((e) => setErr(e.message));
  };

  useEffect(() => {
    setWork(null);
    setPick(null);
    setDiff('');
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [base, cwd]);   // eslint-disable-line react-hooks/exhaustive-deps

  // `tick` is what a command bumps: staging a file changes its diff without
  // changing what is picked, so the viewer has to be told to ask again.
  useEffect(() => {
    if (!pick || !base || !cwd) { setDiff(''); return undefined; }
    let live = true;
    getWorkDiff(base, cwd, pick)
      .then((text) => { if (live) setDiff(text); })
      .catch((e) => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [base, cwd, pick, tick]);

  const run = async (verb, fields = {}, then) => {
    setBusy(true);
    setErr('');
    setSay('');
    try {
      const out = await doWork(base, cwd, verb, fields);
      setSay(String(out || '').trim().split('\n').pop().slice(0, 160));
      if (then) then();
      await load();
      setTick((n) => n + 1);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const staged = work?.staged || [];
  const dirty = work?.unstaged || [];
  const clashes = work?.conflicts || [];
  const log = work?.log || [];
  const files = parseDiff(diff);
  const ahead = work?.ahead || 0;
  const behind = work?.behind || 0;

  // Staging moves a file from one list to the other, so the pick follows it
  // rather than being left pointing at a side that is now empty.
  const stage = (row, wasStaged) =>
    run(wasStaged ? 'unstage' : 'stage', { files: [row.path] }, () =>
      setPick({ file: row.path, staged: !wasStaged }));

  const commit = () =>
    run('commit', { message: msg, amend }, () => { setMsg(''); setAmend(false); setPick(null); });

  const openBranches = async () => {
    setErr('');
    try { setBranches(await listBranches(base, cwd)); }
    catch (e) { setErr(e.message); }
  };

  const switchTo = async (name) => {
    setBusy(true);
    setErr('');
    try {
      await switchBranch(base, cwd, name);
      setBranches(null);
      setPick(null);
      await load();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const viewer = (
    <View style={styles.diffViewer}>
      {files.length ? (
        <ScrollView style={styles.diffScroll} nestedScrollEnabled>
          <ScrollView horizontal contentContainerStyle={styles.diffCodeSheet}>
            <View>
              {files.map((f) => (
                <View key={f.path}>
                  <View style={styles.diffFileHead}>
                    <Text numberOfLines={1} style={styles.diffFileHeadName}>{f.path}</Text>
                    <Text style={styles.diffStats}>
                      <Text style={styles.diffPlus}>+{f.additions}</Text>{' '}
                      <Text style={styles.diffMinus}>−{f.deletions}</Text>
                    </Text>
                  </View>
                  {f.lines.map((line, i) => <DiffLine key={i} line={line} />)}
                </View>
              ))}
            </View>
          </ScrollView>
        </ScrollView>
      ) : (
        <Empty what={pick ? 'nothing textual to show here' : 'pick a file or a commit'} />
      )}
    </View>
  );

  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>work</Text>
        <Text style={styles.model}>
          {work?.detached ? 'detached HEAD' : work?.branch || '…'}
        </Text>
        {!!work?.upstream && (
          <Text style={styles.rowSub}>
            {work.upstream}
            {ahead || behind ? ` · ${ahead ? `↑${ahead}` : ''}${behind ? ` ↓${behind}` : ''}` : ' · in step'}
          </Text>
        )}
        <View style={styles.spacer} />
        {tabs}
      </View>
      <Segmented names={WORK_HALVES} at={half} onAt={setHalf} />
      <View style={styles.manageRow}>
        <PushButton label="fetch" disabled={busy} onPress={() => run('fetch')} style={styles.wkBtn} />
        <PushButton
          label={behind ? `pull ${behind}` : 'pull'}
          lit={behind > 0}
          disabled={busy}
          onPress={() => run('pull')}
          style={styles.wkBtn}
        />
        <PushButton
          label={ahead ? `push ${ahead}` : 'push'}
          colour="#3cd05a"
          lit={ahead > 0}
          disabled={busy}
          onPress={() => run('push')}
          style={styles.wkBtn}
        />
        <PushButton label="branch…" disabled={busy} onPress={openBranches} style={styles.wkBtn} />
        <PushButton
          label="stash"
          disabled={busy || !(staged.length + dirty.length)}
          onPress={() => run('stash')}
          style={styles.wkBtn}
        />
        {!!say && <Text numberOfLines={1} style={styles.caption}>{say}</Text>}
      </View>
      {!!err && <Text style={styles.err}>{err}</Text>}
      {!!clashes.length && (
        <Text style={styles.err}>
          {clashes.length} file{clashes.length === 1 ? '' : 's'} conflicted — resolve in the
          editor, then stage: {clashes.map((c) => c.path).join(', ')}
        </Text>
      )}
      {half === 1 ? (
        <View style={[styles.diffWorkspace, narrow && styles.diffWorkspaceNarrow]}>
          <View style={[styles.wkTree, narrow && styles.diffFilesNarrow]}>
            <ScrollView style={narrow ? styles.wkTreeNarrow : undefined} nestedScrollEnabled>
              {log.map((row, i) => (
                <Pressable
                  key={i}
                  disabled={!row.sha}
                  onPress={() => setPick({ sha: row.sha })}
                  style={[styles.wkCommit, pick?.sha && pick.sha === row.sha && styles.diffFileOn]}>
                  <Text style={styles.wkArt}>{row.art}</Text>
                  {!!row.sha && <Text style={styles.wkSha}>{row.short}</Text>}
                  {!!row.refs && <Text numberOfLines={1} style={styles.wkRefs}>{row.refs}</Text>}
                  <Text numberOfLines={1} style={styles.wkSubject}>{row.subject}</Text>
                  {!!row.sha && <Text style={styles.wkWhen}>{row.who} · {row.when}</Text>}
                </Pressable>
              ))}
              {!log.length && <Empty what="no commits yet" />}
            </ScrollView>
          </View>
          {viewer}
        </View>
      ) : (
        <>
          <View style={[styles.diffWorkspace, narrow && styles.diffWorkspaceNarrow]}>
            <View style={[styles.diffFiles, narrow && styles.diffFilesNarrow]}>
              <ScrollView style={narrow ? styles.wkTreeNarrow : undefined} nestedScrollEnabled>
                {[['staged', staged, true], ['not staged', dirty, false], ['conflicted', clashes, false]].map(
                  ([name, rows, isStaged]) =>
                    rows.length ? (
                      <View key={name}>
                        <Text style={styles.wkGroup}>{name} · {rows.length}</Text>
                        {rows.map((row) => (
                          <WorkFile
                            key={`${name}:${row.path}`}
                            row={row}
                            staged={isStaged}
                            on={pick?.file === row.path && !!pick?.staged === isStaged}
                            onOpen={() => setPick({ file: row.path, staged: isStaged })}
                            onStage={() => stage(row, isStaged)}
                            onDiscard={() =>
                              setAsk({
                                title: `Discard ${row.path}?`,
                                note: 'The changes in this file are not committed and not stashed. git keeps no copy of them.',
                                verb: 'discard',
                                fields: { files: [row.path] },
                              })
                            }
                          />
                        ))}
                      </View>
                    ) : null
                )}
                {!staged.length && !dirty.length && !clashes.length && (
                  <Empty
                    what={work ? 'the tree is clean' : cwd ? 'reading the tree…' : 'no checkout selected'}
                  />
                )}
              </ScrollView>
            </View>
            {viewer}
          </View>
          {!!work?.stashes?.length && (
            <View style={styles.manageRow}>
              {work.stashes.map((s) => (
                <View key={s.ref} style={styles.check}>
                  <Text numberOfLines={1} style={styles.checkName}>{s.ref} {s.text}</Text>
                  <Text
                    accessibilityRole="button"
                    onPress={() => run('stash-pop', { ref: s.ref })}
                    style={styles.wkKey}>
                    pop
                  </Text>
                  <Text
                    accessibilityRole="button"
                    onPress={() =>
                      setAsk({
                        title: `Drop ${s.ref}?`,
                        note: 'A dropped stash is gone; nothing on this screen brings it back.',
                        verb: 'stash-drop',
                        fields: { ref: s.ref },
                      })
                    }
                    style={[styles.wkKey, styles.wkDrop]}>
                    drop
                  </Text>
                </View>
              ))}
            </View>
          )}
          <TextInput
            value={msg}
            onChangeText={setMsg}
            multiline
            placeholder={amend ? 'Amend the last commit (leave empty to keep its message)' : 'Commit message'}
            placeholderTextColor={C.faint}
            style={styles.reviewInput}
          />
          <View style={styles.manageRow}>
            <PushButton
              label={amend ? 'amend' : 'commit'}
              colour="#3cd05a"
              lit
              disabled={busy || (!amend && (!staged.length || !msg.trim()))}
              onPress={commit}
              style={styles.reviewBtn}
            />
            <Text
              accessibilityRole="checkbox"
              accessibilityState={{ checked: amend }}
              onPress={() => setAmend((was) => !was)}
              style={[styles.wkKey, amend && styles.wkKeyOn]}>
              {amend ? 'amending ✓' : 'amend last'}
            </Text>
            <PushButton
              label="stage all"
              disabled={busy || !dirty.length}
              onPress={() => run('stage')}
              style={styles.wkBtn}
            />
            <PushButton
              label="unstage all"
              disabled={busy || !staged.length}
              onPress={() => run('unstage')}
              style={styles.wkBtn}
            />
            <PushButton
              label="discard all"
              colour={C.bad}
              disabled={busy || !dirty.length}
              onPress={() =>
                setAsk({
                  title: `Discard all ${dirty.length} changed files?`,
                  note: 'Every uncommitted change in the tree, including new files git is not tracking yet. There is no undo.',
                  verb: 'discard',
                  fields: {},
                })
              }
              style={styles.wkBtn}
            />
            <Text style={styles.caption}>{staged.length} staged</Text>
          </View>
        </>
      )}
      <Modal visible={!!ask} transparent animationType="fade" onRequestClose={() => setAsk(null)}>
        <View style={styles.modalBack}>
          <View style={styles.receipt}>
            <Text style={styles.receiptTitle}>{ask?.title}</Text>
            <Text style={styles.note}>{ask?.note}</Text>
            <View style={styles.manageRow}>
              <PushButton label="cancel" onPress={() => setAsk(null)} style={styles.reviewBtn} />
              <PushButton
                label={ask?.verb === 'discard' ? 'discard' : 'drop'}
                colour={C.bad}
                lit
                disabled={busy}
                onPress={() => {
                  const it = ask;
                  setAsk(null);
                  run(it.verb, it.fields, () => setPick(null));
                }}
                style={styles.reviewBtn}
              />
            </View>
          </View>
        </View>
      </Modal>
      <Modal visible={!!branches} transparent animationType="fade" onRequestClose={() => setBranches(null)}>
        <View style={styles.modalBack}>
          <View style={styles.receipt}>
            <View style={styles.title}>
              <Text style={styles.receiptTitle}>Branches</Text>
              <View style={styles.spacer} />
              <Text accessibilityRole="button" onPress={() => setBranches(null)} style={styles.receiptClose}>close</Text>
            </View>
            <ScrollView contentContainerStyle={styles.rows}>
              {(branches || []).map((b) => (
                <Pressable
                  key={b.name}
                  // git will not check one branch out in two worktrees, so a
                  // branch already taken says where it is rather than offering
                  // a button whose only outcome is an error.
                  disabled={busy || (!!b.at && b.name !== work?.branch)}
                  onPress={() => switchTo(b.name)}
                  style={[styles.row, b.name === work?.branch && styles.mine]}>
                  <Text style={[styles.rowName, mono]}>{b.name}</Text>
                  <View style={styles.spacer} />
                  {b.name === work?.branch && <Text style={styles.tag}>here</Text>}
                  {!!b.at && b.name !== work?.branch && <Text style={styles.rowSub}>{b.at}</Text>}
                </Pressable>
              ))}
            </ScrollView>
            <View style={styles.manageRow}>
              <TextInput
                value={named}
                onChangeText={setNamed}
                placeholder="new branch"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                style={[styles.wfName, styles.spacer]}
              />
              <PushButton
                label="create"
                disabled={busy || !named.trim()}
                onPress={() =>
                  run('branch', { branch: named.trim() }, () => { setNamed(''); setBranches(null); })
                }
                style={styles.reviewBtn}
              />
            </View>
          </View>
        </View>
      </Modal>
      <Text style={styles.note}>
        Every command here is a verb the server owns, run in this checkout —
        nothing on this screen composes a command line, and git's own refusal is
        what comes back when one is refused.
      </Text>
    </View>
  );
}

// The third level down, and the third idiom: the top tabs are tracked-out
// capitals, GitTabs is a row of words in the title, and this is a segmented
// switch. Three levels of navigation that all looked alike would be three
// levels nobody could tell apart -- what makes this one readable is that it
// does not look like the two above it.
//
// It always answers the same question, whatever pane it is in: what happened,
// or what is configured. History on the left, the files that govern it on the
// right.
function Segmented({ names, at, onAt }) {
  return (
    <View style={styles.seg}>
      {names.map((name, i) => (
        <Pressable
          key={name}
          accessibilityRole="tab"
          accessibilityState={{ selected: i === at }}
          onPress={() => onAt(i)}
          style={[styles.segAt, i === at && styles.segOn]}>
          <Text style={[styles.segText, i === at && styles.segTextOn]}>{name}</Text>
        </Pressable>
      ))}
    </View>
  );
}

// The manage half of pull requests: the files that shape one without being
// one. Deliberately its own component rather than a parameterised Manage --
// the two share their styles and their shape, but a fixed table of three files
// that may not exist yet and an open-ended directory you can add to are
// different enough that one component doing both would be mostly branches.
// A third of these and it is worth extracting the editor; two is not.
function Governs({ base, cwd }) {
  const narrow = useNarrow();
  const [rows, setRows] = useState([]);
  const [at, setAt] = useState('pr-template');
  const [text, setText] = useState('');
  const [was, setWas] = useState('');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [say, setSay] = useState('');
  const dirty = editing && text !== was;
  const here = rows.find((r) => r.key === at);

  const load = () => {
    if (!base || !cwd) return;
    listGoverns(base, cwd)
      .then((next) => { setRows(next.rows || []); setErr(''); })
      .catch((e) => { setRows([]); setErr(e.message); });
  };
  const open = (key) => {
    if (dirty) { setSay('save or cancel first'); return; }
    setEditing(false);
    setErr('');
    setSay('');
    setAt(key);
    getGovern(base, cwd, key)
      .then((g) => { setText(g.body); setWas(g.body); })
      .catch((e) => setErr(e.message));
  };
  useEffect(() => { load(); }, [base, cwd]);
  useEffect(() => { if (base && cwd) open(at); }, [base, cwd]);   // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setBusy(true);
    setErr('');
    try {
      await saveGovern(base, cwd, at, text, !!here?.there);
      setWas(text);
      setEditing(false);
      setSay('saved');
      load();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  // Starting from the empty file is not starting: the point of one of these is
  // the thing it says, so an absent file opens with a starter that already
  // says it, and the author edits rather than stares.
  const start = () => {
    setText(GOVERN_STARTERS[at] || '');
    setWas('');
    setEditing(true);
    setSay('');
  };
  return (
    <>
      {!!err && <Text style={styles.err}>{err}</Text>}
      <View style={[styles.diffWorkspace, narrow && styles.diffWorkspaceNarrow]}>
        <View style={[styles.wfList, narrow && styles.diffFilesNarrow]}>
          <ScrollView horizontal={narrow} showsHorizontalScrollIndicator={false}>
            <View style={narrow ? styles.diffFileTabs : undefined}>
              {rows.map((g) => (
                <Pressable
                  key={g.key}
                  onPress={() => open(g.key)}
                  style={[styles.diffFile, at === g.key && styles.diffFileOn]}>
                  {/* hollow means the file is not there -- which is the row
                      worth reading: no CODEOWNERS is why nobody was asked */}
                  <View
                    style={[styles.chip, g.there
                      ? { backgroundColor: C.accent }
                      : { borderWidth: 1, borderColor: C.edge }]}
                  />
                  <View style={styles.hookNames}>
                    <Text numberOfLines={1} style={[styles.hookName, !g.there && styles.hookOff]}>
                      {g.path.split('/').pop()}
                    </Text>
                    <Text numberOfLines={1} style={styles.hookSub}>
                      {g.there ? `${g.lines} lines` : 'not in this repo'}
                    </Text>
                  </View>
                </Pressable>
              ))}
            </View>
          </ScrollView>
        </View>
        <View style={styles.diffViewer}>
          <View style={styles.diffFileHead}>
            <Text numberOfLines={1} style={[styles.diffFileHeadName, mono]}>
              {here?.path || at}
            </Text>
            {!!say && <Text style={styles.caption}>{say}</Text>}
            {!!dirty && <Text style={styles.mockTag}>unsaved</Text>}
            {editing ? (
              <>
                <Pressable
                  accessibilityRole="button"
                  onPress={() => { setText(was); setEditing(false); setSay(''); }}
                  style={styles.reviewedKey}>
                  <Text style={styles.reviewedKeyText}>Cancel</Text>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  disabled={busy}
                  onPress={save}
                  style={[styles.reviewedKey, styles.reviewedKeyOn]}>
                  <Text style={styles.reviewedKeyText}>
                    {busy ? 'Saving…' : here?.there ? 'Save' : 'Create'}
                  </Text>
                </Pressable>
              </>
            ) : (
              <Pressable
                accessibilityRole="button"
                onPress={() => (here?.there ? setEditing(true) : start())}
                style={styles.reviewedKey}>
                <Text style={styles.reviewedKeyText}>
                  {here?.there ? 'Edit' : 'Start one'}
                </Text>
              </Pressable>
            )}
          </View>
          {!!here && (
            <View style={styles.wfFacts}>
              <Text style={styles.hookSub}>{here.what}</Text>
            </View>
          )}
          {editing ? (
            <TextInput
              value={text}
              onChangeText={setText}
              multiline
              autoCapitalize="none"
              autoCorrect={false}
              spellCheck={false}
              style={[styles.wfEditor, mono]}
            />
          ) : here?.there ? (
            <ScrollView style={styles.diffScroll} nestedScrollEnabled>
              <ScrollView horizontal contentContainerStyle={styles.diffCodeSheet}>
                <Text style={[styles.hookCode, mono]}>{text || ' '}</Text>
              </ScrollView>
            </ScrollView>
          ) : (
            <View style={styles.hookGap}>
              <Text style={styles.emptyText}>
                This repo has no {here?.path.split('/').pop() || 'file'}.
              </Text>
              <Text style={[styles.note, { textAlign: 'center', maxWidth: 380 }]}>
                {here?.what}
              </Text>
            </View>
          )}
        </View>
      </View>
    </>
  );
}

const PR_HALVES = ['open', 'manage'];

// What a keyboard actually puts on the wire. Every one of these is what the
// key sends, not a name for it -- so the server hands them to `send-keys -l`
// and nothing in between has to know what a key is called.
const KEY_BYTES = {
  Enter: '\r',
  Backspace: '\x7f',
  Tab: '\t',
  Escape: '\x1b',
  ArrowUp: '\x1b[A',
  ArrowDown: '\x1b[B',
  ArrowRight: '\x1b[C',
  ArrowLeft: '\x1b[D',
  Home: '\x1b[H',
  End: '\x1b[F',
  PageUp: '\x1b[5~',
  PageDown: '\x1b[6~',
  Delete: '\x1b[3~',
};

// The keys a tablet has no way to press. Not a convenience -- without Esc and
// Ctrl-C there are agent states an iPad simply cannot get out of.
const KEY_PADS = [
  ['esc', '\x1b'],
  ['tab', '\t'],
  ['↑', '\x1b[A'],
  ['↓', '\x1b[B'],
  ['^C', '\x03'],
  ['^D', '\x04'],
  ['^U', '\x15'],
];

function Prs(props) {
  const narrow = useNarrow();
  const [half, setHalf] = useState(0);
  if (half === 0) return <Open {...props} half={half} onHalf={setHalf} />;
  const { base, cwd, tabs } = props;
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>pull requests</Text>
        <View style={styles.spacer} />
        {tabs}
      </View>
      <Segmented names={PR_HALVES} at={half} onAt={setHalf} />
      <Governs base={base} cwd={cwd} />
    </View>
  );
}

function Open({ data, base, cwd, tabs, half, onHalf }) {
  const narrow = useNarrow();
  const [listing, setListing] = useState(data);
  const rows = listing?.rows || [];
  const [picked, setPicked] = useState(null);
  const [detail, setDetail] = useState(null);
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [fileAt, setFileAt] = useState(0);
  const [reviewed, setReviewed] = useState({});
  const refresh = () => {
    if (!base || !cwd) return;
    listPrs(base, cwd).then(setListing).catch((e) => setListing({ rows: [], err: e.message }));
  };
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => clearInterval(timer);
  }, [base, cwd]);
  const open = async (pr) => {
    setPicked(pr);
    setDetail(null);
    setFileAt(0);
    setReviewed({});
    setErr('');
    try { setDetail(await getPr(base, cwd, pr.n)); }
    catch (e) { setErr(e.message); }
  };
  const diffFiles = parseDiff(detail?.diff);
  const activeFile = diffFiles[fileAt];
  const reviewedCount = diffFiles.filter((f) => reviewed[f.path]).length;
  const review = async (action) => {
    setBusy(true);
    setErr('');
    try {
      await reviewPr(base, cwd, picked.n, action, note);
      setNote('');
      if (action !== 'comment') setPicked(null);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>pull requests</Text>
        <Text style={styles.model}>{rows.length} open</Text>
        <View style={styles.spacer} />
        {tabs}
      </View>
      <Segmented names={PR_HALVES} at={half} onAt={onHalf} />
      {!!listing?.err && <Text style={styles.err}>{listing.err}</Text>}
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {rows.map((pr, i) => (
          <Pressable key={i} onPress={() => open(pr)} style={[styles.row, pr.mine && styles.mine]}>
            <View
              style={[styles.chip, { backgroundColor: CI_HEX[pr.checks] || C.edge }]}
            />
            <Text style={[styles.rowSub, mono]}>#{pr.n}</Text>
            <Text style={styles.rowName} numberOfLines={1}>
              {pr.title}
            </Text>
            <View style={styles.spacer} />
            {!!pr.draft && <Text style={styles.tag}>draft</Text>}
            {!!pr.review && <Text style={styles.tag}>{pr.review}</Text>}
            <Text style={styles.rowSub}>{pr.who}</Text>
          </Pressable>
        ))}
        {rows.length === 0 && !listing?.err && <Empty what="nothing open" />}
      </ScrollView>
      <Modal visible={!!picked} transparent animationType="fade" onRequestClose={() => setPicked(null)}>
        <View style={styles.modalBack}>
          <View style={styles.prReview}>
            <View style={styles.title}>
              <Text style={styles.receiptTitle}>Review PR #{picked?.n}</Text>
              <View style={styles.spacer} />
              <Text accessibilityRole="button" onPress={() => setPicked(null)} style={styles.receiptClose}>close</Text>
            </View>
            {!!err && <Text style={styles.err}>{err}</Text>}
            {!detail && !err && <Text style={styles.caption}>Loading review…</Text>}
            {!!detail && (
              <View style={styles.prReviewBody}>
                <ScrollView contentContainerStyle={styles.prReviewIntro}>
                <Text style={styles.prTitle}>{detail.title}</Text>
                <Text style={styles.caption}>{detail.author} · {detail.head} → {detail.base}</Text>
                <Checks runs={detail.runs} />
                {!!detail.body && <Text style={styles.prBody}>{detail.body}</Text>}
                </ScrollView>
                <View style={[styles.diffWorkspace, narrow && styles.diffWorkspaceNarrow]}>
                  <View style={[styles.diffFiles, narrow && styles.diffFilesNarrow]}>
                    <View style={styles.diffProgressRow}>
                      <Text style={styles.diffProgress}>{reviewedCount}/{diffFiles.length} reviewed</Text>
                      <View style={styles.diffProgressTrack}>
                        <View style={[styles.diffProgressFill, { width: `${diffFiles.length ? reviewedCount / diffFiles.length * 100 : 0}%` }]} />
                      </View>
                    </View>
                    <ScrollView horizontal={narrow} showsHorizontalScrollIndicator={false}>
                      <View style={narrow ? styles.diffFileTabs : undefined}>
                        {diffFiles.map((f, i) => (
                          <Pressable key={f.path} onPress={() => setFileAt(i)} style={[styles.diffFile, i === fileAt && styles.diffFileOn]}>
                            <Text style={[styles.diffCheck, reviewed[f.path] && styles.diffCheckOn]}>{reviewed[f.path] ? '✓' : '○'}</Text>
                            <Text numberOfLines={1} style={styles.diffFileName}>{f.path}</Text>
                            <Text style={styles.diffStats}><Text style={styles.diffPlus}>+{f.additions}</Text> <Text style={styles.diffMinus}>−{f.deletions}</Text></Text>
                          </Pressable>
                        ))}
                      </View>
                    </ScrollView>
                  </View>
                  <View style={styles.diffViewer}>
                    {!!activeFile ? (
                      <>
                        <View style={styles.diffFileHead}>
                          <Text numberOfLines={1} style={styles.diffFileHeadName}>{activeFile.path}</Text>
                          <Pressable
                            accessibilityRole="checkbox"
                            accessibilityState={{ checked: !!reviewed[activeFile.path] }}
                            onPress={() => setReviewed((was) => ({ ...was, [activeFile.path]: !was[activeFile.path] }))}
                            style={[styles.reviewedKey, reviewed[activeFile.path] && styles.reviewedKeyOn]}>
                            <Text style={styles.reviewedKeyText}>{reviewed[activeFile.path] ? 'Reviewed ✓' : 'Mark reviewed'}</Text>
                          </Pressable>
                        </View>
                        <ScrollView style={styles.diffScroll} nestedScrollEnabled>
                          <ScrollView horizontal contentContainerStyle={styles.diffCodeSheet}>
                            <View>{activeFile.lines.map((line, i) => <DiffLine key={i} line={line} />)}</View>
                          </ScrollView>
                        </ScrollView>
                      </>
                    ) : <Empty what="No textual diff is available for this PR." />}
                  </View>
                </View>
              </View>
            )}
            <TextInput value={note} onChangeText={setNote} multiline placeholder="Review comment (required for request changes)" placeholderTextColor={C.faint} style={styles.reviewInput} />
            <View style={styles.manageRow}>
              <PushButton label="comment" disabled={busy || !note.trim()} onPress={() => review('comment')} style={styles.reviewBtn} />
              <PushButton label="request changes" colour={C.bad} disabled={busy || !note.trim()} onPress={() => review('request-changes')} style={styles.reviewBtn} />
              <PushButton label="approve" colour="#3cd05a" lit disabled={busy} onPress={() => review('approve')} style={styles.reviewBtn} />
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// Everything from here to Usage is a drawing, not a reading. Both panes are
// wired to constants so the shape can be argued about before anything is
// built to fill them -- which is why each one says MOCK in its own heading
// and names the command it would be running instead. A pane that invents a
// red CI run and does not say so is worse than no pane.
const MOCK = '__mock__';

const MOCK_RUNS = [
  {
    id: 1482, flow: 'CI', state: 'fail', event: 'push', took: '4m 12s',
    when: '14m ago', who: 'app/dependabot', sha: 'dea959f',
    branch: 'dependabot/bun/lucide-react-1.34.0',
    jobs: [
      { name: 'Detect changed paths', state: 'pass', took: '8s' },
      { name: 'Lint & Typecheck', state: 'fail', took: '1m 02s' },
      { name: 'Unit shard 3', state: 'fail', took: '2m 41s' },
      { name: 'e2e build', state: 'fail', took: '3m 09s' },
      { name: 'Deploy to Staging', state: 'none', took: 'skipped' },
    ],
  },
  {
    id: 1481, flow: 'CI', state: 'pending', event: 'pull_request', took: '2m 06s',
    when: 'running', who: 'DanielPCoyle', sha: '9cb7a4b',
    branch: 'feat/pux-215-chat',
    jobs: [
      { name: 'Detect changed paths', state: 'pass', took: '7s' },
      { name: 'Lint & Typecheck', state: 'pass', took: '58s' },
      { name: 'Unit shard 1', state: 'pending', took: '1m 44s' },
      { name: 'Unit shard 2', state: 'pending', took: '1m 41s' },
      { name: 'e2e build', state: 'none', took: 'waiting' },
    ],
  },
  {
    id: 1480, flow: 'Nightly', state: 'pass', event: 'schedule', took: '11m 30s',
    when: '6h ago', who: 'github-actions', sha: '87a7ffc', branch: 'main',
    jobs: [
      { name: 'Tenancy regression', state: 'pass', took: '6m 12s' },
      { name: 'Secret scan (gitleaks)', state: 'pass', took: '41s' },
      { name: 'e2e build', state: 'pass', took: '4m 37s' },
    ],
  },
  {
    id: 1479, flow: 'CI', state: 'pass', event: 'push', took: '3m 48s',
    when: '8h ago', who: 'DanielPCoyle', sha: '8136391', branch: 'main',
    jobs: [
      { name: 'Lint & Typecheck', state: 'pass', took: '54s' },
      { name: 'Unit tests', state: 'pass', took: '2m 12s' },
      { name: 'Deploy to Staging', state: 'pass', took: '42s' },
    ],
  },
  {
    id: 1478, flow: 'Code Scanning', state: 'pass', event: 'push', took: '5m 02s',
    when: '8h ago', who: 'github-actions', sha: '8136391', branch: 'main',
    jobs: [{ name: 'CodeQL-Build (go)', state: 'pass', took: '5m 02s' }],
  },
];

const RUN_SCOPES = [
  ['all', () => true],
  ['failing', (r) => r.state === 'fail'],
  ['running', (r) => r.state === 'pending'],
];

// A run's own word for itself. "pending" is what the rollup calls a check that
// has not finished, which is the right word for a check and the wrong one for
// a run you are watching go.
const RUN_WORD = { fail: 'failed', pass: 'passed', pending: 'running', none: 'skipped' };

// The runs half, which is still a drawing. Split out from the pane so the
// workflows beside it -- a real read of the checkout -- does not have to carry
// its `mock` label, and so the two can stop being one screen's worth of
// honesty problem the day these become real.
function Runs() {
  const narrow = useNarrow();
  const [scope, setScope] = useState(0);
  const [open, setOpen] = useState(MOCK_RUNS[0].id);
  const runs = MOCK_RUNS.filter(RUN_SCOPES[scope][1]);
  const failing = MOCK_RUNS.filter((r) => r.state === 'fail').length;
  return (
    <>
      <View style={styles.manageRow}>
        <View style={styles.walk}>
          {RUN_SCOPES.map(([name], i) => (
            <Text
              key={name}
              onPress={() => setScope(i)}
              style={[styles.walkAt, i === scope && styles.walkOn]}>
              {name}
            </Text>
          ))}
        </View>
        <View style={styles.spacer} />
        <Text style={[styles.model, failing ? { color: C.bad } : undefined]}>
          {failing ? `${failing} failing` : 'all green'}
        </Text>
      </View>
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {runs.map((run) => {
          const on = run.id === open;
          return (
            <View key={run.id} style={[styles.runCard, on && styles.mine]}>
              <Pressable
                style={styles.runHead}
                accessibilityRole="button"
                accessibilityState={{ expanded: on }}
                onPress={() => setOpen(on ? 0 : run.id)}>
                <View style={[styles.chip, { backgroundColor: CI_HEX[run.state] || C.edge }]} />
                <View style={styles.runNames}>
                  <View style={styles.runLine}>
                    <Text style={styles.rowName}>{run.flow}</Text>
                    <Text style={[styles.rowSub, mono]}>#{run.id}</Text>
                    <Text style={styles.tag}>{run.event}</Text>
                  </View>
                  <Text numberOfLines={1} style={[styles.runBranch, mono]}>
                    {run.branch} · {run.sha}
                  </Text>
                </View>
                <View style={styles.spacer} />
                <View style={styles.runFacts}>
                  <Text style={[styles.rowSub, { color: CI_HEX[run.state] || C.faint }]}>
                    {RUN_WORD[run.state]}
                  </Text>
                  <Text style={styles.rowSub}>{run.took} · {run.when}</Text>
                </View>
              </Pressable>
              {on && (
                <View style={styles.runJobs}>
                  {run.jobs.map((job) => (
                    <View key={job.name} style={styles.runJob}>
                      <View style={[styles.chip, { backgroundColor: CI_HEX[job.state] || C.edge }]} />
                      <Text numberOfLines={1} style={styles.runJobName}>{job.name}</Text>
                      <View style={styles.spacer} />
                      <Text style={[styles.rowSub, mono]}>{job.took}</Text>
                    </View>
                  ))}
                  <View style={styles.manageRow}>
                    <PushButton label="re-run failed" colour={C.bad} style={styles.manageBtn} />
                    <PushButton label="open on github" style={styles.manageBtn} />
                    <Text style={styles.caption}>by {run.who}</Text>
                  </View>
                </View>
              )}
            </View>
          );
        })}
        {runs.length === 0 && <Empty what={`nothing ${RUN_SCOPES[scope][0]}`} />}
      </ScrollView>
      <Text style={styles.note}>
        Runs are drawn from constants — `gh run list` once the shape is agreed.
        Manage, beside it, is the real `.github/workflows` in this checkout.
      </Text>
    </>
  );
}

const NEW = '\u0000new';   // the row that is not a file yet

// The manage half: the workflow files themselves, read from and written to the
// checkout. Nothing here is a drawing.
function Manage({ base, cwd, onCount }) {
  const narrow = useNarrow();
  const [list, setList] = useState(null);
  const [at, setAt] = useState(NEW);
  const [text, setText] = useState('');     // the editor's buffer
  const [was, setWas] = useState('');       // what is on disk, to know if it moved
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [say, setSay] = useState('');
  const [name, setName] = useState('');
  const [tpl, setTpl] = useState(TEMPLATES[0].key);
  const rows = list?.rows || [];
  const dirty = editing && text !== was;

  const load = () => {
    if (!base || !cwd) return;
    listWorkflows(base, cwd)
      .then((next) => {
        setList(next);
        setErr('');
        onCount?.(next.rows?.length || 0);
        // land on something real rather than the new-workflow form when the
        // repo already has workflows -- the common errand here is reading one
        setAt((now) => (now === NEW && next.rows?.length ? next.rows[0].name : now));
      })
      .catch((e) => { setList({ rows: [] }); setErr(e.message); });
  };
  useEffect(() => { setAt(NEW); setEditing(false); load(); }, [base, cwd]);
  useEffect(() => {
    if (at !== NEW && !text && !editing) go(at);
  }, [list]);   // eslint-disable-line react-hooks/exhaustive-deps

  // A half-typed workflow is work, and the left column is one tap wide -- so
  // while there are unsaved changes the rows stop navigating and say why,
  // rather than throwing the buffer away for a tap that was probably a miss.
  function go(next) {
    if (dirty) { setSay('save or cancel first'); return; }
    setEditing(false);
    setErr('');
    setSay('');
    setAt(next);
    if (next === NEW) { setText(''); setWas(''); return; }
    getWorkflow(base, cwd, next)
      .then((w) => { setText(w.body); setWas(w.body); })
      .catch((e) => setErr(e.message));
  }

  const create = () => {
    const slug = name.trim();
    if (!WORKFLOW_NAME_RE.test(slug)) {
      setErr('lowercase letters, digits and dashes, starting with a letter');
      return;
    }
    if (rows.some((r) => r.name === slug)) { setErr(`${slug}.yml already exists`); return; }
    setErr('');
    setText(TEMPLATES.find((t) => t.key === tpl).body(slug));
    setWas('');            // nothing on disk yet, so every line of it is unsaved
    setAt(slug);
    setEditing(true);
  };

  const save = async () => {
    setBusy(true);
    setErr('');
    try {
      // `replace` is true exactly when this name is already a file: the server
      // refuses an unflagged write over one, which is what stops a new
      // workflow silently taking the name of a pipeline someone relies on.
      await saveWorkflow(base, cwd, at, text, rows.some((r) => r.name === at));
      setWas(text);
      setEditing(false);
      setSay('saved');
      setName('');
      load();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const here = rows.find((r) => r.name === at);
  const making = !here && at !== NEW;    // typed, not on disk yet
  return (
    <>
      {!!err && <Text style={styles.err}>{err}</Text>}
      <View style={[styles.diffWorkspace, narrow && styles.diffWorkspaceNarrow]}>
        <View style={[styles.wfList, narrow && styles.diffFilesNarrow]}>
          <ScrollView horizontal={narrow} showsHorizontalScrollIndicator={false}>
            <View style={narrow ? styles.diffFileTabs : undefined}>
              {rows.map((wf) => (
                <Pressable
                  key={wf.file}
                  onPress={() => go(wf.name)}
                  accessibilityLabel={`${wf.file} — ${wf.title}`}
                  style={[styles.diffFile, at === wf.name && styles.diffFileOn]}>
                  <View style={[styles.chip, { backgroundColor: wf.editable ? C.accent : C.edge }]} />
                  <View style={styles.hookNames}>
                    <Text numberOfLines={1} style={styles.hookName}>{wf.file}</Text>
                    {/* what fires it and what it needs -- the two facts that
                        decide whether this is the file you are looking for */}
                    <Text numberOfLines={1} style={styles.hookSub}>
                      {(wf.on || []).join(', ') || 'no trigger'}
                      {wf.secrets?.length ? ` · ${wf.secrets.join(', ')}` : ''}
                    </Text>
                  </View>
                </Pressable>
              ))}
              <Pressable onPress={() => go(NEW)} style={[styles.diffFile, at === NEW && styles.diffFileOn]}>
                <Text style={styles.wfPlus}>＋</Text>
                <Text style={styles.hookSub}>new workflow</Text>
              </Pressable>
            </View>
          </ScrollView>
        </View>

        <View style={styles.diffViewer}>
          {at === NEW ? (
            <ScrollView style={styles.diffScroll} contentContainerStyle={styles.wfNew}>
              <Text style={styles.prTitle}>New workflow</Text>
              <Text style={styles.note}>
                The file is `.github/workflows/&lt;name&gt;.yml`. Every template opens
                with the banner the workflows here already use — what it does,
                what secret it needs, and what a fork without that secret sees.
              </Text>
              <TextInput
                value={name}
                onChangeText={setName}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="deploy-web"
                placeholderTextColor={C.faint}
                style={styles.wfName}
              />
              {TEMPLATES.map((t) => (
                <Pressable
                  key={t.key}
                  onPress={() => setTpl(t.key)}
                  style={[styles.wfTpl, t.key === tpl && styles.wfTplOn]}>
                  <Text style={styles.rowName}>{t.label}</Text>
                  <Text style={styles.hookSub}>{t.what}</Text>
                </Pressable>
              ))}
              <PushButton
                label="scaffold it"
                colour={C.accentText}
                lit
                disabled={!name.trim()}
                onPress={create}
                style={styles.manageBtn}
              />
            </ScrollView>
          ) : (
            <>
              <View style={styles.diffFileHead}>
                <Text numberOfLines={1} style={[styles.diffFileHeadName, mono]}>
                  .github/workflows/{at}.yml
                </Text>
                {/* `say` used to be hidden whenever the buffer was dirty, which
                    is the only time the guard that writes it can fire -- the
                    rows refused to move and never said why. */}
                {!!say && <Text style={styles.caption}>{say}</Text>}
                {!!dirty && <Text style={styles.mockTag}>unsaved</Text>}
                {here && !here.editable && <Text style={styles.tag}>read only</Text>}
                {editing ? (
                  <>
                    <Pressable
                      accessibilityRole="button"
                      onPress={() => { setText(was); setEditing(false); setSay(''); }}
                      style={styles.reviewedKey}>
                      <Text style={styles.reviewedKeyText}>Cancel</Text>
                    </Pressable>
                    <Pressable
                      accessibilityRole="button"
                      disabled={busy}
                      onPress={save}
                      style={[styles.reviewedKey, styles.reviewedKeyOn]}>
                      <Text style={styles.reviewedKeyText}>
                        {busy ? 'Saving…' : making ? 'Create' : 'Save'}
                      </Text>
                    </Pressable>
                  </>
                ) : (
                  (here?.editable ?? true) && (
                    <Pressable
                      accessibilityRole="button"
                      onPress={() => { setEditing(true); setSay(''); }}
                      style={styles.reviewedKey}>
                      <Text style={styles.reviewedKeyText}>Edit</Text>
                    </Pressable>
                  )
                )}
              </View>
              {!!here && (
                <View style={styles.wfFacts}>
                  <Text style={styles.rowSub}>{here.title || 'unnamed'}</Text>
                  <Text style={styles.hookSub}>· {here.jobs.length} job{here.jobs.length === 1 ? '' : 's'}</Text>
                  {here.jobs.slice(0, 6).map((j) => (
                    <Text key={j} style={styles.tag}>{j}</Text>
                  ))}
                  {here.jobs.length > 6 && <Text style={styles.hookSub}>+{here.jobs.length - 6}</Text>}
                </View>
              )}
              {editing ? (
                <TextInput
                  value={text}
                  onChangeText={setText}
                  multiline
                  autoCapitalize="none"
                  autoCorrect={false}
                  spellCheck={false}
                  style={[styles.wfEditor, mono]}
                />
              ) : (
                <ScrollView style={styles.diffScroll} nestedScrollEnabled>
                  <ScrollView horizontal contentContainerStyle={styles.diffCodeSheet}>
                    <Text style={[styles.hookCode, mono]}>{text || ' '}</Text>
                  </ScrollView>
                </ScrollView>
              )}
            </>
          )}
        </View>
      </View>
    </>
  );
}

const ACTION_HALVES = ['runs', 'manage'];

function Actions({ base, cwd, tabs }) {
  const narrow = useNarrow();
  const [half, setHalf] = useState(0);
  const [count, setCount] = useState(null);
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>actions</Text>
        {half === 0 && <Text style={styles.mockTag}>mock</Text>}
        {half === 1 && count != null && (
          <Text style={styles.model}>{count} workflow{count === 1 ? '' : 's'}</Text>
        )}
        <View style={styles.spacer} />
        {tabs}
      </View>
      <Segmented names={ACTION_HALVES} at={half} onAt={setHalf} />
      {half === 0 ? <Runs /> : <Manage base={base} cwd={cwd} onCount={setCount} />}
    </View>
  );
}

// The hooks git will actually look for, in the order they fire around a
// commit. Listing the uninstalled ones is the point: "no pre-push here" is
// the answer to why nothing stopped a broken push, and an empty list cannot
// say it.
const MOCK_HOOKS = [
  {
    name: 'pre-commit', by: 'husky', took: '12ms', state: 'pass', when: '2m ago',
    body: '#!/bin/sh\n. "$(dirname -- "$0")/_/husky.sh"\n\nbunx lint-staged\n',
  },
  {
    name: 'prepare-commit-msg', by: '', took: '', state: '', when: '', body: '',
  },
  {
    name: 'commit-msg', by: 'husky', took: '4ms', state: 'pass', when: '2m ago',
    body: '#!/bin/sh\n. "$(dirname -- "$0")/_/husky.sh"\n\nbunx commitlint --edit "$1"\n',
  },
  {
    name: 'post-commit', by: '', took: '', state: '', when: '', body: '',
  },
  {
    name: 'pre-push', by: 'husky', took: '1.2s', state: 'fail', when: '14m ago',
    body: '#!/bin/sh\n. "$(dirname -- "$0")/_/husky.sh"\n\nbun run typecheck\nbun run test --run\n',
    said: 'blocked the push · 2 commits still local',
  },
  {
    name: 'post-merge', by: '', took: '', state: '', when: '', body: '',
  },
  {
    name: 'post-checkout', by: '', took: '', state: '', when: '', body: '',
  },
];

function Hooks({ tabs }) {
  const narrow = useNarrow();
  const [at, setAt] = useState(4);
  const shown = MOCK_HOOKS[at];
  const on = MOCK_HOOKS.filter((h) => h.by);
  const blocked = MOCK_HOOKS.find((h) => h.state === 'fail');
  const by = [...new Set(on.map((h) => h.by))].join(', ');
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>hooks</Text>
        <Text style={styles.mockTag}>mock</Text>
        <View style={styles.spacer} />
        {tabs}
      </View>
      <View style={styles.manageRow}>
        <Text style={styles.model}>{on.length} installed</Text>
        {!!by && <Text style={styles.caption}>· {by}</Text>}
        <View style={styles.spacer} />
        {!!blocked && (
          <Text style={styles.act}>{blocked.name} blocked {blocked.when}</Text>
        )}
      </View>
      <View style={[styles.diffWorkspace, narrow && styles.diffWorkspaceNarrow]}>
        <View style={[styles.diffFiles, narrow && styles.diffFilesNarrow]}>
          <ScrollView horizontal={narrow} showsHorizontalScrollIndicator={false}>
            <View style={narrow ? styles.diffFileTabs : undefined}>
              {MOCK_HOOKS.map((hook, i) => (
                <Pressable
                  key={hook.name}
                  onPress={() => setAt(i)}
                  style={[styles.diffFile, i === at && styles.diffFileOn]}>
                  <View
                    style={[styles.chip, { backgroundColor: hook.by ? CI_HEX[hook.state] || C.edge : 'transparent', borderWidth: hook.by ? 0 : 1, borderColor: C.edge }]}
                  />
                  <View style={styles.hookNames}>
                    <Text numberOfLines={1} style={[styles.hookName, !hook.by && styles.hookOff]}>
                      {hook.name}
                    </Text>
                    <Text style={styles.hookSub}>
                      {hook.by ? `${hook.by} · ${hook.took}` : 'not installed'}
                    </Text>
                  </View>
                </Pressable>
              ))}
            </View>
          </ScrollView>
        </View>
        <View style={styles.diffViewer}>
          <View style={styles.diffFileHead}>
            <Text numberOfLines={1} style={[styles.diffFileHeadName, mono]}>
              .git/hooks/{shown.name}
            </Text>
            {!!shown.by && (
              <Pressable accessibilityRole="button" style={styles.reviewedKey}>
                <Text style={styles.reviewedKeyText}>Run it</Text>
              </Pressable>
            )}
          </View>
          {shown.by ? (
            <ScrollView style={styles.diffScroll} nestedScrollEnabled>
              <ScrollView horizontal contentContainerStyle={styles.diffCodeSheet}>
                <Text style={[styles.hookCode, mono]}>{shown.body}</Text>
              </ScrollView>
            </ScrollView>
          ) : (
            // not an error and not an empty state -- an uninstalled hook is a
            // slot, and the useful thing to offer is filling it
            <View style={styles.hookGap}>
              <Text style={styles.emptyText}>
                Nothing installed at this hook.
              </Text>
              <PushButton label={`add ${shown.name}`} style={styles.manageBtn} />
            </View>
          )}
          {!!shown.said && (
            <Text style={[styles.hookSaid, { color: CI_HEX[shown.state] }]}>
              {shown.when} · {shown.said}
            </Text>
          )}
        </View>
      </View>
      <Text style={styles.note}>
        Drawn from constants, not from the checkout — this is a read of
        `.git/hooks` plus whatever `core.hooksPath` points at, and the timings
        come from the last time one actually ran.
      </Text>
    </View>
  );
}

function Usage({ data, mode, onMode }) {
  const narrow = useNarrow();
  // mode is the app's own state now, threaded down from App.js; data.at is
  // only what's left of what the Push last showed, kept as a fallback for
  // the moment before that state exists (or if this ever renders without
  // it). It used to be the only source, and the row above it only ever
  // highlighted whichever one that was -- pressing it did nothing, which is
  // the same lie a disabled-looking button tells.
  const at = mode ?? data.at ?? 0;
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>usage</Text>
        <View style={styles.spacer} />
        <View style={styles.walk}>
          {['plan', 'by model', 'by agent'].map((n, i) => (
            <Text
              key={n}
              onPress={() => onMode && onMode(i)}
              style={[styles.walkAt, i === at && styles.walkOn]}>
              {n}
            </Text>
          ))}
        </View>
      </View>
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {at === 0 &&
          (data.bars || []).map((b, i) => (
            <Bar key={i} bar={b} />
          ))}
        {at === 1 &&
          (data.models || []).map(([name, n], i) => (
            <View key={i} style={styles.row}>
              <Text style={styles.rowName}>{name}</Text>
              <View style={styles.spacer} />
              <Text style={[styles.rowSub, mono]}>{n.toLocaleString()}</Text>
            </View>
          ))}
        {at === 2 &&
          (data.agents || []).filter(Boolean).map((a, i) => (
            <View key={i} style={styles.row}>
              <Text style={styles.rowName}>{a[0]}</Text>
              <View style={styles.spacer} />
              <Text style={[styles.rowSub, mono]}>out {a[1].toLocaleString()}</Text>
              <Text style={[styles.rowSub, mono]}>ctx {a[2].toLocaleString()}</Text>
              {/* a cached token costs about a tenth of the same token read
                  fresh, so a low row here is the expensive one. Dashes before
                  anything has been sent -- no requests is not a 0% hit rate. */}
              <Text
                style={[
                  styles.rowSub,
                  mono,
                  a[4] != null && { color: a[4] >= 80 ? '#3cd05a' : '#e0a03c' },
                ]}>
                cache {a[4] == null ? '--' : `${a[4]}%`}
              </Text>
            </View>
          ))}
        {!!data.err && <Text style={styles.err}>{data.err}</Text>}
      </ScrollView>
      <Text style={styles.note}>
        Tokens, not money: an invented cost is worse than no cost.
      </Text>
    </View>
  );
}

// The server sends {label, used, severity, ...}. This read `bar.frac`, which
// has never been a key it sends, so every bar fell through to the ?? 0 and
// drew 0% in the comfortable green -- at 98% of a session budget, on the one
// screen you check to decide whether to keep working.
//
// Colour comes off display.py's ramp rather than a threshold of our own:
// severity can push it hotter than the number alone would, never cooler, and
// two places deciding the same thing is what put a 0 here in the first place.
const PLAN_RAMP = ['#3cd05a', '#e0d02c', '#e03c3c'];
const SEVERITY_FLOOR = { warning: 1, critical: 2 };

function Bar({ bar }) {
  const pct = Math.max(0, Math.min(1, Number(bar.used ?? bar.frac ?? bar[1] ?? 0)));
  const label = bar.label ?? bar[0] ?? '';
  const level = pct < 0.6 ? 0 : pct < 0.85 ? 1 : 2;
  const hue = PLAN_RAMP[Math.max(level, SEVERITY_FLOOR[bar.severity] ?? 0)];
  return (
    <View style={styles.bar}>
      <View style={styles.title}>
        <Text style={styles.rowName}>{label}</Text>
        <View style={styles.spacer} />
        <Text style={[styles.rowSub, mono, { color: hue }]}>
          {Math.round(pct * 100)}%
        </Text>
      </View>
      <View style={styles.barTrack}>
        <View
          style={[styles.barFill, { width: `${pct * 100}%`, backgroundColor: hue }]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { flex: 1, padding: 20, gap: 14 },
  // Below 820 the app is one scrollable column and there is no flex:1
  // ancestor left to fill -- height-to-content is what makes that column
  // additive instead of every flex:1 view in it collapsing to zero.
  bodyNarrow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 40 },
  emptyText: { color: C.faint, fontSize: 13 },
  title: { flexDirection: 'row', alignItems: 'baseline', gap: 12 },
  spacer: { flex: 1 },
  h1: { color: C.text, fontSize: 24, fontWeight: '600' },
  place: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingBottom: 2 },
  subTab: { color: C.faint, fontSize: 11 },
  subTabOn: { color: C.text, borderBottomWidth: 1, borderBottomColor: C.accentText },
  placeRepo: { color: C.faint, fontSize: 11 },
  placeBranch: { color: C.dim, fontSize: 11, flexShrink: 1 },
  manageRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  manageBtn: { minWidth: 88 },
  prReview: { width: '100%', maxWidth: 980, height: '90%', backgroundColor: C.panel, borderWidth: 1, borderColor: C.edge, borderRadius: S.radius, padding: 18, gap: 12 },
  prReviewBody: { flex: 1, minHeight: 0, gap: 10 },
  prReviewIntro: { flexGrow: 0, gap: 7, maxHeight: 150 },
  prTitle: { color: C.text, fontSize: 18, fontWeight: '600' },
  prBody: { color: C.dim, fontSize: 13, lineHeight: 20 },
  // Loud on purpose. Every other pane here is a reading of something real,
  // and a drawing that borrows that credibility without saying so is the one
  // way a mockup does harm.
  mockTag: { color: C.warn, fontSize: 10, letterSpacing: 1, borderWidth: 1, borderColor: C.warn, borderRadius: 3, paddingHorizontal: 5, paddingVertical: 1, overflow: 'hidden' },
  runCard: { borderRadius: S.radius, borderWidth: 1, borderColor: C.line, backgroundColor: C.panel, overflow: 'hidden' },
  runHead: { flexDirection: 'row', alignItems: 'center', gap: 11, paddingHorizontal: 12, paddingVertical: 9, minHeight: 52 },
  runNames: { gap: 3, flexShrink: 1 },
  runLine: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  runBranch: { color: C.faint, fontSize: 11 },
  runFacts: { alignItems: 'flex-end', gap: 3 },
  runJobs: { borderTopWidth: 1, borderTopColor: C.line, padding: 12, gap: 8, backgroundColor: C.raised },
  runJob: { flexDirection: 'row', alignItems: 'center', gap: 9, minHeight: 22 },
  runJobName: { color: C.dim, fontSize: 12, flexShrink: 1 },
  // minWidth, not just flex: narrow turns this column into a horizontal
  // strip, and a flex child in an unbounded row has nothing to be 1 of
  // the same box the rail's agent/MCP search uses -- one search field in this
  // app, not a second one that looks nearly like it
  find: { height: 30, color: C.text, backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 6, paddingHorizontal: 9, fontSize: 12 },
  grTrack: { height: 3, borderRadius: 2, backgroundColor: C.raised, overflow: 'hidden' },
  grFill: { height: 3, backgroundColor: '#3cd05a' },
  grPhase: { gap: 5, paddingTop: 6 },
  grPhaseHead: { flexDirection: 'row', alignItems: 'baseline', gap: 8, paddingBottom: 2 },
  grPhaseName: { color: C.text, fontSize: 13, fontWeight: '700', letterSpacing: 0.4 },
  grPhaseWhat: { color: C.faint, fontSize: 11, fontStyle: 'italic' },
  grItem: { borderWidth: 1, borderColor: C.line, borderRadius: S.radius, backgroundColor: C.panel, overflow: 'hidden' },
  grItemOn: { borderColor: '#2a5a35' },
  grRow: { flexDirection: 'row', alignItems: 'center', gap: 11, paddingHorizontal: 12, paddingVertical: 9, minHeight: 44 },
  grBox: { width: 18, height: 18, borderRadius: 4, borderWidth: 1, borderColor: C.edge, alignItems: 'center', justifyContent: 'center' },
  grBoxOn: { borderColor: '#3cd05a', backgroundColor: '#14291a' },
  grTick: { color: '#3cd05a', fontSize: 11, fontWeight: '700' },
  grTitle: { color: C.text, fontSize: 13, flexShrink: 1 },
  grTitleOn: { color: C.dim },
  grCaret: { color: C.faint, fontSize: 14, width: 14, textAlign: 'center' },
  grBody: { borderTopWidth: 1, borderTopColor: C.line, backgroundColor: C.raised, padding: 12, gap: 6 },
  grLabel: { color: C.faint, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase' },
  grText: { color: C.dim, fontSize: 12.5, lineHeight: 19, paddingBottom: 4 },
  grAdd: { borderWidth: 1, borderColor: C.accent, borderRadius: S.radius, backgroundColor: C.panel, padding: 12, gap: 9 },
  grField: { minHeight: 62, textAlignVertical: 'top' },
  grTplRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 7, borderTopWidth: 1, borderTopColor: C.line },
  grTplName: { flex: 1, minWidth: 140 },
  grPhaseEdit: { flex: 1, flexDirection: 'row', gap: 6, minWidth: 200 },
  grPhaseField: { flex: 1, minWidth: 90 },
  grWarn: { color: C.warn, fontSize: 11, flexShrink: 1 },
  seg: { flexDirection: 'row', alignSelf: 'flex-start', borderWidth: 1, borderColor: C.line, borderRadius: 7, overflow: 'hidden', backgroundColor: C.panel },
  segAt: { paddingHorizontal: 14, paddingVertical: 6 },
  segOn: { backgroundColor: C.raised },
  segText: { color: C.faint, fontSize: 11, fontWeight: '600', letterSpacing: 0.6 },
  segTextOn: { color: C.text },
  wfList: { width: 250, borderRightWidth: 1, borderRightColor: C.line, backgroundColor: C.panel },
  wfPane: { flex: 1, minWidth: 0 },
  runList: { gap: 5, padding: 10 },
  wfPlus: { color: C.accentText, fontSize: 15, width: 11, textAlign: 'center' },
  wfNew: { padding: 16, gap: 12 },
  wfName: { color: C.text, fontSize: 14, padding: 10, borderWidth: 1, borderColor: C.edge, borderRadius: 6, backgroundColor: C.panel, ...mono },
  wfTpl: { padding: 10, gap: 3, borderWidth: 1, borderColor: C.line, borderRadius: 6, backgroundColor: C.panel },
  wfTplOn: { borderColor: C.accent, backgroundColor: C.raised },
  wfFacts: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: C.line },
  // A YAML editor is the one place in this app where a proportional font is
  // simply wrong: indentation is the syntax.
  wfEditor: { flex: 1, minHeight: 260, color: C.dim, fontSize: 12, lineHeight: 19, padding: 12, textAlignVertical: 'top' },
  hookNames: { gap: 2, flex: 1, minWidth: 150 },
  hookName: { color: C.text, fontSize: 12, ...mono },
  hookOff: { color: C.faint },
  hookSub: { color: C.faint, fontSize: 10 },
  hookCode: { color: C.dim, fontSize: 12, lineHeight: 19, padding: 12 },
  hookGap: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, padding: 24 },
  hookSaid: { fontSize: 11, paddingHorizontal: 12, paddingVertical: 8, borderTopWidth: 1, borderTopColor: C.line },
  checks: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 },
  check: { flexDirection: 'row', alignItems: 'center', gap: 6, maxWidth: 260, paddingVertical: 3, paddingHorizontal: 8, borderRadius: S.radius, backgroundColor: C.raised },
  checkName: { color: C.dim, fontSize: 11, flexShrink: 1 },
  diffWorkspace: { flex: 1, minHeight: 260, flexDirection: 'row', borderWidth: 1, borderColor: C.line, borderRadius: 6, overflow: 'hidden', backgroundColor: KEYBG() },
  diffWorkspaceNarrow: { flexDirection: 'column' },
  diffFiles: { width: 260, borderRightWidth: 1, borderRightColor: C.line, backgroundColor: C.panel },
  diffFilesNarrow: { width: '100%', borderRightWidth: 0, borderBottomWidth: 1, borderBottomColor: C.line },
  diffProgressRow: { padding: 10, gap: 7 },
  diffProgress: { color: C.dim, fontSize: 11, fontWeight: '600' },
  diffProgressTrack: { height: 3, borderRadius: 2, backgroundColor: C.raised, overflow: 'hidden' },
  diffProgressFill: { height: 3, backgroundColor: '#3cd05a' },
  diffFileTabs: { flexDirection: 'row' },
  diffFile: { minHeight: 42, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: 10, borderTopWidth: 1, borderTopColor: C.line },
  diffFileOn: { backgroundColor: C.raised },
  diffCheck: { color: C.faint, width: 14, fontSize: 13 },
  diffCheckOn: { color: '#3cd05a' },
  diffFileName: { color: C.dim, fontSize: 11, flex: 1, ...mono },
  diffStats: { fontSize: 10, ...mono },
  diffPlus: { color: '#68c77a' },
  diffMinus: { color: '#e77b72' },
  diffViewer: { flex: 1, minWidth: 0 },
  // The work screen's own furniture. The file column is the diff column's,
  // reused as it is; only the things git has and a pull request does not --
  // a state word, a per-row command, a commit graph -- are new here.
  wkGroup: { color: C.faint, fontSize: 10, fontWeight: '600', letterSpacing: 0.8, textTransform: 'uppercase', paddingHorizontal: 10, paddingTop: 10, paddingBottom: 4 },
  wkState: { color: C.faint, fontSize: 10, ...mono },
  wkKey: { color: C.accentText, fontSize: 10, fontWeight: '600', paddingHorizontal: 7, paddingVertical: 6, borderWidth: 1, borderColor: C.edge, borderRadius: 4, overflow: 'hidden' },
  wkKeyOn: { color: C.text, borderColor: C.accent, backgroundColor: C.raised },
  wkDrop: { color: C.bad },
  wkBtn: { minWidth: 84 },
  // Wider than the file column, because a graph row carries git's own art as
  // well as a subject -- and the art is only readable if nothing reflows it.
  wkTree: { width: 460, borderRightWidth: 1, borderRightColor: C.line, backgroundColor: C.panel },
  wkTreeNarrow: { maxHeight: 280 },
  wkCommit: { minHeight: 30, flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: 10 },
  wkArt: { color: C.accentText, fontSize: 12, lineHeight: 18, ...mono },
  wkSha: { color: C.warn, fontSize: 11, ...mono },
  wkRefs: { color: C.good, fontSize: 10, maxWidth: 150, ...mono },
  wkSubject: { color: C.dim, fontSize: 11, flex: 1 },
  wkWhen: { color: C.faint, fontSize: 10 },
  diffFileHead: { minHeight: 43, flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 12, borderBottomWidth: 1, borderBottomColor: C.line, backgroundColor: C.panel },
  diffFileHeadName: { flex: 1, color: C.text, fontSize: 12, ...mono },
  reviewedKey: { paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: C.edge, borderRadius: 5 },
  reviewedKeyOn: { borderColor: '#3cd05a', backgroundColor: '#14291a' },
  reviewedKeyText: { color: C.dim, fontSize: 11, fontWeight: '600' },
  diffScroll: { flex: 1 },
  diffCodeSheet: { minWidth: '100%' },
  diffLine: { minHeight: 20, flexDirection: 'row', alignItems: 'stretch' },
  diff_same: { backgroundColor: 'transparent' },
  diff_add: { backgroundColor: '#11291a' },
  diff_del: { backgroundColor: '#321818' },
  diff_hunk: { backgroundColor: '#172736' },
  diff_meta: { backgroundColor: '#181a1e' },
  diffNumber: { width: 44, paddingHorizontal: 6, color: C.faint, backgroundColor: 'rgba(255,255,255,.025)', borderRightWidth: 1, borderRightColor: C.line, textAlign: 'right', fontSize: 10, lineHeight: 20, ...mono },
  diffMark: { width: 22, color: C.faint, textAlign: 'center', fontSize: 11, lineHeight: 20, ...mono },
  diffCode: { color: C.dim, fontSize: 11, lineHeight: 20, paddingRight: 18, whiteSpace: 'pre', ...mono },
  reviewInput: { minHeight: 72, maxHeight: 130, color: C.text, backgroundColor: KEYBG(), borderWidth: 1, borderColor: C.edge, borderRadius: 6, padding: 10, textAlignVertical: 'top' },
  reviewBtn: { minWidth: 110 },
  subTag: {
    color: C.accentText,
    fontSize: 10,
    borderWidth: 1,
    borderColor: C.accent,
    borderRadius: 3,
    paddingHorizontal: 5,
    paddingVertical: 1,
    overflow: 'hidden',
  },
  model: { color: C.accentText, fontSize: 13, ...mono },
  path: { color: C.faint, fontSize: 13, ...mono },
  // A plain descriptive caption, deliberately not `path`'s mono/dot shape --
  // that shape reads as a sortable column header (see Sessions below), and
  // this isn't one.
  caption: { color: C.faint, fontSize: 12, fontStyle: 'italic' },
  act: { color: C.warn, fontSize: 12, flexShrink: 1 },
  mirrored: { color: C.dim, fontSize: 11, paddingHorizontal: 4, paddingTop: 4 },
  err: { color: C.bad, fontSize: 12 },
  head: { color: C.faint, fontSize: 10, letterSpacing: 1.2 },
  card: {
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    backgroundColor: C.panel,
    padding: 16,
    gap: 11,
  },
  tldrRow: { flexDirection: 'row', gap: 10 },
  tick: { width: 3, borderRadius: 2 },
  // 15/24 rather than 14/21: this is the one place in the app that holds
  // paragraphs rather than labels, and it was set like a label.
  tldrText: { color: C.text, fontSize: 15, lineHeight: 24, flexShrink: 1 },
  tldrBold: { color: C.text, fontWeight: '700' },
  tldrCode: { color: C.accentText, fontSize: 13, ...mono },
  transcript: {
    flex: 1,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    backgroundColor: KEYBG(),
    padding: 14,
    gap: 6,
  },
  contextPanel: { borderWidth: 1, borderColor: C.line, borderRadius: 7, backgroundColor: C.panel, overflow: 'hidden' },
  contextBar: { minHeight: 34, flexDirection: 'row', alignItems: 'center', gap: 9, paddingHorizontal: 10 },
  contextLabel: { color: C.faint, fontSize: 11 },
  contextTrack: { flex: 1, height: 4, borderRadius: 2, backgroundColor: C.raised, overflow: 'hidden' },
  contextFill: { height: '100%', borderRadius: 2, backgroundColor: C.accentText },
  contextPct: { color: C.dim, fontSize: 11, minWidth: 32, textAlign: 'right', ...mono },
  contextAction: { width: 28, height: 28, alignItems: 'center', justifyContent: 'center', borderRadius: 5 },
  contextActionArmed: { backgroundColor: '#321c1c' },
  contextSettings: { minHeight: 34, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 10, borderTopWidth: 1, borderTopColor: C.line },
  contextSettingLabel: { color: C.faint, fontSize: 10 },
  contextSettingRule: { width: 1, height: 16, backgroundColor: C.line, marginHorizontal: 4 },
  contextSelect: { minHeight: 28, justifyContent: 'center', paddingHorizontal: 5 },
  contextSelectText: { color: C.text, fontSize: 11, fontWeight: '500' },
  pretty: { backgroundColor: C.bg, padding: 0, overflow: 'hidden' },
  // A conversation is prose, so it gets prose spacing: turns far enough
  // apart to be separate thoughts, and a measure that stops the eye having
  // to travel a 1400px line back to the start of the next one.
  chat: { padding: 22, gap: 26, maxWidth: 900, width: '100%' },
  userTurn: {
    alignSelf: 'flex-end',
    maxWidth: '84%',
    backgroundColor: C.raised,
    borderRadius: 14,
    borderBottomRightRadius: 4,
    paddingHorizontal: 14,
    paddingVertical: 11,
    gap: 5,
  },
  agentTurn: { alignSelf: 'stretch', maxWidth: 760, gap: 9, paddingVertical: 2 },
  speaker: { color: C.faint, fontSize: 10, fontWeight: '700', letterSpacing: 0.8, textTransform: 'uppercase' },
  markdown: { gap: 9 },
  mdHead: { fontSize: 16, lineHeight: 25, fontWeight: '700', paddingTop: 4 },
  mdShout: { color: C.faint, fontSize: 10, fontWeight: '700', letterSpacing: 1, lineHeight: 16, paddingTop: 4 },
  mdBullet: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  mdBulletMark: { color: C.accentText, fontSize: 15, lineHeight: 24 },
  working: { alignSelf: 'stretch', maxWidth: 760, gap: 6, paddingVertical: 10, paddingHorizontal: 12, borderLeftWidth: 2, borderLeftColor: '#f0c828', backgroundColor: C.panel, borderRadius: 6 },
  workingHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  workingDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: '#f0c828' },
  workingDotOff: { backgroundColor: C.edge },
  workingAct: { color: C.warn, fontSize: 12, flexShrink: 1 },
  workingNow: { color: C.dim, fontSize: 13, lineHeight: 19, ...mono },
  workingMore: { color: C.faint, fontSize: 11 },
  receiptKey: { alignSelf: 'flex-start', paddingVertical: 5, paddingHorizontal: 8 },
  turnActions: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  receiptKeyText: { color: C.faint, fontSize: 11 },
  modalBack: { flex: 1, backgroundColor: 'rgba(0,0,0,.72)', alignItems: 'center', justifyContent: 'center', padding: 24 },
  receipt: { width: '100%', maxWidth: 760, maxHeight: '82%', backgroundColor: C.panel, borderWidth: 1, borderColor: C.edge, borderRadius: S.radius, padding: 18, gap: 14 },
  receiptTitle: { color: C.text, fontSize: 18, fontWeight: '600' },
  receiptClose: { color: C.faint, fontSize: 12, padding: 6 },
  receiptLines: { gap: 5, paddingBottom: 8 },
  receiptLine: { color: C.dim, fontSize: 12, lineHeight: 18, ...mono },
  terminalKey: { minHeight: 38, alignItems: 'center', justifyContent: 'center', borderTopWidth: 1, borderTopColor: C.line },
  terminalKeyText: { color: C.accentText, fontSize: 12, fontWeight: '600' },
  // flex:0 so the box sizes to its lines rather than to a bound that no
  // longer exists; the inner ScrollView's own scroll is turned off to match
  // (scrollEnabled), since a scrollbox with no height just clips otherwise.
  transcriptNarrow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto' },
  // RN-Web's ScrollView hardcodes flexGrow:1/flexShrink:1 on its own base
  // style regardless of what its parent asks for -- flexGrow:0 alone left
  // flexShrink:1 in place, which is enough for it to still collapse inside
  // an auto-height ancestor. Both have to be overridden.
  linesNarrow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto' },
  lines: {},
  // horizontal ScrollView defaults its content to a row and stretches
  // children to its own height -- overridden back to the column of lines
  // this actually is, with alignItems left off 'stretch' so a line's width
  // is its own text, not the box's, which is what lets a long one make the
  // content wider than the box in the first place.
  linesH: { width: '100%' },
  linesHContent: { flexDirection: 'column', alignItems: 'flex-start', gap: 2 },
  // no wrap: tmux already chose where this line ends, and re-wrapping it
  // to this box's width is exactly the bug being fixed.
  // It has to be focusable and it has to be reachable by a tap, so it is a
  // real input sitting under the capture rather than a hidden one -- a caret
  // blinking where you tapped is also the honest signal that keys are live.
  mates: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, paddingBottom: 2 },
  mate: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderWidth: 1, borderColor: C.line, borderRadius: 6, backgroundColor: C.panel },
  mateOn: { borderColor: C.accentText, backgroundColor: C.raised },
  mateName: { color: C.faint, fontSize: 11, fontWeight: '600', maxWidth: 160 },
  mateNameOn: { color: C.text },
  termKeys: { height: 30, marginTop: 6, color: C.text, backgroundColor: KEYBG(), borderWidth: 1, borderColor: C.line, borderRadius: 5, paddingHorizontal: 9, fontSize: 12 },
  termKeysOn: { borderColor: '#3cd05a' },
  termPads: { flexDirection: 'row', flexWrap: 'wrap', gap: 5, paddingTop: 6 },
  termPad: { minWidth: 40, alignItems: 'center', paddingVertical: 5, paddingHorizontal: 8, borderWidth: 1, borderColor: C.edge, borderRadius: 5, backgroundColor: C.panel },
  termPadText: { color: C.dim, fontSize: 11, fontWeight: '600', ...mono },
  line: { color: C.dim, fontSize: 12, lineHeight: 18, whiteSpace: 'pre', ...mono },
  composer: { gap: 8 },
  // Sideways rather than wrapped: two or three of these is the whole point
  // (a paste appends, it doesn't replace), and a wrapping strip would push
  // the box that matters -- the text -- down the page for every one added.
  thumbStrip: { flexDirection: 'row', gap: 8 },
  thumbWrap: { width: 56, height: 56 },
  thumb: {
    width: 56,
    height: 56,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    backgroundColor: C.raised,
  },
  // Overlapping the corner rather than beside it -- a thumbnail this small
  // has no room to spare for a control next to it, only on it.
  thumbX: {
    position: 'absolute',
    top: -6,
    right: -6,
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: C.panel,
    borderWidth: 1,
    borderColor: C.line,
    alignItems: 'center',
    justifyContent: 'center',
  },
  composerInput: {
    minHeight: 60,
    maxHeight: 140,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    backgroundColor: C.raised,
    color: C.text,
    fontSize: 14,
    padding: 12,
    ...mono,
  },
  queued: { color: '#e0a03c', fontSize: 11, lineHeight: 16 },
  queuedLine: { color: '#e0a03c', fontSize: 13, lineHeight: 19 },
  composerRow: { flexDirection: 'row', gap: 8 },
  composerBtn: { flex: 1, paddingHorizontal: 16 },
  rows: { gap: 5, paddingBottom: 8 },
  // RN-Web's ScrollView carries its own flexGrow:1 no matter what its
  // parent asks for, so an auto-height parent (bodyNarrow) still handed it
  // room to try to fill and got back a collapsed box with its rows painted
  // outside it -- the note below and the pads panel after it landed right
  // on top of that overflow. flex:0 here is the same override bodyNarrow
  // already does one level up, just repeated where RN-Web actually needs it.
  rowsNarrow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto' },
  row: {
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    backgroundColor: C.panel,
  },
  mine: { borderColor: C.accent },
  rowBody: { flexShrink: 1, gap: 2 },
  rowName: { color: C.text, fontSize: 14, flexShrink: 1 },
  rowSub: { color: C.faint, fontSize: 12 },
  tag: {
    color: C.dim,
    fontSize: 10,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: 3,
    paddingHorizontal: 5,
    paddingVertical: 1,
    overflow: 'hidden',
  },
  dot: { width: 10, height: 10, borderRadius: 5 },
  chip: { width: 11, height: 11, borderRadius: 3 },
  note: { color: C.faint, fontSize: 11.5, lineHeight: 17 },
  asking: { gap: 16 },
  askHead: { color: C.bad, fontSize: 10, letterSpacing: 1.2 },
  askText: { color: C.dim, fontSize: 13 },
  opts: { gap: 10 },
  opt: { minHeight: 60, alignItems: 'stretch', justifyContent: 'center', paddingHorizontal: 16, paddingBottom: 10 },
  optRow: { flexDirection: 'row', alignItems: 'center', gap: 14, width: '100%' },
  optNum: {
    color: C.bg,
    fontSize: 13,
    fontWeight: '700',
    minWidth: 26,
    textAlign: 'center',
    paddingVertical: 3,
    borderRadius: 5,
    overflow: 'hidden',
    ...mono,
  },
  optLabel: { color: C.text, fontSize: 15, flexShrink: 1 },
  customAnswer: { flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 1, borderRadius: S.radius, padding: 10 },
  customAnswerInput: { flex: 1, minHeight: 42, maxHeight: 110, color: C.text, backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 6, paddingHorizontal: 10, paddingVertical: 8, fontSize: 14 },
  customAnswerSend: { minWidth: 64, minHeight: 42 },
  orDivider: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 6 },
  orRule: { flex: 1, height: 1, backgroundColor: C.line },
  orText: { color: C.faint, fontSize: 10, fontWeight: '600', letterSpacing: 0.8 },
  chatAbout: { alignSelf: 'flex-start', paddingHorizontal: 2, paddingVertical: 10 },
  chatAboutText: { color: C.text, fontSize: 14, fontWeight: '700' },
  walk: { flexDirection: 'row', gap: 14 },
  walkAt: { color: C.faint, fontSize: 12 },
  walkOn: { color: C.text },
  cols: { flex: 1, flexDirection: 'row', gap: 8 },
  // Eight cards in a row do not fit a phone -- wrap them instead of
  // squeezing every one down to unreadable, one full-width card a line.
  colsNarrow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto', flexWrap: 'wrap' },
  col: {
    flex: 1,
    minWidth: 0,
    borderWidth: 1,
    borderColor: C.line,
    borderTopWidth: 2,
    borderRadius: S.radius,
    backgroundColor: C.panel,
    padding: 13,
    gap: 6,
  },
  colNarrow: { flexBasis: '100%', flexGrow: 0, flexShrink: 0 },
  colOn: { backgroundColor: C.raised, borderColor: C.edge },
  colName: { color: C.text, fontSize: 13, fontWeight: '600' },
  colId: { color: C.edge, fontSize: 11 },
  ctxTrack: { height: 4, borderRadius: 2, backgroundColor: C.raised, overflow: 'hidden' },
  ctxFill: { height: '100%', backgroundColor: C.accent },
  bar: { gap: 7, paddingVertical: 4 },
  barTrack: { height: 10, borderRadius: 5, backgroundColor: C.raised, overflow: 'hidden' },
  barFill: { height: '100%' },
});

function KEYBG() {
  return '#08080a';
}
