import { useEffect, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
} from 'react-native';
import { C, PALETTE, hexFor, S } from './theme';

// Only these two. Tool and message changes leave the cache alone, so linting
// anything else here would be noise on a pad that costs nothing to press.
const CACHE_DROPPER = /\/(effort|model)\b/;

export default function Inspector({ index, pad, labels, promptRoot, promptPath, defaultScope = 'global', onSave, onClear, onAddLabel, onDelLabel }) {
  const [label, setLabel] = useState('');
  const [prefix, setPrefix] = useState('');
  const [text, setText] = useState('');
  const [fields, setFields] = useState([]);
  const [tag, setTag] = useState('');
  const [scope, setScope] = useState('global');
  const [newName, setNewName] = useState('');
  const [newColour, setNewColour] = useState(PALETTE[0][1]);

  // Re-seed on a new pad selection only -- not on every render, or the
  // user's own keystrokes would get clobbered mid-edit.
  useEffect(() => {
    setLabel(pad?.label || '');
    const savedPrefix = pad?.prefix || '';
    setPrefix(savedPrefix);
    setText(pad?.dynamic || (savedPrefix && (pad?.text || '').startsWith(savedPrefix)
      ? (pad.text || '').slice(savedPrefix.length).trimStart()
      : pad?.text || ''));
    setFields(Array.isArray(pad?.fields) ? pad.fields : []);
    setTag(pad?.tag || '');
    setScope(pad?.scope || defaultScope);
  }, [index, defaultScope]);

  if (index === null) {
    return (
      <View style={styles.panel}>
        <Text style={styles.empty}>pick a pad</Text>
      </View>
    );
  }

  const save = () => {
    const trimmed = text.trim();
    const staticPrefix = prefix.trim();
    const prompt = [staticPrefix, trimmed].filter(Boolean).join('\n\n');
    if (!prompt) {
      onClear();
      return;
    }
    const lab = labels.find((l) => l.name === tag);
    onSave({
      label: label.trim() || (trimmed || staticPrefix).split(/\s+/).slice(0, 2).join(' '),
      text: prompt,
      prefix: staticPrefix,
      dynamic: trimmed,
      fields: fields.filter((field, i, all) =>
        field.name && all.findIndex((item) => item.name === field.name) === i),
      tag: tag || null,
      colour: lab ? lab.colour : 125,
      // Hardware-only behavior: the app neither exposes nor changes it.
      submit: !!pad?.submit,
      scope,
      project: scope === 'project' ? promptRoot : '',
      worktree: scope === 'local' ? promptPath : '',
    });
  };
  const prefixTokens = Math.ceil(prefix.trim().length / 4);
  const cacheReady = prefixTokens >= 1024;

  const addLabel = () => {
    const name = newName.trim();
    if (!name) return;
    onAddLabel({ name, colour: newColour });
    setNewName('');
  };

  return (
    <ScrollView style={styles.panel} contentContainerStyle={styles.content}>
      <Text style={styles.label}>Label <Text style={styles.hint}>(shown on the Push screen)</Text></Text>
      <TextInput
        style={styles.input}
        value={label}
        onChangeText={setLabel}
        maxLength={24}
        placeholder="auto from text"
        placeholderTextColor={C.faint}
      />

      <View style={styles.cacheIntro}>
        <Text style={styles.cacheTitle}>Cacheable prefix</Text>
        <Text style={styles.hint}>
          Put stable instructions, examples, and long reference material first. Reusing this exact beginning lets the model skip recalculating it.
        </Text>
        <View style={styles.cacheMeterRow}>
          <View style={styles.cacheTrack}>
            <View
              style={[
                styles.cacheFill,
                { width: `${Math.min(100, prefixTokens / 10.24)}%` },
                cacheReady && styles.cacheFillReady,
              ]}
            />
          </View>
          <Text style={[styles.cacheCount, cacheReady && styles.cacheCountReady]}>
            ≈ {prefixTokens.toLocaleString()} / 1,024 tokens
          </Text>
        </View>
        <Text style={styles.cacheStatus}>
          {cacheReady
            ? 'Prefix is large enough for caching on supported models.'
            : 'Caching usually begins once the repeated prefix reaches 1,024 tokens.'}
        </Text>
      </View>
      <TextInput
        style={[styles.input, styles.prefixArea]}
        value={prefix}
        onChangeText={setPrefix}
        multiline
        placeholder="Stable instructions, examples, or documents"
        placeholderTextColor={C.faint}
        autoCapitalize="none"
        autoCorrect={false}
      />

      <Text style={styles.label}>Dynamic form <Text style={styles.hint}>(values are requested when this prompt runs)</Text></Text>
      {fields.map((field, i) => (
        <View key={i} style={styles.fieldCard}>
          <TextInput
            style={[styles.input, styles.fieldInput]}
            value={field.label}
            onChangeText={(value) => setFields((all) => all.map((item, k) => k === i ? { ...item, label: value } : item))}
            placeholder="Field label"
            placeholderTextColor={C.faint}
          />
          <TextInput
            style={[styles.input, styles.fieldInput]}
            value={field.name}
            onChangeText={(value) => setFields((all) => all.map((item, k) => k === i ? { ...item, name: value.toLowerCase().replace(/[^a-z0-9_]/g, '') } : item))}
            placeholder="variable_name"
            placeholderTextColor={C.faint}
            autoCapitalize="none"
            autoCorrect={false}
          />
          <TouchableOpacity
            style={styles.fieldRemove}
            onPress={() => setFields((all) => all.filter((_, k) => k !== i))}>
            <Text style={styles.delText}>×</Text>
          </TouchableOpacity>
        </View>
      ))}
      <TouchableOpacity
        style={[styles.btn, styles.addField]}
        onPress={() => setFields((all) => [...all, { name: `field_${all.length + 1}`, label: '', placeholder: '' }])}>
        <Text style={styles.btnText}>＋ add field</Text>
      </TouchableOpacity>

      <Text style={styles.label}>Dynamic template <Text style={styles.hint}>(insert fields with {'{{variable_name}}'}; this stays after the prefix)</Text></Text>
      <TextInput
        style={[styles.input, styles.textarea]}
        value={text}
        onChangeText={setText}
        multiline
        placeholder={'Review {{topic}} for {{audience}}'}
        placeholderTextColor={C.faint}
      />

      {/* Firing a prompt is free -- appending a message never invalidates a
          cache. These two are not: changing effort drops the messages cache,
          and a model switch drops all of it, because caches are model-scoped.
          Worth knowing before you put one on a pad you tap without thinking. */}
      {CACHE_DROPPER.test(`${prefix}\n${text}`) && (
        <Text style={styles.warn}>
          {/\/model\b/.test(text) ? 'Switches model' : 'Changes effort'} — drops
          this agent's cached prefix, which is re-read at full price on the
          next turn.
        </Text>
      )}

      <Text style={styles.label}>Colour label</Text>
      <View style={styles.chipRow}>
        <TouchableOpacity
          style={[styles.chip, !tag && styles.chipSel]}
          onPress={() => setTag('')}
        >
          <Text style={styles.chipText}>(none)</Text>
        </TouchableOpacity>
        {labels.map((l) => (
          <TouchableOpacity
            key={l.name}
            style={[styles.chip, tag === l.name && styles.chipSel]}
            onPress={() => setTag(l.name)}
          >
            <View style={[styles.swatch, { backgroundColor: hexFor(l.colour) }]} />
            <Text style={styles.chipText}>{l.name}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Scope</Text>
      <View style={styles.chipRow}>
        {[
          ['global', 'global'],
          ['project', 'project'],
          ['local', 'project local'],
        ].map(([key, word]) => (
          <TouchableOpacity
            key={key}
            style={[styles.chip, scope === key && styles.chipSel]}
            onPress={() => setScope(key)}>
            <Text style={styles.chipText}>{word}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <View style={styles.btnRow}>
        <TouchableOpacity style={styles.btn} onPress={save}>
          <Text style={styles.btnText}>Save</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.btn, styles.ghost]} onPress={onClear}>
          <Text style={styles.btnText}>Clear pad</Text>
        </TouchableOpacity>
      </View>

      <Text style={[styles.h1, styles.h1Spaced]}>Colour labels</Text>
      {labels.length === 0 && <Text style={styles.hint}>none yet</Text>}
      {labels.map((l, i) => (
        <View key={l.name} style={styles.labelRow}>
          <View style={[styles.swatch, { backgroundColor: hexFor(l.colour) }]} />
          <Text style={styles.labelName}>{l.name}</Text>
          <TouchableOpacity style={styles.delBtn} onPress={() => onDelLabel(i)}>
            <Text style={styles.delText}>x</Text>
          </TouchableOpacity>
        </View>
      ))}

      <View style={styles.addRow}>
        <TextInput
          style={[styles.input, styles.addInput]}
          value={newName}
          onChangeText={setNewName}
          placeholder="name"
          placeholderTextColor={C.faint}
        />
        <TouchableOpacity style={styles.btn} onPress={addLabel}>
          <Text style={styles.btnText}>Add</Text>
        </TouchableOpacity>
      </View>
      <View style={styles.chipRow}>
        {PALETTE.map(([name, id]) => (
          <TouchableOpacity
            key={id}
            style={[styles.chip, newColour === id && styles.chipSel]}
            onPress={() => setNewColour(id)}
          >
            <View style={[styles.swatch, { backgroundColor: hexFor(id) }]} />
            <Text style={styles.chipText}>{name}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  panel: {
    flexGrow: 0, // a sheet as tall as its content, not as tall as the screen
    backgroundColor: C.panel,
  },
  content: {
    padding: S.pad,
    paddingTop: 4,
    paddingBottom: 40,
  },
  empty: {
    color: C.faint,
    textAlign: 'center',
    marginTop: 40,
    fontSize: 15,
  },
  h1: {
    color: C.dim,
    fontSize: 15,
    fontWeight: '600',
    marginBottom: 10,
  },
  h1Spaced: {
    marginTop: 22,
  },
  label: {
    color: C.dim,
    fontSize: 12,
    marginTop: 12,
    marginBottom: 4,
  },
  hint: {
    color: C.faint,
    fontSize: 11,
  },
  warn: {
    color: '#e0a03c',
    fontSize: 11,
    lineHeight: 16,
    marginBottom: 12,
  },
  cacheIntro: { marginTop: 14, gap: 7, padding: 11, borderWidth: 1, borderColor: C.line, borderRadius: S.radius, backgroundColor: C.bg },
  cacheTitle: { color: C.text, fontSize: 13, fontWeight: '600' },
  cacheMeterRow: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  cacheTrack: { flex: 1, height: 5, borderRadius: 3, backgroundColor: C.raised, overflow: 'hidden' },
  cacheFill: { height: '100%', backgroundColor: '#e0a03c' },
  cacheFillReady: { backgroundColor: '#3cd05a' },
  cacheCount: { color: '#e0a03c', fontSize: 10, minWidth: 112, textAlign: 'right' },
  cacheCountReady: { color: '#3cd05a' },
  cacheStatus: { color: C.faint, fontSize: 10, lineHeight: 15 },
  input: {
    backgroundColor: C.raised,
    color: C.text,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: S.radius,
    padding: 8,
    fontSize: 14,
    minHeight: S.hit,
  },
  textarea: {
    height: 120,
    textAlignVertical: 'top',
  },
  prefixArea: { height: 190, marginTop: 8, textAlignVertical: 'top' },
  fieldCard: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 7 },
  fieldInput: { flex: 1, minWidth: 0 },
  fieldRemove: { width: 30, height: 30, alignItems: 'center', justifyContent: 'center' },
  addField: { alignSelf: 'flex-start', minHeight: 34, marginTop: 8, paddingHorizontal: 10 },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: S.hit,
    paddingHorizontal: 12,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.edge,
    backgroundColor: C.raised,
  },
  chipSel: {
    borderColor: C.accentText,
    backgroundColor: C.accent,
  },
  chipText: {
    color: C.text,
    fontSize: 13,
  },
  swatch: {
    width: 14,
    height: 14,
    borderRadius: 3,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginTop: 14,
  },
  rowLabel: {
    color: C.text,
    fontSize: 13,
    flex: 1,
  },
  btnRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 14,
  },
  btn: {
    minHeight: S.hit,
    paddingHorizontal: 16,
    borderRadius: S.radius,
    backgroundColor: C.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ghost: {
    backgroundColor: C.raised,
  },
  btnText: {
    color: C.text,
    fontSize: 14,
    fontWeight: '600',
  },
  labelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginVertical: 4,
    minHeight: S.hit,
  },
  labelName: {
    color: C.text,
    fontSize: 13,
    flex: 1,
  },
  delBtn: {
    minWidth: S.hit,
    minHeight: S.hit,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: S.radius,
    backgroundColor: C.raised,
    paddingHorizontal: 10,
  },
  delText: {
    color: C.bad,
    fontSize: 14,
  },
  addRow: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 8,
    alignItems: 'center',
  },
  addInput: {
    flex: 1,
  },
});
