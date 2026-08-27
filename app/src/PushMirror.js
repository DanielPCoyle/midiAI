import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { C, S, SEAT_HEX } from './theme';

const SLOTS = 8;
const rgb = ([r, g, b]) => `rgb(${r}, ${g}, ${b})`;

export default function PushMirror({ base, surface, onTab, onSeat }) {
  const views = Array.isArray(surface?.views) ? surface.views : null;
  const hasSurface = !!views;

  return (
    <View style={styles.wrap}>
      <TabRow surface={surface} views={views} onTab={onTab} />
      <Frame base={base} surface={hasSurface ? surface : null} />
      <SeatRow surface={surface} onSeat={onSeat} />
    </View>
  );
}

function TabRow({ surface, views, onTab }) {
  const modes = surface?.modes || [];
  const at = surface?.at || [];
  const colours = surface?.colours || [];
  const active = surface?.view;

  return (
    <View style={styles.row}>
      {Array.from({ length: SLOTS }, (_, i) => {
        if (!views || i >= views.length) {
          return <View key={i} style={styles.slotEmpty} />;
        }
        const isActive = i === active;
        const mode = modes[i];
        const label = mode > 1 ? `${views[i]} ${(at[i] ?? 0) + 1}/${mode}` : views[i];
        const tint = colours[i] ? rgb(colours[i]) : C.accentText;
        return (
          <Pressable
            key={i}
            onPress={() => onTab(i)}
            style={({ pressed }) => [
              styles.slot,
              { borderColor: isActive ? tint : 'transparent' },
              isActive ? styles.slotActive : { opacity: 0.55 },
              pressed && styles.pressed,
            ]}
          >
            <Text style={[styles.slotText, { color: tint }]} numberOfLines={1}>
              {label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function SeatRow({ surface, onSeat }) {
  const seats = surface?.seats || [];
  const current = surface?.current;

  return (
    <View style={styles.row}>
      {Array.from({ length: SLOTS }, (_, i) => {
        const seat = seats[i] || null;
        if (!seat) {
          return (
            <View key={i} style={styles.slotEmpty}>
              <Text style={styles.slotNumber}>{i + 1}</Text>
            </View>
          );
        }
        const [name, status] = seat;
        const isActive = i === current;
        const tint = SEAT_HEX[status] || C.faint;
        return (
          <Pressable
            key={i}
            onPress={() => onSeat(i)}
            style={({ pressed }) => [
              styles.slot,
              { borderColor: isActive ? tint : 'transparent' },
              isActive ? styles.slotActive : { opacity: 0.55 },
              pressed && styles.pressed,
            ]}
          >
            <Text style={[styles.slotText, { color: tint }]} numberOfLines={1}>
              {name}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function Frame({ base, surface }) {
  if (!surface) {
    return (
      <View style={[styles.frame, styles.frameEmpty]}>
        <Text style={styles.waiting}>waiting for push_cc</Text>
      </View>
    );
  }

  const [w, h] = surface.size;
  const [top, bottom] = surface.bands;
  const visible = h - top - bottom;

  return (
    <View style={[styles.frame, { aspectRatio: w / visible }]}>
      {/* display.py draws its own view/seat labels into the top and bottom
          `bands` of the PNG -- this component draws those same labels itself
          as real buttons, so the image is shifted up by `top` and clipped to
          `visible` to cut the baked-in bands out from under them. */}
      <Image
        source={{ uri: `${base}/frame.png?t=${surface.stamp}` }}
        style={{ width: '100%', aspectRatio: w / h, marginTop: `${(-top / w) * 100}%` }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    gap: S.gap,
  },
  row: {
    flexDirection: 'row',
    gap: S.gap / 2,
  },
  slot: {
    flex: 1,
    minHeight: 34,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: S.radius,
    borderWidth: 1.5,
    backgroundColor: C.panel,
    paddingHorizontal: 4,
  },
  slotActive: {
    backgroundColor: C.raised,
  },
  slotEmpty: {
    flex: 1,
    minHeight: 34,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: S.radius,
    backgroundColor: C.panel,
    opacity: 0.25,
  },
  slotNumber: {
    color: C.faint,
    fontSize: 12,
  },
  slotText: {
    fontSize: 12,
    fontWeight: '600',
  },
  pressed: {
    opacity: 0.7,
  },
  frame: {
    width: '100%',
    overflow: 'hidden',
    borderRadius: S.radius,
    backgroundColor: '#000',
  },
  frameEmpty: {
    // no surface yet, so there's no size/bands to derive a real ratio from --
    // just a wide thin placeholder standing in for the display frame's shape.
    aspectRatio: 6,
    justifyContent: 'center',
    alignItems: 'center',
  },
  waiting: {
    color: C.dim,
    fontSize: 13,
  },
});
