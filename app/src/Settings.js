import { useEffect, useState } from 'react';
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import {
  claudeLogin,
  deleteClaudeKey,
  getClaudeSettings,
  saveClaudeKey,
  setClaudeSettings,
} from './api';
import Icon from './Icon';
import Integrations from './Integrations';
import { McpManager } from './Mcps';
import PushButton from './PushButton';
import { C, S } from './theme';

const SECTIONS = [
  ['claude', 'Claude'],
  ['mcps', 'MCPs'],
  ['integrations', 'Integrations'],
];

const MODES = [
  ['subscription', 'subscription', 'your claude.ai login'],
  ['api_key', 'API key', 'billed to the key, stored in the Keychain'],
  ['bedrock', 'Bedrock', "your cloud account's credentials, e.g. an AWS profile"],
  ['vertex', 'Vertex', "your cloud account's credentials, e.g. gcloud"],
];

const MODELS = ['', 'opus', 'sonnet', 'haiku'];
const EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max'];
// '' means "Claude Code's own default" on the wire in both fields -- one word
// for it here rather than two chips that would otherwise just say nothing.
const CHIP_WORD = { '': 'default' };

// The Claude section: how agents midiAI starts authenticate, plus the
// default model/effort new ones start with. Its own component so the modal's
// body switch stays one line, and so it can be mounted only while this
// section is actually showing -- unmounted, it asks the server nothing.
function ClaudeSettings({ base }) {
  const [got, setGot] = useState(null);      // last GET /settings/claude, or null while loading
  const [err, setErr] = useState('');         // the routes may not exist yet -- a quiet line, not a crash
  const [loading, setLoading] = useState(true);
  const [keyField, setKeyField] = useState('');
  const [keyEditing, setKeyEditing] = useState(false);
  const [keyErr, setKeyErr] = useState('');
  const [keyBusy, setKeyBusy] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);
  const [bedrock, setBedrock] = useState({ region: '', profile: '' });
  const [vertex, setVertex] = useState({ region: '', project: '' });
  const [modelField, setModelField] = useState('');

  const load = () => {
    setLoading(true);
    getClaudeSettings(base)
      .then((claude) => {
        setGot(claude);
        setErr('');
        setBedrock(claude.bedrock || { region: '', profile: '' });
        setVertex(claude.vertex || { region: '', project: '' });
        setModelField(claude.model || '');
      })
      // the /settings/claude routes may not exist on an older server yet --
      // this line is the whole point of the catch, so it never throws past it
      .catch((e) => setErr(`couldn't read settings: ${e.message || e}`))
      .finally(() => setLoading(false));
  };

  useEffect(load, [base]);

  // Any subset of {mode, model, effort, bedrock, vertex} -- one call, and the
  // same shape comes back, which becomes the new `got` so every chip reflects
  // what the server actually saved rather than what was merely sent.
  const patch = (fields) =>
    setClaudeSettings(base, fields)
      .then((next) => { setGot(next); setErr(''); })
      .catch((e) => setErr(`couldn't save: ${e.message || e}`));

  async function doLogin() {
    setLoginBusy(true);
    setErr('');
    try {
      await claudeLogin(base);
      // opens Terminal.app on the Mac and runs there -- nothing to await, so
      // a few seconds and a re-read is the whole handshake
      setTimeout(load, 4000);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoginBusy(false);
    }
  }

  async function saveKey() {
    if (!keyField.trim()) return;
    setKeyBusy(true);
    setKeyErr('');
    try {
      const next = await saveClaudeKey(base, keyField.trim());
      setGot(next);
      setKeyField('');            // never kept in state once it is sent
      setKeyEditing(false);
    } catch (e) {
      setKeyErr(e.message || String(e));   // the server's rejection text, shown inline
    } finally {
      setKeyBusy(false);
    }
  }

  async function removeKey() {
    setKeyBusy(true);
    setKeyErr('');
    try {
      setGot(await deleteClaudeKey(base));
    } catch (e) {
      setKeyErr(e.message || String(e));
    } finally {
      setKeyBusy(false);
    }
  }

  if (loading) return <ActivityIndicator color={C.faint} style={styles.spin} />;
  if (err) return <Text style={styles.err}>{err}</Text>;
  if (!got) return null;

  const auth = got.auth || {};
  const statusLine = auth.error
    ? auth.error
    : auth.loggedIn
    ? `signed in as ${auth.email || 'unknown'}${auth.subscriptionType ? ` · ${auth.subscriptionType} plan` : ''}`
    : 'not signed in';

  return (
    <View style={styles.section}>
      <View style={styles.statusRow}>
        <Text style={styles.statusText} numberOfLines={2}>{statusLine}</Text>
        <Pressable
          accessibilityRole="button"
          disabled={loginBusy}
          onPress={doLogin}
          style={styles.linkKey}>
          <Text style={styles.link}>{loginBusy ? 'opening Terminal…' : 'sign in on the Mac'}</Text>
        </Pressable>
      </View>

      <Text style={styles.label}>mode</Text>
      <View style={styles.chips}>
        {MODES.map(([key, word]) => (
          <Text
            key={key}
            accessibilityRole="button"
            accessibilityState={{ selected: got.mode === key }}
            onPress={() => patch({ mode: key })}
            style={[styles.chip, got.mode === key && styles.chipOn]}>
            {word}
          </Text>
        ))}
      </View>
      <Text style={styles.hint}>{MODES.find(([key]) => key === got.mode)?.[2] || ''}</Text>

      {got.mode === 'api_key' && (
        <View style={styles.keyBox}>
          {got.key?.saved && !keyEditing ? (
            <View style={styles.keyRow}>
              <Text style={styles.hint} numberOfLines={1}>
                key saved · {got.key.hint || '…'}
              </Text>
              <Pressable accessibilityRole="button" onPress={() => setKeyEditing(true)} style={styles.linkKey}>
                <Text style={styles.link}>replace</Text>
              </Pressable>
              <Pressable accessibilityRole="button" disabled={keyBusy} onPress={removeKey} style={styles.linkKey}>
                <Text style={[styles.link, styles.linkBad]}>remove</Text>
              </Pressable>
            </View>
          ) : (
            <>
              <TextInput
                value={keyField}
                onChangeText={setKeyField}
                secureTextEntry
                autoComplete="off"
                autoCorrect={false}
                autoCapitalize="none"
                placeholder="sk-ant-…"
                placeholderTextColor={C.faint}
                style={styles.input}
              />
              <PushButton
                label={keyBusy ? '' : 'save & check'}
                colour={C.accentText}
                lit
                disabled={!keyField.trim() || keyBusy}
                onPress={saveKey}
                style={styles.saveBtn}>
                {keyBusy && <ActivityIndicator size="small" color={C.text} />}
              </PushButton>
            </>
          )}
          {!!keyErr && <Text style={styles.err}>{keyErr}</Text>}
        </View>
      )}

      {got.mode === 'bedrock' && (
        <View style={styles.fieldPair}>
          <TextInput
            value={bedrock.region}
            onChangeText={(v) => setBedrock((b) => ({ ...b, region: v }))}
            onBlur={() => patch({ bedrock })}
            placeholder="region"
            placeholderTextColor={C.faint}
            autoCapitalize="none"
            autoCorrect={false}
            style={[styles.input, styles.fieldHalf]}
          />
          <TextInput
            value={bedrock.profile}
            onChangeText={(v) => setBedrock((b) => ({ ...b, profile: v }))}
            onBlur={() => patch({ bedrock })}
            placeholder="AWS profile"
            placeholderTextColor={C.faint}
            autoCapitalize="none"
            autoCorrect={false}
            style={[styles.input, styles.fieldHalf]}
          />
        </View>
      )}

      {got.mode === 'vertex' && (
        <View style={styles.fieldPair}>
          <TextInput
            value={vertex.region}
            onChangeText={(v) => setVertex((b) => ({ ...b, region: v }))}
            onBlur={() => patch({ vertex })}
            placeholder="region"
            placeholderTextColor={C.faint}
            autoCapitalize="none"
            autoCorrect={false}
            style={[styles.input, styles.fieldHalf]}
          />
          <TextInput
            value={vertex.project}
            onChangeText={(v) => setVertex((b) => ({ ...b, project: v }))}
            onBlur={() => patch({ vertex })}
            placeholder="GCP project"
            placeholderTextColor={C.faint}
            autoCapitalize="none"
            autoCorrect={false}
            style={[styles.input, styles.fieldHalf]}
          />
        </View>
      )}

      <Text style={styles.label}>default model</Text>
      <View style={styles.chips}>
        {MODELS.map((m) => (
          <Text
            key={m || 'default'}
            accessibilityRole="button"
            accessibilityState={{ selected: got.model === m }}
            onPress={() => patch({ model: m })}
            style={[styles.chip, got.model === m && styles.chipOn]}>
            {CHIP_WORD[m] || m}
          </Text>
        ))}
      </View>
      <TextInput
        value={modelField}
        onChangeText={setModelField}
        onBlur={() => modelField.trim() !== (got.model || '') && patch({ model: modelField.trim() })}
        onSubmitEditing={() => patch({ model: modelField.trim() })}
        placeholder="or a full model id"
        placeholderTextColor={C.faint}
        autoCapitalize="none"
        autoCorrect={false}
        style={styles.input}
      />

      <Text style={styles.label}>default effort</Text>
      <View style={styles.chips}>
        {EFFORTS.map((ef) => (
          <Text
            key={ef || 'default'}
            accessibilityRole="button"
            accessibilityState={{ selected: got.effort === ef }}
            onPress={() => patch({ effort: ef })}
            style={[styles.chip, got.effort === ef && styles.chipOn]}>
            {CHIP_WORD[ef] || ef}
          </Text>
        ))}
      </View>

      <Text style={styles.note}>applies to agents started from now on</Text>
    </View>
  );
}

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
//   section    'claude' | 'mcps' | 'integrations' | null
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
          {/* Each section mounts only while it is the one showing -- ClaudeSettings
              then asks the server nothing until you have actually opened it, and
              McpManager's 4s health poll runs only while this panel is open. */}
          <ScrollView
            style={styles.bodyScroll}
            contentContainerStyle={styles.body}
            keyboardShouldPersistTaps="handled">
            {visible && section === 'claude' && <ClaudeSettings base={base} />}
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
  section: { gap: 8 },
  spin: { margin: S.pad },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 4 },
  statusText: { color: C.text, fontSize: 13, flexShrink: 1, flexGrow: 1 },
  label: { color: C.dim, fontSize: 12, marginTop: 6 },
  hint: { color: C.faint, fontSize: 11 },
  note: { color: C.faint, fontSize: 11, marginTop: 10 },
  err: { color: C.bad, fontSize: 12, padding: S.pad },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: {
    color: C.faint,
    fontSize: 12,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 6,
  },
  chipOn: { color: C.text, borderColor: C.accentText },
  linkKey: { paddingHorizontal: 4, paddingVertical: 2 },
  link: { color: C.accentText, fontSize: 12 },
  linkBad: { color: C.bad },
  input: {
    backgroundColor: C.raised,
    color: C.text,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: S.radius,
    padding: 8,
    fontSize: 13,
    minHeight: S.hit,
  },
  keyBox: { gap: 8, marginTop: 2 },
  keyRow: { flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  saveBtn: { alignSelf: 'flex-start', minWidth: 140 },
  fieldPair: { flexDirection: 'row', gap: 8 },
  fieldHalf: { flex: 1 },
});
