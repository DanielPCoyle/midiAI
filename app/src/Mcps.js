import { useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { listMcps, mcpDo, mcpHealth } from './api';
import { C, S } from './theme';

// Moved out of Rail.js verbatim -- the MCP manager used to be the rail's
// second tab, and this file is that tab's whole brain, now rendered inside
// Settings.js instead. Nothing about scope, health or the actions changed;
// only where it lives did.
const MCP_SCOPE = {
  local: 'private to this project',
  project: 'shared by this project',
  user: 'available everywhere',
  plugin: 'provided by a plugin',
};
// Four words `claude mcp list` prints, three states worth telling apart, and
// each has a different next move: up is nothing to do, auth is one press from
// working, down is a reason to read. Grey is "not asked yet" -- a server we
// have not checked must not draw as one that failed.
const MCP_HEX = { up: '#3cd05a', auth: '#e0a02c', down: '#e03c3c' };
const MCP_SAID = { up: 'connected', auth: 'needs authentication', down: 'offline' };

const MCP_TABS = [
  ['global', 'global', (mcp) => mcp.scope === 'user' || mcp.scope === 'plugin'],
  ['project', 'project', (mcp) => mcp.scope === 'project'],
  ['local', 'project local', (mcp) => mcp.scope === 'local'],
];

// The rail's footer keeps only the down-count now that the panel itself has
// moved into Settings -- this is that light half: a list read and a health
// poll, nothing interactive. McpManager below runs its own, richer version of
// the same two effects (faster health poll, the add/login/remove actions),
// because it also drives the panel a person is actually looking at.
export function useMcpDown(base, cwds) {
  const [mcps, setMcps] = useState([]);
  const [health, setHealth] = useState({ rows: {} });
  const cwdKey = cwds.join('\u0000');

  useEffect(() => {
    const list = cwds.length ? cwds : [''];
    let live = true;
    Promise.all(list.map((cwd) => listMcps(base, cwd).then((rows) => ({ cwd, rows }))))
      .then((sets) => {
        if (!live) return;
        const byKey = new Map();
        for (const { rows } of sets)
          for (const row of rows) byKey.set(`${row.scope}\u0000${row.name}`, row);
        setMcps([...byKey.values()]);
      })
      .catch(() => live && setMcps([]));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, cwdKey]);

  const healthCwd = cwds[0] || '';
  useEffect(() => {
    if (!base) return undefined;
    let live = true;
    const ask = () =>
      mcpHealth(base, healthCwd).then((got) => live && setHealth(got)).catch(() => {});
    ask();
    // a settled count is worth a slow poll, not a fast one -- the fast poll
    // is McpManager's own, and only while that panel is actually open
    const timer = setInterval(ask, 30000);
    return () => { live = false; clearInterval(timer); };
  }, [base, healthCwd]);

  // a zero is a settled fact, not drawn -- the footer that reads this decides
  // that part (MIDI-014: a faint "0" and a faint "3" read the same at a glance)
  return mcps.filter((mcp) => health.rows?.[mcp.name]?.state === 'down').length;
}

// The full manager: scope tabs, the list with health + actions, and the
// install form. `cwds` is every working directory worth asking about --
// Settings.js passes the one place it knows about (`cwd ? [cwd] : []`), the
// same shape the rail used to build from every live agent's cwd.
//
// props:
//   base   string    -- api base url
//   cwds   string[]  -- working directories to read MCPs for; [] asks once,
//                        user-wide, the same as an empty cwd always has
export function McpManager({ base, cwds }) {
  const [mcps, setMcps] = useState([]);
  const [mcpsBusy, setMcpsBusy] = useState(false);
  const [health, setHealth] = useState({ rows: {}, checking: false });
  const [open, setOpen] = useState('');      // the MCP whose actions are showing
  const [busy, setBusy] = useState('');
  const [said, setSaid] = useState('');
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: '', scope: 'local', transport: 'stdio', target: '' });
  const [mcpTab, setMcpTab] = useState('global');
  const [q, setQ] = useState('');
  // Nothing tells the list an add/login/remove changed it -- every action
  // bumps this, and the fetch effect below re-asks whenever it does. Rail's
  // own `beat` served the same purpose for the tree; this is this panel's.
  const [beat, setBeat] = useState(0);

  const find = q.trim().toLowerCase();
  const hitMcp = (mcp) =>
    !find ||
    `${mcp.name} ${mcp.scope} ${mcp.transport || ''} ${mcp.endpoint || ''}`
      .toLowerCase()
      .includes(find);
  const cwdKey = cwds.join('\u0000');

  useEffect(() => {
    const list = cwds.length ? cwds : [''];
    let live = true;
    setMcpsBusy(true);
    Promise.all(list.map((cwd) => listMcps(base, cwd).then((rows) => ({ cwd, rows }))))
      .then((sets) => {
        if (!live) return;
        const byKey = new Map();
        for (const { cwd, rows } of sets)
          for (const row of rows) {
            const key = `${row.scope}\u0000${row.name}\u0000${row.transport}\u0000${row.endpoint}`;
            const old = byKey.get(key);
            byKey.set(key, old ? { ...old, cwds: [...old.cwds, cwd] } : { ...row, cwds: [cwd] });
          }
        setMcps([...byKey.values()].sort((a, b) => a.name.localeCompare(b.name)));
      })
      .catch(() => live && setMcps([]))
      .finally(() => live && setMcpsBusy(false));
    return () => { live = false; };
  }, [cwdKey, base, beat]);

  // The health check is nine seconds and spawns every stdio server, so it is
  // asked for on the cadence this panel is actually open on -- the rail's own
  // slower poll (useMcpDown, above) is what feeds the footer's count the rest
  // of the time.
  const healthCwd = cwds[0] || '';
  useEffect(() => {
    if (!base) return undefined;
    let live = true;
    const ask = () =>
      mcpHealth(base, healthCwd).then((got) => live && setHealth(got)).catch(() => {});
    ask();
    const timer = setInterval(ask, 4000);
    return () => { live = false; clearInterval(timer); };
  }, [base, healthCwd]);

  const act = async (verb, mcp, extra = {}) => {
    setBusy(`${verb}:${mcp?.name || 'new'}`);
    setSaid('');
    try {
      const answer = await mcpDo(base, verb, {
        cwd: healthCwd, name: mcp?.name, scope: mcp?.scope, ...extra });
      setSaid(String(answer || 'done').slice(0, 160));
      if (verb === 'add') { setAdding(false); setDraft({ ...draft, name: '', target: '' }); }
      setBeat((b) => b + 1);      // the list effect already reruns on this
      mcpHealth(base, healthCwd).then(setHealth).catch(() => {});
    } catch (e) { setSaid(e.message.slice(0, 200)); }
    finally { setBusy(''); }
  };

  const mcpGroups = MCP_TABS.map(([key, label, match]) => ({
    key, label, items: mcps.filter((mcp) => match(mcp) && hitMcp(mcp)),
  }));
  const activeMcpGroup = mcpGroups.find((group) => group.key === mcpTab) || mcpGroups[0];

  return (
    <View style={styles.wrap}>
      <TextInput
        value={q}
        onChangeText={setQ}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
        accessibilityLabel="search MCPs"
        placeholder="search MCPs"
        placeholderTextColor={C.faint}
        style={styles.find}
      />
      <View style={styles.mcpTabs}>
        {mcpGroups.map((group) => (
          <Text
            key={group.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: activeMcpGroup.key === group.key }}
            onPress={() => setMcpTab(group.key)}
            style={[styles.mcpTab, activeMcpGroup.key === group.key && styles.mcpTabOn]}>
            {group.label} · {group.items.length}
          </Text>
        ))}
      </View>
      <View style={styles.list}>
        {mcpsBusy && <Text style={styles.empty}>loading MCPs…</Text>}
        {!mcpsBusy && activeMcpGroup.items.map((mcp) => (
          <Pressable
            key={`${mcp.scope}-${mcp.name}-${mcp.endpoint}`}
            accessibilityRole="button"
            accessibilityState={{ expanded: open === mcp.name }}
            onPress={() => { setOpen(open === mcp.name ? '' : mcp.name); setSaid(''); }}
            style={styles.mcpCard}>
            <View style={styles.row}>
              {/* grey is "not asked yet", never "failed" -- the check takes
                  nine seconds and a pessimistic dot in the meantime would
                  report an outage that has not happened */}
              <View
                style={[styles.mcpDot, {
                  backgroundColor: MCP_HEX[health.rows?.[mcp.name]?.state] || 'transparent',
                  borderWidth: health.rows?.[mcp.name] ? 0 : 1,
                }]}
              />
              <Text style={styles.name} numberOfLines={1}>{mcp.name}</Text>
              <Text style={styles.mcpScope}>{mcp.scope}</Text>
            </View>
            <Text style={styles.meta} numberOfLines={1}>
              {mcp.transport}{mcp.endpoint ? ` · ${mcp.endpoint}` : ''}
            </Text>
            {health.rows?.[mcp.name] ? (
              <Text
                numberOfLines={2}
                style={[styles.mcpWhere, { color: MCP_HEX[health.rows[mcp.name].state] }]}>
                {/* what the server itself said, not our word for it:
                    "CONNECTION_CLOSED" is the thing you can act on */}
                {health.rows[mcp.name].state === 'up'
                  ? MCP_SAID.up
                  : health.rows[mcp.name].said || MCP_SAID[health.rows[mcp.name].state]}
              </Text>
            ) : (
              <Text style={styles.mcpWhere} numberOfLines={2}>
                {health.checking ? 'checking…' : MCP_SCOPE[mcp.scope] || mcp.scope}
              </Text>
            )}
            {open === mcp.name && (
              <View style={styles.mcpKeys}>
                {/* the one that is one press from working comes first, and
                    only when that is actually the state it is in */}
                {health.rows?.[mcp.name]?.state !== 'up' && (
                  <Pressable
                    accessibilityRole="button"
                    disabled={!!busy}
                    onPress={() => act('login', mcp)}
                    style={[styles.mcpKey, styles.mcpKeyOn]}>
                    <Text style={styles.mcpKeyText}>
                      {busy === `login:${mcp.name}` ? '…' : 'Authorise'}
                    </Text>
                  </Pressable>
                )}
                {health.rows?.[mcp.name]?.state === 'up' && (
                  <Pressable
                    accessibilityRole="button"
                    disabled={!!busy}
                    onPress={() => act('login', mcp)}
                    style={styles.mcpKey}>
                    <Text style={styles.mcpKeyText}>Reauth</Text>
                  </Pressable>
                )}
                <Pressable
                  accessibilityRole="button"
                  disabled={!!busy}
                  onPress={() => act('logout', mcp)}
                  style={styles.mcpKey}>
                  <Text style={styles.mcpKeyText}>Sign out</Text>
                </Pressable>
                {mcp.scope === 'project' && (
                  <Pressable
                    accessibilityRole="button"
                    disabled={!!busy}
                    onPress={() => act('disable', mcp)}
                    style={styles.mcpKey}>
                    <Text style={styles.mcpKeyText}>Disable</Text>
                  </Pressable>
                )}
                {mcp.scope !== 'plugin' && (
                  <Pressable
                    accessibilityRole="button"
                    disabled={!!busy}
                    onPress={() => act('remove', mcp)}
                    style={styles.mcpKey}>
                    <Text style={[styles.mcpKeyText, { color: C.bad }]}>Uninstall</Text>
                  </Pressable>
                )}
                {mcp.scope === 'plugin' && (
                  <Text style={styles.mcpWhere}>
                    installed by a plugin — remove it with the plugin
                  </Text>
                )}
                {!!said && <Text style={styles.mcpSaid}>{said}</Text>}
              </View>
            )}
          </Pressable>
        ))}
        {!mcpsBusy && activeMcpGroup.items.length === 0 && (
          <Text style={styles.empty}>
            {find
              ? `no ${activeMcpGroup.label} MCPs match “${q.trim()}”`
              : `no ${activeMcpGroup.label} MCPs configured`}
          </Text>
        )}
      </View>

      {/* Installing one is what you came here to do when the list has not
          got it, so it stays reachable under whatever the list is holding
          rather than needing a scroll first. */}
      {adding ? (
        <View style={styles.mcpAdd}>
          <TextInput
            value={draft.name}
            onChangeText={(v) => setDraft({ ...draft, name: v })}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="name"
            placeholderTextColor={C.faint}
            style={styles.find}
          />
          <View style={styles.mcpKeys}>
            {['stdio', 'http', 'sse'].map((t) => (
              <Pressable
                key={t}
                onPress={() => setDraft({ ...draft, transport: t })}
                style={[styles.mcpKey, draft.transport === t && styles.mcpKeyOn]}>
                <Text style={styles.mcpKeyText}>{t}</Text>
              </Pressable>
            ))}
          </View>
          <View style={styles.mcpKeys}>
            {['local', 'project', 'user'].map((sc) => (
              <Pressable
                key={sc}
                onPress={() => setDraft({ ...draft, scope: sc })}
                style={[styles.mcpKey, draft.scope === sc && styles.mcpKeyOn]}>
                <Text style={styles.mcpKeyText}>{sc}</Text>
              </Pressable>
            ))}
          </View>
          <TextInput
            value={draft.target}
            onChangeText={(v) => setDraft({ ...draft, target: v })}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder={draft.transport === 'stdio' ? 'command' : 'https://…'}
            placeholderTextColor={C.faint}
            style={styles.find}
          />
          <Text style={styles.mcpWhere}>
            {MCP_SCOPE[draft.scope]}
          </Text>
          <View style={styles.mcpKeys}>
            <Pressable
              accessibilityRole="button"
              disabled={!draft.name.trim() || !draft.target.trim() || !!busy}
              onPress={() => act('add', null, { ...draft, name: draft.name.trim(),
                                                target: draft.target.trim() })}
              style={[styles.mcpKey, styles.mcpKeyOn]}>
              <Text style={styles.mcpKeyText}>{busy ? '…' : 'Install'}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => setAdding(false)}
              style={styles.mcpKey}>
              <Text style={styles.mcpKeyText}>Cancel</Text>
            </Pressable>
          </View>
          {!!said && <Text style={styles.mcpSaid}>{said}</Text>}
        </View>
      ) : (
        <Pressable accessibilityRole="button" onPress={() => { setAdding(true); setSaid(''); }}
          style={styles.mcpKey}>
          <Text style={styles.mcpKeyText}>＋ install an MCP</Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: S.gap },
  find: {
    height: 30,
    color: C.text,
    backgroundColor: C.panel,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 6,
    paddingHorizontal: 9,
    fontSize: 12,
  },
  list: { gap: S.gap },
  row: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  name: { color: C.text, fontSize: 13, fontWeight: '600', flexShrink: 1 },
  meta: { color: C.faint, fontSize: 11, flexShrink: 1 },
  empty: { color: C.edge, fontSize: 11, paddingHorizontal: 4, paddingVertical: 8 },
  mcpCard: { borderWidth: 1, borderColor: C.line, borderRadius: S.radius, padding: 12, gap: 6 },
  mcpTabs: { flexDirection: 'row', flexWrap: 'wrap', gap: 9, paddingHorizontal: 4 },
  mcpTab: { color: C.faint, fontSize: 10, paddingBottom: 3 },
  mcpTabOn: { color: C.text, borderBottomWidth: 1, borderBottomColor: C.accentText },
  mcpScope: { color: C.faint, fontSize: 10, marginLeft: 'auto', textTransform: 'uppercase' },
  mcpWhere: { color: C.edge, fontSize: 10 },
  mcpDot: { width: 8, height: 8, borderRadius: 4, borderColor: C.edge },
  mcpKeys: { flexDirection: 'row', flexWrap: 'wrap', gap: 5, alignItems: 'center' },
  mcpKey: { paddingHorizontal: 8, paddingVertical: 5, borderWidth: 1, borderColor: C.edge, borderRadius: 5 },
  mcpKeyOn: { borderColor: C.accentText },
  mcpKeyText: { color: C.dim, fontSize: 10, fontWeight: '600' },
  mcpSaid: { color: C.faint, fontSize: 10, flexBasis: '100%', lineHeight: 15 },
  mcpAdd: { borderWidth: 1, borderColor: C.accent, borderRadius: S.radius, padding: 10, gap: 7 },
});
