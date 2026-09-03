import { useRef, useState } from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import PushButton from './PushButton';
import SessionSheet from './SessionSheet';
import Worktrees from './Worktrees';
import { C, S, seatHue, seatWord } from './theme';

// The agents, as a rail you can read rather than eight anonymous buttons.
// The Push needs them anonymous -- it has exactly eight physical buttons and no
// room for a word above each. Nothing here is short of room.
//
// props:
//   cols       array(8)                 -- unchanged: each slot null or
//                                           { name, status, model, effort, sub, tid, focused, unseen, context, cwd? }.
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
  // Worktrees is its own component, not another SessionSheet mode -- it lists
  // rather than errands, so it gets its own bit of state rather than being
  // squeezed into `sheet`'s shape. Just the seat index; the modal reads
  // cols[wtSeat].cwd itself.
  const [wtSeat, setWtSeat] = useState(null);
  const free = 8 - cols.filter(Boolean).length;

  // PushButton doesn't forward onLongPress (it wraps children in its own
  // Pressable and only wires onPress), so the secondary action is a small
  // "⋯" control inside the card rather than a long press.
  //
  // It draws its own menu rather than calling Alert.alert, which looked like
  // the cheaper answer and is a no-op on web: react-native-web ships
  // `class Alert { static alert() {} }`. On the browser build the menu did
  // nothing at all, silently -- rename, close and worktrees were simply
  // unreachable, and nothing said so. A menu the app draws works on both.
  const [menu, setMenu] = useState(null);   // seat index, or null
  const menuCol = menu != null ? cols[menu] : null;
  const act = (fn) => {
    setMenu(null);
    fn();
  };

  // Where the menu draws: RN's Modal renders its content `position: fixed`
  // over the whole viewport (react-native-web/exports/Modal/ModalContent),
  // so it used to just centre itself there -- nothing tied it to the "⋯"
  // that opened it, and a menu that floats over the transcript acts on
  // whichever agent you *think* you tapped, not the one you did. Measuring
  // the trigger in window coordinates and placing the menu against it fixes
  // that; `position: fixed`'s coordinates are viewport-relative same as
  // `measureInWindow`'s, so this holds after the page has scrolled too.
  const menuBtnRefs = useRef({});
  const [menuAnchor, setMenuAnchor] = useState(null); // {x,y,width,height} of the "⋯", in window coords
  const [menuH, setMenuH] = useState(0); // real height of the drawn menu, filled in by its own onLayout
  const { width: winW, height: winH } = useWindowDimensions();
  const MENU_W = 240;
  const MENU_GAP = 8;
  const MENU_FALLBACK_H = 214; // header + four rows -- close enough to pick a side before onLayout reports back
  const openMenu = (i) => {
    const el = menuBtnRefs.current[i];
    const finish = (pos) => {
      setMenuAnchor(pos);
      setMenuH(0); // the last menu's height would misplace this one for a frame otherwise
      setMenu(i);
    };
    if (el && el.measureInWindow) el.measureInWindow((x, y, width, height) => finish({ x, y, width, height }));
    else finish(null);
  };
  const menuPlace = () => {
    if (!menuAnchor) return null;
    const h = menuH || MENU_FALLBACK_H;
    let left = menuAnchor.x + menuAnchor.width - MENU_W; // right-align under the dots, not centred on the screen
    left = Math.max(MENU_GAP, Math.min(left, winW - MENU_W - MENU_GAP));
    let top = menuAnchor.y + menuAnchor.height + MENU_GAP;
    if (top + h > winH - MENU_GAP) top = menuAnchor.y - h - MENU_GAP; // no room below -- hang it above instead
    top = Math.max(MENU_GAP, top);
    return { left, top };
  };

  const seatFor = (i) => (i != null ? cols[i] : null);
  const activeSeat = sheet && sheet.mode !== 'new' ? seatFor(sheet.seatIndex) : seatFor(current);
  const menuPos = menuPlace();

  return (
    <View style={styles.rail}>
      <Text style={styles.head}>AGENTS</Text>
      <ScrollView contentContainerStyle={styles.list}>
        {Array.from({ length: 8 }, (_, i) => {
          const col = cols[i] || null;
          if (!col) return null;
          const hue = seatHue(col);
          const on = i === current;
          return (
            /* The ⋯ used to sit inside the card. Both are buttons, and a
               button inside a button is not valid HTML -- React says so and
               refuses to hydrate it. It was only a div until it was given a
               name, so naming it is what surfaced this. Sibling now, laid
               over the corner it already occupied. */
            <View key={i} style={styles.slot}>
            <PushButton
              colour={hue}
              // left to itself this announces as "midiAIidle⋯%0" -- the name,
              // the status, the menu glyph and the tmux id run together. Same
              // shape as the tab that answered to "· 0".
              accessibilityLabel={`${col.name} · ${seatWord(col)}`}
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
                  <Text style={[styles.status, { color: hue }]}>{seatWord(col)}</Text>
                </View>
                <View style={styles.row}>
                  {[col.model, col.effort]
                    .filter(Boolean)
                    .map((bit, k) => (
                      <Text key={k} style={styles.meta} numberOfLines={1}>
                        {k ? `· ${bit}` : bit}
                      </Text>
                    ))}
                  {/* the tmux pane id -- real only when hand-editing macros.json,
                      so it drops back further than model/effort rather than
                      reading as a fourth equally-important fact about the card */}
                  {!!col.sub && (
                    <Text style={styles.metaId} numberOfLines={1}>
                      {col.model || col.effort ? `· ${col.sub}` : col.sub}
                    </Text>
                  )}
                </View>
              </View>
            </PushButton>
            <Pressable
              ref={(el) => {
                menuBtnRefs.current[i] = el;
              }}
              accessibilityRole="button"
              accessibilityLabel={`more actions for ${col.name}`}
              onPress={() => openMenu(i)}
              hitSlop={8}
              style={styles.menuBtn}>
              <Text style={styles.menuDots}>⋯</Text>
            </Pressable>
            </View>
          );
        })}
        {free > 0 && (
          <PushButton
            colour="transparent"
            accessibilityLabel="new agent"
            onPress={() => setSheet({ mode: 'new', seatIndex: null })}
            style={styles.free}>
            <Text style={styles.freeText}>＋ new agent</Text>
          </PushButton>
        )}
      </ScrollView>

      <Modal
        visible={menu != null}
        transparent
        animationType="fade"
        onRequestClose={() => setMenu(null)}>
        <Pressable style={styles.backdrop} onPress={() => setMenu(null)}>
          <View
            style={[styles.menu, menuPos && { position: 'absolute', ...menuPos }]}
            onLayout={(e) => setMenuH(e.nativeEvent.layout.height)}>
            <Text style={styles.menuHead} numberOfLines={1}>
              {menuCol?.name || ''}
            </Text>
            {[
              ['rename', () => setSheet({ mode: 'rename', seatIndex: menu })],
              ['new worktree', () => setSheet({ mode: 'worktree', seatIndex: menu })],
              ['worktrees…', () => setWtSeat(menu)],
            ].map(([label, fn]) => (
              <Pressable key={label} style={styles.menuRow} onPress={() => act(fn)}>
                <Text style={styles.menuText}>{label}</Text>
              </Pressable>
            ))}
            {/* close kills something that is running, so it sits apart and
                reads in the colour everything else dangerous does */}
            <Pressable
              style={[styles.menuRow, styles.menuLast]}
              onPress={() => act(() => setSheet({ mode: 'close', seatIndex: menu }))}>
              <Text style={[styles.menuText, styles.menuBad]}>close</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>

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
      {wtSeat != null && (
        <Worktrees
          visible
          base={base}
          cwd={seatFor(wtSeat)?.cwd}
          onClose={() => setWtSeat(null)}
          onChanged={() => onChanged && onChanged()}
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
  metaId: { color: C.edge, fontSize: 11, flexShrink: 1 },
  slot: { position: 'relative' },
  menuBtn: { position: 'absolute', top: 6, right: 6, paddingHorizontal: 4, paddingVertical: 2 },
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
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  menu: {
    width: 240,
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    paddingVertical: 6,
  },
  menuHead: {
    color: C.faint,
    fontSize: 11,
    paddingHorizontal: 14,
    paddingBottom: 6,
  },
  menuRow: { minHeight: S.hit, justifyContent: 'center', paddingHorizontal: 14 },
  menuLast: { borderTopWidth: 1, borderTopColor: C.line, marginTop: 4 },
  menuText: { color: C.text, fontSize: 14 },
  menuBad: { color: C.bad },
});
