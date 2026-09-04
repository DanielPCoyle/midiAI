import { useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { promptAgent } from './api';
import Projects from './Projects';
import PushButton from './PushButton';
import SessionSheet from './SessionSheet';
import { C, S, seatHue, seatWord } from './theme';

// The same two colours the subagent pads use on the glass: yellow is still
// going, green came back.
const SUB_RUN = '#f0c828';
const SUB_DONE = '#3cd05a';

// The left rail, in two groups: the repos with their worktrees, and the agents.
//
// The Push needs the agents anonymous -- it has exactly eight physical buttons
// and no room for a word above each. Nothing here is short of room, so this is
// where the tree lives: PROJECTS answers "what could I be working on", AGENTS
// answers "what is running". Projects.js derives the first from the second's
// cwds; see its own note for why there is no project list on the wire.
//
// props:
//   cols       array(8)                 -- unchanged: each slot null or
//                                           { name, status, model, effort, sub, tid, focused, unseen, context, cwd? }.
//                                           `cwd` is what Projects groups by, and what the 'new' and
//                                           'worktree' sheets default their cwd field to.
//   current    number                   -- unchanged: index of the focused seat
//   onSeat     (i) => void              -- unchanged: tap a card to focus it
//   base       string                   -- api base url, threaded straight through to SessionSheet,
//                                           which does the actual create/rename/close/worktree calls
//   onPick     (pick|null) => void      -- passed straight to Projects: a worktree with nobody in
//                                          it was picked, and the pane draws the panel for it
//   subs       array                    -- the focused agent's subagents, `[{label, type, running}]`,
//                                          straight off surface.views_data.sessions[0].rows. The wire
//                                          only carries the FOCUSED agent's: reading them per seat
//                                          means globbing and parsing every agent's transcript on
//                                          every 400ms poll, so the tree hangs under the lit card.
//   onChanged  () => void               -- fired after any of the four operations succeeds, so the
//                                           coordinator knows to refetch /agents and refresh `cols`
export default function Rail({ cols, current, onSeat, base, onChanged, onPick, subs = [] }) {
  // { mode: 'new'|'rename'|'close'|'worktree', seatIndex: number|null } | null
  const [sheet, setSheet] = useState(null);
  // Projects reads git, not the surface poll, so nothing tells it a worktree
  // was made or removed -- removing one nobody was sitting in does not even
  // change the set of cwds it keys off. Every operation here bumps this.
  const [beat, setBeat] = useState(0);
  const changed = () => {
    setBeat((b) => b + 1);
    onChanged && onChanged();
  };
  const free = 8 - cols.filter(Boolean).length;

  // The card's corner used to be one ⋮ over a menu. It is four keys now: the
  // three things you actually do to a running agent are one tap each rather
  // than a tap, a read and a tap.
  //
  // compact and clear are typed, not called -- /compact and /clear are Claude
  // Code's own commands and there is no API behind them, so this sends the
  // words down the same pty a pad fires into.
  const say = (col, text) => promptAgent(base, text, true, col.tid).catch(() => {});
  // clear throws away everything the agent knows, and it is a 20px target in a
  // 268px column. So it arms first: one tap reddens it, the next does it, and
  // three seconds of not meaning it puts it back.
  const [armed, setArmed] = useState(null);   // tid, or null
  const armTimer = useRef(null);
  const arm = (col) => {
    if (armed === col.tid) {
      clearTimeout(armTimer.current);
      setArmed(null);
      say(col, '/clear');
      return;
    }
    setArmed(col.tid);
    clearTimeout(armTimer.current);
    armTimer.current = setTimeout(() => setArmed(null), 3000);
  };

  // The subagent tree under the lit card. Collapsed is not the resting state:
  // they are only listed while one is running, and something running is worth
  // seeing without a click.
  const [treeShut, setTreeShut] = useState(false);
  const live = subs.filter((x) => x.running).length;

  const seatFor = (i) => (i != null ? cols[i] : null);
  const activeSeat = sheet && sheet.mode !== 'new' ? seatFor(sheet.seatIndex) : seatFor(current);

  return (
    <View style={styles.rail}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Projects
          cols={cols}
          base={base}
          beat={beat}
          onSeat={onSeat}
          onPick={onPick}
          onChanged={changed}
        />
        <Text style={styles.head}>AGENTS</Text>
        <View style={styles.list}>
          {Array.from({ length: 8 }, (_, i) => {
            const col = cols[i] || null;
            if (!col) return null;
            const hue = seatHue(col);
            const on = i === current;
            return (
              /* The ⋮ used to sit inside the card. Both are buttons, and a
                 button inside a button is not valid HTML -- React says so and
                 refuses to hydrate it. It was only a div until it was given a
                 name, so naming it is what surfaced this. Sibling now, laid
                 over the corner it already occupied. */
              <View key={i} style={styles.slot}>
              <PushButton
                colour={hue}
                // left to itself this announces as "midiAIidle⋯%0" -- the name,
                // the status, the menu glyph and the tmux id run together. Same
                // shape as the tab that answered to "· 0".
                accessibilityLabel={`${col.name} · ${seatWord(col)}`}
                lit={on}
                onPress={() => onSeat(i)}
                style={[styles.card, on && { borderColor: hue }]}>
                <View style={styles.cardBody}>
                  {/* the keys sit over this row's right end, so it keeps their
                      width clear -- always, not only while they are shown, or
                      the name would jump every time one appeared */}
                  <View style={[styles.row, styles.topRow]}>
                    <Text
                      style={[styles.name, !on && styles.nameOff]}
                      numberOfLines={1}>
                      {col.name}
                    </Text>
                    <View style={[styles.dot, { backgroundColor: hue }]} />
                    <Text style={[styles.status, { color: hue }]}>{seatWord(col)}</Text>
                  </View>
                  <View style={styles.row}>
                    {[col.model, col.effort]
                      .filter(Boolean)
                      .map((bit, k) => (
                        <Text key={k} style={styles.meta} numberOfLines={1}>
                          {k ? `· ${bit}` : bit}
                        </Text>
                      ))}
                    {/* the tmux pane id -- real only when hand-editing macros.json,
                        so it drops back further than model/effort rather than
                        reading as a fourth equally-important fact about the card */}
                    {!!col.sub && (
                      <Text style={styles.metaId} numberOfLines={1}>
                        {col.model || col.effort ? `· ${col.sub}` : col.sub}
                      </Text>
                    )}
                  </View>
                </View>
              </PushButton>
              <View style={styles.keys}>
                {[
                  // U+FE0E, or Chrome hands ✎ to the emoji font and one key in
                  // four comes out in colour beside three flat glyphs
                  ['✎\uFE0E', `rename ${col.name}`, () => setSheet({ mode: 'rename', seatIndex: i })],
                  ['⊟', `compact ${col.name}'s context`, () => say(col, '/compact')],
                  [
                    '⊘',
                    armed === col.tid
                      ? `clear ${col.name}'s context — tap again to confirm`
                      : `clear ${col.name}'s context`,
                    () => arm(col),
                    armed === col.tid,
                  ],
                  ['×', `close ${col.name}`, () => setSheet({ mode: 'close', seatIndex: i }), true],
                ].map(([glyph, label, fn, hot]) => (
                  <Pressable
                    key={glyph}
                    accessibilityRole="button"
                    accessibilityLabel={label}
                    hitSlop={4}
                    onPress={fn}
                    style={styles.keyBtn}>
                    <Text style={[styles.keyGlyph, hot && styles.keyBad]}>{glyph}</Text>
                  </Pressable>
                ))}
              </View>
              </View>
            );
          })}
          {/* Hung under the AGENTS list rather than inside the lit card: the
              card is a PushButton, and a collapsing tree inside a button is
              the same nested-interactive problem the ⋮ already taught us. */}
          {subs.length > 0 && (
            <View style={styles.tree}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${treeShut ? 'show' : 'hide'} subagents`}
                onPress={() => setTreeShut((v) => !v)}
                style={styles.treeHead}>
                <Text style={styles.caret}>{treeShut ? '▸' : '▾'}</Text>
                <Text style={styles.treeName}>subagents</Text>
                <Text style={styles.treeN}>
                  {live ? `${live} running` : subs.length}
                </Text>
              </Pressable>
              {!treeShut &&
                subs.map((x, k) => (
                  <View key={k} style={styles.treeRow}>
                    <View
                      style={[
                        styles.subDot,
                        { backgroundColor: x.running ? SUB_RUN : SUB_DONE },
                      ]}
                    />
                    <Text style={styles.subName} numberOfLines={1}>
                      {x.label}
                    </Text>
                    <Text style={styles.subType} numberOfLines={1}>
                      {x.type}
                    </Text>
                  </View>
                ))}
            </View>
          )}
          {free > 0 && (
            <PushButton
              colour="transparent"
              accessibilityLabel="new agent"
              onPress={() => setSheet({ mode: 'new', seatIndex: null })}
              style={styles.free}>
              <Text style={styles.freeText}>＋ new agent</Text>
            </PushButton>
          )}
        </View>
      </ScrollView>

      {sheet && (
        <SessionSheet
          visible
          mode={sheet.mode}
          base={base}
          tid={activeSeat?.tid}
          name={activeSeat?.name}
          cwd={activeSeat?.cwd}
          onClose={() => setSheet(null)}
          onDone={() => {
            setSheet(null);
            changed();
          }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  rail: {
    width: 268,
    borderRightWidth: 1,
    borderRightColor: C.line,
    paddingVertical: S.pad,
    paddingHorizontal: 12,
  },
  head: {
    color: C.faint,
    fontSize: 10,
    letterSpacing: 1.2,
    paddingHorizontal: 4,
    paddingTop: 14,
  },
  scroll: { paddingBottom: 12 },
  list: { gap: S.gap, paddingTop: 10 },
  card: {
    minHeight: 62,
    alignItems: 'stretch',
    justifyContent: 'center',
    paddingHorizontal: 12,
    paddingBottom: 10,
  },
  cardBody: { gap: 5, width: '100%' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  topRow: { paddingRight: 82 },
  name: { color: C.text, fontSize: 14, fontWeight: '600', flexShrink: 1 },
  nameOff: { color: C.dim },
  dot: { width: 8, height: 8, borderRadius: 4 },
  status: { fontSize: 11 },
  meta: { color: C.faint, fontSize: 11, flexShrink: 1 },
  metaId: { color: C.edge, fontSize: 11, flexShrink: 1 },
  slot: { position: 'relative' },
  keys: { position: 'absolute', top: 4, right: 4, flexDirection: 'row' },
  keyBtn: { paddingHorizontal: 4, paddingVertical: 2 },
  keyGlyph: { color: C.edge, fontSize: 13 },
  keyBad: { color: C.bad },
  tree: { paddingLeft: 6, paddingTop: 4 },
  treeHead: { flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 26 },
  caret: { color: C.dim, fontSize: 11, width: 11 },
  treeName: { color: C.faint, fontSize: 11, letterSpacing: 0.6 },
  treeN: { color: C.edge, fontSize: 11, marginLeft: 'auto', paddingRight: 4 },
  treeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    minHeight: 24,
    paddingLeft: 17,
  },
  subDot: { width: 6, height: 6, borderRadius: 3 },
  subName: { color: C.dim, fontSize: 12, flexShrink: 1 },
  subType: { color: C.edge, fontSize: 10, marginLeft: 'auto', paddingRight: 4 },
  free: {
    minHeight: 44,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: C.line,
    borderTopColor: C.line, // PushButton's own key style tints the top edge -- flatten it back to the dash
    borderRadius: S.radius,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'transparent',
    paddingBottom: 0,
  },
  freeText: { color: C.edge, fontSize: 11 },
});
