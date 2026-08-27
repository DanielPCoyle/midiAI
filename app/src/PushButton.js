import { Pressable, StyleSheet, Text, View } from 'react-native';
import { C, KEY, S } from './theme';

// A Push 2 button that is not a pad. On the hardware these are matte, almost
// black, and the only bright thing on one is a thin LED bar low on its face --
// dark when the button is off, glowing when it is on. The rows above and below
// the display are exactly this, so the app's own copies of them are too.
//
// The label is ours, not the Push's: the real buttons are blank and the screen
// above them says what they do, and this app crops that band away to put the
// buttons in its place.
export default function PushButton({
  label,
  colour = C.faint,
  lit = false,
  disabled = false,
  onPress,
  style,
  children,
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.key,
        lit && styles.keyLit,
        disabled && styles.disabled,
        pressed && styles.pressed,
        style,
      ]}>
      {children || (
        <Text style={[styles.label, { color: lit ? C.text : C.dim }]} numberOfLines={1}>
          {label}
        </Text>
      )}
      <View
        style={[
          styles.led,
          { backgroundColor: colour, opacity: lit ? 1 : 0.22 },
          lit && { shadowColor: colour, shadowOpacity: 0.9, shadowRadius: 8 },
        ]}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  key: {
    minHeight: S.hit,
    borderRadius: 6,
    backgroundColor: KEY.face,
    borderWidth: 1,
    borderColor: KEY.edge,
    borderTopColor: KEY.top, // the rim, catching light the way the moulding does
    paddingHorizontal: 10,
    paddingBottom: 8, // the LED bar's lane
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  keyLit: {
    backgroundColor: KEY.lit,
  },
  disabled: {
    opacity: 0.4,
  },
  pressed: {
    opacity: 0.7,
  },
  label: {
    fontSize: 12,
    fontWeight: '500',
    letterSpacing: 0.2,
  },
  led: {
    position: 'absolute',
    bottom: 6,
    width: '44%',
    height: 2.5,
    borderRadius: 2,
    shadowOffset: { width: 0, height: 0 },
  },
});
