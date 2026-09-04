import { useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { promptAgent } from './api';
import Projects, { inside } from './Projects';
import PushButton from './PushButton';
import SessionSheet from './SessionSheet';
import { C, S, seatHue, seatWord } from './theme';

// Yellow is still going, the same colour the subagent pads use on the glass.
const SUB_RUN = '#f0c828';

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
//                                          every 400ms poll. So the count sits on the lit card only,
//                                          and says nothing at all about the others rather than
//                                          saying zero about them, which would be a lie.
//   scopePath  string                    -- the worktree "here" means: the one picked in PROJECTS,
//                                          else the one the focused agent is in. What the AGENTS
//                                          filter filters to.
//   onChanged  () => void               -- fired after any of the four operations succeeds, so the
//                                           coordinator knows to refetch /agents and refresh `cols`
export default function Rail({
  cols,
  current,
  onSeat,
  base,
  onChanged,
  onPick,
  subs = [],
  scopePath = '',
}) {
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

  // Eight seats is few enough to read at a glance and too many to read while
  // you are working in one repo of three. `here` is not a default: hiding
  // agents by default is how you lose one, so you have to ask.
  const [onlyHere, setOnlyHere] = useState(false);
  const inScope = (col) => !!scopePath && !!col?.cwd && inside(col.cwd, scopePath);
  const hidden = onlyHere ? cols.filter((c) => c && !inScope(c)).length : 0;

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

  // The tree itself moved into the focus view, where the agent it belongs to
  // already is -- see Pane.js's sub-tabs. What stays here is the count, which
  // is the part you want while scanning the list rather than reading one.
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
        <View style={styles.headRow}>
          <Text style={styles.head}>AGENTS</Text>
          <View style={styles.spacer} />
          {/* only offered when there is a worktree to mean by "here" -- a
              filter that cannot filter is a control that lies about having
              something behind it */}
          {!!scopePath &&
            [
              ['all', false],
              ['here', true],
            ].map(([word, on]) => (
              <Text
                key={word}
                accessibilityRole="button"
                accessibilityState={{ selected: onlyHere === on }}
                onPress={() => setOnlyHere(on)}
                style={[styles.filter, onlyHere === on && styles.filterOn]}>
                {word}
              </Text>
            ))}
        </View>
        <View style={styles.list}>
          {Array.from({ length: 8 }, (_, i) => {
            const col = cols[i] || null;
            if (!col) return null;
            // filtered, never renumbered: `i` is the seat index a /press
            // carries and the Push's eighth button is still the eighth seat
            if (onlyHere && !inScope(col)) return null;
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
                    {/* only the lit card: the wire carries the focused agent's
                        subagents and nobody else's, and a blank where a count
                        would go says "not known" where a 0 would say "none" */}
                    {on && subs.length > 0 && (
                      <Text
                        style={[styles.subs, live > 0 && styles.subsLive]}
                        numberOfLines={1}>
                        {live ? `▸ ${live}/${subs.length}` : `▸ ${subs.length}`}
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
          {hidden > 0 && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`show all agents — ${hidden} hidden`}
              onPress={() => setOnlyHere(false)}
              style={styles.hidden}>
              <Text style={styles.hiddenText}>
                {hidden} elsewhere — show all
              </Text>
            </Pressable>
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
  headRow: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  spacer: { flex: 1 },
  filter: { color: C.faint, fontSize: 11, paddingHorizontal: 2 },
  filterOn: { color: C.accentText },
  hidden: { minHeight: 26, justifyContent: 'center', paddingHorizontal: 4 },
  hiddenText: { color: C.edge, fontSize: 11 },
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
  subs: { color: C.edge, fontSize: 11, marginLeft: 'auto' },
  subsLive: { color: SUB_RUN },
  slot: { position: 'relative' },
  keys: { position: 'absolute', top: 4, right: 4, flexDirection: 'row' },
  keyBtn: { paddingHorizontal: 4, paddingVertical: 2 },
  keyGlyph: { color: C.edge, fontSize: 13 },
  keyBad: { color: C.bad },
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
