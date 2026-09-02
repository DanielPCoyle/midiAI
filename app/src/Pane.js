import { useEffect, useRef, useState } from 'react';
import {
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { pasteImage, promptAgent, startRecording, stopRecording } from './api';
import PushButton from './PushButton';
import { ANSWER_HEX, BREAK, C, S, SEAT_HEX, mono } from './theme';

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
  onAnswer,
  base,
  onSent,
  mode,
  onMode,
  reachable = true,
  onComposerFocus,
  padsOpen,
  padsCount,
  onTogglePads,
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
        sub={data.sub}
        base={base}
        onSent={onSent}
        onComposerFocus={onComposerFocus}
        padsOpen={padsOpen}
        padsCount={padsCount}
        onTogglePads={onTogglePads}
      />
    );
  if (kind === 'sessions') return <Sessions cols={cols} current={current} />;
  if (kind === 'subs') return <Subs data={data} />;
  if (kind === 'tests') return <Tests data={data} />;
  if (kind === 'prs') return <Prs data={data} />;
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
  return (
    <ScrollView contentContainerStyle={[styles.body, styles.asking]}>
      <Text style={styles.askHead}>ASKING</Text>
      <Text style={styles.askText}>{opts.length} ways to answer</Text>
      <View style={styles.opts}>
        {opts.map(([num, label], k) => {
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
      </View>
      <Text style={styles.note}>
        Or answer from the Push — the same colours, down the left column.
      </Text>
    </ScrollView>
  );
}

function Focus({ info, sub, base, onSent, onComposerFocus, padsOpen, padsCount, onTogglePads }) {
  const narrow = useNarrow();
  const railHidden = useRailHidden();
  if (!info) return <Empty what="no agent selected" />;
  const tldr = info.tldr || [];
  const lines = info.lines || [];
  // Status used to be said twice within 200px: once here, once on this same
  // agent's rail card. The rail is the list you scan, so it's the right home
  // for it, and this title already names which agent you're looking at.
  //
  // But the rail is a drawer below BREAK.wide, and then the title is the only
  // home there is -- de-duplicating a fact is only right while both copies are
  // on screen. Say it here exactly when the rail cannot.
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
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
        {!!info.act && (
          <Text style={styles.act} numberOfLines={1}>
            {info.act}
          </Text>
        )}
      </View>

      {/* Composer stays reachable at every width, so the transcript is what
          gives -- below 820 it stops being a bounded scroll box of its own
          (there is no flex:1 ancestor to bound it inside) and just lays its
          lines into the page, which is one long scroll by then anyway. */}
      <View style={[styles.transcript, narrow && styles.transcriptNarrow]}>
        <View style={styles.title}>
          <Text style={styles.head}>TRANSCRIPT</Text>
          <View style={styles.spacer} />
          {info.scroll > 0 && (
            <Text style={styles.dim}>{info.scroll} back</Text>
          )}
        </View>
        {/* tmux capture-pane has already wrapped every line to the real
            pane's width, not this box's -- laying that text into a narrower
            column wraps it again, raggedly, which is not what the terminal
            being mirrored looks like. Each line stays on the row tmux gave
            it (no wrap, no shrink) and the extra width scrolls sideways
            instead, inside this ScrollView alone -- it never reaches the
            page, which still only scrolls the one way at any size. */}
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
      </View>

      {tldr.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.head}>TLDR</Text>
          {tldr.map((line, i) => (
            <View key={i} style={styles.tldrRow}>
              <View
                style={[
                  styles.tick,
                  { backgroundColor: i === 0 ? '#3cd05a' : C.accentText },
                ]}
              />
              <Rich line={line} />
            </View>
          ))}
        </View>
      )}

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

      <Composer
        info={info}
        base={base}
        onSent={onSent}
        onComposerFocus={onComposerFocus}
        padsOpen={padsOpen}
        padsCount={padsCount}
        onTogglePads={onTogglePads}
      />
    </View>
  );
}

// A TLDR row is scraped, so it arrives as the markdown the agent wrote:
// leading bullet, **bold** labels, `code` spans. Drawn raw those markers are
// punctuation in the way of the sentence.
// ponytail: bold and code only, which is all a TLDR actually uses -- an
// unmatched or unknown marker just falls through as the text it already is.
const SPAN = /(\*\*[^*]+\*\*|`[^`]+`)/g;

function Rich({ line }) {
  return (
    <Text style={styles.tldrText}>
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

// The only way to talk to an agent used to be a macro pad -- fixed text,
// picked in advance. This is the other half: whatever you type or say, right
// now.
//
// One Send, and it submits. There used to be two, because staging a prompt in
// the pane and firing it are different acts. That distinction died when this
// box started mirroring the pane's own input line: staging now writes text
// the composer immediately reads back, so the button that did it looked like
// it had done nothing.
function Composer({ info, base, onSent, onComposerFocus, padsOpen, padsCount, onTogglePads }) {
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
      await promptAgent(base, text, submit, info.tid, true);
      setText(''); // only on success -- a failed send keeps what you typed
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
  async function attach(b64) {
    if (!b64) return;
    setShot(true);
    setErr('');
    try {
      const path = await pasteImage(base, b64);
      setText((t) => (t ? `${t.trim()} ${path}` : path));
    } catch (e) {
      setErr(String((e && e.message) || e));
    } finally {
      setShot(false);
    }
  }

  function attachFile(file) {
    if (!file) return;
    const r = new FileReader();
    r.onerror = () => setErr('could not read that file');
    // strip the data: prefix -- the server wants base64, not a URL
    r.onload = () => attach(String(r.result).split(',')[1] || '');
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
      if (!res.canceled) attach(res.assets[0]?.base64);
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
      {/* One row, so labels are short by necessity: a third of the width
          truncates anything longer, and PushButton draws a single line. */}
      <View style={styles.composerRow}>
        {/* The prompts panel opens from here rather than the header: it is
            a way of putting text in this box, so it belongs beside the box
            and not up in the chrome with the connection status. */}
        {!!onTogglePads && (
          <PushButton
            label={`prompts · ${padsCount ?? 0}`}
            colour={C.accentText}
            lit={padsOpen}
            onPress={onTogglePads}
            style={styles.composerBtn}
          />
        )}
        <PushButton
          label={shot ? 'Saving…' : Platform.OS === 'web' ? 'Image ⌘V' : 'Image'}
          disabled={shot || sending}
          onPress={pick}
          style={styles.composerBtn}
        />
        <PushButton
          label={
            rec === 'on' ? 'Listening…'
              : rec === 'busy' ? 'Transcribing…'
              : 'Hold to talk'
          }
          colour="#e03c3c"
          lit={rec === 'on'}
          disabled={sending || rec === 'busy'}
          onPressIn={() => talk(true)}
          onPressOut={() => talk(false)}
          style={styles.composerBtn}
        />
        <PushButton
          label={armed ? 'Cancel queue' : busy ? 'Queue ↵' : 'Send ↵'}
          colour={armed ? '#e0a03c' : C.accent}
          lit={armed || !disabled}
          disabled={sending || (!armed && empty)}
          onPress={() => (armed ? setArmed(false) : busy ? setArmed(true) : send(true))}
          style={styles.composerBtn}
        />
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
        <Text style={styles.path}>repo · status · model · terminal id</Text>
      </View>
      <View style={[styles.cols, narrow && styles.colsNarrow]}>
        {live.map(({ c, i }) => {
          const hue = SEAT_HEX[c.status] || C.faint;
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
              <Text style={[styles.rowSub, { color: hue }]}>{c.status}</Text>
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

function Tests({ data }) {
  const narrow = useNarrow();
  const items = data.items || [];
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>{data.repo}</Text>
        <Text style={styles.path}>/{(data.path || []).join('/')}</Text>
        <View style={styles.spacer} />
        {!!data.running && <Text style={styles.act}>{data.running}</Text>}
        <Text style={[styles.model, { color: '#3cd05a' }]}>{data.passed} passed</Text>
        {data.failed > 0 && (
          <Text style={[styles.model, { color: '#e03c3c' }]}>
            {data.failed} failed
          </Text>
        )}
      </View>
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {items.map((it, i) => (
          <View key={i} style={styles.row}>
            <View
              style={[styles.chip, { backgroundColor: TEST_HEX[it.state] || C.edge }]}
            />
            <Text style={[styles.rowName, mono]}>
              {it.name}
              {it.dir ? '/' : ''}
            </Text>
            <View style={styles.spacer} />
            <Text style={styles.rowSub}>{it.state || 'not run'}</Text>
          </View>
        ))}
        {items.length === 0 && <Empty what="no tests here" />}
      </ScrollView>
      <Text style={styles.note}>
        A directory wears the worst state beneath it — follow red down to the file
        without knowing where it lives.
      </Text>
    </View>
  );
}

function Prs({ data }) {
  const narrow = useNarrow();
  const rows = data.rows || [];
  return (
    <View style={[styles.body, narrow && styles.bodyNarrow]}>
      <View style={styles.title}>
        <Text style={styles.h1}>{data.repo}</Text>
        <Text style={styles.model}>{rows.length} open</Text>
      </View>
      {!!data.err && <Text style={styles.err}>{data.err}</Text>}
      <ScrollView contentContainerStyle={styles.rows} style={narrow ? styles.rowsNarrow : undefined}>
        {rows.map((pr, i) => (
          <View key={i} style={[styles.row, pr.mine && styles.mine]}>
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
          </View>
        ))}
        {rows.length === 0 && !data.err && <Empty what="nothing open" />}
      </ScrollView>
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
  act: { color: C.warn, fontSize: 12, flexShrink: 1 },
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
  tldrText: { color: C.text, fontSize: 14, lineHeight: 21, flexShrink: 1 },
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
  line: { color: C.dim, fontSize: 12, lineHeight: 18, whiteSpace: 'pre', ...mono },
  composer: { gap: 8 },
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
