import { useRef, useState } from 'react';
import {
  Modal,
  Pressable,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { C, S } from './theme';

// The overflow menu, and the ⋮ that opens it. Lifted out of Rail.js when the
// rail's second group needed the same thing -- placement is the whole content
// of this file, and it was already too fiddly to write twice.
//
// It draws itself rather than calling Alert.alert, which looked like the
// cheaper answer and is a no-op on web: react-native-web ships
// `class Alert { static alert() {} }`. On the browser build the menu did
// nothing at all, silently, and nothing said so.
//
// Where it draws: RN's Modal renders its content `position: fixed` over the
// whole viewport (react-native-web/exports/Modal/ModalContent), so left alone
// it centres there -- nothing ties it to the ⋮ that opened it, and a menu
// floating over the transcript acts on whichever row you *think* you tapped,
// not the one you did. Measuring the trigger in window coordinates and placing
// against it fixes that; `position: fixed`'s coordinates are viewport-relative
// same as `measureInWindow`'s, so this holds after the page has scrolled too.

const MENU_W = 240;
const MENU_GAP = 8;
const FALLBACK_H = 214; // header + four rows -- enough to pick a side before onLayout reports back

// The trigger. It measures itself and hands the caller the rectangle, so the
// caller never has to hold a ref or know that placement needs one.
//
// props:
//   label      string        -- the glyph; ⋮ everywhere so far
//   onOpen     (anchor) => void  -- anchor is {x,y,width,height} in window
//                                    coords, or null if it could not measure
//   accessibilityLabel string
//   style, glyphStyle       -- so a card's corner and a 28px row can both
//                              wear it without this file knowing about either
export function MenuButton({ label = '⋮', accessibilityLabel, onOpen, style, glyphStyle }) {
  const ref = useRef(null);
  return (
    <Pressable
      ref={ref}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      hitSlop={8}
      onPress={() => {
        const el = ref.current;
        if (el && el.measureInWindow) {
          el.measureInWindow((x, y, width, height) => onOpen({ x, y, width, height }));
        } else onOpen(null);
      }}
      style={[styles.btn, style]}>
      <Text style={[styles.dots, glyphStyle]}>{label}</Text>
    </Pressable>
  );
}

// The menu itself.
//
// props:
//   anchor   {x,y,width,height} | null   -- from MenuButton's onOpen
//   head     string                      -- what the menu is about, optional
//   items    [{ label, onPress, danger?, disabled? }]
//              `danger` sits the item apart, above a rule, in the colour
//              everything destructive reads in. Only the last run of them is
//              separated -- one rule, not one per item.
//   onClose  () => void
export function Menu({ anchor, head, items, onClose }) {
  // real height of the drawn menu, filled in by its own onLayout. Reset by the
  // caller remounting: a stale height misplaces the next menu for a frame.
  const [h, setH] = useState(0);
  const { width: winW, height: winH } = useWindowDimensions();

  const place = () => {
    if (!anchor) return null;
    const height = h || FALLBACK_H;
    let left = anchor.x + anchor.width - MENU_W; // right-align under the dots, not centred on the screen
    left = Math.max(MENU_GAP, Math.min(left, winW - MENU_W - MENU_GAP));
    let top = anchor.y + anchor.height + MENU_GAP;
    if (top + height > winH - MENU_GAP) top = anchor.y - height - MENU_GAP; // no room below -- hang it above
    top = Math.max(MENU_GAP, top);
    return { left, top };
  };

  const pos = place();
  const firstDanger = items.findIndex((it) => it.danger);

  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <View
          style={[styles.menu, pos && { position: 'absolute', ...pos }]}
          onLayout={(e) => setH(e.nativeEvent.layout.height)}>
          {!!head && (
            <Text style={styles.head} numberOfLines={1}>
              {head}
            </Text>
          )}
          {items.map((it, i) => (
            <Pressable
              key={it.label}
              accessibilityRole="button"
              disabled={it.disabled}
              style={[styles.row, i === firstDanger && i > 0 && styles.rule]}
              onPress={() => {
                onClose();
                it.onPress();
              }}>
              <Text
                style={[
                  styles.text,
                  it.danger && styles.bad,
                  it.disabled && styles.off,
                ]}>
                {it.label}
              </Text>
            </Pressable>
          ))}
        </View>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  btn: { paddingHorizontal: 4, paddingVertical: 2 },
  dots: { color: C.faint, fontSize: 16, fontWeight: '700' },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  menu: {
    width: MENU_W,
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    paddingVertical: 6,
  },
  head: { color: C.faint, fontSize: 11, paddingHorizontal: 14, paddingBottom: 6 },
  row: { minHeight: S.hit, justifyContent: 'center', paddingHorizontal: 14 },
  rule: { borderTopWidth: 1, borderTopColor: C.line, marginTop: 4 },
  text: { color: C.text, fontSize: 14 },
  bad: { color: C.bad },
  off: { color: C.edge },
});
