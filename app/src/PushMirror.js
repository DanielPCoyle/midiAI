import { Image, StyleSheet, Text, View } from 'react-native';
import PushButton from './PushButton';
import { C, S, SEAT_HEX } from './theme';

const SLOTS = 8;
const rgb = ([r, g, b]) => `rgb(${r}, ${g}, ${b})`;

// display.py centres the view-strip text on a band of `bands[0]` px with a
// font a shade taller than the band, so descenders land 2px below it. On the
// Push that is just the label; here it is a row of clipped glyph tails
// hanging under the buttons that replace them.
const BLEED = 2;

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
          return <PushButton key={i} label="" disabled style={styles.slot} />;
        }
        const mode = modes[i];
        const label = mode > 1 ? `${views[i]} ${(at[i] ?? 0) + 1}/${mode}` : views[i];
        return (
          <PushButton
            key={i}
            label={label}
            colour={colours[i] ? rgb(colours[i]) : C.accentText}
            lit={i === active}
            onPress={() => onTab(i)}
            style={styles.slot}
          />
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
          return <PushButton key={i} label={String(i + 1)} disabled style={styles.slot} />;
        }
        const [name, status] = seat;
        return (
          <PushButton
            key={i}
            label={name}
            colour={SEAT_HEX[status] || C.faint}
            lit={i === current}
            onPress={() => onSeat(i)}
            style={styles.slot}
          />
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
  const [band, bottom] = surface.bands;
  const top = band + BLEED;
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
