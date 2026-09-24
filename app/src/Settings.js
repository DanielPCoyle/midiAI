import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import Icon from './Icon';
import Integrations from './Integrations';
import { McpManager } from './Mcps';
import { EnginesSettings, ProvidersSettings } from './Providers';
import { C, S } from './theme';

const SECTIONS = [
  ['providers', 'Providers'],
  ['engines', 'Engines'],
  ['mcps', 'MCPs'],
  ['integrations', 'Integrations'],
];

// The old single-account Claude section (mode/key/login, default model and
// effort, one flat form) is now two: Providers is the credentials for all
// four providers an engine can authenticate through (Anthropic, OpenAI, AWS
// Bedrock, Google Vertex), Engines is which provider + model each of Claude
// Code and Codex starts with. Both live in Providers.js now, not here --
// this file only wires them into the section tabs, same as it already did
// for MCPs (Mcps.js) and Integrations (Integrations.js). See Providers.js
// for what moved and why.

// One modal, two sections, picked by `section` -- the same shape SessionSheet
// uses for its four errands. The card and backdrop follow Pane.js's diffModal
// (a Pressable backdrop that closes on its own press, wrapping a Pressable
// card that swallows the tap) crossed with SessionSheet's card idiom for the
// title/body/close layout.
//
// props:
//   visible    bool                    -- modal open/closed
//   base       string                  -- api base url
//   cwd        string | undefined      -- the checkout Settings was opened
//                                          "about" -- the picked worktree's
//                                          path, else the focused agent's.
//                                          MCPs reads this one place's MCPs.
//   section    'providers' | 'engines' | 'mcps' | 'integrations' | null
//   onSection  (section) => void       -- switch which section is showing
//   onClose    () => void
export default function Settings({ visible, base, cwd, cwds, section, onSection, onClose }) {
  return (
    <Modal visible={!!visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.card} onPress={() => {}}>
          <View style={styles.head}>
            <View style={styles.tabs}>
              {SECTIONS.map(([key, word]) => (
                <Text
                  key={key}
                  accessibilityRole="tab"
                  accessibilityState={{ selected: section === key }}
                  onPress={() => onSection(key)}
                  style={[styles.tab, section === key && styles.tabOn]}>
                  {word}
                </Text>
              ))}
            </View>
            <View style={styles.spacer} />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="close settings"
              hitSlop={8}
              onPress={onClose}
              style={styles.closeKey}>
              <Icon name="close" size={18} color={C.dim} />
            </Pressable>
          </View>
          {/* Each section mounts only while it is the one showing -- Providers/
              Engines then ask the server nothing until you have actually
              opened one, and McpManager's 4s health poll runs only while this
              panel is open. */}
          <ScrollView
            style={styles.bodyScroll}
            contentContainerStyle={styles.body}
            keyboardShouldPersistTaps="handled">
            {visible && section === 'providers' && <ProvidersSettings base={base} />}
            {visible && section === 'engines' && <EnginesSettings base={base} />}
            {visible && section === 'mcps' && <McpManager base={base} cwds={cwds || (cwd ? [cwd] : [])} />}
            {visible && section === 'integrations' && <Integrations base={base} cwd={cwd} />}
          </ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    width: 720,
    maxWidth: '92%',
    maxHeight: '90%',
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    overflow: 'hidden',
  },
  head: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: S.pad,
    paddingTop: S.pad,
    paddingBottom: 4,
    gap: 12,
  },
  tabs: { flexDirection: 'row', gap: 18 },
  tab: { color: C.faint, fontSize: 14, fontWeight: '600', paddingBottom: 8 },
  tabOn: { color: C.text, borderBottomWidth: 2, borderBottomColor: C.accentText },
  spacer: { flex: 1 },
  closeKey: { padding: 4, borderRadius: 6 },
  bodyScroll: { flexGrow: 0, flexShrink: 1, borderTopWidth: 1, borderTopColor: C.line },
  body: { padding: S.pad, gap: 8 },
});
