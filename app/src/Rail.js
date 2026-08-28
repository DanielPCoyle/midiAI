import { useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import PushButton from './PushButton';
import SessionSheet from './SessionSheet';
import Worktrees from './Worktrees';
import { C, S, SEAT_HEX } from './theme';

// The sessions, as a rail you can read rather than eight anonymous buttons.
// The Push needs them anonymous -- it has exactly eight physical buttons and no
// room for a word above each. Nothing here is short of room.
//
// props:
//   cols       array(8)                 -- unchanged: each slot null or
//                                           { name, status, model, effort, sub, tid, focused, context, cwd? }.
//                                           `cwd` is not on the shape yet -- read optionally so the 'new'
//                                           and 'worktree' sheets default their cwd field the moment a
//                                           caller starts including it, without another round of edits here.
//   current    number                   -- unchanged: index of the focused seat
//   onSeat     (i) => void              -- unchanged: tap a card to focus it
//   base       string                   -- api base url, threaded straight through to SessionSheet,
//                                           which does the actual create/rename/close/worktree calls
//   onChanged  () => void               -- fired after any of the four operations succeeds, so the
//                                           coordinator knows to refetch /agents and refresh `cols`
export default function Rail({ cols, current, onSeat, base, onChanged }) {
  // { mode: 'new'|'rename'|'close'|'worktree', seatIndex: number|null } | null
  const [sheet, setSheet] = useState(null);
  // Worktrees is its own component, not another SessionSheet mode -- it lists
  // rather than errands, so it gets its own bit of state rather than being
  // squeezed into `sheet`'s shape. Just the seat index; the modal reads
  // cols[wtSeat].cwd itself.
  const [wtSeat, setWtSeat] = useState(null);
  const free = 8 - cols.filter(Boolean).length;

  // PushButton doesn't forward onLongPress (it wraps children in its own
  // Pressable and only wires onPress), so the secondary action is a small
  // "⋯" control inside the card rather than a long press.
  //
  // It draws its own menu rather than calling Alert.alert, which looked like
  // the cheaper answer and is a no-op on web: react-native-web ships
  // `class Alert { static alert() {} }`. On the browser build the menu did
  // nothing at all, silently -- rename, close and worktrees were simply
  // unreachable, and nothing said so. A menu the app draws works on both.
  const [menu, setMenu] = useState(null);   // seat index, or null
  const menuCol = menu != null ? cols[menu] : null;
  const act = (fn) => {
    setMenu(null);
    fn();
  };

  const seatFor = (i) => (i != null ? cols[i] : null);
  const activeSeat = sheet && sheet.mode !== 'new' ? seatFor(sheet.seatIndex) : seatFor(current);

  return (
    <View style={styles.rail}>
      <Text style={styles.head}>SESSIONS</Text>
      <ScrollView contentContainerStyle={styles.list}>
        {Array.from({ length: 8 }, (_, i) => {
          const col = cols[i] || null;
          if (!col) return null;
          const hue = SEAT_HEX[col.status] || C.faint;
          const on = i === current;
          return (
            <PushButton
              key={i}
              colour={hue}
              lit={on}
              onPress={() => onSeat(i)}
              style={[styles.card, on && { borderColor: hue }]}>
              <View style={styles.cardBody}>
                <View style={styles.row}>
                  <Text
                    style={[styles.name, !on && styles.nameOff]}
                    numberOfLines={1}>
                    {col.name}
                  </Text>
                  <View style={[styles.dot, { backgroundColor: hue }]} />
                  <Text style={[styles.status, { color: hue }]}>{col.status}</Text>
                  <Pressable
                    onPress={() => setMenu(i)}
                    hitSlop={8}
                    style={styles.menuBtn}>
                    <Text style={styles.menuDots}>⋯</Text>
                  </Pressable>
                </View>
                <View style={styles.row}>
                  {[col.model, col.effort, col.sub]
                    .filter(Boolean)
                    .map((bit, k) => (
                      <Text key={k} style={styles.meta} numberOfLines={1}>
                        {k ? `· ${bit}` : bit}
                      </Text>
                    ))}
                </View>
              </View>
            </PushButton>
          );
        })}
        {free > 0 && (
          <PushButton
            colour="transparent"
            onPress={() => setSheet({ mode: 'new', seatIndex: null })}
            style={styles.free}>
            <Text style={styles.freeText}>＋ new session</Text>
          </PushButton>
        )}
      </ScrollView>

      <Modal
        visible={menu != null}
        transparent
        animationType="fade"
        onRequestClose={() => setMenu(null)}>
        <Pressable style={styles.backdrop} onPress={() => setMenu(null)}>
          <View style={styles.menu}>
            <Text style={styles.menuHead} numberOfLines={1}>
              {menuCol?.name || ''}
            </Text>
            {[
              ['rename', () => setSheet({ mode: 'rename', seatIndex: menu })],
              ['new worktree', () => setSheet({ mode: 'worktree', seatIndex: menu })],
              ['worktrees…', () => setWtSeat(menu)],
            ].map(([label, fn]) => (
              <Pressable key={label} style={styles.menuRow} onPress={() => act(fn)}>
                <Text style={styles.menuText}>{label}</Text>
              </Pressable>
            ))}
            {/* close kills something that is running, so it sits apart and
                reads in the colour everything else dangerous does */}
            <Pressable
              style={[styles.menuRow, styles.menuLast]}
              onPress={() => act(() => setSheet({ mode: 'close', seatIndex: menu }))}>
              <Text style={[styles.menuText, styles.menuBad]}>close</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>

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
            onChanged && onChanged();
          }}
        />
      )}
      {wtSeat != null && (
        <Worktrees
          visible
          base={base}
          cwd={seatFor(wtSeat)?.cwd}
          onClose={() => setWtSeat(null)}
          onChanged={() => onChanged && onChanged()}
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
  head: { color: C.faint, fontSize: 10, letterSpacing: 1.2, paddingHorizontal: 4 },
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
  name: { color: C.text, fontSize: 14, fontWeight: '600', flexShrink: 1 },
  nameOff: { color: C.dim },
  dot: { width: 8, height: 8, borderRadius: 4 },
  status: { fontSize: 11 },
  meta: { color: C.faint, fontSize: 11, flexShrink: 1 },
  menuBtn: { marginLeft: 'auto', paddingHorizontal: 4 },
  menuDots: { color: C.faint, fontSize: 16, fontWeight: '700' },
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
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  menu: {
    width: 240,
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    paddingVertical: 6,
  },
  menuHead: {
    color: C.faint,
    fontSize: 11,
    paddingHorizontal: 14,
    paddingBottom: 6,
  },
  menuRow: { minHeight: S.hit, justifyContent: 'center', paddingHorizontal: 14 },
  menuLast: { borderTopWidth: 1, borderTopColor: C.line, marginTop: 4 },
  menuText: { color: C.text, fontSize: 14 },
  menuBad: { color: C.bad },
});
