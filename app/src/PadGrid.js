import { Pressable, StyleSheet, Text, View } from 'react-native';
import { C, PAD_HEX, hexFor, S, mono } from './theme';

// note 36 is the Push's bottom-left pad. Row 0 (indices 0-7) is the bottom
// screen row here, and index 56-63 sit at the top -- build rows top to
// bottom on screen but count them r=7..0 so index math still reads i=r*8+c.
const ROWS = [7, 6, 5, 4, 3, 2, 1, 0];

function Pad({ i, macro, isSel, isMoving, onPress }) {
  return (
    <Pressable
      onPress={() => onPress(i)}
      style={({ pressed }) => [
        styles.pad,
        isSel && styles.sel,
        isMoving && styles.moving,
        pressed && styles.pressed,
      ]}
    >
      {macro && (
        <View style={[styles.swatch, { backgroundColor: hexFor(macro.colour) }]} />
      )}
      <Text style={styles.note}>{36 + i}</Text>
      {macro && (
        <Text style={styles.label} numberOfLines={3}>
          {macro.label}
        </Text>
      )}
    </Pressable>
  );
}

export default function PadGrid({ macros, sel, moving, onPress }) {
  return (
    <View style={styles.grid}>
      {ROWS.map((r) => (
        <View key={r} style={styles.row}>
          {Array.from({ length: 8 }, (_, c) => {
            const i = r * 8 + c;
            return (
              <Pad
                key={i}
                i={i}
                macro={macros[i]}
                isSel={sel === i}
                isMoving={moving === i}
                onPress={onPress}
              />
            );
          })}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  grid: {
    flex: 1,
    flexDirection: 'column',
  },
  row: {
    flex: 1,
    flexDirection: 'row',
  },
  pad: {
    flex: 1,
    aspectRatio: 1,
    minHeight: S.hit,
    margin: S.gap / 2,
    borderRadius: S.radius,
    backgroundColor: C.raised,
    borderWidth: 1,
    borderColor: C.line,
    padding: S.gap / 2,
    justifyContent: 'flex-end',
    overflow: 'hidden',
  },
  pressed: {
    backgroundColor: C.panel,
  },
  sel: {
    borderColor: C.accentText,
    borderWidth: 2,
  },
  // in your hand, about to land wherever you tap next -- needs to read as
  // clearly different from merely-selected, not just a brighter version of it
  moving: {
    borderColor: PAD_HEX[8],
    borderWidth: 2,
    borderStyle: 'dashed',
    backgroundColor: C.accent,
  },
  swatch: {
    position: 'absolute',
    left: 0,
    top: 0,
    bottom: 0,
    width: 4,
  },
  note: {
    position: 'absolute',
    top: 3,
    right: 5,
    color: C.faint,
    fontSize: 10,
    ...mono,
  },
  label: {
    color: C.text,
    fontSize: 11,
  },
});
