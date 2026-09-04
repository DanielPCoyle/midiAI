import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import Inspector from './Inspector';
import PushButton from './PushButton';
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
  panel,
  onPanel,
  catalog,
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
}) {
  const [q, setQ] = useState('');

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
            Pad <Text style={styles.titleNote}>{36 + sel}</Text>
          </Text>
          <View style={styles.spacer} />
          <PushButton label="close" onPress={onClose} style={styles.key} />
        </View>
        <Inspector
          index={sel}
          pad={sel === null ? null : macros[sel] || null}
          labels={labels}
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
  const filled = macros.filter(Boolean).length;
  const skills = catalog?.skills || [];
  const hooks = catalog?.hooks || [];
  const counts = { prompts: filled, skills: skills.length, hooks: hooks.length };
  const at = panel || 'prompts';
  const placeholder =
    at === 'prompts' ? 'search prompts' : at === 'skills' ? 'search skills' : 'search hooks';

  return (
    <View style={styles.rail}>
      <View style={styles.headCol}>
        <View style={styles.tabs}>
          {['prompts', 'skills', 'hooks'].map((k) => (
            <Text
              key={k}
              accessibilityRole="tab"
              accessibilityState={{ selected: at === k }}
              onPress={() => onPanel && onPanel(k, true)}
              style={[styles.tab, at === k && styles.tabOn]}>
              {k} · {counts[k]}
            </Text>
          ))}
        </View>
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
      </View>

      {at === 'prompts' && (
        <Library
          macros={macros}
          labels={labels}
          q={q}
          sel={sel}
          onPress={onPress}
          onExecute={onExecute}
          onEdit={onEdit}
        />
      )}
      {at === 'skills' && <Scoped rows={skills} q={q} kind="skills" />}
      {at === 'hooks' && <Scoped rows={hooks} q={q} kind="hooks" />}
    </View>
  );
}

// Where a thing came from is the first fact about it: a hook in the repo is
// the team's, one in ~/.claude is yours, and telling them apart is most of
// what you open this panel to do. So scope is the grouping, not a tag.
const SCOPE_NAME = {
  project: 'project',
  local: 'project · local',
  user: 'global',
  plugin: 'plugins',
};
const SCOPE_ORDER = ['project', 'local', 'user', 'plugin'];

function Scoped({ rows, q, kind }) {
  // Which scope is showing. 131 plugin skills over 38 of your own is not a
  // list you scroll looking for one of the 38 -- so scope is a tab, not a
  // heading you pass on the way down.
  const [scope, setScope] = useState(null);
  const find = q.trim().toLowerCase();
  const hit = (r) =>
    !find ||
    `${r.name || ''} ${r.description || ''} ${r.event || ''} ${r.matcher || ''} ${r.command || ''}`
      .toLowerCase()
      .includes(find);
  const shown = rows.filter(hit);
  const groups = SCOPE_ORDER.map((scope) => ({
    scope,
    items: shown.filter((r) => r.scope === scope),
  })).filter((g) => g.items.length);
  // anything the server labelled with a scope this list has never heard of
  // still belongs on screen -- silently dropping a row is worse than a
  // heading nobody planned
  const rest = shown.filter((r) => !SCOPE_ORDER.includes(r.scope));
  if (rest.length) groups.push({ scope: 'other', items: rest });

  // A search that empties the tab you were on would otherwise read as "no
  // hooks at all" -- fall through to the first that has something instead.
  const at = groups.find((g) => g.scope === scope) || groups[0] || null;
  // One scope is not a choice, so it is not drawn as one.
  const bar = groups.length > 1;

  return (
    <>
      {bar && (
        <View style={styles.subTabs}>
          {groups.map((g) => (
            <Text
              key={g.scope}
              accessibilityRole="tab"
              accessibilityState={{ selected: at?.scope === g.scope }}
              onPress={() => setScope(g.scope)}
              style={[styles.subTab, at?.scope === g.scope && styles.subTabOn]}>
              {SCOPE_NAME[g.scope] || g.scope}
              <Text style={styles.subTabN}> {g.items.length}</Text>
            </Text>
          ))}
        </View>
      )}
      <ScrollView contentContainerStyle={styles.list}>
        {!shown.length && (
          <Text style={styles.none}>
            {find ? `nothing matches “${q.trim()}”` : `no ${kind}`}
          </Text>
        )}
        {!!at && (
        <View key={at.scope} style={styles.group}>
          {at.items.map((r, i) => (
            <View key={i} style={styles.item}>
              <View style={styles.pick}>
                <View style={[styles.bar, { backgroundColor: SCOPE_HEX[at.scope] || C.edge }]} />
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
            </View>
          ))}
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
function Library({ macros, labels, q, sel, onPress, onExecute, onEdit }) {
  // label, prompt body and tag all: you look for a prompt by whichever of
  // the three you happen to remember
  const find = q.trim().toLowerCase();
  const hit = (m) =>
    !find || `${m.label} ${m.text} ${m.tag || ''}`.toLowerCase().includes(find);
  const groups = labels.map((l) => ({
    name: l.name,
    colour: l.colour,
    items: macros
      .map((m, i) => (m && m.tag === l.name && hit(m) ? { m, i } : null))
      .filter(Boolean),
  }));
  const loose = macros
    .map((m, i) =>
      m && !labels.some((l) => l.name === m.tag) && hit(m) ? { m, i } : null
    )
    .filter(Boolean);
  if (loose.length) groups.push({ name: 'untagged', colour: null, items: loose });

  return (
    <ScrollView contentContainerStyle={styles.list}>
      {!!find && !groups.some((g) => g.items.length) && (
        <Text style={styles.none}>nothing matches “{q.trim()}”</Text>
      )}
      {groups
        .filter((g) => g.items.length)
        .map((g) => (
          <View key={g.name} style={styles.group}>
            <View style={styles.groupHead}>
              <View
                style={[
                  styles.swatch,
                  { backgroundColor: g.colour === null ? C.edge : hexFor(g.colour) },
                ]}
              />
              <Text style={styles.groupName}>{g.name}</Text>
              <Text style={styles.groupN}>{g.items.length}</Text>
            </View>
            {g.items.map(({ m, i }) => (
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
                      {!!m.submit && <Text style={styles.fires}>AUTO-SUBMITS</Text>}
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
                      onPress={() => onExecute(i)}
                      style={styles.actKey}
                    />
                    <PushButton
                      label="edit"
                      colour={C.accentText}
                      onPress={() => onEdit(i)}
                      style={styles.actKey}
                    />
                  </View>
                ) : (
                  <Text style={styles.note}>{36 + i}</Text>
                )}
              </View>
            ))}
          </View>
        ))}
    </ScrollView>
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
  tab: { color: C.faint, fontSize: 11, letterSpacing: 0.4 },
  tabOn: { color: C.text, fontWeight: '600' },
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
