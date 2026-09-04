import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { forgetProject, listProjects, openWorktree } from './api';
import SessionSheet from './SessionSheet';
import { C, seatHue } from './theme';

// The rail's upper half: the repos, and under each one its worktrees.
//
// The server owns the list (`GET /projects`) and hands back each repo with its
// worktrees already attached -- one call, not one per repo. It was derived here
// at first, from the agents' own cwds, and that could only ever show you what
// you were already working on: a repo with nothing running in it did not exist
// as far as the wire was concerned. Running an agent somewhere is what adds one
// now, and it stays.
//
// props:
//   cols       array(8)   -- the seats, same shape Rail draws; only `cwd` and
//                            the hue fields are read here, to say which
//                            worktree each agent is living in
//   base       string     -- api base url
//   beat       number     -- bumped by the caller after any create/close/
//                            worktree operation; a change refetches
//   onSeat     (i) => void -- focus a seat, for a worktree that already has one
//   onChanged  () => void  -- fired after opening a worktree spawns an agent
export default function Projects({ cols, base, beat, onSeat, onChanged }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [shut, setShut] = useState({});   // project path -> collapsed
  const [busy, setBusy] = useState(null); // path of the row being acted on
  const [err, setErr] = useState('');
  // { mode: 'project'|'branch', cwd } | null -- SessionSheet does the picking
  // and the call; this only says which errand and re-reads the list after.
  const [sheet, setSheet] = useState(null);

  const seats = cols.map((c, i) => ({ col: c, i })).filter((s) => s.col && s.col.cwd);
  // Sorted and joined so the effect fires on a real change of repo set, not on
  // every 400ms surface poll handing back the same cwds in the same order. An
  // agent starting somewhere new is exactly what adds a project server-side,
  // so it is worth asking again the moment that set moves.
  const key = [...new Set(seats.map((s) => s.col.cwd))].sort().join('\n');

  useEffect(() => {
    let live = true;
    setLoading(true);
    listProjects(base)
      .then((rows) => live && setProjects(rows))
      .catch((e) => live && setErr(e.message || String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [base, key, beat]);

  const refresh = () => listProjects(base).then(setProjects).catch(() => {});

  const inside = (cwd, path) =>
    cwd === path || cwd.startsWith(path.replace(/\/$/, '') + '/');

  // Worktree path -> the seats living in it. A seat is placed in its LONGEST
  // matching worktree, so a worktree nested inside the main checkout is not
  // also claimed by the checkout -- and two agents in one worktree stay two.
  const seatsIn = (project) => {
    const at = {};
    for (const s of seats) {
      let best = null;
      for (const row of project.worktrees) {
        if (!inside(s.col.cwd, row.path)) continue;
        if (!best || row.path.length > best.path.length) best = row;
      }
      if (best) (at[best.path] = at[best.path] || []).push(s);
    }
    return at;
  };

  // Every button here runs one of these: guard against a double tap, say what
  // went wrong in the rail rather than in a modal, and let go of `busy`
  // whatever happened.
  async function run(at, fn) {
    if (busy) return;
    setErr('');
    setBusy(at);
    try {
      await fn();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  function tap(project, row, here) {
    // already open -- go to it. More than one agent in a worktree and this
    // takes the first; the AGENTS group below is where you pick between them.
    if (here.length) return onSeat(here[0].i);
    if (!row.exists) return;
    run(row.path, async () => {
      await openWorktree(base, project.path, row.path);
      onChanged && onChanged();
    });
  }

  return (
    <View style={styles.wrap}>
      <View style={styles.headRow}>
        <Text style={styles.head}>PROJECTS</Text>
        {loading && <ActivityIndicator size="small" color={C.faint} />}
      </View>

      {!projects.length && !loading && <Text style={styles.empty}>no projects yet</Text>}

      {projects.map((p) => {
        const open = !shut[p.path];
        const at = seatsIn(p);
        const held = Object.keys(at).length > 0;
        return (
          <View key={p.path}>
            <View style={styles.project}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${p.name} — ${open ? 'collapse' : 'expand'}`}
                onPress={() => setShut((s) => ({ ...s, [p.path]: open }))}
                style={styles.projectPick}>
                <Text style={styles.caret}>{open ? '▾' : '▸'}</Text>
                <Text style={styles.projectName} numberOfLines={1}>
                  {p.name}
                </Text>
                <Text style={styles.count}>{p.worktrees.length}</Text>
              </Pressable>
              {/* forgetting is the list's own memory and nothing else -- no
                  confirmation, because there is nothing on disk to lose and an
                  agent running there puts it straight back. Not offered while
                  one is, which would be a button that undoes itself. */}
              {!held && (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`forget ${p.name}`}
                  hitSlop={8}
                  onPress={() =>
                    run(p.path, async () => {
                      await forgetProject(base, p.path);
                      await refresh();
                    })
                  }
                  style={styles.forget}>
                  <Text style={styles.forgetX}>×</Text>
                </Pressable>
              )}
            </View>

            {open &&
              p.worktrees.map((row) => {
                const here = at[row.path] || [];
                const seat = here[0] || null;
                const label = row.detached
                  ? (row.head || '').slice(0, 7)
                  : row.branch || '(no branch)';
                return (
                  <View key={row.path} style={styles.wtSlot}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={
                      seat
                        ? `${label} — focus ${seat.col.name}`
                        : `${label} — open an agent here`
                    }
                    onPress={() => tap(p, row, here)}
                    style={[styles.wt, seat && styles.wtOn]}>
                    <View
                      style={[
                        styles.dot,
                        { backgroundColor: seat ? seatHue(seat.col) : 'transparent' },
                        !seat && styles.dotOff,
                      ]}
                    />
                    <Text
                      style={[
                        styles.branch,
                        !row.exists && styles.gone,
                        seat && styles.branchOn,
                      ]}
                      numberOfLines={1}>
                      {label}
                    </Text>
                    {here.length > 1 && <Text style={styles.tag}>{here.length}</Text>}
                    {row.main && label !== 'main' && <Text style={styles.tag}>main</Text>}
                    {!row.exists && <Text style={styles.tagGone}>gone</Text>}
                    {busy === row.path && (
                      <ActivityIndicator size="small" color={C.faint} />
                    )}
                    {/* a sibling, not a child: a button inside a button is not
                        valid HTML, which the rail's own ⋯ already learned */}
                  </Pressable>
                  {row.exists && (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`switch the branch in ${label}`}
                      hitSlop={6}
                      onPress={() => setSheet({ mode: 'branch', cwd: row.path })}
                      style={styles.swap}>
                      <Text style={styles.swapGlyph}>⇄</Text>
                    </Pressable>
                  )}
                  </View>
                );
              })}
          </View>
        );
      })}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="add a project"
        onPress={() => setSheet({ mode: 'project' })}
        style={styles.add}>
        <Text style={styles.addText}>＋ add project</Text>
      </Pressable>

      {!!err && <Text style={styles.err}>{err}</Text>}

      {sheet && (
        <SessionSheet
          visible
          mode={sheet.mode}
          base={base}
          cwd={sheet.cwd}
          onClose={() => setSheet(null)}
          onDone={() => {
            setSheet(null);
            refresh();
            // a branch switch changes what the agents in that worktree are
            // sitting on top of, which is the coordinator's business too
            if (sheet.mode === 'branch') onChanged && onChanged();
          }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: C.line },
  headRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 4 },
  head: { color: C.faint, fontSize: 10, letterSpacing: 1.2 },
  empty: { color: C.edge, fontSize: 11, paddingHorizontal: 4, paddingTop: 8 },
  project: { flexDirection: 'row', alignItems: 'center', marginTop: 6 },
  projectPick: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 30,
    paddingHorizontal: 4,
  },
  caret: { color: C.dim, fontSize: 12, width: 12 },
  projectName: { color: C.text, fontSize: 13, fontWeight: '600', flexShrink: 1 },
  count: { color: C.edge, fontSize: 11, marginLeft: 'auto' },
  forget: { paddingHorizontal: 6, paddingVertical: 2 },
  forgetX: { color: C.edge, fontSize: 15 },
  wt: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    minHeight: 28,
    paddingLeft: 20,
    paddingRight: 4,
    borderRadius: 4,
  },
  wtSlot: { flexDirection: 'row', alignItems: 'center' },
  wtOn: { backgroundColor: C.raised },
  swap: { paddingHorizontal: 5, paddingVertical: 2 },
  swapGlyph: { color: C.edge, fontSize: 12 },
  dot: { width: 6, height: 6, borderRadius: 3 },
  // a worktree nobody is in still gets the dot's width, so the branch names
  // line up whether or not an agent is living there
  dotOff: { borderWidth: 1, borderColor: C.edge },
  branch: { color: C.dim, fontSize: 12, flexShrink: 1 },
  branchOn: { color: C.text },
  gone: { color: C.faint, textDecorationLine: 'line-through' },
  tag: { color: C.edge, fontSize: 10 },
  tagGone: { color: C.warn, fontSize: 10 },
  add: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: 28,
    paddingHorizontal: 4,
    marginTop: 8,
  },
  addText: { color: C.edge, fontSize: 11 },
  err: { color: C.bad, fontSize: 11, paddingHorizontal: 4, paddingTop: 6 },
});
