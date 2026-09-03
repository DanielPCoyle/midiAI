import { useCallback, useRef, useState } from 'react';
import { Image, PanResponder, StyleSheet, Text, View } from 'react-native';
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

// The physical map, sourced rather than remembered: Ableton's own
// "Ableton/push-interface" repo (github.com/Ableton/push-interface,
// doc/AbletonPush2MIDIDisplayInterface.asc + doc/MidiMapping.png) is the
// public MIDI and Display Interface Manual the ticket points at, and its
// diagram lays out every control at its real position. Read top to bottom:
//
//   - encoders (Tempo, 8 assignable, Master) across the very top
//   - Metronome sits at the left end of the row of 8 view buttons above the
//     display; Setup/User (unmapped here) sit at the right end
//   - Delete/Undo stack at the display's left edge; Add Device/Add Track
//     stack at its right edge, with Browse (of an unmapped 2x2 with Device/
//     Mix/Clip) beside them -- collapsed to one 3-tall column here rather
//     than a stack-plus-grid, noted as an approximation
//   - Mute/Solo/Stop Clip sit left of the 8 under-display buttons; the
//     Left/Up/Down/Right diamond sits to their right, past an unmapped
//     Master button
//   - left of the touchstrip, top to bottom: Convert/Double Loop/Quantize,
//     then Duplicate/New, then Automate/Record (bigger cells, RGB LEDs),
//     then Play alone at the very bottom -- exactly this grouping and order
//   - right of the pad grid, an unmapped column (Repeat/Accent/Scale/
//     Layout/Note/Session) before the Octave/Page diamond and, below that,
//     Shift/Select in the bottom-right corner
//
// Everything above is a direct read of the manual. The one genuine guess is
// collapsing Add Device/Add Track/Browse into a single column instead of a
// stack beside a 2x2 grid -- see the comment on DisplayRight below.
// A pad grid this wide is also, near enough, this tall (PadRows' height is
// an identity of its own width -- 8 square cells plus 7 gaps sums back to
// the width they were cut from), and that single fact drives the whole
// canvas towards square unless the rails either side of it carry real
// width. The manual's own device outline (its drawn border, not the legend
// or the encoder labels hanging above it) measures 4950x3974px --
// 1.25:1, matching the ticket's anchor -- with the rails either side of the
// pad grid at roughly 30% (left) and 35% (right) of the grid's own width.
// RAIL_L/RAIL_R below lean on that ratio, widened a little further to buy
// back what the fixed-height rows above the pads (encoders, the display)
// don't otherwise contribute, so the mirror lands wider than tall the way
// the hardware does rather than the square-ish result a narrower rail
// produces.
const RAIL_L = 280; // the left-edge column's width, in canvas px (see CANVAS_W)
const RAIL_R = 340; // the right-edge column's width -- wider, it carries a diamond
const STRIP_W = 36; // the touchstrip, immediately left of the pad grid
const DIAMOND_SIZE = 120; // the 4-way diamonds' own size, independent of their rail's width
const CANVAS_W = 1400; // the whole mirror is authored at this width and scaled to fit
// The pad row has a 4th column (the touchstrip) the rows above and below it
// don't -- so its left rail has to give up exactly the width the strip and
// its own gap take, or the pad grid stops lining up under the button rows
// above it. Derived, not picked, so the two stay in sync if RAIL_L ever
// moves.
const PAD_RAIL_L = RAIL_L - STRIP_W - S.gap / 2;

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

// The mirror is authored once at CANVAS_W and an intrinsic height (the
// display's real aspect ratio and the pad grid's square cells decide that,
// not a number picked here), then the whole thing is scaled uniformly to
// whatever room the pane actually has -- width or height, whichever binds
// first -- the same way a photo fits inside a frame. A CSS transform scales
// text, LEDs and hit targets together, so nothing about "small buttons stay
// small" needs separate handling: the diamond keys read tiny at 820px
// because the same diamond keys are tiny on the hardware.
//
// Two measurements, not one: `box` is how much room the surrounding pane
// gives this component (its own onLayout), and `native` is how tall the
// authored-at-CANVAS_W content turns out to be (the canvas's own onLayout).
// RN computes layout before it applies a transform, so reading the canvas's
// size while a scale transform is already applied still returns the
// unscaled, native number -- there is no chicken-and-egg here.
function useFit() {
  const [box, setBox] = useState({ w: CANVAS_W, h: CANVAS_W / 1.25 });
  const [native, setNative] = useState({ w: CANVAS_W, h: CANVAS_W / 1.25 });
  const onBoxLayout = useCallback((e) => {
    const { width, height } = e.nativeEvent.layout;
    if (width > 0 && height > 0) setBox({ w: width, h: height });
  }, []);
  const onCanvasLayout = useCallback((e) => {
    const { width, height } = e.nativeEvent.layout;
    if (width > 0 && height > 0) setNative({ w: width, h: height });
  }, []);
  const scale = Math.min(box.w / native.w, box.h / native.h) || 1;
  return { onBoxLayout, onCanvasLayout, scale };
}

export default function PushMirror({ base, surface, macros, onTab, onSeat, press }) {
  const views = Array.isArray(surface?.views) ? surface.views : null;
  const hasSurface = !!views;
  // No caller has forgotten to wire `press` yet, but a mirror that throws
  // because one did would be a worse failure than a control that quietly
  // does nothing -- the same standard the wire contract itself asks for.
  const send = press || (() => {});
  const { onBoxLayout, onCanvasLayout, scale } = useFit();

  return (
    <View style={styles.wrap} onLayout={onBoxLayout}>
      <View
        onLayout={onCanvasLayout}
        style={[styles.canvas, { width: CANVAS_W, transform: [{ scale }] }]}>
        <EncoderRow press={send} />
        <AboveDisplayRow surface={surface} views={views} onTab={onTab} press={send} />
        <DisplayRow base={base} surface={hasSurface ? surface : null} press={send} />
        <UnderDisplayRow surface={surface} onSeat={onSeat} press={send} />
        <PadArea macros={macros || []} press={send} />
      </View>
    </View>
  );
}

// Tempo and Master flank the eight assignable encoders the same way they
// flank them on the hardware -- and now the same way RAIL_L/RAIL_R flank
// every row below, so the encoder row's ends line up with the columns
// beside the display, the under-display row and the pads.
function EncoderRow({ press }) {
  return (
    <View style={styles.railRow}>
      <Encoder
        label="Tempo"
        accessibilityLabel="Tempo encoder — scrolls the focus view"
        press={press}
        cc={TEMPO_CC}
        style={{ width: RAIL_L }}
      />
      <View style={[styles.row, styles.mid]}>
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
      </View>
      <Encoder
        label="Master"
        accessibilityLabel="Master encoder — up and down arrows at the current agent"
        press={press}
        cc={VOLUME_CC}
        style={{ width: RAIL_R }}
      />
    </View>
  );
}

// The row of 8 view buttons above the display, Metronome at its left end
// exactly where the manual puts it -- the far right (Setup/User) is
// unmapped, so it stays a blank spacer rather than an invented button, kept
// only to hold the display's width steady against the row below it.
function AboveDisplayRow({ surface, views, onTab, press }) {
  const modes = surface?.modes || [];
  const at = surface?.at || [];
  const colours = surface?.colours || [];
  const active = surface?.view;

  return (
    <View style={styles.railRow}>
      <PushButton
        label="Metronome"
        accessibilityLabel="Metronome — /mcp"
        colour={C.accentText}
        style={{ width: RAIL_L }}
        onPress={tapCC(press, METRO_CC)}
      />
      <View style={[styles.row, styles.mid]}>
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
      <View style={{ width: RAIL_R }} />
    </View>
  );
}

// Delete/Undo stack at the display's left edge; Add Device/Add Track/Browse
// at its right. On the hardware the right side is a 2-stack (Add Device,
// Add Track) beside a 2x2 grid (Device/Mix above Browse/Clip) -- Device, Mix
// and Clip aren't in push_cc.py's map, so rather than draw empty cells next
// to real ones, this collapses the mapped three into one column. Real
// grouping (all three sit right of the display, nowhere else), approximate
// arrangement (one column, not stack-beside-grid).
//
// Both rail columns default to stretch (no alignSelf override) so they fill
// whatever height the display itself settles at, same trick PadArea's
// flanks use below.
function DisplayRow({ base, surface, press }) {
  return (
    <View style={styles.railRow}>
      <View style={[styles.railCol, { width: RAIL_L }]}>
        <PushButton
          label="Delete"
          accessibilityLabel="Delete — close the current agent's pane"
          colour={C.bad}
          style={styles.fill}
          onPress={tapCC(press, DELETE_CC)}
        />
        <PushButton
          label="Undo"
          accessibilityLabel="Undo — backspace the prompt empty"
          colour={C.accentText}
          style={styles.fill}
          onPress={tapCC(press, UNDO_CC)}
        />
      </View>
      <Frame base={base} surface={surface} />
      <View style={[styles.railCol, { width: RAIL_R }]}>
        <PushButton
          label="Add Device"
          accessibilityLabel="Add Device — split, new claude in auto mode"
          colour={C.accentText}
          style={styles.fill}
          onPress={tapCC(press, ADD_DEVICE_CC)}
        />
        <PushButton
          label="Add Track"
          accessibilityLabel="Add Track — new worktree, named from what is typed"
          colour={C.accentText}
          style={styles.fill}
          onPress={tapCC(press, ADD_TRACK_CC)}
        />
        <PushButton
          label="Browse"
          accessibilityLabel="Browse — this repo's pull requests, in a browser"
          colour={C.accentText}
          style={styles.fill}
          onPress={tapCC(press, BROWSE_CC)}
        />
      </View>
    </View>
  );
}

// Mute/Solo/Stop Clip sit left of the 8 under-display (session) buttons, and
// the Left/Up/Down/Right diamond sits to their right -- the manual's Master
// button between them is unmapped and skipped. Both flanks opt out of
// stretch (alignSelf: flex-start) so the compact session row doesn't get
// stretched to the diamond's taller, self-sized square -- the diamond alone
// decides this row's height, the same way the display alone decides
// DisplayRow's, just with the tall/short roles swapped.
function UnderDisplayRow({ surface, onSeat, press }) {
  const seats = surface?.seats || [];
  const current = surface?.current;

  return (
    <View style={styles.railRow}>
      <View style={[styles.row, { width: RAIL_L, alignSelf: 'flex-start' }]}>
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
          label="Stop"
          accessibilityLabel="Stop Clip — escape, interrupts the agent"
          colour={C.bad}
          style={styles.slot}
          onPress={tapCC(press, STOP_CC)}
        />
      </View>
      <View style={[styles.row, styles.mid, { alignSelf: 'flex-start' }]}>
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
      <View style={{ width: RAIL_R, alignItems: 'center' }}>
        <FourWay
          style={{ width: DIAMOND_SIZE }}
          up={{ label: '↑', accessibilityLabel: 'Up arrow — straight through to the agent', onPress: tapCC(press, UP_CC) }}
          down={{ label: '↓', accessibilityLabel: 'Down arrow — straight through to the agent', onPress: tapCC(press, DOWN_CC) }}
          left={{ label: '←', accessibilityLabel: 'Left arrow — straight through to the agent', onPress: tapCC(press, LEFT_CC) }}
          right={{ label: '→', accessibilityLabel: 'Right arrow — straight through to the agent', onPress: tapCC(press, RIGHT_CC) }}
        />
      </View>
    </View>
  );
}

// Left of the touchstrip: Convert/Double Loop/Quantize, then Duplicate/New,
// then the bigger Automate/Record pair, then Play alone at the bottom --
// this is the manual's own grouping and order, not a guess. Right of the
// pad grid: a flex spacer standing in for the unmapped Repeat/Accent/Scale/
// Layout/Note/Session column, then the Octave/Page diamond, then Shift/
// Select in the bottom-right corner, all three real positions.
function PadArea({ macros, press }) {
  const record = useHold(press, RECORD_CC);
  const shift = useHold(press, SHIFT_CC);
  return (
    <View style={styles.railRow}>
      <View style={[styles.padRail, { width: PAD_RAIL_L }]}>
        <View style={styles.padRailGroup}>
          <PushButton
            label="Convert"
            accessibilityLabel="Convert — /compact"
            colour={C.accentText}
            style={styles.fill}
            onPress={tapCC(press, CONVERT_CC)}
          />
          <PushButton
            label="Double Loop"
            accessibilityLabel="Double Loop — /effort"
            colour={C.accentText}
            style={styles.fill}
            onPress={tapCC(press, DBLOOP_CC)}
          />
          <PushButton
            label="Quantize"
            accessibilityLabel="Quantize — /model"
            colour={C.accentText}
            style={styles.fill}
            onPress={tapCC(press, QUANTIZE_CC)}
          />
        </View>
        <View style={styles.padRailGroup}>
          <PushButton
            label="Duplicate"
            accessibilityLabel="Duplicate — fork the current agent into a new session"
            colour={C.accentText}
            style={styles.fill}
            onPress={tapCC(press, DUPLICATE_CC)}
          />
          <PushButton
            label="New"
            accessibilityLabel="New — /clear"
            colour={C.accentText}
            style={styles.fill}
            onPress={tapCC(press, NEW_CC)}
          />
        </View>
        <View style={[styles.padRailGroup, { flex: 1.4 }]}>
          <PushButton
            label="Automate"
            accessibilityLabel="Automate — tap, then tap a pad, to arm that row as a chain"
            colour={C.warn}
            style={styles.fill}
            onPress={tapCC(press, AUTOMATE_CC)}
          />
          <PushButton
            label="Record"
            accessibilityLabel="Record — hold, then tap a pad, to save the prompt onto it"
            colour={C.bad}
            lit={record.held}
            style={styles.fill}
            onPressIn={record.onPressIn}
            onPressOut={record.onPressOut}
          />
        </View>
        <PushButton
          label="Play"
          accessibilityLabel="Play — enter, submits what is typed"
          colour={C.good}
          style={[styles.fill, { flex: 1.6 }]}
          onPress={tapCC(press, PLAY_CC)}
        />
      </View>
      <TouchStrip press={press} />
      <PadRows macros={macros} />
      <View style={[styles.padRail, { width: RAIL_R }]}>
        <View style={{ flex: 1 }} />
        <FourWay
          style={{ width: DIAMOND_SIZE, alignSelf: 'center' }}
          up={{ label: '▲', accessibilityLabel: 'Octave up — pages the focus view back through history', onPress: tapCC(press, SCROLL_UP_CC) }}
          down={{ label: '▼', accessibilityLabel: 'Octave down — pages the focus view forward', onPress: tapCC(press, SCROLL_DOWN_CC) }}
          left={{ label: '‹', accessibilityLabel: 'Page left — previous pad page', onPress: () => press({ page: -1 }) }}
          right={{ label: '›', accessibilityLabel: 'Page right — next pad page', onPress: () => press({ page: 1 }) }}
        />
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
        </View>
      </View>
    </View>
  );
}

// A generic 4-way diamond: up/down/left/right each a cell in a 3x3 grid with
// the corners left empty, the nearest a rectangular grid gets to the
// hardware's diamond-shaped cluster. Used for both the arrow-key diamond
// (under-display row) and the Octave/Page diamond (beside the pads) --
// same shape on the hardware, same component here.
function FourWay({ up, down, left, right, style }) {
  return (
    <View style={[styles.fourWay, style]}>
      <View style={styles.fourWayRow}>
        <View style={styles.fourWaySpacer} />
        <PushButton {...up} colour={C.accentText} style={styles.fourWayCell} />
        <View style={styles.fourWaySpacer} />
      </View>
      <View style={styles.fourWayRow}>
        <PushButton {...left} colour={C.accentText} style={styles.fourWayCell} />
        <View style={styles.fourWaySpacer} />
        <PushButton {...right} colour={C.accentText} style={styles.fourWayCell} />
      </View>
      <View style={styles.fourWayRow}>
        <View style={styles.fourWaySpacer} />
        <PushButton {...down} colour={C.accentText} style={styles.fourWayCell} />
        <View style={styles.fourWaySpacer} />
      </View>
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

// The grid shares its width with the button rows above and below it via
// `styles.mid`, so a pad sits under the button that governs its column. The
// labels are the app's own addition: on the glass a pad is light alone, and
// light alone is not readable across a desk.
function PadRows({ macros }) {
  return (
    <View style={[styles.pads, styles.mid]}>
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
      <View style={[styles.frame, styles.frameEmpty, styles.mid]}>
        <Text style={styles.waiting}>waiting for push_cc</Text>
      </View>
    );
  }

  const [w, h] = surface.size;
  const [band, bottom] = surface.bands;
  const top = band + BLEED;
  const visible = h - top - bottom;

  return (
    <View style={[styles.frame, styles.mid, { aspectRatio: w / visible, alignSelf: 'flex-start' }]}>
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
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  // Authored at a fixed CANVAS_W and an intrinsic height, then scaled as one
  // unit by `useFit` -- see the comment there. transform-origin defaults to
  // the element's own centre, which is exactly right here: `wrap` centres
  // this box at its native size, and the scale then shrinks or grows around
  // that same centre, so the result stays centred in `wrap` either way.
  canvas: {
    gap: S.gap,
  },
  row: {
    flexDirection: 'row',
    gap: S.gap / 2,
  },
  slot: {
    flex: 1,
  },
  fill: {
    flex: 1,
  },
  // Every row below the encoders shares this shape: a fixed-width column at
  // each edge (RAIL_L/RAIL_R, matching the encoder row's Tempo/Master) and a
  // flexible middle -- `mid` below -- so the display, the two 8-wide button
  // rows and the pad grid all come out the same width and stay lined up
  // under each other, the way they are on the hardware.
  railRow: {
    flexDirection: 'row',
    gap: S.gap / 2,
  },
  // The middle column's width: not a flex ratio (rows around it don't all
  // have the same number of flex siblings), an explicit share of CANVAS_W so
  // every row's middle is the same pixel width regardless of what's next to
  // it. See the RAIL_L/RAIL_R/STRIP_W comment above CANVAS_W.
  mid: {
    width: CANVAS_W - RAIL_L - RAIL_R - S.gap,
  },
  // Delete/Undo and Add Device/Add Track/Browse: stacked, stretched to
  // whatever height the display next to them settles at (default
  // alignItems is stretch; `fill` on each button turns that stretch into an
  // even 2- or 3-way split).
  railCol: {
    gap: S.gap / 2,
  },
  padRail: {
    gap: S.gap,
  },
  padRailGroup: {
    gap: S.gap / 2,
  },
  frame: {
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
    height: 60,
    borderRadius: 6,
    backgroundColor: KEY.face,
    borderWidth: 1,
    borderColor: KEY.edge,
    borderTopColor: KEY.top,
    alignItems: 'center',
    justifyContent: 'center',
  },
  knobLabel: {
    color: C.dim,
    fontSize: 11,
    fontWeight: '500',
  },
  strip: {
    width: STRIP_W,
    borderRadius: STRIP_W / 2,
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
    borderRadius: STRIP_W / 2,
  },
  pads: { gap: S.gap / 2 },
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
  // The diamond: a 3x3 grid with the corners left empty. Square (aspectRatio
  // 1) against its own width, so it reads as a compact cluster rather than
  // a stretched row -- on the hardware it is taller than the single button
  // row beside it, which is exactly what a square gets you here for free.
  fourWay: {
    aspectRatio: 1,
    gap: S.gap / 4,
  },
  fourWayRow: {
    flex: 1,
    flexDirection: 'row',
    gap: S.gap / 4,
  },
  fourWayCell: {
    flex: 1,
  },
  fourWaySpacer: {
    flex: 1,
  },
});
