import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Linking,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { deployAction, getDeploy, getDeploys, readSpec } from './api';
import PushButton from './PushButton';
import { BREAK, C, PAD_HEX, S, mono } from './theme';

// Every colour here is a theme token already spent on the same meaning
// elsewhere, not a new hex borrowed from the mockup -- ready is the wire's
// own green, building/queued its own orange, error/blocked C.bad, and
// cancelled/unknown C.faint, the same four the seat and guardrail dots use.
const STATE_HEX = {
  ready: PAD_HEX[21],
  building: PAD_HEX[60],
  queued: PAD_HEX[60],
  error: C.bad,
  canceled: C.faint,
};
const STATE_WORD = {
  ready: 'ready', building: 'building', queued: 'queued', error: 'error', canceled: 'cancelled',
};
// pass borrows the seat colour an idle agent already means, same reasoning
// Pane.js's own VERDICT_HEX gives for CommitProvenance's chips.
const VERDICT_HEX = { pass: PAD_HEX[21], fail: C.bad, error: C.bad, na: C.faint, unapproved: PAD_HEX[60] };

const ENV_FILTERS = [['all', 'all'], ['production', 'production'], ['preview', 'preview']];

function shortSha(sha) {
  return (sha || '').slice(0, 7);
}

// "3h ago", "2d ago" -- coarse on purpose, the same rule Memory.js's agoText
// uses; a history list is for "roughly when", not a timestamp read twice.
function agoText(ms) {
  if (!ms) return '';
  const secs = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (secs < 45) return 'now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function tookText(d) {
  if (!d.created || !d.ready) return '—';
  const secs = Math.max(0, Math.round((d.ready - d.created) / 1000));
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

function useNarrow() {
  return useWindowDimensions().width < BREAK.mid;
}

// A row of guardrail verdict chips, "made by" and "view spec" -- the same
// three facts CommitProvenance draws under a commit in the GIT tab's
// history, because a deploy's provenance is that same /work/commit-meta
// shape carried over from its sha. Not imported from Pane.js (not exported,
// and Deploy.js is out-of-scope for touching that file) -- redrawn here at
// the same fields.
function Provenance({ base, cwd, provenance, cols, onSeat }) {
  const [openChip, setOpenChip] = useState(-1);
  const [specView, setSpecView] = useState(null);
  if (!provenance) return null;
  const results = provenance.guardrails?.predicate?.results || [];
  const session = provenance.session;
  const spec = provenance.spec;
  if (!results.length && !session && !spec) return null;

  const seatAt = session?.live_tid
    ? (cols || []).findIndex((c) => c && c.tid === session.live_tid)
    : -1;

  const openSpec = () => {
    if (!spec?.path) return;
    readSpec(base, cwd, spec.path)
      .then((r) => setSpecView({ title: spec.title || r.path, text: r.text }))
      .catch(() => setSpecView({ title: spec.title || spec.path, text: '' }));
  };

  return (
    <View style={styles.provWrap}>
      {!!results.length && (
        <View style={styles.chips}>
          {results.map((r, i) => (
            <Pressable
              key={r.id || i}
              accessibilityRole="button"
              accessibilityLabel={`${r.gate || r.phase || 'guardrail'}: ${r.verdict}${r.reason ? `, ${r.reason}` : ''}`}
              onPress={() => setOpenChip(openChip === i ? -1 : i)}
              style={[styles.provChip, { borderColor: VERDICT_HEX[r.verdict] || C.faint }]}>
              <Text numberOfLines={1} style={{ color: VERDICT_HEX[r.verdict] || C.faint, fontSize: 10 }}>
                {r.gate || r.phase || r.id} · {r.verdict}
              </Text>
            </Pressable>
          ))}
        </View>
      )}
      {openChip >= 0 && !!results[openChip]?.reason && (
        <Text style={styles.reason}>{results[openChip].reason}</Text>
      )}
      {!!session && (
        <View style={styles.provLine}>
          <Text numberOfLines={1} style={styles.dimText}>
            made by {session.name || (session.id || '').slice(0, 8)}
          </Text>
          {seatAt >= 0 && (
            <Text accessibilityRole="button" onPress={() => onSeat?.(seatAt)} style={styles.link}>
              open agent
            </Text>
          )}
        </View>
      )}
      {!!spec && (
        <Text
          accessibilityRole="button"
          accessibilityLabel={`spec: ${spec.title || spec.path}, view`}
          onPress={openSpec}
          numberOfLines={1}
          style={styles.link}>
          spec: {spec.title || spec.path}
        </Text>
      )}
      <Modal visible={!!specView} transparent animationType="fade" onRequestClose={() => setSpecView(null)}>
        <Pressable style={styles.backdrop} onPress={() => setSpecView(null)}>
          <Pressable style={styles.specCard} onPress={() => {}}>
            <View style={styles.specHead}>
              <Text numberOfLines={1} style={[styles.specTitle, styles.spacer]}>{specView?.title}</Text>
              <Text accessibilityRole="button" onPress={() => setSpecView(null)} style={styles.link}>close</Text>
            </View>
            <ScrollView>
              <Text style={[styles.dimText, mono]}>{specView?.text || ''}</Text>
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

// One deploy row, drawn either as a history line or reused for an
// environment lane's headline -- both read the same normalised fields.
function StateDot({ state }) {
  return <View style={[styles.dot, { backgroundColor: STATE_HEX[state] || C.faint }]} />;
}

// The two live lanes: current production and the newest preview. AWS/Sentry
// would add more lanes later (their own env or a third provider); today only
// Vercel's production|preview exist, matching the app-spec's own env filter.
// The card is a plain View, not a Pressable, because it holds action buttons
// of its own (PushButton, itself a Pressable) -- a Pressable wrapped around
// other Pressables draws as a nested <button> on web and the inner ones stop
// being reachable. Only the header block picks the deploy for the side
// panel; the button row is its sibling, never its child.
function EnvCard({ title, deploy, rollbackTo, onPick, onAction, warn }) {
  if (!deploy) return null;
  const url = deploy.url ? (deploy.url.startsWith('http') ? deploy.url : `https://${deploy.url}`) : '';
  return (
    <View style={[styles.envCard, warn && styles.envCardWarn]}>
      <Pressable accessibilityRole="button" onPress={() => onPick(deploy)} style={styles.envHead}>
        <View style={styles.row}>
          <StateDot state={deploy.state} />
          <Text style={styles.envTitle}>{title}</Text>
          <Text style={styles.providerTag}>{deploy.provider}</Text>
          <View style={styles.spacer} />
          <Text style={[styles.stateWord, { color: STATE_HEX[deploy.state] || C.faint }]}>
            {STATE_WORD[deploy.state] || deploy.state}
          </Text>
        </View>
        <Text style={[styles.commitLine, mono]} numberOfLines={1}>
          {shortSha(deploy.sha)} · {deploy.message || deploy.branch || ''}
        </Text>
      </Pressable>
      <View style={styles.rowGap}>
        {deploy.env === 'production' && !!rollbackTo && (
          <PushButton
            label={`rollback to ${shortSha(rollbackTo.sha)}`}
            onPress={() => onAction('rollback', rollbackTo)}
            style={styles.envBtn}
          />
        )}
        {deploy.env === 'preview' && deploy.state === 'ready' && !deploy.current && (
          <PushButton
            label="promote to production"
            colour={PAD_HEX[53]}
            onPress={() => onAction('promote', deploy)}
            style={styles.envBtn}
          />
        )}
        {(deploy.state === 'building' || deploy.state === 'queued') && (
          <PushButton
            label="cancel"
            onPress={() => onAction('cancel', deploy)}
            style={styles.envBtnSmall}
          />
        )}
        {!!url && (
          <PushButton
            label="open ↗"
            onPress={() => Linking.openURL(url).catch(() => {})}
            style={styles.envBtnSmall}
          />
        )}
      </View>
    </View>
  );
}

// The DEPLOY tab, app-only: rendered and selected entirely by App.js's own
// `deployOpen` flag, never sent to the Push as a `tab` press (see App.js).
// Every environment card, history row and the side panel all read one poll
// of GET /deploy; actions confirm first, always, then re-poll immediately
// rather than trusting the next tick to catch the new state.
//
// props:
//   base               string
//   cwd                string             -- the checkout this DEPLOY is about
//   cols               array              -- for provenance's "open agent"
//   onSeat             (i) => void
//   onOpenIntegrations () => void         -- empty state's way into Settings
export default function Deploy({ base, cwd, cols, onSeat, onOpenIntegrations }) {
  const narrow = useNarrow();
  const [providers, setProviders] = useState([]);
  const [deploys, setDeploys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [envFilter, setEnvFilter] = useState('all');
  const [pick, setPick] = useState(null); // {provider, id}
  const [pickDetail, setPickDetail] = useState(null);
  const [pickLoading, setPickLoading] = useState(false);
  const [confirm, setConfirm] = useState(null); // {op, deploy, label}
  const [actionBusy, setActionBusy] = useState(false);
  const [actionResult, setActionResult] = useState(null); // {blocked, results} | {ok, results}
  const [note, setNote] = useState('');
  const timerRef = useRef(null);
  const reloadRef = useRef(() => {});

  useEffect(() => {
    setDeploys([]);
    setProviders([]);
    setErr('');
    setLoading(true);
    if (!base || !cwd) { setLoading(false); return undefined; }
    let live = true;

    const pull = () => {
      getDeploys(base, cwd)
        .then((d) => {
          if (!live) return;
          setProviders(d.providers || []);
          setDeploys(d.deploys || []);
          setErr('');
          const building = (d.deploys || []).some((x) => x.state === 'building' || x.state === 'queued');
          timerRef.current = setTimeout(pull, building ? 4000 : 15000);
        })
        .catch((e) => {
          if (!live) return;
          // an older server, or nothing mapped yet -- quiet, the empty state
          // below already says what to do
          setErr(String(e.message || e));
          timerRef.current = setTimeout(pull, 15000);
        })
        .finally(() => live && setLoading(false));
    };
    reloadRef.current = pull;
    pull();
    return () => { live = false; clearTimeout(timerRef.current); };
  }, [base, cwd]);

  useEffect(() => {
    if (!pick) { setPickDetail(null); return undefined; }
    setPickLoading(true);
    let live = true;
    getDeploy(base, cwd, pick.provider, pick.id)
      .then((d) => live && setPickDetail(d))
      .catch(() => live && setPickDetail(null))
      .finally(() => live && setPickLoading(false));
    return () => { live = false; };
  }, [base, cwd, pick]);

  const say = (text) => {
    setNote(text);
    setTimeout(() => setNote(''), 3000);
  };

  const filtered = envFilter === 'all' ? deploys : deploys.filter((d) => d.env === envFilter);
  const byProvider = (d) => providers.find((p) => p.name === d.provider);

  const prodAll = deploys.filter((d) => d.env === 'production');
  const currentProd = prodAll.find((d) => d.current) || prodAll[0] || null;
  const rollbackTo = currentProd
    ? prodAll.find((d) => d.id !== currentProd.id && d.state === 'ready') || null
    : null;
  const previewAll = deploys.filter((d) => d.env === 'preview');
  const newestPreview = previewAll[0] || null;

  const buildingCount = deploys.filter((d) => d.state === 'building' || d.state === 'queued').length;

  const askConfirm = (op, deploy) => {
    setActionResult(null);
    const label =
      op === 'promote' ? `Promote ${shortSha(deploy.sha)} to production?`
      : op === 'rollback' ? `Roll back production to ${shortSha(deploy.sha)}?`
      : 'Cancel this deploy?';
    setConfirm({ op, deploy, label });
  };

  const doConfirm = async () => {
    if (!confirm) return;
    setActionBusy(true);
    try {
      const body = await deployAction(base, cwd, confirm.deploy.provider, confirm.op, confirm.deploy.id);
      if (body.blocked) {
        setActionResult(body);
      } else {
        setActionResult(null);
        setConfirm(null);
        say(`${confirm.op} sent`);
        reloadRef.current();
      }
    } catch (e) {
      setActionResult({ blocked: false, error: e.message || String(e) });
    } finally {
      setActionBusy(false);
    }
  };

  const openPick = (deploy) => setPick({ provider: deploy.provider, id: deploy.id });
  const closePick = () => setPick(null);

  if (!cwd) {
    return (
      <View style={styles.emptyWrap}>
        <Text style={styles.emptyText}>no checkout — pick a worktree, or focus an agent</Text>
      </View>
    );
  }

  if (!loading && providers.length === 0) {
    return (
      <View style={styles.emptyWrap}>
        <Text style={styles.emptyTitle}>nothing deploys this checkout yet</Text>
        <Text style={styles.emptyText}>
          map an integration to this project in Settings › Integrations to see it here.
        </Text>
        <PushButton label="open Settings › Integrations" lit colour={PAD_HEX[53]} onPress={onOpenIntegrations} style={styles.emptyBtn} />
        {!!err && <Text style={styles.err}>{err}</Text>}
      </View>
    );
  }

  return (
    <View style={[styles.root, narrow && styles.rootNarrow]}>
      <View style={styles.main}>
        <View style={styles.headRow}>
          <Text style={styles.h1}>deploy</Text>
          {loading && <ActivityIndicator size="small" color={C.faint} />}
          <View style={styles.spacer} />
          <View style={styles.envTabs}>
            {ENV_FILTERS.map(([key, word]) => (
              <Text
                key={key}
                accessibilityRole="button"
                accessibilityState={{ selected: envFilter === key }}
                onPress={() => setEnvFilter(key)}
                style={[styles.envTab, envFilter === key && styles.envTabOn]}>
                {word}
              </Text>
            ))}
          </View>
        </View>

        {!!note && <Text style={styles.note}>{note}</Text>}
        {!!err && <Text style={styles.err}>couldn't reach deploys: {err}</Text>}

        <View style={[styles.lanes, narrow && styles.lanesNarrow]}>
          <EnvCard title="Production" deploy={currentProd} rollbackTo={rollbackTo} onPick={openPick} onAction={askConfirm} warn={currentProd?.state === 'error'} />
          <EnvCard title="Preview" deploy={newestPreview} onPick={openPick} onAction={askConfirm} warn={newestPreview?.state === 'error'} />
        </View>

        <View style={styles.history}>
          <View style={styles.historyHead}>
            <Text style={styles.historyTitle}>HISTORY</Text>
            <View style={styles.spacer} />
            <Text style={styles.historyHint} numberOfLines={1}>
              {buildingCount > 0 ? `${buildingCount} building` : `${filtered.length} deploy${filtered.length === 1 ? '' : 's'}`}
            </Text>
          </View>
          <ScrollView style={styles.historyScroll}>
            {filtered.map((d) => (
              <Pressable
                key={`${d.provider}:${d.id}`}
                accessibilityRole="button"
                onPress={() => openPick(d)}
                style={[styles.historyRow, pick?.id === d.id && pick?.provider === d.provider && styles.historyRowOn]}>
                <StateDot state={d.state} />
                <Text style={styles.historyEnv} numberOfLines={1}>{d.env}</Text>
                <Text style={styles.historyProvider} numberOfLines={1}>{byProvider(d)?.title || d.provider}</Text>
                <Text style={[styles.historyCommit, mono]} numberOfLines={1}>
                  {shortSha(d.sha)} {d.message || ''}
                </Text>
                <Text style={styles.historyMade} numberOfLines={1}>
                  {d.author || (d.provenance?.session ? `agent · ${d.provenance.session.name || ''}` : '')}
                </Text>
                <Text style={styles.historyTook} numberOfLines={1}>{tookText(d)}</Text>
                <Text style={styles.historyWhen} numberOfLines={1}>{agoText(d.created)}</Text>
              </Pressable>
            ))}
            {!loading && filtered.length === 0 && (
              <Text style={styles.emptyText}>no deploys yet</Text>
            )}
          </ScrollView>
        </View>
      </View>

      {!!pick && (
        <View style={[styles.aside, narrow && styles.asideNarrow]}>
          <View style={styles.asideHead}>
            <Text numberOfLines={1} style={[styles.asideTitle, styles.spacer]}>
              {pickDetail ? `${pickDetail.env} · ${shortSha(pickDetail.sha)}` : '…'}
            </Text>
            <Text accessibilityRole="button" accessibilityLabel="close" onPress={closePick} style={styles.link}>close</Text>
          </View>
          {pickLoading && <ActivityIndicator color={C.faint} style={styles.spin} />}
          {!pickLoading && pickDetail && (
            <ScrollView style={styles.asideBody} contentContainerStyle={styles.asideBodyContent}>
              <View style={styles.row}>
                <StateDot state={pickDetail.state} />
                <Text style={[styles.stateWord, { color: STATE_HEX[pickDetail.state] || C.faint }]}>
                  {STATE_WORD[pickDetail.state] || pickDetail.state}
                </Text>
              </View>
              <Text style={styles.dimText} numberOfLines={2}>{pickDetail.message || ''}</Text>
              <Provenance base={base} cwd={cwd} provenance={pickDetail.provenance} cols={cols} onSeat={onSeat} />
              {!!(pickDetail.log || []).length && (
                <>
                  <Text style={styles.asideLabel}>pipeline log</Text>
                  <View style={styles.logBox}>
                    <Text style={[styles.logText, mono]}>{(pickDetail.log || []).join('\n')}</Text>
                  </View>
                </>
              )}
              <View style={styles.rowGap}>
                {pickDetail.env === 'preview' && pickDetail.state === 'ready' && !pickDetail.current && (
                  <PushButton label="promote to production" lit colour={PAD_HEX[53]} onPress={() => askConfirm('promote', pickDetail)} style={styles.envBtn} />
                )}
                {pickDetail.env === 'production' && !pickDetail.current && pickDetail.state === 'ready' && (
                  <PushButton label="rollback to this" onPress={() => askConfirm('rollback', pickDetail)} style={styles.envBtn} />
                )}
                {(pickDetail.state === 'building' || pickDetail.state === 'queued') && (
                  <PushButton label="cancel" onPress={() => askConfirm('cancel', pickDetail)} style={styles.envBtn} />
                )}
              </View>
            </ScrollView>
          )}
        </View>
      )}

      <Modal visible={!!confirm} transparent animationType="fade" onRequestClose={() => { setConfirm(null); setActionResult(null); }}>
        <Pressable style={styles.backdrop} onPress={() => { if (!actionBusy) { setConfirm(null); setActionResult(null); } }}>
          <Pressable style={styles.confirmCard} onPress={() => {}}>
            <Text style={styles.confirmText}>{confirm?.label}</Text>
            {actionResult?.blocked && (
              <View style={styles.blockedBox}>
                <Text style={styles.blockedTitle}>blocked by deploy:promote</Text>
                {(actionResult.results || []).filter((r) => r.verdict !== 'pass' && r.verdict !== 'na').map((r, i) => (
                  <View key={r.id || i} style={styles.blockedRow}>
                    <Text style={[styles.blockedVerdict, { color: VERDICT_HEX[r.verdict] || C.bad }]}>
                      {r.gate || r.phase || r.id} · {r.verdict}
                    </Text>
                    {!!r.reason && <Text style={styles.dimText}>{r.reason}</Text>}
                  </View>
                ))}
              </View>
            )}
            {!!actionResult?.error && <Text style={styles.err}>{actionResult.error}</Text>}
            <View style={styles.rowGap}>
              {actionResult?.blocked || actionResult?.error ? (
                <PushButton label="close" onPress={() => { setConfirm(null); setActionResult(null); }} style={styles.envBtn} />
              ) : (
                <>
                  <PushButton
                    // the verb, not "confirm": beside a "cancel" key, "cancel this
                    // deploy?" read both ways
                    label={actionBusy ? '…' : ({ promote: 'promote', rollback: 'roll back', cancel: 'cancel deploy' }[confirm?.op] || 'confirm')}
                    lit
                    colour={PAD_HEX[53]}
                    disabled={actionBusy}
                    onPress={doConfirm}
                    style={styles.envBtn}
                  />
                  <PushButton label="back" disabled={actionBusy} onPress={() => setConfirm(null)} style={styles.envBtn} />
                </>
              )}
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, flexDirection: 'row', minHeight: 0 },
  rootNarrow: { flexDirection: 'column' },
  main: { flex: 1, minWidth: 0, padding: S.pad, gap: 14 },
  spacer: { flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  rowGap: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 4 },
  dot: { width: 8, height: 8, borderRadius: 4 },

  headRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  h1: { color: C.text, fontSize: 22, fontWeight: '700' },
  envTabs: { flexDirection: 'row', borderWidth: 1, borderColor: C.line, borderRadius: 6, overflow: 'hidden' },
  envTab: { color: C.dim, fontSize: 12, paddingHorizontal: 12, paddingVertical: 6 },
  envTabOn: { color: C.text, backgroundColor: C.raised },

  note: { color: C.accentText, fontSize: 12 },
  err: { color: C.bad, fontSize: 12 },

  lanes: { flexDirection: 'row', gap: 14, flexWrap: 'wrap' },
  lanesNarrow: { flexDirection: 'column' },
  envCard: { flexGrow: 1, flexBasis: 260, borderWidth: 1, borderColor: C.line, borderRadius: S.radius, backgroundColor: C.panel, padding: 14, gap: 8 },
  envCardWarn: { borderColor: C.bad },
  envHead: { gap: 8 },
  envTitle: { color: C.text, fontWeight: '600', fontSize: 14 },
  providerTag: { color: C.dim, fontSize: 11, borderWidth: 1, borderColor: C.line, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 1 },
  stateWord: { fontSize: 11, fontWeight: '600' },
  commitLine: { color: C.dim, fontSize: 12 },
  envBtn: { minHeight: 34, paddingHorizontal: 12 },
  envBtnSmall: { minHeight: 34, paddingHorizontal: 10 },

  history: { flex: 1, minHeight: 160, borderWidth: 1, borderColor: C.line, borderRadius: S.radius, backgroundColor: C.panel },
  historyHead: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderBottomWidth: 1, borderBottomColor: C.line },
  historyTitle: { color: C.dim, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  historyHint: { color: C.faint, fontSize: 11 },
  historyScroll: { flexGrow: 0 },
  historyRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: C.line },
  historyRowOn: { backgroundColor: C.raised },
  historyEnv: { color: C.dim, fontSize: 12, width: 70 },
  historyProvider: { color: C.faint, fontSize: 11, width: 60 },
  historyCommit: { color: C.text, fontSize: 12, flexGrow: 1, flexShrink: 1, flexBasis: 120 },
  historyMade: { color: C.faint, fontSize: 11, width: 110 },
  historyTook: { color: C.faint, fontSize: 11, width: 60 },
  historyWhen: { color: C.faint, fontSize: 11, width: 60, textAlign: 'right' },

  aside: { width: 340, flexShrink: 0, borderLeftWidth: 1, borderLeftColor: C.line, padding: S.pad, gap: 12 },
  asideNarrow: { width: '100%', borderLeftWidth: 0, borderTopWidth: 1, borderTopColor: C.line },
  asideHead: { flexDirection: 'row', alignItems: 'center' },
  asideTitle: { color: C.dim, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  asideBody: { flexGrow: 0 },
  asideBodyContent: { gap: 10 },
  asideLabel: { color: C.dim, fontSize: 12, marginTop: 4 },
  spin: { margin: S.pad },
  dimText: { color: C.dim, fontSize: 12, lineHeight: 17 },

  logBox: { backgroundColor: C.bg, borderWidth: 1, borderColor: C.line, borderRadius: 6, padding: 10, maxHeight: 220 },
  logText: { color: C.dim, fontSize: 11, lineHeight: 16 },

  provWrap: { gap: 6 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  provChip: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 8, paddingVertical: 3 },
  reason: { color: C.dim, fontSize: 11, lineHeight: 15 },
  provLine: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  link: { color: C.accentText, fontSize: 12 },

  emptyWrap: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32, gap: 10 },
  emptyTitle: { color: C.text, fontSize: 15, fontWeight: '600' },
  emptyText: { color: C.faint, fontSize: 12, textAlign: 'center' },
  emptyBtn: { minWidth: 220 },

  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.55)', alignItems: 'center', justifyContent: 'center', padding: 24 },
  confirmCard: { width: 420, maxWidth: '92%', backgroundColor: C.panel, borderRadius: S.radius, borderWidth: 1, borderColor: C.line, padding: 18, gap: 12 },
  confirmText: { color: C.text, fontSize: 15, lineHeight: 21 },
  blockedBox: { borderWidth: 1, borderColor: C.bad, borderRadius: 6, padding: 10, gap: 6 },
  blockedTitle: { color: C.bad, fontSize: 12, fontWeight: '700' },
  blockedRow: { gap: 2 },
  blockedVerdict: { fontSize: 12, fontWeight: '600' },

  specCard: { width: 640, maxWidth: '92%', maxHeight: '80%', backgroundColor: C.panel, borderRadius: S.radius, borderWidth: 1, borderColor: C.line, padding: 16, gap: 10 },
  specHead: { flexDirection: 'row', alignItems: 'center' },
  specTitle: { color: C.text, fontSize: 15, fontWeight: '600' },
});
