import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import PushButton from './PushButton';
import { C, S } from './theme';
import {
  addProject,
  chooseDir,
  closeAgent,
  createAgent,
  getProviders,
  listBranches,
  listDirs,
  listProjects,
  makeWorktree,
  renameAgent,
  switchBranch,
} from './api';

// Which engine a new agent starts as, offered here rather than buried a
// Settings tab away -- Engines' "default engine" only decides what this
// picker opens on.
const ENGINES = [['claude', 'Claude Code'], ['codex', 'Codex']];

// The same rule mapui.py enforces server-side -- checked here too so a typo
// is caught before the round trip rather than after a 400 comes back.
// exported: the conversation header renames by the same rule, not a copy
export const NAME_RE = /^[a-z][a-z0-9_-]{0,31}$/;
export const NAME_HELP = 'lowercase letters, digits, _ or - only, must start with a letter, 32 chars max';

// Folders come from the server, not the tablet: a file picker here would
// browse the iPad, and the repos are on the machine running the agents.
//
// This is the `project` mode's control, and only its. Browsing the whole disk
// used to be how you started an agent, which put the same question -- where is
// this repo -- in front of you every single time. Adding the project answers
// it once; `new` picks from what has been answered.
//
// Walking and choosing used to be the same gesture: the folder you were
// looking at WAS the answer, so there was no "use this one". That reads fine
// until the modal has a branch list under it and the folder list is holding
// 190px open for a question already answered. So a row now offers **select**,
// and selecting folds the whole list away to one line with **remove** on it.
function FolderPick({ base, value, onChange }) {
  const [listing, setListing] = useState(null);
  const [loading, setLoading] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [err, setErr] = useState('');
  const [chosen, setChosen] = useState(false);
  // hover is a mouse's answer and the iPad has none, so the row that is
  // already the value shows its key unconditionally -- a touch can always
  // reach at least the one it is on.
  const [hover, setHover] = useState(null);

  const load = async (path) => {
    setLoading(true);
    setErr('');
    try {
      setListing(await listDirs(base, path));
    } catch (e) {
      setListing(null);
      setErr(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(value || undefined);
    // only on open: after that, navigation drives it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const go = (path) => {
    onChange(path);
    load(path);
  };

  // The real Finder dialog, opened on the machine the agents run on. It has
  // the sidebar, the search and cmd-shift-G that this list never will -- but
  // the window appears over there, which is why the list stays for the iPad.
  const browse = async () => {
    setBrowsing(true);
    setErr('');
    try {
      const path = await chooseDir(base, value);
      // null is a cancel, and a cancel changes nothing. A path is not a walk:
      // the Finder dialog's whole gesture is "this one", so it selects.
      if (path) choose(path);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBrowsing(false);
    }
  };

  // a repo is nearly always what you mean, so they sort first
  const entries = [...(listing?.entries || [])].sort(
    (a, b) => (b.git ? 1 : 0) - (a.git ? 1 : 0) || a.name.localeCompare(b.name)
  );

  const choose = (path) => {
    onChange(path);
    setChosen(true);
  };

  if (chosen) {
    return (
      <View style={styles.chosen}>
        <Text style={styles.chosenPath} numberOfLines={1}>
          {value}
        </Text>
        {/* the list is still mounted behind this, so reopening lands back
            where you were rather than at the top of the disk */}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="remove this folder and choose another"
          hitSlop={6}
          onPress={() => {
            setChosen(false);
            onChange('');
          }}
          style={styles.rowKey}>
          <Text style={styles.removeText}>remove</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.pick}>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChange}
        onSubmitEditing={() => load(value)}
        placeholder="/path/to/project"
        placeholderTextColor={C.faint}
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="go"
      />
      <View style={styles.pickHead}>
        <Text style={styles.hint} numberOfLines={1}>
          {listing?.git ? 'a repo · ' : ''}
          {entries.length} folder{entries.length === 1 ? '' : 's'}
        </Text>
        {loading && <ActivityIndicator size="small" color={C.faint} />}
        <View style={styles.spacer} />
        {!!listing?.path && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="select this folder"
            onPress={() => choose(listing.path)}
            style={styles.rowKey}>
            <Text style={styles.selectText}>select this folder</Text>
          </Pressable>
        )}
        <Pressable onPress={browse} disabled={browsing} style={styles.browse}>
          {browsing ? (
            <ActivityIndicator size="small" color={C.faint} />
          ) : (
            <Text style={styles.browseText}>browse on the Mac…</Text>
          )}
        </Pressable>
      </View>
      {!!err && <Text style={styles.error}>{err}</Text>}
      <ScrollView style={styles.pickList} keyboardShouldPersistTaps="handled">
        {!!listing?.parent && (
          <Pressable style={styles.pickRow} onPress={() => go(listing.parent)}>
            <Text style={styles.pickUp}>..</Text>
          </Pressable>
        )}
        {entries.map((e) => (
          <View
            key={e.path}
            onPointerEnter={() => setHover(e.path)}
            onPointerLeave={() => setHover((h) => (h === e.path ? null : h))}
            style={styles.pickRow}>
            {/* the name still walks into the folder -- select is the separate
                act, so browsing through a folder never chooses it by accident */}
            <Pressable style={styles.pickWalk} onPress={() => go(e.path)}>
              <Text style={[styles.pickName, e.git && styles.pickRepo]} numberOfLines={1}>
                {e.name}
              </Text>
            </Pressable>
            {/* an agent already lives here; starting a second is allowed but
                worth knowing before you do it */}
            {e.busy && <Text style={styles.pickTag}>in use</Text>}
            {(hover === e.path || value === e.path) && (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`select ${e.name}`}
                hitSlop={4}
                onPress={() => choose(e.path)}
                style={styles.rowKey}>
                <Text style={styles.selectText}>select</Text>
              </Pressable>
            )}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

// Where a new agent goes: one of the worktrees of a project already known.
// The field stays and stays editable -- a path you can type is never a dead
// end, which matters on the first run, when there are no projects yet.
function PlacePick({ base, value, onChange }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  useEffect(() => {
    let live = true;
    listProjects(base)
      .then((rows) => live && setProjects(rows))
      .catch((e) => live && setErr(String(e.message || e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [base]);

  return (
    <View style={styles.pick}>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChange}
        placeholder="/path/to/project"
        placeholderTextColor={C.faint}
        autoCapitalize="none"
        autoCorrect={false}
      />
      <View style={styles.pickHead}>
        <Text style={styles.hint} numberOfLines={1}>
          {loading ? 'reading projects…' : `${projects.length} project${projects.length === 1 ? '' : 's'}`}
        </Text>
        {loading && <ActivityIndicator size="small" color={C.faint} />}
      </View>
      {!!err && <Text style={styles.error}>{err}</Text>}
      {!loading && !projects.length && (
        <Text style={styles.hint}>none yet — add one from the rail, or type a path</Text>
      )}
      <ScrollView style={styles.pickList} keyboardShouldPersistTaps="handled">
        {projects.map((p) => (
          <View key={p.path}>
            <Text style={styles.pickGroup} numberOfLines={1}>{p.name}</Text>
            {p.worktrees
              .filter((w) => w.exists)
              .map((w) => (
                <Pressable
                  key={w.path}
                  style={[styles.pickRow, value === w.path && styles.pickRowOn]}
                  onPress={() => onChange(w.path)}>
                  <Text
                    style={[styles.pickName, value === w.path && styles.pickRepo]}
                    numberOfLines={1}>
                    {w.detached ? (w.head || '').slice(0, 7) : w.branch || w.path}
                  </Text>
                  {w.main && <Text style={styles.pickTag}>main</Text>}
                </Pressable>
              ))}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

// The branches of the repo this worktree belongs to. One already checked out
// somewhere else is shown and not offered: git refuses a second checkout of the
// same branch, so a row that could only ever produce that error is drawn as the
// fact it is rather than as a button.
function BranchPick({ base, cwd, value, onChange, onRows, quiet }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  // Debounced, because `cwd` is a field somebody is typing into in two of the
  // three places this is used -- without it every keystroke asks the server
  // about a path that is half a path.
  useEffect(() => {
    let live = true;
    setLoading(true);
    if (!cwd) {
      // an empty path is not a repo to ask about -- and /branches with no cwd
      // answers for whatever the server itself is sitting in, which is a list
      // of somebody else's branches under a blank field
      setRows([]);
      setLoading(false);
      onRows && onRows([]);
      return () => {
        live = false;
      };
    }
    const t = setTimeout(() => {
      listBranches(base, cwd)
        .then((got) => {
          if (!live) return;
          setRows(got);
          setErr('');
          onRows && onRows(got);
        })
        // not a repo (yet) is the ordinary case while typing a path, not an
        // error worth a red line under the field
        .catch((e) => live && (setRows([]), onRows && onRows([]), setErr(quiet ? '' : String(e.message || e))))
        .finally(() => live && setLoading(false));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
    // onRows is a fresh closure every render and is not a reason to refetch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, cwd, quiet]);

  const same = (p) => p && cwd && p.replace(/\/$/, '') === cwd.replace(/\/$/, '');

  // `quiet` is the optional use: a folder that is not a repo has nothing to
  // say here, and saying it takes up the modal.
  if (quiet && !rows.length) return null;

  return (
    <View style={styles.pick}>
      {loading && <ActivityIndicator size="small" color={C.faint} />}
      {!!err && <Text style={styles.error}>{err}</Text>}
      <ScrollView style={styles.pickList} keyboardShouldPersistTaps="handled">
        {!loading && !rows.length && <Text style={styles.hint}>no branches</Text>}
        {rows.map((b) => {
          const here = same(b.at);
          const taken = !!b.at && !here;
          return (
            <Pressable
              key={b.name}
              disabled={taken || here}
              style={[styles.pickRow, value === b.name && styles.pickRowOn]}
              onPress={() => onChange(b.name)}>
              <Text
                style={[
                  styles.pickName,
                  value === b.name && styles.pickRepo,
                  taken && styles.pickOff,
                ]}
                numberOfLines={1}>
                {b.name}
              </Text>
              {here && <Text style={styles.pickTag}>here</Text>}
              {taken && <Text style={styles.pickTag} numberOfLines={1}>in use</Text>}
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const TITLE = {
  new: 'new agent',
  rename: 'rename',
  close: 'close agent',
  worktree: 'new worktree',
  project: 'add project',
  branch: 'switch branch',
};

function nameValid(mode, value) {
  if (mode === 'new') return value === '' || NAME_RE.test(value); // blank is fine, server names it
  if (mode === 'rename') return NAME_RE.test(value);
  return true; // close and worktree don't gate on this field
}

// One modal, four errands, picked by `mode` -- keeping the name rule, the
// error readout, and the in-flight guard written once rather than four times.
//
// props:
//   visible  bool                              -- modal open/closed
//   mode     'new' | 'rename' | 'close' | 'worktree' | 'project' | 'branch'
//   base     string                            -- api base url, passed straight to api.js
//   tid      string | null                     -- terminal id; required for rename and close
//   name     string                            -- current agent name; prefills rename, labels close
//   cwd      string | undefined                -- the directory the errand is about: the default cwd
//                                                  for new/worktree/project, and for `branch` the
//                                                  worktree whose branch is being switched
//   onClose  () => void                        -- dismiss without doing anything
//   onDone   () => void                        -- fired after the api call succeeds; caller should
//                                                  dismiss (visible=false) and refetch /agents
export default function SessionSheet({ visible, mode, base, tid, name, cwd, place, onClose, onDone }) {
  const [cwdField, setCwdField] = useState('');
  const [nameField, setNameField] = useState('');
  const [branchField, setBranchField] = useState('');
  // The branches BranchPick last read, so a name picked off that list passes
  // validation as itself. NAME_RE is the rule for names we mint; a branch that
  // already exists may carry a slash or a capital and is not ours to judge.
  const [branchRows, setBranchRows] = useState([]);
  const [wt, setWt] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  // 'new' only: which engine the agent starts as, and an optional model id.
  // Seeded from Settings' "default engine" each time the sheet opens -- a
  // quiet 'claude' when /providers is not there yet (an older server, or
  // this one mid-rollout), since that is the one engine that always exists.
  const [engine, setEngine] = useState('claude');
  const [engineModel, setEngineModel] = useState('');

  // re-seed the form every time the sheet opens, or a rename typed for one
  // seat would still be sitting in the field when it opens again for another.
  useEffect(() => {
    if (!visible) return;
    setCwdField(cwd || '');
    setNameField(mode === 'rename' ? name || '' : '');
    setBranchField('');
    setBranchRows([]);
    setWt(false);
    setBusy(false);
    setErr('');
    setEngine('claude');
    setEngineModel('');
    if (mode === 'new' && base) {
      getProviders(base)
        .then((res) => res?.engines?.default && setEngine(res.engines.default))
        .catch(() => {}); // /providers may not exist yet -- 'claude' already set above
    }
  }, [visible, mode, cwd, name, base]);

  const needsCwd = mode === 'new' || mode === 'worktree' || mode === 'project';
  const cwdOk = !needsCwd || cwdField.trim().length > 0;
  const nameOk = nameValid(mode, nameField);
  // `branch` picks an existing name off a list, so NAME_RE has no business
  // near it -- real branches carry slashes, and the rule is for names we mint.
  // a branch that already exists is always a legal answer, whatever it is
  // called -- `worktree create` falls back to checking one out when `-b` finds
  // it taken, which is exactly what picking it off the list means
  const existing = branchRows.some((b) => b.name === branchField);
  const branchOk =
    mode === 'worktree' || (mode === 'new' && wt)
      ? existing || NAME_RE.test(branchField)
      : mode === 'branch' ? branchField.length > 0
      : true;
  const canConfirm = cwdOk && nameOk && branchOk && !busy;

  async function confirm() {
    if (!canConfirm) return;
    setErr('');
    setBusy(true);
    try {
      if (mode === 'new' && wt)
        await makeWorktree(
          base, cwdField.trim(), branchField.trim(), nameField.trim() || undefined,
          engine, engineModel.trim() || undefined
        );
      else if (mode === 'new')
        await createAgent(base, cwdField.trim(), nameField.trim() || undefined, engine, engineModel.trim() || undefined);
      else if (mode === 'rename') await renameAgent(base, tid, nameField.trim());
      else if (mode === 'close') await closeAgent(base, tid);
      else if (mode === 'worktree') await makeWorktree(base, cwdField.trim(), branchField.trim());
      else if (mode === 'project') {
        const where = cwdField.trim();
        await addProject(base, where);
        // asked for, not required: adding a repo you already have is often
        // "and put me on this branch", and doing it in two steps means
        // finding the row again first
        if (branchField) await switchBranch(base, where, branchField);
      }
      else if (mode === 'branch') await switchBranch(base, cwd, branchField);
      onDone();
    } catch (e) {
      setErr(e.message || String(e)); // api.js's post() already reads "<status> <body>" off the server
      setBusy(false);
    }
  }

  return (
    <Modal visible={!!visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Text style={styles.title}>{TITLE[mode] || mode}</Text>
          {/* the body scrolls, the title and the keys do not: `project` can
              carry a folder list and a branch list at once, which is taller
              than a short window, and confirm sliding off the bottom edge is
              the one thing this modal must never do */}
          <ScrollView
            style={styles.bodyScroll}
            contentContainerStyle={styles.body}
            keyboardShouldPersistTaps="handled">

          {mode === 'new' && (
            <>
              <Text style={styles.label}>where</Text>
              {/* opened from a branch row: the place is decided, so say it */}
              {place ? (
                <View>
                  <Text style={styles.placeName}>{place}</Text>
                  <Text style={styles.hint} numberOfLines={1}>{cwdField}</Text>
                </View>
              ) : (
                <PlacePick base={base} value={cwdField} onChange={setCwdField} />
              )}
              <Text style={styles.label}>name <Text style={styles.hint}>(optional)</Text></Text>
              <TextInput
                style={styles.input}
                value={nameField}
                onChangeText={setNameField}
                placeholder="auto"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {!nameOk && <Text style={styles.rule}>{NAME_HELP}</Text>}

              <Text style={styles.label}>engine</Text>
              <View style={styles.chips}>
                {ENGINES.map(([key, word]) => (
                  <Text
                    key={key}
                    accessibilityRole="button"
                    accessibilityState={{ selected: engine === key }}
                    onPress={() => setEngine(key)}
                    style={[styles.chip, engine === key && styles.chipOn]}>
                    {word}
                  </Text>
                ))}
              </View>
              <TextInput
                style={styles.input}
                value={engineModel}
                onChangeText={setEngineModel}
                placeholder="model (optional)"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                autoCorrect={false}
              />

              <Pressable style={styles.checkRow} onPress={() => setWt((v) => !v)}>
                <View style={[styles.checkbox, wt && styles.checkboxOn]}>
                  {wt && <Text style={styles.checkMark}>✓</Text>}
                </View>
                <Text style={styles.checkLabel}>new worktree</Text>
              </Pressable>
              {wt && (
                <>
                  <Text style={styles.label}>new branch name</Text>
                  <TextInput
                    style={styles.input}
                    value={branchField}
                    onChangeText={setBranchField}
                    placeholder="feature-name"
                    placeholderTextColor={C.faint}
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                  {!branchOk && branchField.length > 0 && (
                    <Text style={styles.rule}>{NAME_HELP}</Text>
                  )}
                </>
              )}
            </>
          )}

          {mode === 'rename' && (
            <>
              <Text style={styles.label}>name</Text>
              <TextInput
                style={styles.input}
                value={nameField}
                onChangeText={setNameField}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {!nameOk && <Text style={styles.rule}>{NAME_HELP}</Text>}
            </>
          )}

          {mode === 'close' && (
            <Text style={styles.confirmText}>
              close <Text style={styles.confirmName}>{name || tid}</Text>? this ends the running agent.
            </Text>
          )}

          {mode === 'worktree' && (
            <>
              <Text style={styles.label}>branches from</Text>
              <TextInput
                style={styles.input}
                value={cwdField}
                onChangeText={setCwdField}
                placeholder="/path/to/project"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <Text style={styles.label}>
                branch <Text style={styles.hint}>(a new name, or one that exists)</Text>
              </Text>
              <TextInput
                style={styles.input}
                value={branchField}
                onChangeText={setBranchField}
                placeholder="feature-name"
                placeholderTextColor={C.faint}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {/* `worktree create` already falls back to checking a branch out
                  when `-b` finds it taken, so an existing name has always
                  worked here -- there was just no way to see one from the app */}
              <BranchPick
                base={base}
                cwd={cwdField.trim()}
                value={branchField}
                onChange={setBranchField}
                onRows={setBranchRows}
                quiet
              />
              {!branchOk && branchField.length > 0 && <Text style={styles.rule}>{NAME_HELP}</Text>}
            </>
          )}

          {mode === 'project' && (
            <>
              <Text style={styles.label}>folder</Text>
              <FolderPick base={base} value={cwdField} onChange={setCwdField} />
              <Text style={styles.hint}>
                any path inside the repo will do — it is remembered as the main checkout
              </Text>
              {/* quiet: a folder that is not a repo draws nothing at all here,
                  which is most of the folders you pass through on the way */}
              <BranchPick
                base={base}
                cwd={cwdField.trim()}
                value={branchField}
                onChange={setBranchField}
                onRows={setBranchRows}
                quiet
              />
              {!!branchRows.length && (
                <Text style={styles.hint}>branch — optional, checks it out as you add it</Text>
              )}
            </>
          )}

          {mode === 'branch' && (
            <>
              <Text style={styles.label} numberOfLines={1}>
                in {cwd || '?'}
              </Text>
              <BranchPick base={base} cwd={cwd} value={branchField} onChange={setBranchField} />
            </>
          )}

          {!!err && <Text style={styles.error}>{err}</Text>}
          </ScrollView>

          <View style={styles.row}>
            <PushButton label="cancel" onPress={onClose} disabled={busy} style={styles.btn} />
            <PushButton
              label={busy ? '' : mode === 'close' ? 'close' : mode === 'branch' ? 'switch' : 'confirm'}
              colour={mode === 'close' ? C.bad : C.accentText}
              lit
              onPress={confirm}
              disabled={!canConfirm}
              style={styles.btn}>
              {busy && <ActivityIndicator size="small" color={C.text} />}
            </PushButton>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  placeName: { color: C.text, fontSize: 14, fontWeight: '600' },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  card: {
    width: 340,
    maxWidth: '90%',
    maxHeight: '88%',
    backgroundColor: C.panel,
    borderRadius: S.radius,
    borderWidth: 1,
    borderColor: C.line,
    padding: S.pad,
    gap: 8,
  },
  title: { color: C.text, fontSize: 16, fontWeight: '600', marginBottom: 4 },
  bodyScroll: { flexGrow: 0, flexShrink: 1 },
  body: { gap: 8 },
  label: { color: C.dim, fontSize: 12 },
  hint: { color: C.faint, fontSize: 11 },
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
  pick: { gap: 6 },
  pickHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  spacer: { flex: 1 },
  browse: { justifyContent: 'center', minHeight: 28, paddingHorizontal: 4 },
  browseText: { color: C.accentText, fontSize: 12 },
  pickList: {
    maxHeight: 190,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: S.radius,
    backgroundColor: C.bg,
  },
  pickRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 10,
    minHeight: 34,
  },
  pickWalk: { flex: 1, justifyContent: 'center', minHeight: 34 },
  rowKey: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 5,
    borderWidth: 1,
    borderColor: C.edge,
  },
  selectText: { color: C.accentText, fontSize: 11 },
  removeText: { color: C.bad, fontSize: 11 },
  chosen: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: S.hit,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: S.radius,
    backgroundColor: C.raised,
  },
  chosenPath: { color: C.text, fontSize: 13, flex: 1 },
  pickGroup: {
    color: C.faint,
    fontSize: 10,
    letterSpacing: 1,
    paddingHorizontal: 10,
    paddingTop: 8,
    paddingBottom: 2,
    textTransform: 'uppercase',
  },
  pickRowOn: { backgroundColor: C.raised },
  pickOff: { color: C.edge },
  pickName: { color: C.dim, fontSize: 13, flexShrink: 1 },
  pickRepo: { color: C.text, fontWeight: '600' },
  pickUp: { color: C.faint, fontSize: 13 },
  pickTag: { color: C.faint, fontSize: 10 },
  checkRow: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: S.hit },
  checkbox: {
    width: 18,
    height: 18,
    borderWidth: 1,
    borderColor: C.edge,
    borderRadius: S.radius,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxOn: { borderColor: C.accentText },
  checkMark: { color: C.accentText, fontSize: 12, fontWeight: '700' },
  checkLabel: { color: C.dim, fontSize: 12 },
  rule: { color: C.warn, fontSize: 11 },
  error: { color: C.bad, fontSize: 12 },
  confirmText: { color: C.text, fontSize: 14, lineHeight: 20 },
  confirmName: { fontWeight: '600' },
  row: { flexDirection: 'row', gap: 8, marginTop: 8 },
  btn: { flex: 1, minHeight: S.hit },
});
