import { useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import Icon from './Icon';
import Inspector from './Inspector';
import { MemoryPanel } from './Memory';
import PushButton from './PushButton';
import { QueuePanel } from './Queue';
import { ANSWER_HEX, C, S, hexFor, mono } from './theme';

const TEST_HEX = { pass: '#3cd05a', fail: '#e03c3c', run: '#f0c828', '': C.edge };

// The scope stripe, borrowed from the pad palette so the panel reads as one
// thing whichever tab it is on.
const SCOPE_HEX = {
  project: hexFor(21),   // green -- this repo's own
  local: hexFor(33),     // cyan -- this repo, not committed
  user: hexFor(45),      // blue -- yours, everywhere
  plugin: hexFor(53),    // violet -- somebody else's
};

// The right rail is always the same question answered: what are the pads right
// now? On the Push that answer is the grid itself changing job. Here it is a
// list, because a list can say what a lit square cannot.
export default function Pads({
  kind,
  data,
  opts,
  macros,
  labels,
  promptRoot,
  promptPath,
  panel,
  onPanel,
  catalog,
  onEntry,
  onNewPad,
  sel,
  editing,
  onPress,
  onExecute,
  onEdit,
  onSave,
  onClear,
  onAddLabel,
  onDelLabel,
  onClose,
  onAnswer,
  onCollapse,
  queue = [],
  onQueueChange,
  queueErr,
  base,
  cwd,
}) {
  const [q, setQ] = useState('');
  const [masterScope, setMasterScope] = useState('global');

  // whatever the view, a pending question is what the pads are
  if (opts && opts.length) {
    return (
      <Shell title="ANSWER" sub={`${opts.length} · what the pads are`}>
        {opts.map(([num, label], k) => {
          const hue = ANSWER_HEX[k % ANSWER_HEX.length];
          return (
            <PushButton
              key={k}
              colour={hue}
              lit
              onPress={() => onAnswer(k)}
              style={[styles.ans, { borderColor: hue }]}>
              <View style={styles.ansRow}>
                <Text style={[styles.ansNum, { backgroundColor: hue }]}>{num}</Text>
                <Text style={styles.rowName} numberOfLines={3}>
                  {label}
                </Text>
              </View>
            </PushButton>
          );
        })}
      </Shell>
    );
  }

  if (editing) {
    return (
      <View style={styles.rail}>
        <View style={styles.head}>
          <View
            style={[styles.bar, { backgroundColor: hexFor((macros[sel] || {}).colour) }]}
          />
          {/* the note number only matters with macros.json open by hand --
              dropped well back so "Pad" reads first and the address second */}
          <Text style={styles.title}>
            Prompt
          </Text>
          <View style={styles.spacer} />
          <PushButton label="close" onPress={onClose} style={styles.key} />
        </View>
        <Inspector
          index={sel}
          pad={sel === null ? null : macros[sel] || null}
          labels={labels}
          promptRoot={promptRoot}
          promptPath={promptPath}
          defaultScope={masterScope}
          onSave={onSave}
          onClear={onClear}
          onAddLabel={onAddLabel}
          onDelLabel={onDelLabel}
        />
      </View>
    );
  }

  if (kind === 'subs' || kind === 'sessions') {
    const rows = (data || {}).rows || [];
    return (
      <Shell title="SUBAGENTS" sub={`${rows.length} · what the pads are`}>
        {rows.map((r, i) => (
          <View key={i} style={styles.row}>
            <View
              style={[
                styles.dot,
                { backgroundColor: r.running ? '#f0c828' : '#3cd05a' },
              ]}
            />
            <View style={styles.rowBody}>
              <Text style={styles.rowName} numberOfLines={2}>
                {r.label}
              </Text>
              <Text style={styles.rowSub}>{r.type}</Text>
            </View>
          </View>
        ))}
        {rows.length === 0 && <Text style={styles.none}>none spawned yet</Text>}
      </Shell>
    );
  }

  if (kind === 'tests') {
    const items = (data || {}).items || [];
    return (
      <Shell title="TEST ITEMS" sub={`${items.length} · what the pads are`}>
        {items.map((it, i) => (
          <View key={i} style={styles.row}>
            <View
              style={[styles.dot, { backgroundColor: TEST_HEX[it.state] || C.edge }]}
            />
            <Text style={[styles.rowName, mono]} numberOfLines={1}>
              {it.name}
              {it.dir ? '/' : ''}
            </Text>
          </View>
        ))}
        {items.length === 0 && <Text style={styles.none}>nothing here</Text>}
      </Shell>
    );
  }

  // focus, prs, usage: the macros -- and, on the same shelf, the two other
  // things an agent works from. Prompts are what you send it; skills and hooks
  // are what it already has. Same column, three tabs, because they answer the
  // same question at three removes and only one of them fits at a time.
  const skills = catalog?.skills || [];
  const hooks = catalog?.hooks || [];
  const inMasterScope = (row) => masterScope === 'global'
    ? row.scope === 'user' || row.scope === 'plugin'
    : row.scope === masterScope;
  const promptInMasterScope = (prompt) => {
    const scope = prompt?.scope || 'global';
    if (scope !== masterScope) return false;
    if (scope === 'project' && prompt.project && prompt.project !== promptRoot) return false;
    if (scope === 'local' && prompt.worktree && prompt.worktree !== promptPath) return false;
    return true;
  };
  const counts = {
    prompts: macros.filter((prompt) => prompt && promptInMasterScope(prompt)).length,
    skills: skills.filter(inMasterScope).length,
    hooks: hooks.filter(inMasterScope).length,
    queue: queue.length,
  };
  const at = panel || 'prompts';
  const placeholder =
    at === 'prompts' ? 'search prompts' : at === 'skills' ? 'search skills' : 'search hooks';
  // the queue and memory are each their own thing, not the catalog: the
  // queue is this agent's own list, memory is a search over a different
  // store entirely, so neither wants the scope tabs, the catalog search or ＋
  const catalogTab = at !== 'queue' && at !== 'memory';

  return (
    <View style={styles.rail}>
      <View style={styles.headCol}>
        {catalogTab && (
        <View style={styles.masterTabs}>
          {[
            ['global', 'global'],
            ['project', 'project'],
            ['local', 'project local'],
          ].map(([key, word]) => (
            <Pressable
              key={key}
              accessibilityRole="tab"
              accessibilityState={{ selected: masterScope === key }}
              onPress={() => setMasterScope(key)}>
              <Text style={[styles.masterTab, masterScope === key && styles.masterTabOn]}>
                {word}
              </Text>
            </Pressable>
          ))}
        </View>
        )}
        <View style={styles.tabs}>
          {['prompts', 'skills', 'hooks', 'queue', 'memory'].map((k) => (
            <Text
              key={k}
              accessibilityRole="tab"
              accessibilityState={{ selected: at === k }}
              onPress={() => onPanel && onPanel(k, true)}
              style={[styles.tab, at === k && styles.tabOn]}>
              {counts[k] == null ? k : `${k} · ${counts[k]}`}
            </Text>
          ))}
          {/* the pane's title row used to own open/close for this panel;
              now that it doesn't, the panel needs its own way out */}
          {onCollapse && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="collapse the panel"
              onPress={onCollapse}
              style={styles.collapse}>
              <Icon name="right" size={18} color={C.faint} />
            </Pressable>
          )}
        </View>
        {catalogTab && (
        <TextInput
          value={q}
          onChangeText={setQ}
          autoCapitalize="none"
          autoCorrect={false}
          clearButtonMode="while-editing"
          style={styles.find}
          placeholder={placeholder}
          placeholderTextColor={C.faint}
        />
        )}
      </View>

      {/* Every tab gets the same key, in the same place: the panel is one
          shelf with three things on it, and a ＋ that moves or vanishes
          between them would read as three panels that happen to share a
          column. */}
      {catalogTab && (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`new ${at === 'prompts' ? 'prompt' : at.slice(0, -1)}`}
        onPress={() =>
          at === 'prompts' ? onNewPad && onNewPad() : onEntry && onEntry(at, null, masterScope)
        }
        style={styles.add}>
        <Text style={styles.addText}>
          ＋ {at === 'prompts' ? 'prompt' : at.slice(0, -1)}
        </Text>
      </Pressable>
      )}

      {at === 'prompts' && (
        <Library
          macros={macros}
          labels={labels}
          promptRoot={promptRoot}
          promptPath={promptPath}
          promptScope={masterScope}
          q={q}
          sel={sel}
          onPress={onPress}
          onExecute={onExecute}
          onEdit={onEdit}
        />
      )}
      {at === 'skills' && <Scoped rows={skills} labels={labels} q={q} kind="skills" masterScope={masterScope} onEntry={onEntry} />}
      {at === 'queue' && <QueuePanel queue={queue} onChange={onQueueChange} err={queueErr} />}
      {at === 'hooks' && <Scoped rows={hooks} labels={labels} q={q} kind="hooks" masterScope={masterScope} onEntry={onEntry} />}
      {at === 'memory' && <MemoryPanel base={base} cwd={cwd} />}
    </View>
  );
}

// Where a thing came from is the first fact about it: a hook in the repo is
// the team's, one in ~/.claude is yours, and telling them apart is most of
// what you open this panel to do. So scope is the grouping, not a tag.
function Scoped({ rows, labels, q, kind, masterScope, onEntry }) {
  const [skillLabel, setSkillLabel] = useState('all');
  const find = q.trim().toLowerCase();
  const hit = (r) =>
    !find ||
    `${r.name || ''} ${r.label || ''} ${r.description || ''} ${r.event || ''} ${r.matcher || ''} ${r.command || ''}`
      .toLowerCase()
      .includes(find);
  const inMasterScope = (row) => masterScope === 'global'
    ? row.scope === 'user' || row.scope === 'plugin'
    : row.scope === masterScope;
  const shown = rows.filter((row) => inMasterScope(row) && hit(row));
  const knownLabels = new Set((labels || []).map((item) => item.name));
  const rowLabel = (row) => knownLabels.has(row.label) ? row.label : 'untagged';
  const itemLabels = [...new Set(
    shown.map(rowLabel)
  )].sort((a, b) => a.localeCompare(b));
  const labelTabs = ['all', ...itemLabels];
  const activeSkillLabel = labelTabs.includes(skillLabel) ? skillLabel : 'all';
  const visibleItems = activeSkillLabel !== 'all'
    ? shown.filter((row) => rowLabel(row) === activeSkillLabel)
    : shown;

  return (
    <>
      {labelTabs.length > 1 && (
        <View style={styles.subTabs}>
          {labelTabs.map((label) => {
            const count = label === 'all'
              ? shown.length
              : shown.filter((row) => rowLabel(row) === label).length;
            return (
              <Pressable
                key={label}
                accessibilityRole="tab"
                accessibilityState={{ selected: activeSkillLabel === label }}
                onPress={() => setSkillLabel(label)}
                style={styles.promptTab}>
                <Text style={[styles.subTab, activeSkillLabel === label && styles.subTabOn]}>
                  {label}<Text style={styles.subTabN}> {count}</Text>
                </Text>
              </Pressable>
            );
          })}
        </View>
      )}
      <ScrollView contentContainerStyle={styles.list}>
        {!shown.length && (
          <Text style={styles.none}>
            {find ? `nothing matches “${q.trim()}”` : `no ${kind}`}
          </Text>
        )}
        {!!shown.length && (
        <View key={masterScope} style={styles.group}>
          {visibleItems.map((r, i) => {
            // a plugin's skill opens too -- read-only, to be read, opened in an
            // editor, or copied somewhere it becomes yours. What it is not is
            // saveable, which its next update would undo without saying so.
            const locked = r.scope === 'plugin';
            return (
            <Pressable
              key={i}
              accessibilityRole="button"
              accessibilityLabel={`${locked ? 'read' : 'edit'} ${
                kind === 'skills' ? r.name : `${r.event} hook`
              }`}
              onPress={() => onEntry && onEntry(kind, r)}
              style={styles.item}>
              <View style={styles.pick}>
                <View style={[styles.bar, { backgroundColor: SCOPE_HEX[r.scope] || C.edge }]} />
                <View style={styles.rowBody}>
                  <Text style={styles.rowName} numberOfLines={1}>
                    {kind === 'skills' ? r.name : r.event}
                    {kind === 'hooks' && !!r.matcher && (
                      <Text style={styles.rowSub}> {r.matcher}</Text>
                    )}
                  </Text>
                  <Text style={[styles.rowSub, kind === 'hooks' && mono]} numberOfLines={2}>
                    {kind === 'skills' ? r.description : r.command}
                  </Text>
                </View>
              </View>
            </Pressable>
            );
          })}
        </View>
        )}
      </ScrollView>
    </>
  );
}

function Shell({ title, sub, children }) {
  return (
    <View style={styles.rail}>
      <View style={styles.headCol}>
        <Text style={styles.label}>{title}</Text>
        <Text style={styles.sub}>{sub}</Text>
      </View>
      <ScrollView contentContainerStyle={styles.list}>{children}</ScrollView>
    </View>
  );
}

// Grouped by the colour labels themselves: the tag is already how you think
// about a pad, and the grid could only ever show it as a stripe.
function Library({ macros, labels, promptRoot, promptPath, promptScope, q, sel, onPress, onExecute, onEdit }) {
  const [labelTab, setLabelTab] = useState(null);
  const [formPrompt, setFormPrompt] = useState(null);
  const [formValues, setFormValues] = useState({});
  // label, prompt body and tag all: you look for a prompt by whichever of
  // the three you happen to remember
  const find = q.trim().toLowerCase();
  const hit = (m) =>
    !find || `${m.label} ${m.text} ${m.tag || ''}`.toLowerCase().includes(find);
  const scopedMacros = macros.map((m, i) => ({ m, i })).filter(({ m }) => {
    if (!m) return false;
    const scope = m.scope || 'global';
    if (scope !== promptScope) return false;
    if (scope === 'project' && m.project && m.project !== promptRoot) return false;
    if (scope === 'local' && m.worktree && m.worktree !== promptPath) return false;
    return true;
  });
  const allItems = scopedMacros
    .map(({ m, i }) => (hit(m) ? { m, i } : null))
    .filter(Boolean);
  const groups = [{ name: 'all', colour: null, items: allItems }, ...labels.map((l) => ({
    name: l.name,
    colour: l.colour,
    items: scopedMacros
      .map(({ m, i }) => (m.tag === l.name && hit(m) ? { m, i } : null))
      .filter(Boolean),
  }))];
  const loose = scopedMacros
    .map(({ m, i }) =>
      !labels.some((l) => l.name === m.tag) && hit(m) ? { m, i } : null
    )
    .filter(Boolean);
  if (loose.length) groups.push({ name: 'untagged', colour: null, items: loose });
  const available = groups.filter((g) => g.items.length);
  // Searching can empty the selected label. Falling through to the first tab
  // with a hit keeps a cross-label search from looking falsely empty.
  const active = available.find((g) => g.name === labelTab) || available[0] || null;
  const runPrompt = (m, i) => {
    if (!m.fields?.length) return onExecute(i);
    setFormValues({});
    setFormPrompt({ m, i });
  };
  const stageFormPrompt = () => {
    if (!formPrompt) return;
    const { m, i } = formPrompt;
    let dynamic = m.dynamic || (m.prefix && m.text.startsWith(m.prefix)
      ? m.text.slice(m.prefix.length).trimStart()
      : m.text);
    for (const field of m.fields || [])
      dynamic = dynamic.split(`{{${field.name}}}`).join((formValues[field.name] || '').trim());
    const rendered = [m.prefix?.trim(), dynamic.trim()].filter(Boolean).join('\n\n');
    setFormPrompt(null);
    onExecute(i, rendered);
  };

  return (
    <>
      {available.length > 1 && (
        <View style={styles.subTabs}>
          {available.map((g) => (
            <Pressable
              key={g.name}
              accessibilityRole="tab"
              accessibilityState={{ selected: active?.name === g.name }}
              onPress={() => setLabelTab(g.name)}
              style={styles.promptTab}>
              <View
                style={[
                  styles.promptTabDot,
                  g.name === 'all' && styles.promptTabDotAll,
                  { backgroundColor: g.colour === null ? C.edge : hexFor(g.colour) },
                ]}
              />
              <Text style={[styles.subTab, active?.name === g.name && styles.subTabOn]}>
                {g.name}<Text style={styles.subTabN}> {g.items.length}</Text>
              </Text>
            </Pressable>
          ))}
        </View>
      )}
      <ScrollView contentContainerStyle={styles.list}>
      {!active && (
        <Text style={styles.none}>
          {find ? `nothing matches “${q.trim()}”` : 'no prompts'}
        </Text>
      )}
      {!!active && (
          <View key={active.name} style={styles.group}>
            {active.items.map(({ m, i }) => (
              <View key={i} style={[styles.item, sel === i && styles.itemOn]}>
                {/* only the label picks the pad. A responder on the whole row
                    swallows the taps meant for the row's own keys: it toggled
                    the selection off and edit never heard the press. */}
                <Pressable style={styles.pick} onPress={() => onPress(i)}>
                  <View style={[styles.bar, { backgroundColor: hexFor(m.colour) }]} />
                  <View style={styles.rowBody}>
                    <View style={styles.itemTop}>
                      <Text style={styles.rowName} numberOfLines={1}>
                        {m.label}
                      </Text>
                    </View>
                    {m.text !== m.label && (
                      <Text style={styles.rowSub} numberOfLines={1}>
                        {m.text}
                      </Text>
                    )}
                  </View>
                </Pressable>
                {sel === i ? (
                  <View style={styles.acts}>
                    <PushButton
                      label="run"
                      colour="#f03c3c"
                      lit
                      onPress={() => runPrompt(m, i)}
                      style={styles.actKey}
                    />
                    <PushButton
                      label="edit"
                      colour={C.accentText}
                      onPress={() => onEdit(i)}
                      style={styles.actKey}
                    />
                  </View>
                ) : null}
              </View>
            ))}
          </View>
      )}
      </ScrollView>
      <Modal visible={!!formPrompt} transparent animationType="fade" onRequestClose={() => setFormPrompt(null)}>
        <Pressable style={styles.formBack} onPress={() => setFormPrompt(null)}>
          <Pressable style={styles.formModal} onPress={() => {}}>
            <Text style={styles.formTitle}>{formPrompt?.m.label || 'Prompt details'}</Text>
            <Text style={styles.formHint}>
              These values are appended to the stable cached prefix. After the prompt is added to the input, send it without editing its beginning so the prefix remains cacheable.
            </Text>
            <ScrollView contentContainerStyle={styles.formFields} keyboardShouldPersistTaps="handled">
              {(formPrompt?.m.fields || []).map((field, i) => (
                <View key={`${field.name}-${i}`} style={styles.formField}>
                  <Text style={styles.formLabel}>{field.label || field.name}</Text>
                  <TextInput
                    value={formValues[field.name] || ''}
                    onChangeText={(value) => setFormValues((all) => ({ ...all, [field.name]: value }))}
                    placeholder={field.placeholder || `Enter ${field.label || field.name}`}
                    placeholderTextColor={C.faint}
                    style={styles.formInput}
                    multiline
                  />
                </View>
              ))}
            </ScrollView>
            <View style={styles.formActions}>
              <Pressable onPress={() => setFormPrompt(null)} style={styles.formCancel}>
                <Text style={styles.formCancelText}>cancel</Text>
              </Pressable>
              <PushButton label="add to input" colour={C.accentText} lit onPress={stageFormPrompt} style={styles.formSubmit} />
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  rail: { width: 344, borderLeftWidth: 1, borderLeftColor: C.line },
  headCol: {
    paddingHorizontal: 12,
    paddingTop: S.pad,
    paddingBottom: 10,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: C.line,
  },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  label: { color: C.faint, fontSize: 10, letterSpacing: 1.2 },
  sub: { color: C.edge, fontSize: 11 },
  title: { color: C.text, fontSize: 15, fontWeight: '600' },
  titleNote: { color: C.edge, fontWeight: '400' },
  spacer: { flex: 1 },
  key: { height: 32, minHeight: 32, minWidth: 70 },
  tabs: { flexDirection: 'row', gap: 14, paddingBottom: 2 },
  masterTabs: { flexDirection: 'row', gap: 14, paddingBottom: 2 },
  masterTab: { color: C.faint, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 },
  masterTabOn: { color: C.accentText, fontWeight: '600' },
  add: { minHeight: 30, justifyContent: 'center', paddingHorizontal: 12, paddingTop: 8 },
  addText: { color: C.accentText, fontSize: 12 },
  subTabs: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 2,
  },
  subTab: { color: C.faint, fontSize: 11 },
  subTabOn: { color: C.accentText },
  subTabN: { color: C.edge, fontSize: 10 },
  promptTab: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  promptTabDot: { width: 6, height: 6, borderRadius: 2 },
  promptTabDotAll: { opacity: 0 },
  formBack: { flex: 1, backgroundColor: 'rgba(0,0,0,.72)', alignItems: 'center', justifyContent: 'center', padding: 24 },
  formModal: { width: '100%', maxWidth: 620, maxHeight: '84%', backgroundColor: C.panel, borderWidth: 1, borderColor: C.edge, borderRadius: S.radius, padding: 18, gap: 12 },
  formTitle: { color: C.text, fontSize: 18, fontWeight: '600' },
  formHint: { color: C.faint, fontSize: 12, lineHeight: 18 },
  formFields: { gap: 12, paddingVertical: 4 },
  formField: { gap: 5 },
  formLabel: { color: C.dim, fontSize: 12, fontWeight: '600' },
  formInput: { minHeight: 44, maxHeight: 120, color: C.text, backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 6, padding: 10, fontSize: 14 },
  formActions: { flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 10 },
  formCancel: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 12 },
  formCancelText: { color: C.faint, fontSize: 12 },
  formSubmit: { minHeight: 40, minWidth: 120 },
  tab: { color: C.faint, fontSize: 11, letterSpacing: 0.4 },
  tabOn: { color: C.text, fontWeight: '600' },
  collapse: { minWidth: S.hit, minHeight: S.hit, alignItems: 'center', justifyContent: 'center', marginLeft: 'auto' },
  find: {
    height: 32,
    color: C.text,
    backgroundColor: C.panel,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 6,
    paddingHorizontal: 10,
    fontSize: 13,
  },
  list: { padding: 12, gap: 4 },
  group: { gap: 3, paddingBottom: 8 },
  groupHead: { flexDirection: 'row', alignItems: 'center', gap: 7, paddingVertical: 6, paddingHorizontal: 4 },
  swatch: { width: 8, height: 8, borderRadius: 2 },
  groupName: { color: C.dim, fontSize: 11, fontWeight: '600' },
  groupN: { color: C.edge, fontSize: 11 },
  item: {
    minHeight: S.hit,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 6,
  },
  itemOn: { backgroundColor: C.raised, borderWidth: 1, borderColor: C.edge },
  pick: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: S.hit },
  itemTop: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  bar: { width: 3, alignSelf: 'stretch', borderRadius: 2, minHeight: 22 },
  rowBody: { flex: 1, gap: 2 },
  rowName: { color: C.text, fontSize: 13, flexShrink: 1 },
  rowSub: { color: C.faint, fontSize: 11 },
  fires: {
    color: C.bad,
    fontSize: 9,
    borderWidth: 1,
    borderColor: '#4a2a2a',
    borderRadius: 3,
    paddingHorizontal: 4,
    overflow: 'hidden',
  },
  note: { color: C.edge, fontSize: 11, ...mono },
  acts: { flexDirection: 'row', gap: 6 },
  actKey: { height: 30, minHeight: 30, minWidth: 54 },
  row: {
    minHeight: S.hit,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    backgroundColor: C.panel,
    borderWidth: 1,
    borderColor: C.line,
  },
  dot: { width: 9, height: 9, borderRadius: 5 },
  none: { color: C.faint, fontSize: 12, padding: 12 },
  ans: { minHeight: 54, alignItems: 'stretch', justifyContent: 'center', paddingHorizontal: 12, paddingBottom: 10 },
  ansRow: { flexDirection: 'row', alignItems: 'center', gap: 10, width: '100%' },
  ansNum: {
    color: C.bg,
    fontSize: 12,
    fontWeight: '700',
    minWidth: 22,
    textAlign: 'center',
    paddingVertical: 2,
    borderRadius: 4,
    overflow: 'hidden',
    ...mono,
  },
});
