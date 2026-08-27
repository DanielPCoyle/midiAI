import { useMemo, useRef, useState } from 'react';
import { PanResponder, StyleSheet, Text, View } from 'react-native';
import { C, PAD_HEX, hexFor, S, mono } from './theme';

// note 36 is the Push's bottom-left pad. Row 0 (indices 0-7) is the bottom
// screen row here, and index 56-63 sit at the top -- build rows top to
// bottom on screen but count them r=7..0 so index math still reads i=r*8+c.
const ROWS = [7, 6, 5, 4, 3, 2, 1, 0];
const SIDE = 8;
const SLOP = 6; // past this, a press was a drag and not a tap

function Pad({ i, macro, isSel, isMoving, isHeld, isOver }) {
  return (
    <View
      style={[
        styles.pad,
        isSel && styles.sel,
        isMoving && styles.moving,
        isOver && styles.over,
        isHeld && styles.held,
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
    </View>
  );
}

export default function PadGrid({ macros, sel, moving, onPress, onDrop }) {
  const [drag, setDrag] = useState(null);
  const live = useRef(null); // the same drag, readable inside the responder
  const frame = useRef(null);
  const grid = useRef(null);

  // One responder for the whole grid rather than sixty-four: the pads are a
  // regular 8x8, so where a finger is *is* which pad it is on, and a drag that
  // crosses pads never has to be handed from one child to the next.
  const cellAt = (pageX, pageY) => {
    const f = frame.current;
    if (!f || !f.width || !f.height) return null;
    const c = Math.floor(((pageX - f.x) / f.width) * SIDE);
    const r = Math.floor(((pageY - f.y) / f.height) * SIDE);
    if (c < 0 || c >= SIDE || r < 0 || r >= SIDE) return null;
    return (SIDE - 1 - r) * SIDE + c;
  };

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (e) => {
          const from = cellAt(e.nativeEvent.pageX, e.nativeEvent.pageY);
          live.current = { from, over: from, moved: false };
          setDrag(live.current);
        },
        onPanResponderMove: (e, g) => {
          const d = live.current;
          if (!d) return;
          const over = cellAt(e.nativeEvent.pageX, e.nativeEvent.pageY);
          const moved = d.moved || Math.hypot(g.dx, g.dy) > SLOP;
          if (over !== d.over || moved !== d.moved) {
            live.current = { ...d, over, moved };
            setDrag(live.current);
          }
        },
        onPanResponderRelease: () => {
          const d = live.current;
          live.current = null;
          setDrag(null);
          if (!d || d.from === null) return;
          // a drag that ends off the grid, or back where it started, is a
          // change of mind -- not a swap, and not a selection either
          if (d.moved) {
            if (d.over !== null && d.over !== d.from) onDrop(d.from, d.over);
            return;
          }
          onPress(d.from);
        },
        onPanResponderTerminate: () => {
          live.current = null;
          setDrag(null);
        },
      }),
    [onDrop, onPress]
  );

  const dragging = drag && drag.moved;

  return (
    <View
      ref={grid}
      style={styles.grid}
      onLayout={() =>
        grid.current?.measureInWindow((x, y, width, height) => {
          frame.current = { x, y, width, height };
        })
      }
      {...responder.panHandlers}
    >
      {ROWS.map((r) => (
        <View key={r} style={styles.row}>
          {Array.from({ length: SIDE }, (_, c) => {
            const i = r * SIDE + c;
            return (
              <Pad
                key={i}
                i={i}
                macro={macros[i]}
                isSel={sel === i}
                isMoving={moving === i || (dragging && drag.from === i)}
                isHeld={!!drag && !drag.moved && drag.from === i}
                isOver={!!dragging && drag.over === i && drag.over !== drag.from}
              />
            );
          })}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  // The pads used to carry `aspectRatio: 1` and a `minHeight`, which fought
  // the row flex and pushed the bottom row off the bottom of the screen. Rows
  // divide whatever height there is; a pad is whatever shape that leaves.
  grid: {
    flex: 1,
    flexDirection: 'column',
    userSelect: 'none', // or a drag across the grid drags a text selection
  },
  row: {
    flex: 1,
    flexDirection: 'row',
  },
  pad: {
    flex: 1,
    margin: S.gap / 2,
    borderRadius: S.radius,
    backgroundColor: C.raised,
    borderWidth: 1,
    borderColor: C.line,
    padding: S.gap / 2,
    justifyContent: 'flex-end',
    overflow: 'hidden',
  },
  held: {
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
  // where it would land if you let go now
  over: {
    borderColor: PAD_HEX[8],
    borderWidth: 2,
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
