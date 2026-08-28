import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import Inspector from './Inspector';
import PadGrid from './PadGrid';
import PushButton from './PushButton';
import { ANSWER_HEX, C, S, hexFor, mono } from './theme';

const TEST_HEX = { pass: '#3cd05a', fail: '#e03c3c', run: '#f0c828', '': C.edge };

// The right rail is always the same question answered: what are the pads right
// now? On the Push that answer is the grid itself changing job. Here it is a
// list, because a list can say what a lit square cannot.
export default function Pads({
  kind,
  data,
  opts,
  macros,
  labels,
  sel,
  moving,
  armed,
  grid,
  onGrid,
  editing,
  page,
  total,
  onward,
  onPage,
  onPress,
  onDrop,
  onExecute,
  onEdit,
  onSave,
  onClear,
  onAddLabel,
  onDelLabel,
  onClose,
  onAnswer,
}) {
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
          <Text style={styles.title}>Pad {36 + sel}</Text>
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

  // focus, prs, usage: the macros. On the Push the grid darkens in prs and
  // usage -- sixty-four lit pads about something else says nothing. That reason
  // is about the grid, not about the macros, and it does not survive the trip.
  const filled = macros.filter(Boolean).length;
  return (
    // the grid is 1:1 with the Push, and eight columns of readable label do not
    // fit a list's width -- so the rail takes the room it needs to be that
    <View style={[styles.rail, grid && styles.railWide]}>
      <View style={styles.headCol}>
        <View style={styles.head}>
          <Text style={styles.label}>MACROS · {filled}</Text>
          <View style={styles.spacer} />
          <View style={styles.seg}>
            <Text
              onPress={() => onGrid(false)}
              style={[styles.segAt, !grid && styles.segOn]}>
              Library
            </Text>
            <Text
              onPress={() => onGrid(true)}
              style={[styles.segAt, grid && styles.segOn]}>
              Grid
            </Text>
          </View>
        </View>
        {grid && (
          <View style={styles.head}>
            <PushButton
              label="‹"
              colour={C.accentText}
              lit={page > 0}
              onPress={() => onPage(-1)}
              style={styles.pageKey}
            />
            <Text style={styles.pageAt}>
              page {page + 1}/{total}
            </Text>
            <PushButton
              label="›"
              colour={C.accentText}
              lit={onward}
              onPress={() => onPage(1)}
              style={styles.pageKey}
            />
          </View>
        )}
      </View>

      {grid ? (
        <View style={styles.gridWrap}>
          <PadGrid
            key={page}
            macros={macros}
            sel={sel}
            moving={moving}
            armed={armed}
            opts={[]}
            onPress={onPress}
            onDrop={onDrop}
            onExecute={onExecute}
            onEdit={onEdit}
            onAnswer={() => {}}
          />
        </View>
      ) : (
        <Library
          macros={macros}
          labels={labels}
          sel={sel}
          onPress={onPress}
          onExecute={onExecute}
          onEdit={onEdit}
        />
      )}
    </View>
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
function Library({ macros, labels, sel, onPress, onExecute, onEdit }) {
  const groups = labels.map((l) => ({
    name: l.name,
    colour: l.colour,
    items: macros
      .map((m, i) => (m && m.tag === l.name ? { m, i } : null))
      .filter(Boolean),
  }));
  const loose = macros
    .map((m, i) => (m && !labels.some((l) => l.name === m.tag) ? { m, i } : null))
    .filter(Boolean);
  if (loose.length) groups.push({ name: 'untagged', colour: null, items: loose });

  return (
    <ScrollView contentContainerStyle={styles.list}>
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
  railWide: { width: 528 },
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
  spacer: { flex: 1 },
  key: { height: 32, minHeight: 32, minWidth: 70 },
  seg: { flexDirection: 'row', borderWidth: 1, borderColor: C.line, borderRadius: 6, overflow: 'hidden' },
  segAt: { color: C.faint, fontSize: 11, paddingHorizontal: 11, paddingVertical: 5 },
  segOn: { color: C.text, backgroundColor: C.line },
  pageKey: { width: 52, height: 30, minHeight: 30, minWidth: 0 },
  pageAt: { color: C.dim, fontSize: 11, flex: 1, textAlign: 'center' },
  gridWrap: { flex: 1, padding: 8 },
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
