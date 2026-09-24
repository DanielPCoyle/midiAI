import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import {
  deleteIntegrationSecret,
  integrationResources,
  listIntegrations,
  mapIntegration,
  setIntegrationConfig,
  setIntegrationSecret,
} from './api';
import PushButton from './PushButton';
import { C, PAD_HEX, S, mono } from './theme';

// Where a capability's namespace lands in the app, and the hue it draws in --
// borrowed straight off the wire palette rather than invented, so this reads
// as one more place the app is already coloured. Only deploy.* is wired to
// anything yet (the DEPLOY tab); logs/metrics/errors/alerts are named here
// because the contract already reserves them for Sentry/AWS/Telemetry later,
// per the mockup's own "how an integration plugs in" panel.
const CAP_GROUP = (cap) => {
  if (cap.startsWith('deploy.')) return 'deploy';
  if (cap.startsWith('logs.') || cap.startsWith('metrics.')) return 'telemetry';
  if (cap.startsWith('errors.') || cap.startsWith('alerts.')) return 'errors';
  return 'other';
};
const GROUP_HEX = { deploy: PAD_HEX[53], telemetry: PAD_HEX[33], errors: C.bad, other: C.faint };
const GROUP_WORD = { deploy: 'deploy', telemetry: 'logs · metrics', errors: 'errors', other: 'other' };

function groupCaps(capabilities) {
  const by = new Map();
  for (const cap of capabilities || []) {
    const g = CAP_GROUP(cap);
    const op = cap.split('.')[1] || cap;
    if (!by.has(g)) by.set(g, []);
    by.get(g).push(op);
  }
  // deploy first, always -- it is the one that lights up a tab today
  return [...by.entries()].sort(([a], [b]) => (a === 'deploy' ? -1 : b === 'deploy' ? 1 : 0));
}

// One secret: masked field, save & check, or "saved · …hint" with replace /
// remove -- the same shape ClaudeSettings' API-key box uses, one per row's
// declared secret rather than the one key that box was built for.
function SecretRow({ base, name, secret, onRows }) {
  const [editing, setEditing] = useState(!secret.set);
  const [field, setField] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function save() {
    if (!field.trim()) return;
    setBusy(true);
    setErr('');
    try {
      const rows = await setIntegrationSecret(base, name, secret.key, field.trim());
      onRows(rows);
      setField('');
      setEditing(false);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setErr('');
    try {
      onRows(await deleteIntegrationSecret(base, name, secret.key));
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.secretBox}>
      <Text style={styles.label}>{secret.label || secret.key}</Text>
      {secret.set && !editing ? (
        <View style={styles.secretRow}>
          <Text style={styles.hint} numberOfLines={1}>
            saved{secret.hint ? ` · …${secret.hint}` : ''}
          </Text>
          <Pressable accessibilityRole="button" onPress={() => setEditing(true)} style={styles.linkKey}>
            <Text style={styles.link}>replace</Text>
          </Pressable>
          <Pressable accessibilityRole="button" disabled={busy} onPress={remove} style={styles.linkKey}>
            <Text style={[styles.link, styles.linkBad]}>remove</Text>
          </Pressable>
        </View>
      ) : (
        <View style={styles.secretRow}>
          <TextInput
            value={field}
            onChangeText={setField}
            secureTextEntry
            autoComplete="off"
            autoCorrect={false}
            autoCapitalize="none"
            placeholder={secret.help || secret.key}
            placeholderTextColor={C.faint}
            style={[styles.input, styles.secretInput]}
          />
          <PushButton
            label={busy ? '' : 'save & check'}
            colour={C.accentText}
            lit
            disabled={!field.trim() || busy}
            onPress={save}
            style={styles.saveBtn}>
            {busy && <ActivityIndicator size="small" color={C.text} />}
          </PushButton>
          {secret.set && (
            <Pressable accessibilityRole="button" disabled={busy} onPress={() => setEditing(false)} style={styles.linkKey}>
              <Text style={styles.link}>cancel</Text>
            </Pressable>
          )}
        </View>
      )}
      {!!secret.help && <Text style={styles.hint}>{secret.help}</Text>}
      {!!err && <Text style={styles.err}>{err}</Text>}
    </View>
  );
}

// One config field -- plain text, saved on blur, same idiom as
// ClaudeSettings' bedrock/vertex region+profile pair.
function ConfigRow({ base, name, field, onRows }) {
  const [val, setVal] = useState(field.value || '');
  const [err, setErr] = useState('');
  useEffect(() => setVal(field.value || ''), [field.value]);

  const save = async () => {
    if (val === (field.value || '')) return;
    try {
      onRows(await setIntegrationConfig(base, name, { [field.key]: val }));
      setErr('');
    } catch (e) {
      setErr(e.message || String(e));
    }
  };

  return (
    <View style={styles.secretBox}>
      <Text style={styles.label}>{field.label || field.key}</Text>
      <TextInput
        value={val}
        onChangeText={setVal}
        onBlur={save}
        onSubmitEditing={save}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder={field.key}
        placeholderTextColor={C.faint}
        style={styles.input}
      />
      {!!err && <Text style={styles.err}>{err}</Text>}
    </View>
  );
}

// "map to this project": pick a resource from resources.list for the current
// checkout, or show the one already picked. `cwd` is the checkout root
// Settings was opened about; /integrations' own state is keyed by it the
// same way, so this reads `row.map?.[cwd]` rather than asking a second route.
function MapRow({ base, name, cwd, mapped, onRows }) {
  const [picking, setPicking] = useState(false);
  const [resources, setResources] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const openPicker = () => {
    setPicking(true);
    if (resources != null) return;
    setLoading(true);
    integrationResources(base, name)
      .then((r) => { setResources(r); setErr(''); })
      .catch((e) => { setResources([]); setErr(e.message || String(e)); })
      .finally(() => setLoading(false));
  };

  const pick = async (resource) => {
    setBusy(true);
    try {
      onRows(await mapIntegration(base, name, cwd, resource.id, resource.name));
      setPicking(false);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const unmap = async () => {
    setBusy(true);
    try {
      onRows(await mapIntegration(base, name, cwd, null, ''));
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!cwd) return null;

  return (
    <View style={styles.mapBox}>
      <View style={styles.mapRow}>
        <Text style={styles.hint} numberOfLines={1}>
          {mapped ? (
            <>mapped to <Text style={styles.mapWho}>{mapped.label || mapped.resource}</Text></>
          ) : 'not mapped to this project'}
        </Text>
        <View style={styles.spacer} />
        <Pressable accessibilityRole="button" onPress={openPicker} style={styles.linkKey}>
          <Text style={styles.link}>map resources</Text>
        </Pressable>
        {!!mapped && (
          <Pressable accessibilityRole="button" disabled={busy} onPress={unmap} style={styles.linkKey}>
            <Text style={[styles.link, styles.linkBad]}>unmap</Text>
          </Pressable>
        )}
      </View>
      {picking && (
        <View style={styles.resList}>
          {loading && <ActivityIndicator color={C.faint} style={styles.spin} />}
          {!loading && (resources || []).map((r) => (
            <Pressable
              key={r.id}
              accessibilityRole="button"
              disabled={busy}
              onPress={() => pick(r)}
              style={styles.resRow}>
              <Text style={styles.resName} numberOfLines={1}>{r.name || r.id}</Text>
              {!!r.framework && <Text style={styles.resMeta}>{r.framework}</Text>}
            </Pressable>
          ))}
          {!loading && resources && resources.length === 0 && (
            <Text style={styles.hint}>no resources found — check the secret above is saved</Text>
          )}
          <Pressable accessibilityRole="button" onPress={() => setPicking(false)} style={styles.linkKey}>
            <Text style={styles.link}>close</Text>
          </Pressable>
        </View>
      )}
      {!!err && <Text style={styles.err}>{err}</Text>}
    </View>
  );
}

// One integration: status, capability chips, its secrets and config, and the
// map-to-this-project row. `row` is one entry of listIntegrations' rows.
function IntegrationCard({ base, cwd, row, onRows }) {
  const groups = groupCaps(row.capabilities);
  const statusWord = row.error ? row.error : row.ok === true ? 'connected' : row.ok === false ? 'not connected' : 'unchecked';
  const statusHex = row.error || row.ok === false ? C.bad : row.ok === true ? PAD_HEX[21] : C.faint;
  const mapped = row.map && cwd ? row.map[cwd] : null;

  return (
    <View style={styles.card}>
      <View style={styles.cardHead}>
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{(row.title || row.name || '?').slice(0, 2).toUpperCase()}</Text>
        </View>
        <View style={styles.cardTitles}>
          <Text style={styles.cardTitle}>{row.title || row.name}</Text>
          <Text style={styles.cardSub} numberOfLines={1}>
            {row.builtin ? 'built in' : 'your own'} · integrations{row.builtin ? '' : '/'}{row.builtin ? '' : row.name}/
          </Text>
        </View>
        <View style={styles.spacer} />
        <View style={styles.statusChip}>
          <View style={[styles.statusDot, { backgroundColor: statusHex }]} />
          <Text style={[styles.statusWord, { color: statusHex }]} numberOfLines={1}>{statusWord}</Text>
        </View>
      </View>

      {!!groups.length && (
        <View style={styles.chips}>
          {groups.map(([g, ops]) => (
            <View key={g} style={[styles.capChip, { backgroundColor: `${GROUP_HEX[g]}22` }]}>
              <Text style={[styles.capChipText, { color: GROUP_HEX[g] }]} numberOfLines={1}>
                {GROUP_WORD[g]} · {ops.join(', ')}
              </Text>
            </View>
          ))}
        </View>
      )}

      {(row.secrets || []).map((secret) => (
        <SecretRow key={secret.key} base={base} name={row.name} secret={secret} onRows={onRows} />
      ))}
      {(row.config || []).map((field) => (
        <ConfigRow key={field.key} base={base} name={row.name} field={field} onRows={onRows} />
      ))}

      <MapRow base={base} name={row.name} cwd={cwd} mapped={mapped} onRows={onRows} />
    </View>
  );
}

// Settings › Integrations. Read-only until a secret is saved, a config field
// is blurred, or a resource is mapped -- every one of those hands back the
// fresh rows (see api.js) so this component never has to re-derive state.
//
// props:
//   base   string             -- api base url
//   cwd    string | undefined -- the checkout root a mapping is read/written
//                                 for; Settings.js's "about" cwd
export default function Integrations({ base, cwd }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    listIntegrations(base)
      .then((r) => { if (live) { setRows(r); setErr(''); } })
      // an older server 404s this route -- read as "nothing here yet", not a
      // crash, same as ClaudeSettings' own catch
      .catch((e) => { if (live) { setRows([]); setErr(String(e.message || e)); } })
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [base]);

  if (loading) return <ActivityIndicator color={C.faint} style={styles.spin} />;

  return (
    <View style={styles.wrap}>
      {!!err && !rows?.length && <Text style={styles.err}>couldn't read integrations: {err}</Text>}
      {(rows || []).map((row) => (
        <IntegrationCard key={row.name} base={base} cwd={cwd} row={row} onRows={setRows} />
      ))}

      <View style={styles.explainer}>
        <Text style={styles.explainerTitle}>ADD YOUR OWN</Text>
        <Text style={styles.explainerBody}>
          A folder in <Text style={mono}>~/.podium/integrations/&lt;name&gt;/</Text> with a{' '}
          <Text style={mono}>manifest.json</Text> and one executable that answers JSON on stdin.
          It declares what it can do; each capability lights up a place in the app.
        </Text>
        <Text style={[styles.explainerCode, mono]}>{MANIFEST_EXAMPLE}</Text>
        <View style={styles.explainerLegend}>
          <View style={styles.explainerRow}>
            <Text style={[styles.explainerKey, mono, { color: PAD_HEX[53] }]}>deploy.*</Text>
            <Text style={styles.explainerVal}>DEPLOY tab, the deploy:promote gate</Text>
          </View>
          <View style={styles.explainerRow}>
            <Text style={[styles.explainerKey, mono, { color: PAD_HEX[33] }]}>logs · metrics</Text>
            <Text style={styles.explainerVal}>TELEMETRY, later</Text>
          </View>
          <View style={styles.explainerRow}>
            <Text style={[styles.explainerKey, mono, { color: C.bad }]}>errors.*</Text>
            <Text style={styles.explainerVal}>TELEMETRY issues, later</Text>
          </View>
        </View>
        <Text style={styles.explainerNote}>
          Secrets are asked for once and stored in the Keychain; the executable gets them per
          call and the app never sees them again — the same bargain as the Anthropic key.
        </Text>
      </View>
    </View>
  );
}

const MANIFEST_EXAMPLE = `{
  "name": "vercel",
  "run": "vercel.py",
  "secrets": [{"key": "token", "label": "Access token"}],
  "capabilities": [
    "auth.check", "resources.list",
    "deploy.list", "deploy.get",
    "deploy.promote", "deploy.rollback", "deploy.cancel"
  ]
}`;

const styles = StyleSheet.create({
  wrap: { gap: 12 },
  spin: { margin: S.pad },
  spacer: { flex: 1 },
  err: { color: C.bad, fontSize: 12 },
  hint: { color: C.faint, fontSize: 11 },
  label: { color: C.dim, fontSize: 12, marginBottom: 2 },
  link: { color: C.accentText, fontSize: 12 },
  linkBad: { color: C.bad },
  linkKey: { paddingHorizontal: 4, paddingVertical: 2 },
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

  card: { borderWidth: 1, borderColor: C.line, borderRadius: S.radius, padding: 14, gap: 10 },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  badge: {
    width: 32, height: 32, borderRadius: 6, backgroundColor: C.raised,
    alignItems: 'center', justifyContent: 'center',
  },
  badgeText: { color: C.dim, fontSize: 11, fontWeight: '700' },
  cardTitles: { gap: 1, flexShrink: 1 },
  cardTitle: { color: C.text, fontSize: 15, fontWeight: '600' },
  cardSub: { color: C.faint, fontSize: 11, ...mono },
  statusChip: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  statusWord: { fontSize: 12, fontWeight: '600' },

  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  capChip: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10 },
  capChipText: { fontSize: 11 },

  secretBox: { gap: 6 },
  secretRow: { flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  secretInput: { flexGrow: 1, minWidth: 160 },
  saveBtn: { minWidth: 120 },

  mapBox: { gap: 8, marginTop: 2, borderTopWidth: 1, borderTopColor: C.line, paddingTop: 10 },
  mapRow: { flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  mapWho: { color: C.text, fontWeight: '600' },
  resList: { gap: 4, backgroundColor: C.raised, borderRadius: S.radius, padding: 8 },
  resRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6, paddingHorizontal: 4 },
  resName: { color: C.text, fontSize: 13, flexShrink: 1, flexGrow: 1 },
  resMeta: { color: C.faint, fontSize: 11 },

  explainer: { borderWidth: 1, borderColor: C.line, borderRadius: S.radius, padding: 14, gap: 10 },
  explainerTitle: { color: C.dim, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  explainerBody: { color: C.dim, fontSize: 13, lineHeight: 19 },
  explainerCode: { color: C.dim, fontSize: 11, lineHeight: 17, backgroundColor: C.raised, borderRadius: S.radius, padding: 10 },
  explainerLegend: { gap: 6 },
  explainerRow: { flexDirection: 'row', gap: 8 },
  explainerKey: { width: 96, fontSize: 11 },
  explainerVal: { color: C.faint, fontSize: 11, flexShrink: 1 },
  explainerNote: { color: C.faint, fontSize: 11, lineHeight: 16 },
});
