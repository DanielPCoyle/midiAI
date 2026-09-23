import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';
import { closeAgent, forgetProject, listProjects, openWorktree, removeWorktree } from './api';
import { Menu, MenuButton } from './Menu';
import SessionSheet from './SessionSheet';
import { C, seatHue } from './theme';

// Is `cwd` this worktree, or under it. Exported because App asks the same
// question of the picked worktree that the rail asks of every row, and one
// definition beats two that can drift.
export const inside = (cwd, path) =>
  !!cwd && !!path && (cwd === path || cwd.startsWith(path.replace(/\/$/, '') + '/'));

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
//   onPick     (pick|null) => void -- a worktree with nobody in it was chosen;
//                            { project, path, label }. The pane draws the
//                            "no active agent" panel from this, so the buttons
//                            for it are where you are looking rather than
//                            behind the ⋮ you have just closed.
// One worktree's name: the branch, or a short sha when it is detached.
const labelOf = (row) =>
  row.detached ? (row.head || '').slice(0, 7)
    // a project folder with no repository has no branch and no head at all
    : row.branch || (row.head ? '(no branch)' : 'no repo');

// What the pane needs to draw its "no active agent" panel and act on it.
const pickOf = (project, row) => ({
  project: project.path,
  path: row.path,
  label: labelOf(row),
  main: !!row.main,
});

export default function Projects({ cols, base, beat, onSeat, onChanged, onPick }) {
  // 30% of the window, not of the rail: the rail is one scroll holding this
  // and the agents below it, so a percentage here would measure against a
  // parent that grows with the list and never cap anything. Thirty-one
  // worktrees pushed AGENTS off the bottom of the screen entirely.
  const roof = Math.round(useWindowDimensions().height * 0.30);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [shut, setShut] = useState({});   // project path -> collapsed
  const [busy, setBusy] = useState(null); // path of the row being acted on
  const [err, setErr] = useState('');
  // { mode: 'project'|'branch', cwd } | null -- SessionSheet does the picking
  // and the call; this only says which errand and re-reads the list after.
  const [sheet, setSheet] = useState(null);
  // { at: 'project'|'worktree', key, anchor, ...row } | null -- Menu.js draws
  // it and places it against the ⋮ that opened it.
  const [menu, setMenu] = useState(null);

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

  // Every agent living in this worktree, ended. The seats hold the terminal
  // id, which is the handle /agents/close takes.
  async function closeHere(project, row, here) {
    for (const s of here) await closeAgent(base, s.col.tid);
    onChanged && onChanged();
    // "if an agent is removed" -- the pane would otherwise fall back to
    // whichever seat the Push happens to be on, or to nothing, with no sign of
    // what you just emptied. Selecting it says so, and offers the buttons.
    onPick && onPick(pickOf(project, row));
  }

  function closeAndRemove(project, row, here) {
    run(row.path, async () => {
      await closeHere(project, row, here);
      // The pane is killed at once, but the claude inside it takes a moment
      // to actually go, and the server refuses to remove a worktree while an
      // agent is still living there -- by design, since removal deletes the
      // directory. So the first attempt can lose a race the second one wins.
      // Only that one refusal is retried: a dirty tree is not a race and must
      // stay a refusal you read.
      try {
        await removeWorktree(base, project.path, row.path, false);
      } catch (e) {
        if (!String(e.message || e).includes('is running in')) throw e;
        await new Promise((r) => setTimeout(r, 1500));
        await removeWorktree(base, project.path, row.path, false);
      }
      await refresh();
      onChanged && onChanged();
      onPick && onPick(null);   // it is gone; nothing to be looking at
    });
  }

  function open(project, row) {
    run(row.path, async () => {
      await openWorktree(base, project.path, row.path);
      onChanged && onChanged();
    });
  }

  function tap(project, row, here) {
    // already open -- go to it. More than one agent in a worktree and this
    // takes the first; the AGENTS group below is where you pick between them.
    if (here.length) {
      onPick && onPick(null);
      return onSeat(here[0].i);
    }
    // Empty: select it rather than starting something. Tapping used to spawn
    // a claude on the spot, which is a lot to happen from one tap on a list --
    // now the pane says there is nobody here and offers the buttons for it.
    onPick && onPick(row.exists ? pickOf(project, row) : null);
  }

  return (
    <View style={styles.wrap}>
      <View style={styles.headRow}>
        <Text style={styles.head}>PROJECTS</Text>
        {loading && <ActivityIndicator size="small" color={C.faint} />}
      </View>

      {!projects.length && !loading && <Text style={styles.empty}>no projects yet</Text>}

      {/* The header above and ＋ add project below stay put; only the list
          moves. A key that slides further away the more projects you have is
          a key you stop finding -- the same reason ＋ new agent sits at the
          top of the agent list rather than after eight cards. */}
      <ScrollView style={{ maxHeight: roof }} nestedScrollEnabled>
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
              {/* the parent row's one action is making another worktree, so
                  it is a ＋ rather than a menu with a single item in it. What
                  the ⋮ used to hold besides that was forgetting the project,
                  which goes back to the × it had: nothing on disk is lost, and
                  an agent running there puts it straight back, so it needs no
                  confirmation and no menu to sit in. */}
              {p.git !== false && (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`new worktree in ${p.name}`}
                hitSlop={6}
                onPress={() => setSheet({ mode: 'worktree', cwd: p.path })}
                style={styles.dots}>
                <Text style={styles.plus}>＋</Text>
              </Pressable>
              )}
              {!held && (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`forget ${p.name}`}
                  hitSlop={6}
                  onPress={() =>
                    run(p.path, async () => {
                      await forgetProject(base, p.path);
                      await refresh();
                    })
                  }
                  style={styles.dots}>
                  <Text style={styles.forgetX}>×</Text>
                </Pressable>
              )}
            </View>

            {open &&
              p.worktrees.map((row) => {
                const here = at[row.path] || [];
                const seat = here[0] || null;
                const label = labelOf(row);
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
                    {/* which rows are worktrees, said once and in the same
                        column every time: a linked worktree branches off the
                        checkout above it, the checkout itself gets the blank.
                        The `main` tag stays -- it names the row, this marks
                        the kind. */}
                    <Text style={styles.kind}>{row.main ? '' : '↳'}</Text>
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
                    {row.main && label !== 'main' && p.git !== false && <Text style={styles.tag}>main</Text>}
                    {!row.exists && <Text style={styles.tagGone}>gone</Text>}
                    {busy === row.path && (
                      <ActivityIndicator size="small" color={C.faint} />
                    )}
                    {/* a sibling, not a child: a button inside a button is not
                        valid HTML, which the rail's own ⋯ already learned */}
                  </Pressable>
                  <MenuButton
                    accessibilityLabel={`more actions for ${label}`}
                    onOpen={(anchor) =>
                      setMenu({ key: row.path, anchor, p, row, here })
                    }
                    style={styles.dots}
                    glyphStyle={styles.dotsGlyph}
                  />
                  </View>
                );
              })}
          </View>
        );
      })}
      </ScrollView>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="add a project"
        onPress={() => setSheet({ mode: 'project' })}
        style={styles.add}>
        <Text style={styles.addText}>＋ add project</Text>
      </Pressable>

      {!!err && <Text style={styles.err}>{err}</Text>}

      {menu && (
        <Menu
          // remounted per open: Menu measures its own height, and a stale one
          // from the last menu would misplace this one for a frame
          key={menu.key}
          anchor={menu.anchor}
          head={labelOf(menu.row)}
          onClose={() => setMenu(null)}
          items={[
                  ...(menu.here.length
                    ? []
                    : [{ label: '＋ new agent here', onPress: () => open(menu.p, menu.row) }]),
                  { label: 'switch branch', onPress: () => setSheet({ mode: 'branch', cwd: menu.row.path }) },
                  ...(menu.here.length
                    ? [{
                        label: menu.here.length > 1 ? `close ${menu.here.length} agents` : 'close agent',
                        danger: true,
                        onPress: () =>
                          run(menu.row.path, () => closeHere(menu.p, menu.row, menu.here)),
                      }]
                    : []),
                  // the main checkout cannot be removed -- the server answers
                  // 409 every time, so it is not offered
                  ...(menu.row.main
                    ? []
                    : [{
                        label: 'close + delete worktree',
                        danger: true,
                        onPress: () => closeAndRemove(menu.p, menu.row, menu.here),
                      }]),
          ]}
        />
      )}

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
  dots: { paddingHorizontal: 6, paddingVertical: 2 },
  // smaller and quieter than the agent cards' own ⋮: these rows are 28px and
  // there are one of these per worktree, so at the card's weight they became
  // the loudest thing in the group
  dotsGlyph: { color: C.edge, fontSize: 13 },
  // a fixed column so the branch names line up whether or not the row has one
  kind: { color: C.edge, fontSize: 11, width: 9 },
  plus: { color: C.faint, fontSize: 13 },
  forgetX: { color: C.edge, fontSize: 14 },
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
