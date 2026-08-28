import { ScrollView, StyleSheet, Text, View } from 'react-native';
import PushButton from './PushButton';
import { C, S, SEAT_HEX } from './theme';

// The sessions, as a rail you can read rather than eight anonymous buttons.
// The Push needs them anonymous -- it has exactly eight physical buttons and no
// room for a word above each. Nothing here is short of room.
export default function Rail({ cols, current, onSeat }) {
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
        {cols.filter(Boolean).length < 8 && (
          <View style={styles.free}>
            <Text style={styles.freeText}>
              {8 - cols.filter(Boolean).length} free slots
            </Text>
          </View>
        )}
      </ScrollView>
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
  free: {
    minHeight: 44,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: C.line,
    borderRadius: S.radius,
    alignItems: 'center',
    justifyContent: 'center',
  },
  freeText: { color: C.edge, fontSize: 11 },
});
