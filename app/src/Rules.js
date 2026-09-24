import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import Icon from './Icon';
import PushButton from './PushButton';
import {
  deleteRule,
  effectiveRules,
  getGuardrails,
  listRules,
  moveRule,
  readRule,
  reviewRules,
  saveGuardrails,
  writeRule,
  writeRuleFile,
} from './api';
import { C, S, mono } from './theme';

// Where a *new* rule can be filed. Unlike skills and hooks there is no
// `local` rules directory -- CLAUDE.local.md is the one file that scope
// owns, it is always already listed as a file row, and it is never created
// here.
const RULE_SCOPES = [
  ['project', 'project'],
  ['global', 'global'],
];

const AMBER_LINES = 200;

// The id a rule becomes on GUARDRAILS' own checklist, so "enforce" and a
// second look at the same rule agree on whether it is already there.
function slugify(title) {
  const s = (title || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return (s || 'rule').slice(0, 60).replace(/-+$/g, '') || 'rule';
}

// `read()` hands back the file exactly as it sits on disk -- frontmatter and
// the `# title` heading included. The list already parsed both of those
// (`title`, `paths`), so editing only wants the prose underneath them.
function bodyFromText(text) {
  let body = text || '';
  const fm = body.match(/^---\n[\s\S]*?\n---\n?/);
  if (fm) body = body.slice(fm[0].length);
  body = body.replace(/^\n+/, '');
  const heading = body.match(/^#[^\n]*\n?/);
  if (heading) body = body.slice(heading[0].length);
  return body.replace(/^\n+/, '');
}

function isAgentsMdRow(row) {
  return row?.kind === 'file' && /(^|\/)AGENTS\.md$/.test(row.path || row.shown || '');
}

// The rules tab: a sibling of skills and hooks, not a new design. Pads owns
// the tab row, the global/project/local scope row, the search box and the ＋
// key already -- this only supplies what sits under them.
//
// props:
//   base, cwd     api base url, and which checkout the scope words mean
//   q             the shared search box's text
//   masterScope   'global' | 'project' | 'local' -- the shared scope tab
//   agentName     the focused agent's name, for "what X reads"
//   newSignal     bumped by Pads' shared ＋ key when `at === 'rules'`
//   onCount       (n) => void -- how many rows are in the current scope, for
//                 the tab's own count
export default function Rules({ base, cwd, q, masterScope, agentName, newSignal, onCount, active }) {
  const [rows, setRows] = useState([]);
  const [agentsMd, setAgentsMd] = useState(null);
  const [editor, setEditor] = useState(null); // { row: row|null } | null
  const [reading, setReading] = useState(false); // the "what it reads" modal

  const load = useCallback(() => {
    if (!base) return;
    listRules(base, cwd)
      .then((d) => {
        setRows(d.rows || []);
        setAgentsMd(d.agents_md || null);
      })
      // the server side of this tab may not exist yet -- an empty list reads
      // as "nothing here", not as a wall of red
      .catch(() => {
        setRows([]);
        setAgentsMd(null);
      });
  }, [base, cwd]);

  useEffect(load, [load]);

  // The ＋ key lives in Pads' shared header, above every catalog panel, and
  // reaches this tab by bumping a counter rather than a prop only this tab
  // would understand.
  const seenSignal = useRef(newSignal);
  useEffect(() => {
    if (newSignal !== seenSignal.current) {
      seenSignal.current = newSignal;
      setEditor({ row: null });
    }
  }, [newSignal]);

  const inScope = (r) => r.scope === masterScope;
  const find = (q || '').trim().toLowerCase();
  const hit = (r) =>
    !find ||
    `${r.title || ''} ${r.shown || ''} ${(r.paths || []).join(' ')}`.toLowerCase().includes(find);
  const scoped = rows.filter(inScope);
  const shown = scoped.filter(hit);
  const files = shown.filter((r) => r.kind === 'file');
  const ruleRows = shown.filter((r) => r.kind === 'rule');

  useEffect(() => {
    onCount && onCount(scoped.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, masterScope]);

  // Mounted whether or not this tab is the one on screen -- skills and hooks
  // get their counts from `catalog`, fetched in App.js regardless of which
  // panel is open, and the rules tab's own count wants the same "already
  // known before you click it" feel rather than staying blank until the
  // first visit.
  if (!active) return null;

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`what ${agentName || 'this agent'} reads`}
        onPress={() => setReading(true)}
        style={styles.whatReads}>
        <Icon name="agent" size={13} color={C.faint} />
        <Text style={styles.whatReadsText}>what {agentName || 'this agent'} reads</Text>
      </Pressable>
      <ScrollView contentContainerStyle={styles.list}>
        {!files.length && !ruleRows.length && (
          <Text style={styles.none}>
            {find ? `nothing matches “${q.trim()}”` : 'no rules here yet'}
          </Text>
        )}
        {!!files.length && (
          <View style={styles.group}>
            {files.map((r) => (
              <Row
                key={r.path || r.shown}
                row={r}
                agentsMd={isAgentsMdRow(r) ? agentsMd : null}
                onPress={() => setEditor({ row: r })}
              />
            ))}
          </View>
        )}
        {!!ruleRows.length && (
          <View style={styles.group}>
            {ruleRows.map((r) => (
              <Row key={r.path} row={r} onPress={() => setEditor({ row: r })} />
            ))}
          </View>
        )}
      </ScrollView>
      {!!editor && (
        <RuleEditor
          base={base}
          cwd={cwd}
          row={editor.row}
          masterScope={masterScope}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            load();
          }}
        />
      )}
      {reading && (
        <ReadsModal
          base={base}
          cwd={cwd}
          agentName={agentName}
          rows={rows}
          onClose={() => setReading(false)}
          onOpenFile={(row) => {
            setReading(false);
            setEditor({ row });
          }}
        />
      )}
    </>
  );
}

// One row, whichever kind it is. A missing always-on file reads as an
// invitation ("create") rather than as an error; a rule's globs are the one
// thing about it that is not on every skill or hook row, so they get their
// own chip.
function Row({ row, agentsMd, onPress }) {
  const missing = row.kind === 'file' && row.exists === false;
  const amber = !missing && (row.lines || 0) > AMBER_LINES;
  const hasGlobs = !missing && row.kind === 'rule' && !!(row.paths && row.paths.length);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={missing ? `create ${row.shown}` : `edit ${row.shown}`}
      onPress={onPress}
      style={styles.item}>
      <View style={styles.rowBody}>
        <View style={styles.itemTop}>
          <Text style={[styles.rowName, missing && styles.rowFaint]} numberOfLines={1}>
            {row.title || row.shown}
          </Text>
          {missing && <Text style={styles.createNote}>create</Text>}
          {!missing && row.kind === 'rule' && (
            <Text style={styles.globChip}>{hasGlobs ? `only for ${row.paths.join(', ')}` : 'always'}</Text>
          )}
        </View>
        <Text style={[styles.rowSub, missing && styles.rowFaint]} numberOfLines={1}>
          {row.shown}
          {!missing && `  ·  ${row.lines || 0} lines`}
        </Text>
        {!!agentsMd && (
          <View style={styles.syncRow}>
            {!agentsMd.synced && <Icon name="trouble" size={11} color={C.warn} />}
            <Text style={[styles.rowSub, agentsMd.synced ? styles.inSync : styles.outSync]}>
              {agentsMd.synced ? 'in sync' : 'out of sync'}
            </Text>
          </View>
        )}
        {!!agentsMd?.doubled && (
          <View style={styles.syncRow}>
            <Icon name="trouble" size={11} color={C.warn} />
            <Text style={styles.doubled}>
              no CLAUDE.md here, so Claude also reads AGENTS.md and sees these rules twice
            </Text>
          </View>
        )}
      </View>
      {amber && <View style={styles.amberDot} />}
    </Pressable>
  );
}

// The editor for one row -- a rule (title, body, globs, scope) or an
// always-on file (just its text). Modelled on EntrySheet's skill/hook sheet:
// one card, a scope picker that turns "save" into "move here" when it
// disagrees with where the row already lives, delete behind a confirming
// second tap.
function RuleEditor({ base, cwd, row, masterScope, onClose, onSaved }) {
  const isFile = row?.kind === 'file';
  const existsAlready = isFile ? row?.exists !== false : !!row;
  const [scope, setScope] = useState(
    row?.scope || (masterScope === 'local' ? 'project' : masterScope) || 'project'
  );
  const [title, setTitle] = useState(!isFile && row ? row.title || '' : '');
  const [pathsText, setPathsText] = useState((row?.paths || []).join('\n'));
  const [body, setBody] = useState('');
  const [loading, setLoading] = useState(!!row && existsAlready);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [sure, setSure] = useState(false);
  const [savedNote, setSavedNote] = useState('');
  const [enfBusy, setEnfBusy] = useState(false);
  const [enfMsg, setEnfMsg] = useState('');

  useEffect(() => {
    if (!row || !existsAlready) {
      setLoading(false);
      return;
    }
    let live = true;
    readRule(base, cwd, row.path)
      .then((text) => {
        if (live) setBody(bodyFromText(text));
      })
      .catch((e) => live && setErr(e.message || String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, cwd, row?.path]);

  const canSave = isFile ? !loading && !busy : !!title.trim() && !loading && !busy;
  const moving = !isFile && !!row && scope !== row.scope;

  async function save() {
    if (!canSave) return;
    setErr('');
    setBusy(true);
    setSavedNote('');
    try {
      if (isFile) {
        await writeRuleFile(base, cwd, row.path, body);
      } else {
        const paths = pathsText
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean);
        await writeRule(base, {
          cwd,
          scope,
          title: title.trim(),
          body,
          paths,
          ...(row ? { path: row.path } : {}),
        });
        if (scope === 'project') setSavedNote('AGENTS.md updated');
      }
      onSaved();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function move() {
    if (busy || !row) return;
    setErr('');
    setBusy(true);
    try {
      await moveRule(base, cwd, row.path, scope);
      onSaved();
    } catch (e) {
      setErr(e.message || String(e));
      setBusy(false);
    }
  }

  async function remove() {
    if (busy || !row || !existsAlready) return;
    if (!sure) return setSure(true); // one tap arms, the next does it
    setErr('');
    setBusy(true);
    try {
      // CLAUDE.md/AGENTS.md are never deleted -- the app empties them instead
      if (isFile) await writeRuleFile(base, cwd, row.path, '');
      else await deleteRule(base, cwd, row.path);
      onSaved();
    } catch (e) {
      setErr(e.message || String(e));
      setBusy(false);
      setSure(false);
    }
  }

  async function enforce() {
    if (enfBusy || !title.trim()) return;
    setEnfBusy(true);
    setEnfMsg('');
    try {
      const st = await getGuardrails(base, cwd);
      const id = `rule-${slugify(title)}`;
      if ((st.custom || []).some((c) => c.id === id)) {
        setEnfMsg('already in GUARDRAILS › Implement');
      } else {
        await saveGuardrails(base, cwd, {
          ...st,
          custom: [
            ...(st.custom || []),
            { id, phase: 'implement', title: title.trim(), implemented: body, validate: '' },
          ],
        });
        setEnfMsg('added to GUARDRAILS › Implement — make it enforceable there');
      }
    } catch (e) {
      setEnfMsg(e.message || String(e));
    } finally {
      setEnfBusy(false);
    }
  }

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>
            {isFile ? row?.title || row?.shown || 'file' : existsAlready ? 'edit rule' : 'new rule'}
          </Text>

          <ScrollView
            style={styles.bodyScroll}
            contentContainerStyle={styles.bodyCol}
            keyboardShouldPersistTaps="handled">
            {!isFile && (
              <>
                <Text style={styles.label}>where</Text>
                <View style={styles.chips}>
                  {RULE_SCOPES.map(([key, word]) => (
                    <Text
                      key={key}
                      accessibilityRole="button"
                      accessibilityState={{ selected: scope === key }}
                      onPress={() => setScope(key)}
                      style={[styles.chip, scope === key && styles.chipOn]}>
                      {word}
                    </Text>
                  ))}
                </View>

                <Text style={styles.label}>title</Text>
                <TextInput
                  style={styles.input}
                  value={title}
                  onChangeText={setTitle}
                  placeholder="my-rule"
                  placeholderTextColor={C.faint}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </>
            )}

            {loading && <ActivityIndicator size="small" color={C.faint} />}

            <Text style={styles.label}>{isFile ? 'text' : 'body'}</Text>
            <TextInput
              style={[styles.input, styles.taller, mono]}
              value={body}
              onChangeText={setBody}
              editable={!loading}
              multiline
              autoCapitalize="none"
              autoCorrect={false}
              placeholder={isFile ? '' : '# what this rule is for\n\nSay it plainly.'}
              placeholderTextColor={C.faint}
            />

            {!isFile && (
              <>
                <Text style={styles.label}>
                  only when working on <Text style={styles.hint}>— one glob per line, empty = always</Text>
                </Text>
                <TextInput
                  style={[styles.input, styles.tall, mono]}
                  value={pathsText}
                  onChangeText={setPathsText}
                  multiline
                  autoCapitalize="none"
                  autoCorrect={false}
                  placeholder="src/api/**"
                  placeholderTextColor={C.faint}
                />

                <PushButton
                  label={enfBusy ? '' : 'enforce as guardrail'}
                  onPress={enforce}
                  disabled={enfBusy || !title.trim()}
                  style={styles.wide}>
                  {enfBusy && <ActivityIndicator size="small" color={C.text} />}
                </PushButton>
                {!!enfMsg && <Text style={styles.hint}>{enfMsg}</Text>}
              </>
            )}

            {!!savedNote && <Text style={styles.savedNote}>{savedNote}</Text>}
            {!!err && <Text style={styles.error}>{err}</Text>}
          </ScrollView>

          <View style={styles.row}>
            <PushButton label="cancel" onPress={onClose} disabled={busy} style={styles.btn} />
            {existsAlready && (
              <PushButton
                label={sure ? 'really delete' : isFile ? 'empty' : 'delete'}
                colour={C.bad}
                lit={sure}
                onPress={remove}
                disabled={busy}
                style={styles.btn}
              />
            )}
            <PushButton
              label={busy ? '' : moving ? 'move here' : 'save'}
              colour={C.accentText}
              lit
              onPress={moving ? move : save}
              disabled={moving ? busy : !canSave}
              style={styles.btn}>
              {busy && <ActivityIndicator size="small" color={C.text} />}
            </PushButton>
          </View>
        </View>
      </View>
    </Modal>
  );
}

// The "what <agent> reads" key's modal: effective(cwd) in load order, plus a
// review that hands the same concatenation to an isolated model and lists
// what it found. Tapping a file, in either list, opens it in the row editor
// above -- the same sheet, reached from a different door.
function ReadsModal({ base, cwd, agentName, rows, onClose, onOpenFile }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [reviewNote, setReviewNote] = useState('');
  const [issues, setIssues] = useState(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    effectiveRules(base, cwd)
      .then((d) => {
        if (live) {
          setData(d);
          setNote('');
        }
      })
      // same "quietly missing" rule as the list itself
      .catch(() => live && setNote('not available yet'))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [base, cwd]);

  function runReview() {
    if (reviewing) return;
    setReviewing(true);
    setReviewNote('');
    setIssues(null);
    reviewRules(base, cwd)
      .then((d) => setIssues(d.issues || []))
      .catch((e) => setReviewNote(e.message || 'not available yet'))
      .finally(() => setReviewing(false));
  }

  function openFile(shown) {
    const found = rows.find((r) => r.shown === shown || r.path === shown);
    if (found) onOpenFile(found);
  }

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>what {agentName || 'this agent'} reads</Text>

          <ScrollView
            style={styles.bodyScroll}
            contentContainerStyle={styles.bodyCol}
            keyboardShouldPersistTaps="handled">
            {loading && <ActivityIndicator size="small" color={C.faint} />}
            {!!note && <Text style={styles.hint}>{note}</Text>}
            {!!data && (
              <>
                <Text style={styles.hint}>{data.total_lines || 0} lines total, in load order</Text>
                {(data.files || []).map((f, i) => (
                  <Pressable
                    key={`${f.path || f.shown}-${i}`}
                    accessibilityRole="button"
                    accessibilityLabel={`open ${f.shown}`}
                    onPress={() => openFile(f.shown)}
                    style={styles.readRow}>
                    <Text style={styles.readPath} numberOfLines={1}>
                      {f.shown}
                    </Text>
                    <Text style={styles.readMeta}>
                      {f.scope} · {f.when} · {f.lines} lines
                      {f.imported_by ? ` · via ${f.imported_by}` : ''}
                    </Text>
                  </Pressable>
                ))}
                {!!(data.warnings || []).length && (
                  <>
                    <Text style={styles.advanced}>warnings</Text>
                    {data.warnings.map((w, i) => (
                      <Text key={i} style={styles.warnLine}>
                        {w}
                      </Text>
                    ))}
                  </>
                )}
              </>
            )}

            <Text style={styles.advanced}>review</Text>
            <PushButton
              label={reviewing ? '' : 'review for conflicts'}
              onPress={runReview}
              disabled={reviewing}
              style={styles.wide}>
              {reviewing && <ActivityIndicator size="small" color={C.text} />}
            </PushButton>
            {reviewing && <Text style={styles.hint}>can take a minute</Text>}
            {!!reviewNote && <Text style={styles.hint}>{reviewNote}</Text>}
            {!!issues && !issues.length && <Text style={styles.hint}>no issues found</Text>}
            {!!issues &&
              issues.map((it, i) => (
                <View key={i} style={styles.issue}>
                  <Text style={styles.issueKind}>{it.kind}</Text>
                  {(it.files || []).map((f, j) => (
                    <Pressable
                      key={j}
                      accessibilityRole="button"
                      accessibilityLabel={`open ${f}`}
                      onPress={() => openFile(f)}>
                      <Text style={styles.readPath}>{f}</Text>
                    </Pressable>
                  ))}
                  {!!it.quote && (
                    <Text style={[styles.hint, mono]} numberOfLines={3}>
                      “{it.quote}”
                    </Text>
                  )}
                  {!!it.why && <Text style={styles.hint}>{it.why}</Text>}
                  {!!it.suggest && <Text style={styles.hint}>suggest: {it.suggest}</Text>}
                </View>
              ))}
          </ScrollView>

          <View style={styles.row}>
            <PushButton label="close" onPress={onClose} style={styles.btn} />
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  whatReads: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 2,
  },
  whatReadsText: { color: C.faint, fontSize: 11 },
  list: { padding: 12, gap: 4 },
  group: { gap: 3, paddingBottom: 8 },
  item: {
    minHeight: S.hit,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
  },
  rowBody: { flex: 1, gap: 2 },
  itemTop: { flexDirection: 'row', alignItems: 'center', gap: 7, flexWrap: 'wrap' },
  rowName: { color: C.text, fontSize: 13, flexShrink: 1 },
  rowFaint: { color: C.faint },
  rowSub: { color: C.faint, fontSize: 11 },
  createNote: { color: C.accentText, fontSize: 11 },
  globChip: {
    color: C.faint,
    fontSize: 10,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  syncRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  inSync: { color: C.good },
  outSync: { color: C.warn },
  doubled: { color: C.warn, fontSize: 11, flexShrink: 1 },
  amberDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: C.warn },
  none: { color: C.faint, fontSize: 12, padding: 12 },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  card: {
    width: 520,
    maxWidth: '92%',
    maxHeight: '88%',
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    padding: S.pad,
    gap: 8,
  },
  title: { color: C.text, fontSize: 16, fontWeight: '600', marginBottom: 4 },
  bodyScroll: { flexGrow: 0, flexShrink: 1 },
  bodyCol: { gap: 8 },
  label: { color: C.dim, fontSize: 12 },
  hint: { color: C.faint, fontSize: 11 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: {
    color: C.faint,
    fontSize: 11,
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 6,
  },
  chipOn: { color: C.text, borderColor: C.accentText },
  input: {
    backgroundColor: C.raised,
    color: C.text,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: S.radius,
    padding: 8,
    fontSize: 13,
    minHeight: S.hit,
  },
  tall: { minHeight: 72, textAlignVertical: 'top' },
  taller: { minHeight: 160, textAlignVertical: 'top' },
  savedNote: { color: C.good, fontSize: 11 },
  error: { color: C.bad, fontSize: 12 },
  row: { flexDirection: 'row', gap: 8, marginTop: 8 },
  wide: { minHeight: S.hit, marginTop: 8 },
  btn: { flex: 1, minHeight: S.hit },
  advanced: {
    color: C.faint,
    fontSize: 11,
    marginTop: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  readRow: { paddingVertical: 6, gap: 2 },
  readPath: { color: C.accentText, fontSize: 12 },
  readMeta: { color: C.faint, fontSize: 10 },
  warnLine: { color: C.warn, fontSize: 11 },
  issue: {
    gap: 3,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: C.line,
  },
  issueKind: { color: C.text, fontSize: 11, fontWeight: '600', textTransform: 'uppercase' },
});
