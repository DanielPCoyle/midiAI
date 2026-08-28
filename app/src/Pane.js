import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { promptAgent } from './api';
import PushButton from './PushButton';
import { ANSWER_HEX, C, S, SEAT_HEX, mono } from './theme';

const TEST_HEX = { pass: '#3cd05a', fail: '#e03c3c', run: '#f0c828', '': C.edge };
const CI_HEX = { pass: '#3cd05a', fail: '#e03c3c', pending: '#e0d02c', none: C.edge };

// The centre pane: the active view, drawn at sizes a person reads from a desk
// rather than scaled up off a 960x160 strip meant to be read from a keyboard.
//
// New props, for the composer on the focus view (the contract to wire from
// App.js against):
//   base    - string, server base url (api.baseFor(host)). Required for the
//             composer to send anything; omit it and Send/Send ↵ still
//             render but every send rejects, same as any other bad host.
//   onSent  - optional (): void, called after a send lands. The composer
//             clears itself either way it can tell the send worked; this is
//             for the app to refresh sooner than its next poll, not for the
//             composer's own state.
export default function Pane({ data, opts, cols, current, onAnswer, base, onSent }) {
  const kind = (data || {}).kind;
  // A question takes the glass only where the glass was already about this
  // session. Walk to tests or prs with one pending and the Push keeps drawing
  // tests -- the pads are what answer from wherever you are, and here that is
  // the rail.
  if (opts && opts.length && (!kind || kind === 'focus')) {
    return <Question opts={opts} onAnswer={onAnswer} />;
  }
  if (kind === 'focus')
    return <Focus info={data.info} sub={data.sub} base={base} onSent={onSent} />;
  if (kind === 'sessions') return <Sessions cols={cols} current={current} />;
  if (kind === 'subs') return <Subs data={data} />;
  if (kind === 'tests') return <Tests data={data} />;
  if (kind === 'prs') return <Prs data={data} />;
  if (kind === 'usage') return <Usage data={data} />;
  return <Empty what="waiting for push_cc" />;
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

function Focus({ info, sub, base, onSent }) {
  if (!info) return <Empty what="no session selected" />;
  const hue = SEAT_HEX[info.status] || C.faint;
  const tldr = info.tldr || [];
  const lines = info.lines || [];
  return (
    <View style={styles.body}>
      <View style={styles.title}>
        <Text style={styles.h1}>{info.name}</Text>
        {!!sub && <Text style={styles.subTag}>subagent</Text>}
        <Text style={styles.model}>{info.model}</Text>
        <Text style={[styles.model, { color: hue }]}>{info.status}</Text>
        <View style={styles.spacer} />
        {!!info.act && (
          <Text style={styles.act} numberOfLines={1}>
            {info.act}
          </Text>
        )}
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
              <Text style={styles.tldrText}>{line}</Text>
            </View>
          ))}
        </View>
      )}

      {!!info.pending && (
        <View style={[styles.card, info.suggested && styles.suggested]}>
          <Text style={styles.head}>
            {info.suggested ? 'PROPOSED — A MACRO PUT THIS THERE' : 'TYPING'}
          </Text>
          <Text style={styles.pending}>{info.pending}</Text>
        </View>
      )}

      <View style={styles.transcript}>
        <View style={styles.title}>
          <Text style={styles.head}>TRANSCRIPT</Text>
          <View style={styles.spacer} />
          {info.scroll > 0 && (
            <Text style={styles.dim}>{info.scroll} back</Text>
          )}
        </View>
        <ScrollView contentContainerStyle={styles.lines}>
          {lines.slice(-60).map((line, i) => (
            <Text key={i} style={styles.line}>
              {line}
            </Text>
          ))}
        </ScrollView>
      </View>

      <Composer info={info} base={base} onSent={onSent} />
    </View>
  );
}

// The only way to talk to an agent used to be a macro pad -- fixed text,
// picked in advance. This is the other half: whatever you type, right now.
// Two buttons rather than one because a macro pad draws that same line --
// loading a prompt and firing it are different acts, and collapsing them
// into one button would make this the one control that can't tell you which
// it just did.
function Composer({ info, base, onSent }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState('');

  // a question is answered from the opt rows, not typed over -- two ways to
  // answer the same thing could disagree, so while one is open the composer
  // steps back rather than competing with it.
  if (info.opts && info.opts.length) return null;

  const empty = !text.trim();
  const disabled = empty || sending;

  async function send(submit) {
    setSending(true);
    setErr('');
    try {
      await promptAgent(base, text, submit, info.tid);
      setText(''); // only on success -- a failed send keeps what you typed
      onSent && onSent();
    } catch (e) {
      setErr(String((e && e.message) || e));
    } finally {
      setSending(false);
    }
  }

  return (
    <View style={styles.composer}>
      <TextInput
        style={styles.composerInput}
        value={text}
        onChangeText={setText}
        placeholder="type to the agent"
        placeholderTextColor={C.faint}
        multiline
        editable={!sending}
      />
      {!!err && <Text style={styles.err}>{err}</Text>}
      <View style={styles.composerRow}>
        <PushButton
          label="Send"
          disabled={disabled}
          onPress={() => send(false)}
          style={styles.composerBtn}
        />
        <PushButton
          label="Send ↵"
          colour={C.accent}
          lit={!disabled}
          disabled={disabled}
          onPress={() => send(true)}
          style={styles.composerBtn}
        />
      </View>
    </View>
  );
}

// Every session side by side. The id tail earns its space: two checkouts of
// one repo show the same name, and it is the only thing that tells them apart.
function Sessions({ cols, current }) {
  const live = (cols || []).map((c, i) => ({ c, i })).filter((x) => x.c);
  return (
    <View style={styles.body}>
      <View style={styles.title}>
        <Text style={styles.h1}>Every session</Text>
        <Text style={styles.path}>repo · status · model · terminal id</Text>
      </View>
      <View style={styles.cols}>
        {live.map(({ c, i }) => {
          const hue = SEAT_HEX[c.status] || C.faint;
          return (
            <View
              key={i}
              style={[styles.col, { borderTopColor: hue }, i === current && styles.colOn]}>
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
        {live.length === 0 && <Empty what="no sessions" />}
      </View>
    </View>
  );
}

function Subs({ data }) {
  const rows = data.rows || [];
  return (
    <View style={styles.body}>
      <View style={styles.title}>
        <Text style={styles.h1}>{data.repo}</Text>
        <Text style={styles.model}>{rows.length} subagents</Text>
      </View>
      <ScrollView contentContainerStyle={styles.rows}>
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
  const items = data.items || [];
  return (
    <View style={styles.body}>
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
      <ScrollView contentContainerStyle={styles.rows}>
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
  const rows = data.rows || [];
  return (
    <View style={styles.body}>
      <View style={styles.title}>
        <Text style={styles.h1}>{data.repo}</Text>
        <Text style={styles.model}>{rows.length} open</Text>
      </View>
      {!!data.err && <Text style={styles.err}>{data.err}</Text>}
      <ScrollView contentContainerStyle={styles.rows}>
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

function Usage({ data }) {
  const at = data.at || 0;
  return (
    <View style={styles.body}>
      <View style={styles.title}>
        <Text style={styles.h1}>usage</Text>
        <View style={styles.spacer} />
        <View style={styles.walk}>
          {['plan', 'by model', 'by agent'].map((n, i) => (
            <Text key={n} style={[styles.walkAt, i === at && styles.walkOn]}>
              {n}
            </Text>
          ))}
        </View>
      </View>
      <ScrollView contentContainerStyle={styles.rows}>
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

function Bar({ bar }) {
  const pct = Math.max(0, Math.min(1, Number(bar.frac ?? bar[1] ?? 0)));
  const label = bar.label ?? bar[0] ?? '';
  const hue = pct > 0.8 ? '#e08a2c' : '#3cd05a';
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
  suggested: { borderColor: C.accent },
  tldrRow: { flexDirection: 'row', gap: 10 },
  tick: { width: 3, borderRadius: 2 },
  tldrText: { color: C.text, fontSize: 14, lineHeight: 21, flexShrink: 1 },
  pending: { color: C.text, fontSize: 14, lineHeight: 21, ...mono },
  transcript: {
    flex: 1,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    backgroundColor: KEYBG(),
    padding: 14,
    gap: 6,
  },
  lines: { gap: 2 },
  line: { color: C.dim, fontSize: 12, lineHeight: 18, ...mono },
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
  composerRow: { flexDirection: 'row', gap: 8 },
  composerBtn: { flex: 1, paddingHorizontal: 16 },
  rows: { gap: 5, paddingBottom: 8 },
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
