import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { getProviders, patchProviders, providerLogin, saveProviderKey, deleteProviderKey } from './api';
import PushButton from './PushButton';
import { C, S } from './theme';

// Agents run as Claude Code or Codex (the engines); each engine authenticates
// through one of four providers, each with its own credentials. This file is
// what used to be Settings.js's Claude section, split in two the same way the
// server's own store is split: Providers is the credentials, Engines is which
// provider + model each engine starts with. Nothing here duplicates that
// section's old logic -- ProviderCard below is ClaudeSettings' mode/key/login
// block, generalised from "the one Anthropic account" to "whichever of the
// four this card is".

const MODELS = ['', 'opus', 'sonnet', 'haiku'];
const EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max'];
// '' means "the engine's own default" on the wire in every chip row below --
// one word for it rather than a chip that would otherwise say nothing.
const CHIP_WORD = { '': 'default' };
const SANDBOXES = ['read-only', 'workspace-write', 'danger-full-access'];
const ENGINES = [['claude', 'Claude Code'], ['codex', 'Codex']];
// Which providers each engine can pick from -- Anthropic and OpenAI only
// authenticate their own maker's engine; Bedrock and Vertex are cloud
// accounts either engine can run through, Vertex support for Codex still
// unverified server-side (Facts, engines-spec.md) -- offered here regardless,
// since a save the server actually can't honour comes back as an inline
// error the same way a bad model id would, not a client-side guess.
const CLAUDE_PROVIDERS = [['anthropic', 'Anthropic'], ['bedrock', 'Bedrock'], ['vertex', 'Vertex']];
const CODEX_PROVIDERS = [['openai', 'OpenAI'], ['bedrock', 'Bedrock'], ['vertex', 'Vertex']];

// One GET, shared shape, read quietly: the /providers route may not exist
// yet on this server (it is new, same as /settings/claude once was), and
// that reads as a quiet error line here rather than a crash -- the same
// bargain every other settings panel in this app already makes.
function useProviders(base) {
  const [got, setGot] = useState(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    getProviders(base)
      .then((res) => { setGot(res); setErr(''); })
      .catch((e) => setErr(`couldn't read providers: ${e.message || e}`))
      .finally(() => setLoading(false));
  };

  useEffect(load, [base]);

  // Any subset of {providers: {...}, engines: {...}} -- one call, and the
  // same shape comes back, which becomes the new `got` so every chip and
  // field reflects what the server actually saved.
  const patch = (fields) =>
    patchProviders(base, fields)
      .then((next) => { setGot(next); setErr(''); return next; })
      .catch((e) => { setErr(`couldn't save: ${e.message || e}`); throw e; });

  return { got, err, loading, load, patch };
}

function Chips({ options, value, onPick, wordOf }) {
  return (
    <View style={styles.chips}>
      {options.map((opt) => {
        const key = Array.isArray(opt) ? opt[0] : opt;
        const word = Array.isArray(opt) ? opt[1] : (wordOf ? wordOf(opt) : opt);
        // [key, word, disabled]: an option the server says cannot work here is
        // shown with its reason as the word, but cannot be picked
        const off = Array.isArray(opt) && !!opt[2];
        return (
          <Text
            key={key || 'default'}
            accessibilityRole="button"
            accessibilityState={{ selected: value === key, disabled: off }}
            onPress={off ? undefined : () => onPick(key)}
            style={[styles.chip, value === key && styles.chipOn, off && styles.chipOff]}>
            {off ? word : (CHIP_WORD[key] ?? word)}
          </Text>
        );
      })}
    </View>
  );
}

// The credentials half of one provider card: a mode toggle (subscription/
// chatgpt vs api_key), the key box for api_key, and a "sign in on the Mac"
// button for the other -- exactly ClaudeSettings' old body, parameterised.
//
// provider   'anthropic' | 'openai'
// modes      [[key, word, hint], ...] -- 2 rows for these two providers
// data       providers[provider] from GET /providers, e.g. {mode, key}
// statusLine already-computed auth/login status text for this provider
function ProviderCard({ base, provider, title, modes, data, statusLine, patch, reload }) {
  const [keyField, setKeyField] = useState('');
  const [keyEditing, setKeyEditing] = useState(false);
  const [keyErr, setKeyErr] = useState('');
  const [keyBusy, setKeyBusy] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);

  const mode = data?.mode || modes[0][0];

  async function doLogin() {
    setLoginBusy(true);
    try {
      await providerLogin(base, provider);
      // opens Terminal.app on the Mac and runs there -- nothing to await, so
      // a few seconds and a re-read is the whole handshake
      setTimeout(reload, 4000);
    } catch (e) {
      setKeyErr(e.message || String(e));
    } finally {
      setLoginBusy(false);
    }
  }

  async function saveKey() {
    if (!keyField.trim()) return;
    setKeyBusy(true);
    setKeyErr('');
    try {
      await saveProviderKey(base, provider, keyField.trim());
      setKeyField('');   // never kept in state once it is sent
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
      await deleteProviderKey(base, provider);
    } catch (e) {
      setKeyErr(e.message || String(e));
    } finally {
      setKeyBusy(false);
    }
  }

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{title}</Text>
      {!!statusLine && <Text style={styles.statusText} numberOfLines={2}>{statusLine}</Text>}

      <View style={styles.chips}>
        {modes.map(([key, word]) => (
          <Text
            key={key}
            accessibilityRole="button"
            accessibilityState={{ selected: mode === key }}
            onPress={() => patch({ providers: { [provider]: { mode: key } } })}
            style={[styles.chip, mode === key && styles.chipOn]}>
            {word}
          </Text>
        ))}
      </View>
      <Text style={styles.hint}>{modes.find(([key]) => key === mode)?.[2] || ''}</Text>

      {mode === modes[0][0] && (
        <Pressable
          accessibilityRole="button"
          disabled={loginBusy}
          onPress={doLogin}
          style={styles.linkKey}>
          <Text style={styles.link}>{loginBusy ? 'opening Terminal…' : 'sign in on the Mac'}</Text>
        </Pressable>
      )}

      {mode === 'api_key' && (
        <View style={styles.keyBox}>
          {data?.key?.saved && !keyEditing ? (
            <View style={styles.keyRow}>
              <Text style={styles.hint} numberOfLines={1}>
                key saved · {data.key.hint || '…'}
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
                placeholder="sk-…"
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
    </View>
  );
}

// Bedrock and Vertex are not a mode toggle, just the cloud account's own
// coordinates -- a region plus whichever field names the account (an AWS
// profile, a GCP project). Both engines share these, unlike the key above.
function FieldCard({ provider, title, fields, data, patch }) {
  const [local, setLocal] = useState(() =>
    Object.fromEntries(fields.map(([k]) => [k, data?.[k] || '']))
  );
  useEffect(() => {
    setLocal(Object.fromEntries(fields.map(([k]) => [k, data?.[k] || ''])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{title}</Text>
      <View style={styles.fieldPair}>
        {fields.map(([key, placeholder]) => (
          <TextInput
            key={key}
            value={local[key]}
            onChangeText={(v) => setLocal((s) => ({ ...s, [key]: v }))}
            onBlur={() => patch({ providers: { [provider]: local } })}
            placeholder={placeholder}
            placeholderTextColor={C.faint}
            autoCapitalize="none"
            autoCorrect={false}
            style={[styles.input, styles.fieldHalf]}
          />
        ))}
      </View>
    </View>
  );
}

// The Providers section: one card per provider, credentials only. Engines
// (which provider each of Claude Code/Codex uses, model, effort, and
// Codex's sandbox/approval) is a separate section below, ProvidersSettings'
// sibling export -- the two are drawn as separate Settings tabs.
export function ProvidersSettings({ base }) {
  const { got, err, loading, load, patch } = useProviders(base);

  if (loading) return <ActivityIndicator color={C.faint} style={styles.spin} />;
  if (err) return <Text style={styles.err}>{err}</Text>;
  if (!got) return null;

  const providers = got.providers || {};
  const auth = got.claude?.auth || {};
  const anthropicStatus = auth.error
    ? auth.error
    : auth.loggedIn
    ? `signed in as ${auth.email || 'unknown'}${auth.subscriptionType ? ` · ${auth.subscriptionType} plan` : ''}`
    : 'not signed in';

  const codex = got.codex || {};
  const codexInstall = codex.installed
    ? `Codex CLI ${codex.version || ''}`.trim()
    : 'Codex CLI not found on this Mac';
  const openaiStatus = `${codexInstall} · ${codex.logged_in ? 'signed in' : 'not signed in'}`;

  return (
    <View style={styles.section}>
      <ProviderCard
        base={base}
        provider="anthropic"
        title="Anthropic"
        modes={[
          ['subscription', 'subscription', 'your claude.ai login'],
          ['api_key', 'API key', 'billed to the key, stored in the Keychain'],
        ]}
        data={providers.anthropic}
        statusLine={anthropicStatus}
        patch={patch}
        reload={load}
      />
      <ProviderCard
        base={base}
        provider="openai"
        title="OpenAI"
        modes={[
          ['chatgpt', 'ChatGPT', 'your ChatGPT login'],
          ['api_key', 'API key', 'billed to the key, stored in the Keychain'],
        ]}
        data={providers.openai}
        statusLine={openaiStatus}
        patch={patch}
        reload={load}
      />
      <FieldCard
        provider="bedrock"
        title="AWS Bedrock"
        fields={[['region', 'region'], ['profile', 'AWS profile']]}
        data={providers.bedrock}
        patch={patch}
      />
      <FieldCard
        provider="vertex"
        title="Google Vertex"
        fields={[['region', 'region'], ['project', 'GCP project']]}
        data={providers.vertex}
        patch={patch}
      />
      <Text style={styles.note}>credentials -- which provider each engine uses is in Engines</Text>
    </View>
  );
}

// One engine's own card: provider picker (limited to what it supports),
// model chips + a free id, effort, and (Codex only) sandbox + approval.
function EngineCard({ title, engineKey, providerOptions, data, patch, extra }) {
  const [modelField, setModelField] = useState(data?.model || '');
  const [effortField, setEffortField] = useState(data?.effort || '');
  const [approvalField, setApprovalField] = useState(data?.approval || '');
  useEffect(() => { setModelField(data?.model || ''); }, [data?.model]);
  useEffect(() => { setEffortField(data?.effort || ''); }, [data?.effort]);
  useEffect(() => { setApprovalField(data?.approval || ''); }, [data?.approval]);

  const patchEngine = (fields) => patch({ engines: { [engineKey]: fields } });

  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{title}</Text>
      {extra}

      <Text style={styles.label}>provider</Text>
      <Chips
        options={providerOptions}
        value={data?.provider}
        onPick={(provider) => patchEngine({ provider })}
      />

      <Text style={styles.label}>model</Text>
      {/* Claude Code's short names (opus/sonnet/haiku) are chips because
          there are only three of them; Codex has no such fixed, verified
          list (Facts, engines-spec.md) so it gets the free-id field alone
          rather than three names that would just be wrong. */}
      {engineKey === 'claude' && (
        <Chips options={MODELS} value={data?.model} onPick={(model) => patchEngine({ model })} />
      )}
      <TextInput
        value={modelField}
        onChangeText={setModelField}
        onBlur={() => modelField.trim() !== (data?.model || '') && patchEngine({ model: modelField.trim() })}
        onSubmitEditing={() => patchEngine({ model: modelField.trim() })}
        placeholder={engineKey === 'claude' ? 'or a full model id' : 'model id, e.g. gpt-5-codex'}
        placeholderTextColor={C.faint}
        autoCapitalize="none"
        autoCorrect={false}
        style={styles.input}
      />

      <Text style={styles.label}>effort</Text>
      {/* Claude Code's effort values are the fixed, verified set `--effort`
          takes. Codex's `-c model_reasoning_effort=…` key is flagged
          unverified in the spec, and low/medium/high/xhigh/max is Claude's
          vocabulary, not confirmed for Codex -- a free field says nothing
          untrue, chips borrowed from the other engine would. */}
      {engineKey === 'claude' ? (
        <Chips options={EFFORTS} value={data?.effort} onPick={(effort) => patchEngine({ effort })} />
      ) : (
        <TextInput
          value={effortField}
          onChangeText={setEffortField}
          onBlur={() => effortField.trim() !== (data?.effort || '') && patchEngine({ effort: effortField.trim() })}
          onSubmitEditing={() => patchEngine({ effort: effortField.trim() })}
          placeholder="reasoning effort, e.g. medium"
          placeholderTextColor={C.faint}
          autoCapitalize="none"
          autoCorrect={false}
          style={styles.input}
        />
      )}

      {engineKey === 'codex' && (
        <>
          <Text style={styles.label}>sandbox</Text>
          <Chips
            options={SANDBOXES}
            value={data?.sandbox}
            onPick={(sandbox) => patchEngine({ sandbox })}
            wordOf={(s) => s}
          />
          <Text style={styles.label}>approval</Text>
          <TextInput
            value={approvalField}
            onChangeText={setApprovalField}
            onBlur={() => approvalField.trim() !== (data?.approval || '') && patchEngine({ approval: approvalField.trim() })}
            onSubmitEditing={() => patchEngine({ approval: approvalField.trim() })}
            placeholder="on-request"
            placeholderTextColor={C.faint}
            autoCapitalize="none"
            autoCorrect={false}
            style={styles.input}
          />
        </>
      )}
    </View>
  );
}

// The Engines section: which provider + model each of Claude Code and Codex
// starts with, and which one a bare "new agent" defaults to.
export function EnginesSettings({ base }) {
  const { got, err, loading, patch } = useProviders(base);

  if (loading) return <ActivityIndicator color={C.faint} style={styles.spin} />;
  if (err) return <Text style={styles.err}>{err}</Text>;
  if (!got) return null;

  const engines = got.engines || {};
  const codex = got.codex || {};
  const codexNote = codex.installed
    ? `installed${codex.version ? ` · ${codex.version}` : ''} · ${codex.logged_in ? 'signed in' : 'not signed in'}`
    : 'not found on this Mac -- install the Codex CLI to use it';

  return (
    <View style={styles.section}>
      <Text style={styles.label}>default engine</Text>
      <Chips
        options={ENGINES}
        value={engines.default}
        onPick={(key) => patch({ engines: { default: key } })}
      />
      <Text style={styles.hint}>which engine a bare "new agent" starts</Text>

      <EngineCard
        title="Claude Code"
        engineKey="claude"
        providerOptions={CLAUDE_PROVIDERS}
        data={engines.claude}
        patch={patch}
      />
      <EngineCard
        title="Codex"
        engineKey="codex"
        // Codex speaks only the Responses API; Vertex's OpenAI-compatible
        // endpoint documents chat completions only -- the server says so
        providerOptions={engines.codex?.vertex_supported === false
          ? CODEX_PROVIDERS.map((o) => (o[0] === 'vertex' ? ['vertex', 'Vertex · not supported by Codex yet', true] : o))
          : CODEX_PROVIDERS}
        data={engines.codex}
        patch={patch}
        extra={<Text style={styles.hint}>{codexNote}</Text>}
      />
      <Text style={styles.note}>applies to agents started from now on</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 12 },
  card: { gap: 8, padding: S.pad, borderWidth: 1, borderColor: C.line, borderRadius: S.radius },
  cardTitle: { color: C.text, fontSize: 14, fontWeight: '600' },
  spin: { margin: S.pad },
  statusText: { color: C.text, fontSize: 13 },
  label: { color: C.dim, fontSize: 12, marginTop: 6 },
  hint: { color: C.faint, fontSize: 11 },
  note: { color: C.faint, fontSize: 11, marginTop: 4 },
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
  chipOff: { opacity: 0.4 },
  linkKey: { paddingHorizontal: 4, paddingVertical: 2, alignSelf: 'flex-start' },
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
