import { useCallback, useRef, useState } from 'react';
import { Image, PanResponder, ScrollView, StyleSheet, Text, View } from 'react-native';
import PushButton from './PushButton';
import { C, KEY, S, SEAT_HEX, hexFor } from './theme';

const SLOTS = 8;
const rgb = ([r, g, b]) => `rgb(${r}, ${g}, ${b})`;

// display.py centres the view-strip text on a band of `bands[0]` px with a
// font a shade taller than the band, so descenders land 2px below it. On the
// Push that is just the label; here it is a row of clipped glyph tails
// hanging under the buttons that replace them.
const BLEED = 2;

// The rest of push_cc.py's ~54 mapped controls (its constants section, lines
// roughly 74-200) as plain numbers -- there is no import between Python and
// this bundle, so if a CC ever moves there, it has to move here too. Grouped
// the way the docstring groups them, not the way the layout below groups
// them, so a diff against push_cc.py stays easy to eyeball.
const PLAY_CC = 85; // enter, submits what is typed
const RECORD_CC = 86; // hold, then tap a pad: saves the prompt onto it
const AUTOMATE_CC = 89; // latch: tap, then tap a pad, to arm that row
const DUPLICATE_CC = 88; // fork the current agent into a new session
const STOP_CC = 29; // escape, interrupts the agent
const UNDO_CC = 119; // backspace the prompt empty
const DELETE_CC = 118; // close the current agent's pane
const MUTE_CC = 60; // tab, accepts an autocomplete
const SOLO_CC = 61; // pin: neither questions nor the terminal move it
const SHIFT_CC = 49; // held modifier, the standard Push idiom
const SELECT_CC = 48; // tap, then tap two pads, to swap them on the grid
const ADD_DEVICE_CC = 52; // split, new claude in auto mode
const ADD_TRACK_CC = 53; // new worktree, named from what is typed
const BROWSE_CC = 111; // this repo's pull requests, in a browser
const CONVERT_CC = 35; // /compact
const NEW_CC = 87; // /clear
const QUANTIZE_CC = 116; // /model
const DBLOOP_CC = 117; // /effort
const METRO_CC = 9; // /mcp
const LEFT_CC = 44, RIGHT_CC = 45, UP_CC = 46, DOWN_CC = 47; // arrow keys, straight through
const SCROLL_UP_CC = 55, SCROLL_DOWN_CC = 54; // octave up/down: page the focus view
const TEMPO_CC = 14; // scrolls the focus view
const VOLUME_CC = 79; // master: up/down arrows at the current agent
const ENC_CCS = [71, 72, 73, 74, 75, 76, 77, 78]; // one per agent column

// mido pitchwheel runs -8192..8191, same span push_cc.py's effort_at reads.
const PITCH_SPAN = 8192 * 2;

// A tap is one POST: push_cc only ever looks for the down-edge (`if
// msg.value`) on a plain button, so the wire contract's default value of 127
// is a whole press by itself and there is nothing to release. `press` is
// App.js's own helper and already swallows a failed POST into a toast rather
// than throwing -- against an old, four-verb backend this CC simply means
// nothing to it, and the tap quietly does nothing, which is the degrade the
// ticket asks for.
const tapCC = (press, cc) => () => press({ cc });

// Record, Automate and Shift are the three controls that need a gesture
// rather than a click: PushButton already carries onPressIn/onPressOut for
// hold-to-talk, and value 127 down / 0 up is how the wire contract spells a
// real hold -- which Record and Shift are, and Automate and Select are not.
// Those two toggle on the press and ignore the release, so they are taps
// here. The handler never changed: the synthetic messages run the same chain
// a physical press does, so the mirror has to model each control the way the
// hardware actually behaves rather than the way the surface table reads.
function useHold(press, cc) {
  const [held, setHeld] = useState(false);
  const onPressIn = useCallback(() => {
    setHeld(true);
    press({ cc, value: 127 });
  }, [press, cc]);
  const onPressOut = useCallback(() => {
    setHeld(false);
    press({ cc, value: 0 });
  }, [press, cc]);
  return { held, onPressIn, onPressOut };
}

// Keeps a PanResponder's closures off whatever `press` happened to be when
// the responder was built -- PanResponder.create runs once, in a useRef, so
// a handler that closed over its first `onTurn`/`emit` would keep firing
// against a stale `base` forever if the prop identity ever changed.
function useLatest(value) {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}

export default function PushMirror({ base, surface, macros, onTab, onSeat, press }) {
  const views = Array.isArray(surface?.views) ? surface.views : null;
  const hasSurface = !!views;
  // No caller has forgotten to wire `press` yet, but a mirror that throws
  // because one did would be a worse failure than a control that quietly
  // does nothing -- the same standard the wire contract itself asks for.
  const send = press || (() => {});

  // ~54 controls do not fit a fixed viewport height the way the original
  // four rows did -- that version's PadRows was `flex:1`, expanding or
  // shrinking to soak up whatever room was left. With this many more rows
  // above it there may be nothing left to soak up at 820px tall, and a pad
  // grid squeezed to zero height is clipped in exactly the sense the ticket
  // rules out. So the pad grid goes back to sizing itself (aspect-ratio
  // squares, see `pad` below) and the whole device scrolls vertically
  // instead -- everything stays legible at its natural size, and a short
  // window trades a scrollbar for what would otherwise be missing rows.
  // Nothing about the horizontal layout changes: it already fits three
  // widths without help, because every row keeps the same 8-column split
  // TabRow and SeatRow always used.
  return (
    <ScrollView style={styles.wrap} contentContainerStyle={styles.wrapContent}>
      <EncoderRow press={send} />
      <TabRow surface={surface} views={views} onTab={onTab} />
      <Frame base={base} surface={hasSurface ? surface : null} />
      <View style={styles.seatBlock}>
        <LeftColumn press={send} />
        <SeatRow surface={surface} onSeat={onSeat} />
      </View>
      <SlashRow press={send} />
      <TransportRow press={send} />
      <NavRow press={send} />
      <BigTransport press={send} />
      <View style={styles.padArea}>
        <TouchStrip press={send} />
        <PadRows macros={macros || []} />
      </View>
    </ScrollView>
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

// Under the display, unchanged from before this ticket -- but no longer the
// full width of the row on its own. The left-hand device/track column sits
// beside it now, so this needs to claim the width that column doesn't, hence
// `styles.seatRowFill` rather than the plain `styles.row` every other row
// uses: it is the only row nested inside another row instead of sitting
// straight in the wrap's own column flow, and Yoga only stretches a
// non-flex child along a cross axis, never a main one.
function SeatRow({ surface, onSeat }) {
  const seats = surface?.seats || [];
  const current = surface?.current;

  return (
    <View style={[styles.row, styles.seatRowFill]}>
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

// Add Device, Add Track and Browse: on the hardware these sit to the left of
// the under-display row rather than in line with it, so they get their own
// column here too instead of being folded into an 8-wide row that has no
// room for a ninth thing. Taller than the row it stands beside on purpose --
// `seatBlock` below opts out of the default cross-axis stretch so this can
// be its own natural height without forcing every seat button to match it.
function LeftColumn({ press }) {
  return (
    <View style={styles.leftColumn}>
      <PushButton
        label="Browse"
        accessibilityLabel="Browse — this repo's pull requests, in a browser"
        colour={C.accentText}
        onPress={tapCC(press, BROWSE_CC)}
      />
      <PushButton
        label="Add Device"
        accessibilityLabel="Add Device — split, new claude in auto mode"
        colour={C.accentText}
        onPress={tapCC(press, ADD_DEVICE_CC)}
      />
      <PushButton
        label="Add Track"
        accessibilityLabel="Add Track — new worktree, named from what is typed"
        colour={C.accentText}
        onPress={tapCC(press, ADD_TRACK_CC)}
      />
    </View>
  );
}

// Convert, New, Quantize, Double Loop, Metronome -- five buttons that each
// type a fixed slash command and, bar Convert, a newline that submits it.
function SlashRow({ press }) {
  const cmds = [
    ['Convert', '/compact', CONVERT_CC],
    ['New', '/clear', NEW_CC],
    ['Quantize', '/model', QUANTIZE_CC],
    ['Double Loop', '/effort', DBLOOP_CC],
    ['Metronome', '/mcp', METRO_CC],
  ];
  return (
    <View style={styles.row}>
      {cmds.map(([label, cmd, cc]) => (
        <PushButton
          key={cc}
          label={label}
          accessibilityLabel={`${label} — ${cmd}`}
          colour={C.accentText}
          style={styles.slot}
          onPress={tapCC(press, cc)}
        />
      ))}
    </View>
  );
}

// The rest of the transport/edit cluster, eight across like every other row
// here: Shift is the one hold in this row (Record and Automate get their own
// row below, sized to match how much bigger they are on the hardware).
function TransportRow({ press }) {
  const shift = useHold(press, SHIFT_CC);
  return (
    <View style={styles.row}>
      <PushButton
        label="Shift"
        accessibilityLabel="Shift — held modifier; hold and tap a pad to read what it does without running it"
        colour={C.accentText}
        lit={shift.held}
        style={styles.slot}
        onPressIn={shift.onPressIn}
        onPressOut={shift.onPressOut}
      />
      <PushButton
        label="Select"
        accessibilityLabel="Select — tap, then tap two pads, to swap them on the grid"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, SELECT_CC)}
      />
      <PushButton
        label="Mute"
        accessibilityLabel="Mute — tab, accepts an autocomplete"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, MUTE_CC)}
      />
      <PushButton
        label="Solo"
        accessibilityLabel="Solo — pin the screen"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, SOLO_CC)}
      />
      <PushButton
        label="Undo"
        accessibilityLabel="Undo — backspace the prompt empty"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, UNDO_CC)}
      />
      <PushButton
        label="Delete"
        accessibilityLabel="Delete — close the current agent's pane"
        colour={C.bad}
        style={styles.slot}
        onPress={tapCC(press, DELETE_CC)}
      />
      <PushButton
        label="Duplicate"
        accessibilityLabel="Duplicate — fork the current agent into a new session"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, DUPLICATE_CC)}
      />
      <PushButton
        label="Stop Clip"
        accessibilityLabel="Stop Clip — escape, interrupts the agent"
        colour={C.bad}
        style={styles.slot}
        onPress={tapCC(press, STOP_CC)}
      />
    </View>
  );
}

// Page left/right use `page`, the old four-verb vocabulary, not `cc` -- the
// ticket calls this out on purpose: it is the one control here that works
// against push_cc.py exactly as it stands today, which is the end-to-end
// proof that the wiring above the network call is right regardless of how
// far the parallel worker has got. Arrows and octave up/down are genuinely
// new (`cc`) and wait on that work the way every other new control does.
function NavRow({ press }) {
  return (
    <View style={styles.row}>
      <PushButton
        label="‹"
        accessibilityLabel="Page left — previous pad page"
        colour={C.accentText}
        style={styles.slot}
        onPress={() => press({ page: -1 })}
      />
      <PushButton
        label="←"
        accessibilityLabel="Left arrow — straight through to the agent"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, LEFT_CC)}
      />
      <PushButton
        label="↑"
        accessibilityLabel="Up arrow — straight through to the agent"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, UP_CC)}
      />
      <PushButton
        label="↓"
        accessibilityLabel="Down arrow — straight through to the agent"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, DOWN_CC)}
      />
      <PushButton
        label="→"
        accessibilityLabel="Right arrow — straight through to the agent"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, RIGHT_CC)}
      />
      <PushButton
        label="›"
        accessibilityLabel="Page right — next pad page"
        colour={C.accentText}
        style={styles.slot}
        onPress={() => press({ page: 1 })}
      />
      <PushButton
        label="▲"
        accessibilityLabel="Octave up — pages the focus view back through history"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, SCROLL_UP_CC)}
      />
      <PushButton
        label="▼"
        accessibilityLabel="Octave down — pages the focus view forward"
        colour={C.accentText}
        style={styles.slot}
        onPress={tapCC(press, SCROLL_DOWN_CC)}
      />
    </View>
  );
}

// Record, Automate and Play, bigger than everything above them the way they
// are on the actual device -- three keys splitting the row rather than eight.
function BigTransport({ press }) {
  const record = useHold(press, RECORD_CC);
  return (
    <View style={styles.row}>
      <PushButton
        label="Record"
        accessibilityLabel="Record — hold, then tap a pad, to save the prompt onto it"
        colour={C.bad}
        lit={record.held}
        style={styles.slot}
        onPressIn={record.onPressIn}
        onPressOut={record.onPressOut}
      />
      {/* A latch on the hardware, not a hold: push_cc toggles `automating`
          on the press and ignores the release, and its comment says why --
          picking a row to run does not want deliberation the way overwriting
          a pad does, and a two-handed hold does not land on a surface played
          one-handed. Record beside it really is a hold, which is why they
          look alike here and behave differently. Nothing in the payload says
          whether the latch is currently on, so this does not pretend to
          know. */}
      <PushButton
        label="Automate"
        accessibilityLabel="Automate — tap, then tap a pad, to arm that row as a chain"
        colour={C.warn}
        style={styles.slot}
        onPress={tapCC(press, AUTOMATE_CC)}
      />
      <PushButton
        label="Play"
        accessibilityLabel="Play — enter, submits what is typed"
        colour={C.good}
        style={styles.slot}
        onPress={tapCC(press, PLAY_CC)}
      />
    </View>
  );
}

// Encoders are relative, never absolute (push_cc.turn(), ~line 1766): 1..63
// is a clockwise step, 127..65 anticlockwise, and there is no such thing as
// an encoder's "value" at rest. A drag has no clockwise-ness of its own
// either, so this treats every TICK_PX of vertical movement as one step and
// calls "up" the positive direction -- the same sense a mouse wheel's
// forward notch already uses, which is the nearest thing a person dragging
// on a screen has felt before.
const TICK_PX = 12;

function useEncoderResponder(onTurn) {
  const cb = useLatest(onTurn);
  const last = useRef(0);
  return useRef(
    PanResponder.create({
      onStartShouldSetPanResponderCapture: () => true,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: () => {
        last.current = 0;
      },
      onPanResponderMove: (_e, g) => {
        const moved = -g.dy;
        while (moved - last.current >= TICK_PX) {
          cb.current(1);
          last.current += TICK_PX;
        }
        while (moved - last.current <= -TICK_PX) {
          cb.current(-1);
          last.current -= TICK_PX;
        }
      },
    })
  ).current;
}

function Encoder({ label, accessibilityLabel, press, cc, style }) {
  const turn = useCallback((dir) => press({ cc, value: dir > 0 ? 1 : 127 }), [press, cc]);
  const responder = useEncoderResponder(turn);
  return (
    <View
      accessibilityRole="adjustable"
      accessibilityLabel={accessibilityLabel}
      style={[styles.knob, style]}
      {...responder.panHandlers}>
      <Text style={styles.knobLabel}>{label}</Text>
    </View>
  );
}

// Tempo and Master flank the eight assignable encoders the same way they
// flank them on the hardware -- narrower, because the knob itself is
// smaller, not because either matters less.
function EncoderRow({ press }) {
  return (
    <View style={styles.row}>
      <Encoder
        label="Tempo"
        accessibilityLabel="Tempo encoder — scrolls the focus view"
        press={press}
        cc={TEMPO_CC}
        style={styles.knobSide}
      />
      {ENC_CCS.map((cc, i) => (
        <Encoder
          key={cc}
          label={String(i + 1)}
          accessibilityLabel={`Encoder ${i + 1} — scrolls the agent above it`}
          press={press}
          cc={cc}
          style={styles.slot}
        />
      ))}
      <Encoder
        label="Master"
        accessibilityLabel="Master encoder — up and down arrows at the current agent"
        press={press}
        cc={VOLUME_CC}
        style={styles.knobSide}
      />
    </View>
  );
}

// The one absolute control on the surface: a vertical drag sets reasoning
// effort directly from finger position, top full and bottom empty, the same
// way effort_at reads the hardware strip's pitch bend. getBoundingClientRect
// is read fresh on every grant rather than cached from onLayout, same reason
// PadGrid.js does it that way -- a strip that has scrolled since its last
// layout would otherwise read a stale rectangle.
// A `pitch` POST isn't a raw pitchwheel frame on this wire -- the backend
// brackets each one with its own touch note_on/note_off, a whole touch and
// release rather than an incremental nudge (confirmed against the live
// stack). onPanResponderMove fires far more often than that gesture is
// meant to repeat, so this only actually sends once the strip position has
// moved a visible amount, rather than once a pixel.
const STRIP_STEP = 0.01;

function TouchStrip({ press }) {
  const ref = useRef(null);
  const frame = useRef({ top: 0, height: 1 });
  const [level, setLevel] = useState(0.5);
  const sent = useRef(null);

  const emit = (pageY, force) => {
    const { top, height } = frame.current;
    const frac = Math.min(1, Math.max(0, 1 - (pageY - top) / height));
    setLevel(frac);
    if (!force && sent.current !== null && Math.abs(frac - sent.current) < STRIP_STEP) return;
    sent.current = frac;
    const pitch = Math.round(frac * PITCH_SPAN - PITCH_SPAN / 2);
    press({ pitch: Math.min(8191, Math.max(-8192, pitch)) });
  };
  const emitRef = useLatest(emit);

  const responder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponderCapture: () => true,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: (e) => {
        if (ref.current && ref.current.getBoundingClientRect) {
          const r = ref.current.getBoundingClientRect();
          frame.current = { top: r.top, height: r.height };
        }
        sent.current = null;
        emitRef.current(e.nativeEvent.pageY, true);
      },
      onPanResponderMove: (e) => emitRef.current(e.nativeEvent.pageY, false),
    })
  ).current;

  return (
    <View
      ref={ref}
      accessibilityRole="adjustable"
      accessibilityLabel="Touchstrip — drag to set reasoning effort, bottom low and top max"
      style={styles.strip}
      {...responder.panHandlers}>
      <View style={[styles.stripFill, { height: `${level * 100}%` }]} />
    </View>
  );
}

// note 36 is the bottom-left pad, so the top screen row is the highest indices
const ROWS = [7, 6, 5, 4, 3, 2, 1, 0];

// The grid shares one 8-column track with the button rows above and below it,
// so a pad sits under the button that governs its column. Centring it on its
// own width made a tidier square and a worse mirror -- alignment is the only
// thing this view owes you. The labels are the app's own addition: on the glass
// a pad is light alone, and light alone is not readable across a desk.
function PadRows({ macros }) {
  return (
    <View style={styles.pads}>
      {ROWS.map((r) => (
        <View key={r} style={styles.padRow}>
          {Array.from({ length: SLOTS }, (_, c) => {
            const i = r * SLOTS + c;
            const m = macros[i];
            return (
              <View
                key={c}
                style={[
                  styles.pad,
                  m ? { backgroundColor: hexFor(m.colour) } : styles.padOff,
                ]}>
                {!!m && (
                  <Text style={styles.padText} numberOfLines={2}>
                    {m.label}
                  </Text>
                )}
              </View>
            );
          })}
        </View>
      ))}
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
    flex: 1,
  },
  wrapContent: {
    gap: S.gap,
    paddingBottom: S.gap,
  },
  row: {
    flexDirection: 'row',
    gap: S.gap / 2,
  },
  slot: {
    flex: 1,
  },
  // Only SeatRow nests inside another row (seatBlock, for the left column)
  // rather than sitting straight in wrap's own column flow -- see the
  // comment on SeatRow for why that needs flex:1 where styles.row alone
  // would collapse it.
  seatRowFill: {
    flex: 1,
  },
  seatBlock: {
    flexDirection: 'row',
    gap: S.gap / 2,
    // Default stretch would force SeatRow's own row to match LeftColumn's
    // taller, three-button height -- and stretching *that* row would in turn
    // stretch its eight buttons into something much taller than every other
    // row on this surface. flex-start lets each side keep its own height.
    alignItems: 'flex-start',
  },
  leftColumn: {
    width: 92,
    gap: S.gap / 2,
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
  knob: {
    flex: 1,
    height: 34,
    borderRadius: 6,
    backgroundColor: KEY.face,
    borderWidth: 1,
    borderColor: KEY.edge,
    borderTopColor: KEY.top,
    alignItems: 'center',
    justifyContent: 'center',
  },
  knobSide: {
    flex: 0.6,
  },
  knobLabel: {
    color: C.dim,
    fontSize: 10,
    fontWeight: '500',
  },
  padArea: {
    flexDirection: 'row',
    gap: S.gap / 2,
  },
  strip: {
    width: 28,
    borderRadius: 14,
    backgroundColor: KEY.face,
    borderWidth: 1,
    borderColor: KEY.edge,
    borderTopColor: KEY.top,
    overflow: 'hidden',
    justifyContent: 'flex-end',
  },
  stripFill: {
    width: '100%',
    backgroundColor: C.accentText,
    borderRadius: 14,
  },
  // Still `flex: 1`, but only doing half the job it used to: inside
  // `padArea`'s row, that's what claims the width TouchStrip's fixed 28px
  // doesn't -- same reason SeatRow needs `seatRowFill`. It no longer governs
  // height (see the comment on the ScrollView above); a pad is a square now,
  // sized off its own width, so the grid's total height is just eight of
  // those plus the row gaps, whatever that comes to.
  pads: { flex: 1, gap: S.gap / 2 },
  padRow: { flexDirection: 'row', gap: S.gap / 2 },
  pad: {
    flex: 1,
    aspectRatio: 1,
    borderRadius: 5,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 4,
    overflow: 'hidden',
  },
  padOff: { backgroundColor: '#141418' },
  padText: {
    color: C.bg,
    fontSize: 10,
    fontWeight: '600',
    textAlign: 'center',
  },
});
