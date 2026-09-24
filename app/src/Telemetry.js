import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { getTelemetry } from './api';
import Icon from './Icon';
import { BREAK, C, S, mono } from './theme';

const RANGES = [['1h', 3600], ['24h', 86400], ['7d', 604800]];
const POLL_MS = 10000;

// telemetry.py's http spans are tagged `midiai.poll=true` for the hot-path
// polls (mapui's own /surface and /queue), but the /telemetry summary
// contract (its `http` rows: {route, count, errors, p50, p95, max}) does not
// carry that per-row flag through -- so "hide polls" matches on the route
// text itself, the same two paths the telemetry spec names as the pollers.
// the server marks poll routes (`poll` on each http row); the route-name test
// is only for a server from before that field existed
const isPollRow = (r) => (typeof r?.poll === 'boolean' ? r.poll : /\/(surface|queue)\b/.test(r?.route || ''));

function fmtMs(n) {
  if (n == null) return '—';
  return n < 1000 ? `${Math.round(n)}ms` : `${(n / 1000).toFixed(1)}s`;
}
function fmtCost(n) {
  const v = Number(n) || 0;
  if (!v) return '$0';
  return v < 0.01 ? '<$0.01' : `$${v.toFixed(2)}`;
}
function fmtTok(n) {
  const v = Number(n) || 0;
  if (!v) return '0';
  if (v >= 1000000) return `${(v / 1000000).toFixed(1)}m`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return `${v}`;
}
function fmtPct(n) {
  return `${(Number(n) || 0).toFixed(1)}%`;
}
// ts is seconds (Python's time.time()), same coarse "how long ago" rule
// Deploy.js's agoText uses for milliseconds.
function agoText(tsSec) {
  if (!tsSec) return '';
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - tsSec));
  if (secs < 45) return 'now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function useNarrow() {
  return useWindowDimensions().width < BREAK.mid;
}

function Tile({ label, value, valueColor, sub }) {
  return (
    <View style={styles.tile}>
      <Text style={styles.tileLabel}>{label}</Text>
      <Text style={[styles.tileValue, !!valueColor && { color: valueColor }]}>{value}</Text>
      {!!sub && <Text numberOfLines={1} style={styles.tileSub}>{sub}</Text>}
    </View>
  );
}

function Section({ title, hint, right, style, children }) {
  return (
    <View style={[styles.section, style]}>
      <View style={styles.sectionHead}>
        <Text style={styles.sectionTitle}>{title}</Text>
        <View style={styles.spacer} />
        {right}
        {!!hint && <Text style={styles.sectionHint}>{hint}</Text>}
      </View>
      {children}
    </View>
  );
}

function Row({ children, last }) {
  return <View style={[styles.row, !last && styles.rowBorder]}>{children}</View>;
}

function Cell({ children, style, mono: isMono, dim }) {
  return (
    <Text
      numberOfLines={1}
      style={[styles.cell, dim && styles.cellDim, isMono && mono, style]}>
      {children}
    </Text>
  );
}

function Empty({ text }) {
  return <Text style={styles.emptyRow}>{text}</Text>;
}

// The TELEMETRY tab, app-only: rendered and selected entirely by App.js's
// own `telemetryOpen` flag, never sent to the Push as a `tab` press (see
// App.js, which mirrors the same rule DEPLOY's `deployOpen` already follows
// and keeps the two mutually exclusive). One poll of GET /telemetry, every
// POLL_MS while this is mounted.
//
// props:
//   base   string
export default function Telemetry({ base }) {
  const narrow = useNarrow();
  // The design canvas offers "your app" / "your agents" as the subject --
  // that view needs Sentry/Vercel/CloudWatch, an integration this session
  // does not have (see the spec's note to leave it for later). Only midiAI's
  // own health is wired up, so the segment is fixed to it and the other
  // reads as disabled rather than lying about being a live choice.
  const [since, setSince] = useState(86400);
  const [hidePolls, setHidePolls] = useState(true);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const timerRef = useRef(null);

  useEffect(() => {
    setData(null);
    setErr('');
    setLoading(true);
    if (!base) { setLoading(false); return undefined; }
    let live = true;
    const pull = () => {
      getTelemetry(base, since)
        .then((d) => {
          if (!live) return;
          setData(d);
          setErr('');
        })
        .catch((e) => {
          // No route yet (an older mapui, or the Python half of this still
          // landing) -- quiet, the empty state below already says so rather
          // than a red error banner over a feature that just isn't up yet.
          if (!live) return;
          setData(null);
          setErr(String((e && e.message) || e));
        })
        .finally(() => live && setLoading(false));
    };
    pull();
    timerRef.current = setInterval(pull, POLL_MS);
    return () => { live = false; clearInterval(timerRef.current); };
  }, [base, since]);

  const totals = data?.totals || {};
  const http = data?.http || [];
  const models = data?.models || [];
  const tools = data?.tools || [];
  const guardrails = data?.guardrails || [];
  const integrations = data?.integrations || [];
  const errors = data?.errors || [];

  const errorPct = totals.requests ? (totals.errors / totals.requests) * 100 : 0;
  const slowest = http.reduce((a, b) => ((b.p95 || 0) > (a?.p95 || 0) ? b : a), null);
  const tokensIn = models.reduce((n, m) => n + (m.input_tokens || 0), 0);
  const tokensOut = models.reduce((n, m) => n + (m.output_tokens || 0), 0);

  const shownHttp = (hidePolls ? http.filter((r) => !isPollRow(r)) : http)
    .slice()
    .sort((a, b) => (b.p95 || 0) - (a.p95 || 0));
  const shownTools = tools.slice().sort((a, b) => (b.p95 || 0) - (a.p95 || 0));

  if (!base) return null;

  const quiet = !loading && !data;

  return (
    <View style={styles.root}>
      <ScrollView contentContainerStyle={styles.scrollBody}>
        <View style={[styles.headRow, narrow && styles.headRowNarrow]}>
          <Text style={styles.h1}>telemetry</Text>
          {loading && <ActivityIndicator size="small" color={C.faint} />}

          <View style={styles.segTabs}>
            <Text style={[styles.segTab, styles.segTabOn]}>midiAI</Text>
            <Text
              accessibilityRole="button"
              accessibilityState={{ disabled: true }}
              accessibilityLabel="your app — needs an integration"
              style={[styles.segTab, styles.segTabDisabled]}>
              your app <Text style={styles.segTabHint}>needs an integration</Text>
            </Text>
          </View>

          <View style={styles.spacer} />

          <View style={styles.segTabs}>
            {RANGES.map(([word, secs]) => (
              <Text
                key={word}
                accessibilityRole="button"
                accessibilityState={{ selected: since === secs }}
                onPress={() => setSince(secs)}
                style={[styles.segTab, since === secs && styles.segTabOn]}>
                {word}
              </Text>
            ))}
          </View>
        </View>

        {!!err && quiet && (
          <Text style={styles.quietText}>telemetry isn't reachable yet — {err}</Text>
        )}

        {!quiet && (
          <>
            <View style={[styles.tiles, narrow && styles.tilesNarrow]}>
              <Tile
                label="REQUESTS"
                value={fmtTok(totals.requests)}
                sub={`${fmtPct(errorPct)} error`}
                valueColor={errorPct > 0 ? C.bad : undefined}
              />
              <Tile
                label="SLOWEST ROUTE"
                value={slowest ? fmtMs(slowest.p95) : '—'}
                sub={slowest ? slowest.route : 'nothing recorded'}
              />
              <Tile
                label="MODEL CALLS"
                value={fmtTok(totals.model_calls)}
                sub={`${fmtCost(totals.cost_usd)} · ${fmtTok(tokensIn)} in / ${fmtTok(tokensOut)} out`}
              />
              <Tile
                label="ERRORS"
                value={fmtTok(totals.errors)}
                valueColor={totals.errors > 0 ? C.bad : C.good}
                sub={`since ${RANGES.find(([, s]) => s === since)?.[0] || ''}`}
              />
            </View>

            <Section
              title="ROUTES BY P95"
              right={
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ checked: hidePolls }}
                  onPress={() => setHidePolls((v) => !v)}
                  style={styles.toggle}>
                  <Icon name={hidePolls ? 'ticked' : 'unticked'} size={14} color={hidePolls ? C.accentText : C.dim} />
                  <Text style={styles.toggleWord}>hide polls</Text>
                </Pressable>
              }>
              {shownHttp.length === 0 && <Empty text="no requests recorded" />}
              {shownHttp.map((r, i) => (
                <Row key={r.route} last={i === shownHttp.length - 1}>
                  <Cell style={styles.colWide}>{r.route}</Cell>
                  <Cell dim style={styles.colNum}>{fmtTok(r.count)}</Cell>
                  <Cell dim={!r.errors} style={[styles.colNum, r.errors > 0 && styles.textBad]}>{fmtTok(r.errors)}</Cell>
                  <Cell mono dim style={styles.colNum}>{fmtMs(r.p50)}</Cell>
                  <Cell mono style={styles.colNum}>{fmtMs(r.p95)}</Cell>
                  <Cell mono dim style={styles.colNum}>{fmtMs(r.max)}</Cell>
                </Row>
              ))}
            </Section>

            <Section title="MODEL CALLS BY PURPOSE">
              {models.length === 0 && <Empty text="no model calls recorded" />}
              {models.map((m, i) => (
                <Row key={`${m.purpose}:${m.model}`} last={i === models.length - 1}>
                  <Cell style={styles.colWide}>{m.purpose}</Cell>
                  <Cell dim style={styles.colModel} numberOfLines={1}>{m.model}</Cell>
                  <Cell dim style={styles.colNum}>{fmtTok(m.count)}</Cell>
                  <Cell mono dim style={styles.colNum}>{fmtMs(m.p50)}</Cell>
                  <Cell mono style={styles.colNum}>{fmtCost(m.cost_usd)}</Cell>
                  <Cell dim style={styles.colNum}>{fmtTok(m.input_tokens)}/{fmtTok(m.output_tokens)}</Cell>
                </Row>
              ))}
            </Section>

            <View style={[styles.twoUp, narrow && styles.twoUpNarrow]}>
              <Section title="EXTERNAL TOOLS BY P95" style={styles.half}>
                {shownTools.length === 0 && <Empty text="no exec spans recorded" />}
                {shownTools.map((t, i) => (
                  <Row key={t.name} last={i === shownTools.length - 1}>
                    <Cell style={styles.colWide}>{t.name}</Cell>
                    <Cell dim style={styles.colNum}>{fmtTok(t.count)}</Cell>
                    <Cell dim={!t.errors} style={[styles.colNum, t.errors > 0 && styles.textBad]}>{fmtTok(t.errors)}</Cell>
                    <Cell mono style={styles.colNum}>{fmtMs(t.p95)}</Cell>
                  </Row>
                ))}
              </Section>

              <Section title="GUARDRAILS" style={styles.half}>
                {guardrails.length === 0 && <Empty text="no guardrail runs recorded" />}
                {guardrails.map((g, i) => (
                  <Row key={g.id} last={i === guardrails.length - 1}>
                    <Cell style={styles.colWide}>{g.id}</Cell>
                    <Cell dim style={styles.colNum}>{fmtTok(g.runs)}</Cell>
                    <Cell dim={!g.fail} style={[styles.colNum, g.fail > 0 && styles.textBad]}>{fmtTok(g.fail)} fail</Cell>
                    <Cell mono dim style={styles.colNum}>{fmtMs(g.p95)}</Cell>
                  </Row>
                ))}
              </Section>
            </View>

            <View style={[styles.twoUp, narrow && styles.twoUpNarrow]}>
              <Section title="INTEGRATIONS" style={styles.half}>
                {integrations.length === 0 && <Empty text="no integration calls recorded" />}
                {integrations.map((n, i) => (
                  <Row key={n.name} last={i === integrations.length - 1}>
                    <Cell style={styles.colWide}>{n.name}</Cell>
                    <Cell dim style={styles.colNum}>{fmtTok(n.count)}</Cell>
                    <Cell dim={!n.errors} style={[styles.colNum, n.errors > 0 && styles.textBad]}>{fmtTok(n.errors)}</Cell>
                    <Cell mono dim style={styles.colNum}>{fmtMs(n.p95)}</Cell>
                  </Row>
                ))}
              </Section>

              <Section title="RECENT ERRORS" style={styles.half}>
                {errors.length === 0 && <Empty text="none — clean" />}
                {errors.slice(0, 50).map((e, i) => (
                  <Row key={`${e.ts}:${i}`} last={i === Math.min(errors.length, 50) - 1}>
                    <Cell dim mono style={styles.colWhen}>{agoText(e.ts)}</Cell>
                    <Cell style={styles.colWide}>{e.name}</Cell>
                    <Cell dim style={styles.colErr} numberOfLines={1}>{e.err}</Cell>
                  </Row>
                ))}
              </Section>
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, minHeight: 0 },
  scrollBody: { padding: S.pad, gap: 14 },
  spacer: { flex: 1 },

  headRow: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  headRowNarrow: { flexWrap: 'wrap', rowGap: 10 },
  h1: { color: C.text, fontSize: 22, fontWeight: '700' },

  segTabs: { flexDirection: 'row', borderWidth: 1, borderColor: C.line, borderRadius: 6, overflow: 'hidden' },
  segTab: { color: C.dim, fontSize: 12, paddingHorizontal: 12, paddingVertical: 6 },
  segTabOn: { color: C.text, backgroundColor: C.raised },
  segTabDisabled: { color: C.faint, opacity: 0.6 },
  segTabHint: { color: C.faint, fontSize: 10 },

  quietText: { color: C.faint, fontSize: 12, textAlign: 'center', padding: 24 },

  tiles: { flexDirection: 'row', gap: 14, flexWrap: 'wrap' },
  tilesNarrow: { flexDirection: 'column' },
  tile: { flexGrow: 1, flexBasis: 180, borderWidth: 1, borderColor: C.line, borderRadius: S.radius, backgroundColor: C.panel, padding: 14, gap: 6 },
  tileLabel: { color: C.dim, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  tileValue: { color: C.text, fontSize: 24, fontWeight: '700' },
  tileSub: { color: C.faint, fontSize: 11 },

  section: { borderWidth: 1, borderColor: C.line, borderRadius: S.radius, backgroundColor: C.panel },
  sectionHead: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderBottomWidth: 1, borderBottomColor: C.line },
  sectionTitle: { color: C.dim, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  sectionHint: { color: C.faint, fontSize: 11 },

  toggle: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 4, paddingHorizontal: 6 },
  toggleWord: { color: C.dim, fontSize: 11 },

  twoUp: { flexDirection: 'row', gap: 14 },
  twoUpNarrow: { flexDirection: 'column' },
  half: { flex: 1, minWidth: 0 },

  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 12, paddingVertical: 9 },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: C.line },
  cell: { color: C.text, fontSize: 12 },
  cellDim: { color: C.dim },
  colWide: { flexGrow: 1, flexShrink: 1, flexBasis: 100 },
  colModel: { flexGrow: 0, flexShrink: 1, flexBasis: 90, width: 90 },
  colNum: { width: 56, textAlign: 'right' },
  colWhen: { width: 56 },
  colErr: { flexGrow: 1, flexShrink: 1, flexBasis: 100 },
  textBad: { color: C.bad },

  emptyRow: { color: C.faint, fontSize: 12, padding: 12 },
});
