import { useEffect, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  ScrollView,
  TouchableOpacity,
  Switch,
  StyleSheet,
} from 'react-native';
import { C, PALETTE, hexFor, S } from './theme';

export default function Inspector({ index, pad, labels, onSave, onClear, onAddLabel, onDelLabel }) {
  const [label, setLabel] = useState('');
  const [text, setText] = useState('');
  const [tag, setTag] = useState('');
  const [submit, setSubmit] = useState(false);
  const [newName, setNewName] = useState('');
  const [newColour, setNewColour] = useState(PALETTE[0][1]);

  // Re-seed on a new pad selection only -- not on every render, or the
  // user's own keystrokes would get clobbered mid-edit.
  useEffect(() => {
    setLabel(pad?.label || '');
    setText(pad?.text || '');
    setTag(pad?.tag || '');
    setSubmit(!!pad?.submit);
  }, [index]);

  if (index === null) {
    return (
      <View style={styles.panel}>
        <Text style={styles.empty}>pick a pad</Text>
      </View>
    );
  }

  const save = () => {
    const trimmed = text.trim();
    if (!trimmed) {
      onClear();
      return;
    }
    const lab = labels.find((l) => l.name === tag);
    onSave({
      label: label.trim() || trimmed.split(/\s+/).slice(0, 2).join(' '),
      text: trimmed,
      tag: tag || null,
      colour: lab ? lab.colour : 125,
      submit,
    });
  };

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

      <Text style={styles.label}>Text <Text style={styles.hint}>(inserted into the prompt)</Text></Text>
      <TextInput
        style={[styles.input, styles.textarea]}
        value={text}
        onChangeText={setText}
        multiline
        placeholder="empty clears the pad"
        placeholderTextColor={C.faint}
      />

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

      <View style={styles.row}>
        <Switch value={submit} onValueChange={setSubmit} />
        <Text style={styles.rowLabel}>
          Auto submit <Text style={styles.hint}>(fires at the session on tap -- careful, this is live)</Text>
        </Text>
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
