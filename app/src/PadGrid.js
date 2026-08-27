import { useEffect, useMemo, useRef, useState } from 'react';
import { PanResponder, Pressable, StyleSheet, Text, View } from 'react-native';
import PushButton from './PushButton';
import { C, PAD_HEX, SEAT_HEX, hexFor, S, mono } from './theme';

// note 36 is the Push's bottom-left pad. Row 0 (indices 0-7) is the bottom
// screen row here, and index 56-63 sit at the top -- build rows top to
// bottom on screen but count them r=7..0 so index math still reads i=r*8+c.
const ROWS = [7, 6, 5, 4, 3, 2, 1, 0];
const SIDE = 8;
const SLOP = 6; // past this, a press was a drag and not a tap
const DWELL = 400; // hover or tap has to mean it before the card appears
const GRACE = 400; // ... and you get this long to cross the gap into the card
const EXCERPT = 200;
const CARD_W = 300;
const LAP = 8; // the card reaches back over the pad, leaving no dead ground

function Pad({ i, macro, isSel, isMoving, isHeld, isOver, onHoverIn, onHoverOut }) {
  return (
    <Pressable
      onHoverIn={() => onHoverIn(i)}
      onHoverOut={() => onHoverOut(i)}
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
    </Pressable>
  );
}

// What a pad says and what it would send, before you find out the hard way.
function PadCard({ index, macro, box, onExecute, onEdit, onHoverIn }) {
  const cell = { w: box.width / SIDE, h: box.height / SIDE };
  const col = index % SIDE;
  const screenRow = SIDE - 1 - Math.floor(index / SIDE);
  const left = Math.max(
    0,
    Math.min(box.width - CARD_W, col * cell.w + cell.w / 2 - CARD_W / 2)
  );
  // below the pad, unless the pad is low enough that below is off the grid
  // the card reaches back over the pad's own cell: a strip of bare grid
  // between the two, however thin, is somewhere the pointer is in neither
  const under = screenRow < SIDE - 3;
  const place = under
    ? { top: (screenRow + 1) * cell.h - LAP }
    : { bottom: box.height - screenRow * cell.h - LAP };
  const text = (macro.text || '').trim();
  const excerpt =
    text.length > EXCERPT ? `${text.slice(0, EXCERPT).trimEnd()}…` : text;

  return (
    <Pressable
      onHoverIn={onHoverIn}
      style={[styles.card, { left, width: CARD_W }, place]}
    >
      <Text style={styles.cardTitle} numberOfLines={2}>
        {macro.label}
      </Text>
      {/* a pad whose label is its whole prompt would say it twice */}
      {excerpt !== macro.label && (
        <Text style={styles.cardText}>{excerpt}</Text>
      )}
      <View style={styles.cardRow}>
        <PushButton
          label={macro.submit ? 'run + enter' : 'run'}
          colour={SEAT_HEX.blocked}
          lit
          onPress={() => onExecute(index)}
          style={styles.cardKey}
        />
        <PushButton
          label="edit"
          colour={C.accentText}
          onPress={() => onEdit(index)}
          style={styles.cardKey}
        />
      </View>
    </Pressable>
  );
}

export default function PadGrid({
  macros,
  sel,
  moving,
  armed,
  onPress,
  onDrop,
  onExecute,
  onEdit,
}) {
  const [drag, setDrag] = useState(null);
  const [peek, setPeek] = useState(null);
  const peeked = useRef(null); // the same answer, readable from a stale closure
  const dwell = useRef(null);
  const leave = useRef(null);
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

  const show = (i) => {
    peeked.current = i;
    setPeek(i);
  };
  const shut = () => {
    clearTimeout(dwell.current);
    clearTimeout(leave.current);
    show(null);
  };
  useEffect(() => shut, []); // never fire a timer into an unmounted grid

  // hover and tap both mean "tell me about this pad", and both have to mean it
  // for a moment first -- a cursor crossing the grid is not a question.
  //
  // The pending close is cancelled first and for every pad, empty ones
  // included. The card sits against the pad it describes, so the way to its
  // own keys runs over whatever is next to it, and an empty pad that cancelled
  // nothing on the way past was enough to lose the card before you arrived.
  const openLater = (i) => {
    clearTimeout(dwell.current);
    clearTimeout(leave.current);
    if (armed || !macros[i]) {
      // an empty pad has nothing to say, but resting on one is still an answer
      leave.current = setTimeout(() => show(null), GRACE);
      return;
    }
    if (i === peeked.current) return;
    dwell.current = setTimeout(() => show(i), DWELL);
  };
  const closeLater = () => {
    clearTimeout(dwell.current);
    clearTimeout(leave.current);
    leave.current = setTimeout(() => show(null), GRACE);
  };
  const hold = () => {
    clearTimeout(leave.current);
  };

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponderCapture: () => true,
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (e) => {
          shut(); // a hand on the grid is not asking to read about a pad
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
          if (peeked.current === d.from) return shut(); // tapped again: away
          openLater(d.from);
        },
        onPanResponderTerminate: () => {
          live.current = null;
          setDrag(null);
        },
      }),
    [armed, macros, onDrop, onPress]
  );

  const dragging = drag && drag.moved;

  return (
    <View style={styles.wrap}>
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
                onHoverIn={openLater}
                onHoverOut={closeLater}
              />
            );
          })}
        </View>
      ))}
      </View>
      {peek !== null && macros[peek] && frame.current && (
        <PadCard
          index={peek}
          macro={macros[peek]}
          box={frame.current}
          onExecute={(i) => {
            shut();
            onExecute(i);
          }}
          onEdit={(i) => {
            shut();
            onEdit(i);
          }}
          onHoverIn={hold}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flex: 1,
  },
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
  card: {
    position: 'absolute',
    padding: S.pad,
    gap: 10,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.edge,
    backgroundColor: C.panel,
    shadowColor: '#000',
    shadowOpacity: 0.55,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 8 },
  },
  cardTitle: { color: C.text, fontSize: 14, fontWeight: '600' },
  cardText: { color: C.dim, fontSize: 12, lineHeight: 17 },
  cardRow: { flexDirection: 'row', gap: S.gap },
  cardKey: { flex: 1, height: S.control, minHeight: S.control, minWidth: 0 },
});
