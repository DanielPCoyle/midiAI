import { Fragment, useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { getMemoryEntry, memoryStats, recentMemory, searchMemory } from './api';
import { C, PAD_HEX, S, mono } from './theme';

const DEBOUNCE_MS = 300;

// A subtle, distinct hue per kind -- borrowed from the pad palette so a
// memory row reads as one more thing this app already colours, not a new
// scheme of its own.
const KIND_HEX = {
  summary: PAD_HEX[21],      // green
  observation: PAD_HEX[45],  // blue
  prompt: PAD_HEX[60],       // orange
  reply: PAD_HEX[49],        // indigo
  tool: C.faint,
};

// [ and ] mark a search hit inside a snippet -- drawn as accent text, not as
// literal brackets, so a match reads like a highlight rather than a typo.
const MARK = /\[([^\]]*)\]/g;
function Highlighted({ text, style, numberOfLines }) {
  const parts = String(text || '').split(MARK);
  return (
    <Text style={style} numberOfLines={numberOfLines}>
      {parts.map((part, i) => (i % 2 === 1 ? (
        <Text key={i} style={styles.mark}>{part}</Text>
      ) : part))}
    </Text>
  );
}

// "3h ago", "2d ago" -- coarse on purpose; a memory panel is for "roughly
// when", not a timestamp you'd need to read twice.
function agoText(iso) {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return '';
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

const firstLines = (text, n) => String(text || '').split('\n').slice(0, n).join('\n');

// The memory tab: search what an agent decided, tried and learned, scoped to
// this checkout or to every project. Read-only -- everything here is GET.
export function MemoryPanel({ base, cwd }) {
  const [q, setQ] = useState('');
  const [scope, setScope] = useState(cwd ? 'project' : 'everywhere');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [openId, setOpenId] = useState(null);
  const [entries, setEntries] = useState({});   // id -> { loading, err, item }
  const [stats, setStats] = useState(null);
  const reqId = useRef(0);
  const effCwd = scope === 'project' ? cwd : '';

  // Debounced fetch; a query that changes again cancels the timer outright,
  // and a request that lands after a newer one has already answered is
  // dropped by id rather than allowed to overwrite it.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr('');
    const timer = setTimeout(() => {
      const id = ++reqId.current;
      const call = q.trim() ? searchMemory(base, q, effCwd) : recentMemory(base, effCwd);
      call
        .then((d) => {
          if (cancelled || reqId.current !== id) return;
          setItems(d.items || []);
          setLoading(false);
        })
        .catch((e) => {
          if (cancelled || reqId.current !== id) return;
          setItems([]);
          setErr(String((e && e.message) || e));
          setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [base, q, effCwd]);

  useEffect(() => {
    let live = true;
    memoryStats(base).then((d) => live && setStats(d)).catch(() => {});
    return () => { live = false; };
  }, [base]);

  const toggleRow = (id) => {
    if (openId === id) {
      setOpenId(null);
      return;
    }
    setOpenId(id);
    if (entries[id]) return;
    setEntries((e) => ({ ...e, [id]: { loading: true } }));
    getMemoryEntry(base, id)
      .then((d) => setEntries((e) => ({ ...e, [id]: { loading: false, item: d.item } })))
      .catch((err2) =>
        setEntries((e) => ({ ...e, [id]: { loading: false, err: String((err2 && err2.message) || err2) } }))
      );
  };

  const total = stats ? Object.values(stats.by_kind || {}).reduce((a, b) => a + b, 0) : null;

  return (
    <Fragment>
      <View style={styles.headCol}>
        <View style={styles.scopeTabs}>
          {[['project', 'this project'], ['everywhere', 'everywhere']].map(([key, label]) => (
            <Pressable
              key={key}
              accessibilityRole="tab"
              accessibilityState={{ selected: scope === key }}
              onPress={() => setScope(key)}>
              <Text style={[styles.scopeTab, scope === key && styles.scopeTabOn]}>{label}</Text>
            </Pressable>
          ))}
        </View>
        <TextInput
          value={q}
          onChangeText={setQ}
          autoCapitalize="none"
          autoCorrect={false}
          clearButtonMode="while-editing"
          style={styles.find}
          placeholder="search memory — what was decided, tried, learned"
          placeholderTextColor={C.faint}
        />
      </View>

      <ScrollView contentContainerStyle={styles.list}>
        {!!err && <Text style={styles.err}>{err}</Text>}
        {!err && loading && !items.length && (
          <Text style={styles.none}>{q.trim() ? 'searching memory…' : 'loading recent memory…'}</Text>
        )}
        {!err && !loading && !items.length && (
          <Text style={styles.none}>
            {q.trim() ? `nothing matches “${q.trim()}”` : 'nothing remembered yet'}
          </Text>
        )}
        {items.map((it) => {
          const preview = it.snippet != null ? it.snippet : firstLines(it.body, 3);
          const meta = [
            agoText(it.ts),
            scope === 'everywhere' ? it.project : null,
            it.source === 'claude-mem' ? 'claude-mem' : null,
          ].filter(Boolean).join(' · ');
          const open = openId === it.id;
          const entry = entries[it.id];
          return (
            <Pressable
              key={it.id}
              accessibilityRole="button"
              accessibilityLabel={`${it.kind}: ${it.title}`}
              onPress={() => toggleRow(it.id)}
              style={styles.row}>
              <Text style={[styles.kindTag, { color: KIND_HEX[it.kind] || C.faint }]}>{it.kind}</Text>
              <Text style={styles.title} numberOfLines={2}>{it.title}</Text>
              {!!meta && <Text style={styles.meta}>{meta}</Text>}
              {!!preview && (
                <Highlighted text={preview} style={styles.preview} numberOfLines={open ? undefined : 3} />
              )}
              {open && (
                <View style={styles.body}>
                  {!entry || entry.loading ? (
                    <Text style={styles.none}>loading…</Text>
                  ) : entry.err ? (
                    <Text style={styles.err}>{entry.err}</Text>
                  ) : (
                    <Text selectable style={styles.bodyText}>{entry.item?.body || ''}</Text>
                  )}
                </View>
              )}
            </Pressable>
          );
        })}
      </ScrollView>

      {!!stats && (
        <Text style={styles.footer}>
          {total} entries · {stats.sessions} sessions
        </Text>
      )}
    </Fragment>
  );
}

const styles = StyleSheet.create({
  headCol: {
    paddingHorizontal: 12,
    paddingTop: S.pad,
    paddingBottom: 10,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: C.line,
  },
  scopeTabs: { flexDirection: 'row', gap: 14 },
  scopeTab: { color: C.faint, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 },
  scopeTabOn: { color: C.accentText, fontWeight: '600' },
  find: {
    height: 32,
    color: C.text,
    backgroundColor: C.panel,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 6,
    paddingHorizontal: 10,
    fontSize: 13,
  },
  list: { padding: 12, gap: 8 },
  none: { color: C.faint, fontSize: 12, lineHeight: 18 },
  err: { color: C.bad, fontSize: 12 },
  row: {
    gap: 4,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    padding: 8,
    backgroundColor: C.panel,
  },
  kindTag: { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  title: { color: C.text, fontSize: 13, fontWeight: '600' },
  meta: { color: C.faint, fontSize: 10, ...mono },
  preview: { color: C.dim, fontSize: 12, lineHeight: 17 },
  mark: { color: C.accentText, fontWeight: '700' },
  body: { marginTop: 4, paddingTop: 8, borderTopWidth: 1, borderTopColor: C.line },
  bodyText: { color: C.text, fontSize: 13, lineHeight: 19 },
  footer: {
    color: C.faint,
    fontSize: 10,
    textAlign: 'center',
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: C.line,
  },
});
