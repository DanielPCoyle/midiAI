import { useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import PushButton from './PushButton';
import SessionSheet from './SessionSheet';
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
  const free = 8 - cols.filter(Boolean).length;

  // PushButton doesn't forward onLongPress (it wraps children in its own
  // Pressable and only wires onPress) and it is out of scope here to add
  // that -- so the secondary action is a small "⋯" control inside the card
  // instead of a long press. It opens a native action list rather than a
  // custom menu component, which keeps this file free of new UI plumbing.
  const openMenu = (i, col) => {
    Alert.alert(col.name, 'choose an action', [
      { text: 'rename', onPress: () => setSheet({ mode: 'rename', seatIndex: i }) },
      { text: 'new worktree', onPress: () => setSheet({ mode: 'worktree', seatIndex: i }) },
      { text: 'close', style: 'destructive', onPress: () => setSheet({ mode: 'close', seatIndex: i }) },
      { text: 'cancel', style: 'cancel' },
    ]);
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
                    onPress={() => openMenu(i, col)}
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
});
