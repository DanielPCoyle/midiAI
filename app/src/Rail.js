import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {
  addProject,
  chooseDir,
  closeAgent,
  forgetProject,
  listProjects,
  openWorktree,
  addToQueue,
  removeWorktree,
} from './api';
import Icon from './Icon';
import { Menu, MenuButton } from './Menu';
import { useMcpDown } from './Mcps';
import { inside } from './Projects';
import SessionSheet from './SessionSheet';
import { C, S, SEAT_HEX, fillHue, seatHue, seatWord } from './theme';

// One worktree's name: the branch, or a short sha when it is detached. The
// same rule Projects.js uses for its own rows -- not imported, because only
// `inside` is exported from there and four lines here beat a second file
// this task is not allowed to touch.
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

// Worst-first, so a collapsed repo's one dot answers "is anything blocked in
// here" rather than only "is anything here at all".
const STATUS_PRIORITY = ['blocked', 'working', 'done', 'idle', 'unknown'];
const summaryHue = (seatsHere) => {
  if (!seatsHere.length) return null;
  const words = new Set(seatsHere.map((s) => seatWord(s.col) || 'unknown'));
  for (const w of STATUS_PRIORITY) if (words.has(w)) return SEAT_HEX[w] || C.faint;
  return C.faint;
};

// The left rail: a fixed header, one scrolling body that is the tree, and a
// fixed footer with the door into MCPs.
//
// The tree is repos over their worktrees over the agents living in each, and
// it is the whole body now. PROJECTS and AGENTS used to be two capped scrolls
// stacked in one rail scroll, and the fact that mattered most -- which
// worktree a given agent is living in -- was not drawn anywhere; you read a
// card's cwd and matched it against the tree above by eye. Placement is now
// the tree's own structure.
//
// Placement is longest-match: this repo keeps worktrees under
// `.claude/worktrees/` INSIDE the main checkout, so a first-match rule would
// file every one of them under the checkout instead. `inside()` from
// Projects.js is the one place that predicate is defined; App.js's own
// `place` follows the same rule for the same reason -- this file follows it
// a third time rather than inventing a fourth version that could disagree.
//
// MCPS used to be a second body here, behind the footer's row swapping the
// scroll in place. The manager itself (scope tabs, health checking, the add
// form, every endpoint) moved into Settings.js's MCPs section as McpManager,
// in app/src/Mcps.js -- unchanged behaviour, a different home. The footer's
// row is now just a door: press it and `onOpenSettings('mcps')` opens that
// section there. The row keeps its red down-count because that is worth
// seeing without opening anything, so it still polls -- through Mcps.js's
// useMcpDown, the light half of what McpManager itself runs.
//
// props:
//   cols       array(8)                 -- unchanged: each slot null or
//                                           { name, status, model, effort, sub, tid, focused, unseen, context, cwd? }.
//                                           `cwd` is what the tree groups by, and what the 'new' and
//                                           'worktree' sheets default their cwd field to.
//   current    number                   -- unchanged: index of the focused seat
//   onSeat     (i) => void              -- unchanged: tap a card to focus it
//   base       string                   -- api base url, threaded straight through to SessionSheet,
//                                           which does the actual create/rename/close/worktree calls
//   onPick     (pick|null) => void      -- a worktree with nobody in it was picked, and the pane
//                                          draws the panel for it -- called straight from here now
//                                          that the tree is drawn here rather than handed to Projects.js
//   subs       array                    -- the focused agent's subagents, `[{label, type, running}]`,
//                                          straight off surface.views_data.sessions[0].rows. Pane.js's
//                                          sub-tabs are what draws these; a nested tree row has no width
//                                          left for a third fact once the name and model are drawn, so
//                                          this prop goes unread here now.
//   scopePath  string                    -- the worktree "here" means: the one picked in the tree,
//                                          else the one the focused agent is in. What the AGENTS
//                                          filter filters to.
//   onChanged  () => void               -- fired after any create/close/rename/worktree/branch
//                                           operation succeeds, so the coordinator knows to refetch
//                                           /agents and refresh `cols`
//   onOpenSettings (section) => void    -- opens Settings.js at the given section; pressing the
//                                           footer's MCPs row calls this with 'mcps' instead of
//                                           flipping a local tab, since the panel lives there now
export default function Rail({
  cols,
  current,
  onSeat,
  base,
  onChanged,
  onPick,
  subs = [],
  scopePath = '',
  onOpenSettings,
}) {
  // { mode: 'new'|'rename'|'close', seatIndex: number|null } | null -- agent errands
  const [sheet, setSheet] = useState(null);
  // Nothing tells the tree a worktree was made or removed on its own -- the
  // server owns that list, not the surface poll. Every operation here bumps
  // this, and the project fetch below re-asks whenever it does.
  const [beat, setBeat] = useState(0);
  const changed = () => {
    setBeat((b) => b + 1);
    onChanged && onChanged();
  };
  const free = 8 - cols.filter(Boolean).length;

  // -- the tree: repos, their worktrees, and the agents living in each -----
  const [projects, setProjects] = useState([]);
  const [projLoading, setProjLoading] = useState(false);
  const [shut, setShut] = useState({});      // project path -> collapsed
  const [wbusy, setWbusy] = useState(null);  // path of the row a project errand is running against
  const [werr, setWerr] = useState('');       // a button in the tree failed
  const [listErr, setListErr] = useState(''); // the list itself failed to load
  // { mode: 'project'|'branch'|'worktree', cwd } | null -- SessionSheet does the picking and the call
  const [wsheet, setWsheet] = useState(null);
  // { key, anchor, p, row, here } | null -- Menu.js draws it against the ⋮ that opened it
  const [wmenu, setWmenu] = useState(null);

  const seats = cols.map((c, i) => ({ col: c, i })).filter((s) => s.col && s.col.cwd);
  // Sorted and joined so the effect fires on a real change of repo set, not on
  // every 400ms surface poll handing back the same cwds in the same order.
  const projKey = [...new Set(seats.map((s) => s.col.cwd))].sort().join('\n');

  useEffect(() => {
    let live = true;
    let retry;
    setProjLoading(true);
    listProjects(base)
      // a success clears a failure: one dropped request (mapui restarting)
      // used to leave "Failed to fetch" under the list for good
      .then((rows) => { if (live) { setProjects(rows); setListErr(''); } })
      .catch((e) => {
        if (!live) return;
        setListErr(`couldn't list projects: ${e.message || e}`);
        retry = setTimeout(() => setBeat((b) => b + 1), 5000);   // and ask again
      })
      .finally(() => live && setProjLoading(false));
    return () => { live = false; clearTimeout(retry); };
  }, [base, projKey, beat]);

  const refreshProjects = () => listProjects(base)
    .then((rows) => { setProjects(rows); setListErr(''); }).catch(() => {});

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

  // Every button in the tree runs one of these: guard against a double tap,
  // say what went wrong in the rail rather than in a modal, and let go of
  // `wbusy` whatever happened.
  async function run(at, fn) {
    if (wbusy) return;
    setWerr('');
    setWbusy(at);
    try {
      await fn();
    } catch (e) {
      setWerr(e.message || String(e));
    } finally {
      setWbusy(null);
    }
  }

  // Every agent living in this worktree, ended. The seats hold the terminal
  // id, which is the handle /agents/close takes.
  async function closeHere(project, row, here) {
    for (const s of here) await closeAgent(base, s.col.tid);
    changed();
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
      await refreshProjects();
      changed();
      onPick && onPick(null);   // it is gone; nothing to be looking at
    });
  }

  function openHereWt(project, row) {
    run(row.path, async () => {
      await openWorktree(base, project.path, row.path);
      changed();
    });
  }

  function tapWt(project, row, here) {
    // already open -- go to it. More than one agent in a worktree and this
    // takes the first; the rows nested underneath are where you pick between them.
    if (here.length) {
      onPick && onPick(null);
      return onSeat(here[0].i);
    }
    // Empty: select it rather than starting something. Tapping used to spawn
    // a claude on the spot, which is a lot to happen from one tap on a list --
    // now the pane says there is nobody here and offers the buttons for it.
    onPick && onPick(row.exists ? pickOf(project, row) : null);
  }

  // Eight seats is few enough to read at a glance and too many to read while
  // you are working in one repo of three. `here` is not a default: hiding
  // agents by default is how you lose one, so you have to ask.
  const [onlyHere, setOnlyHere] = useState(false);
  // The tree's own search. MCPs had one too, but it is Mcps.js's own state
  // now -- a field that emptied itself every time you looked at the other
  // body used to read as two search problems sharing one box; two bodies in
  // two places no longer need to share anything.
  const [q, setQ] = useState('');
  const find = q.trim().toLowerCase();
  const hitAgent = (col) =>
    !find ||
    `${col.name} ${col.model || ''} ${col.effort || ''} ${col.cwd || ''}`
      .toLowerCase()
      .includes(find);
  const inScope = (col) => !!scopePath && !!col?.cwd && inside(col.cwd, scopePath);
  const hidden = onlyHere ? cols.filter((c) => c && !inScope(c)).length : 0;
  // Every live agent's cwd, for the footer's down-count -- the same set
  // McpManager itself would read from, were it mounted here.
  const mcpCwds = [...new Set(cols.filter(Boolean).map((c) => c.cwd).filter(Boolean))];
  // The one number the footer's row is worth interrupting you for. A zero
  // stays unshown rather than drawn faint -- a faint "0" and a faint "3" read
  // the same at a glance, and that exact confusion is MIDI-014.
  const mcpDown = useMcpDown(base, mcpCwds);

  // The card's corner used to be one ⋮ over a menu. It is four keys now: the
  // three things you actually do to a running agent are one tap each rather
  // than a tap, a read and a tap.
  //
  // compact and clear are typed, not called -- /compact and /clear are Claude
  // Code's own commands and there is no API behind them. They go through the
  // up-next queue, which types them when the agent is free, never mid-turn.
  const say = (col, text) => addToQueue(base, col.tid, text).catch(() => {});
  // clear throws away everything the agent knows, and it is a 20px target in a
  // 268px column. So it arms first: one tap reddens it, the next does it, and
  // three seconds of not meaning it puts it back.
  const [armed, setArmed] = useState(null);   // tid, or null
  const armTimer = useRef(null);
  const arm = (col) => {
    if (armed === col.tid) {
      clearTimeout(armTimer.current);
      setArmed(null);
      say(col, '/clear');
      return;
    }
    setArmed(col.tid);
    clearTimeout(armTimer.current);
    armTimer.current = setTimeout(() => setArmed(null), 3000);
  };

  const seatFor = (i) => (i != null ? cols[i] : null);
  const activeSeat = sheet && sheet.mode !== 'new' ? seatFor(sheet.seatIndex) : seatFor(current);

  // Search now filters the whole tree, not only the agents in it: a project
  // matching keeps every worktree under it, a worktree matching keeps itself,
  // and an agent matching keeps the worktree it is living in even if neither
  // of their own names do.
  const hitProject = (p) => !find || p.name.toLowerCase().includes(find);
  const hitWorktree = (row) => !find || labelOf(row).toLowerCase().includes(find);
  const visibleProjects = projects
    .map((p) => {
      const at = seatsIn(p);
      const rows = p.worktrees.filter((row) => {
        if (!find) return true;
        const here = at[row.path] || [];
        return hitProject(p) || hitWorktree(row) || here.some((s) => hitAgent(s.col));
      });
      // The one row that reads as selected: the worktree "here" is in (the
      // picked one, else the focused agent's), longest match so a nested
      // worktree wins over its checkout. Having an agent is not being
      // selected -- two repos each with an agent on main lit both mains.
      const sel = scopePath
        ? p.worktrees.filter((r) => inside(scopePath, r.path))
          .sort((a, b) => b.path.length - a.path.length)[0]?.path
        : null;
      return { p, at, rows, sel };
    })
    .filter(({ p, rows }) => !find || hitProject(p) || rows.length > 0);

  // Adding a project is picking a folder, so in the browser it is the Mac's
  // own Finder chooser and nothing else -- chosen is added. The tablet keeps
  // the sheet with its in-app folder list: the Finder window would open on
  // the Mac, across the room, where nobody is looking.
  const [picking, setPicking] = useState(false);
  const [addErr, setAddErr] = useState('');
  async function addProjectFolder() {
    if (Platform.OS !== 'web') return setWsheet({ mode: 'project' });
    if (picking) return;               // the dialog is already up
    setPicking(true);
    setAddErr('');
    try {
      const path = await chooseDir(base, '', 'midiAI: add a project');
      if (path) {                       // null is a cancel: nothing to do
        await addProject(base, path);
        refreshProjects();
      }
    } catch (e) {
      setAddErr(String((e && e.message) || e));
    } finally {
      setPicking(false);
    }
  }

  return (
    <View style={styles.rail}>
      {/* Fixed chrome: search, the scope filter and adding a project. Above
          the body scroll, not merely first inside it -- a control that slides
          away the longer the tree gets is a control you stop finding. */}
      <View style={styles.headRow}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="add a project"
          onPress={addProjectFolder}
          style={styles.addRow}>
          <Text style={styles.addText}>{picking ? 'choosing a folder on the Mac…' : '＋ add project'}</Text>
        </Pressable>
        <View style={styles.spacer} />
        {/* scoping to "here" is a fact about the worktree picked in the tree,
            offered whenever there is one to mean */}
        {!!scopePath &&
          [
            ['all', false],
            ['here', true],
          ].map(([word, on]) => (
            <Text
              key={word}
              accessibilityRole="button"
              accessibilityState={{ selected: onlyHere === on }}
              onPress={() => setOnlyHere(on)}
              style={[styles.filter, onlyHere === on && styles.filterOn]}>
              {word}
            </Text>
          ))}
      </View>
      {!!addErr && <Text style={styles.addErr}>{addErr}</Text>}
      <TextInput
        value={q}
        onChangeText={setQ}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
        accessibilityLabel="search agents & repos"
        placeholder="search agents & repos"
        placeholderTextColor={C.faint}
        style={styles.find}
      />

      <ScrollView style={styles.bodyScroll} contentContainerStyle={styles.scroll}>
        {/* The tree: repos, their worktrees, and the agents in each. There is
            no capped inner scroll left inside it -- the whole thing rides
            this one body scroll now. */
        <View style={styles.list}>
          {!projects.length && !projLoading && (
            <Text style={styles.empty}>no projects yet</Text>
          )}
          {!!find && projects.length > 0 && visibleProjects.length === 0 && (
            <Text style={styles.empty}>no matches for “{q.trim()}”</Text>
          )}
          {visibleProjects.map(({ p, at, rows, sel }) => {
            const isOpen = !shut[p.path];
            const held = Object.keys(at).length > 0;
            const repoHue = summaryHue(Object.values(at).flat());
            return (
              <View key={p.path}>
                <View style={styles.project}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`${p.name} — ${isOpen ? 'collapse' : 'expand'}`}
                    onPress={() => setShut((s) => ({ ...s, [p.path]: isOpen }))}
                    style={styles.projectPick}>
                    <Text style={styles.caret}>{isOpen ? '▾' : '▸'}</Text>
                    <Text style={styles.projectName} numberOfLines={1}>
                      {p.name}
                    </Text>
                    <Text style={styles.count}>{p.worktrees.length}</Text>
                  </Pressable>
                  {/* collapsed, the tree still answers "is anything blocked in
                      here" -- worst status among everything nested underneath */}
                  {!isOpen && !!repoHue && (
                    <View style={[styles.repoDot, { backgroundColor: repoHue }]} />
                  )}
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
                    onPress={() => setWsheet({ mode: 'worktree', cwd: p.path })}
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
                          await refreshProjects();
                        })
                      }
                      style={styles.dots}>
                      <Text style={styles.forgetX}>×</Text>
                    </Pressable>
                  )}
                </View>

                {isOpen &&
                  rows.map((row) => {
                    const hereAll = at[row.path] || [];
                    const shownHere = hereAll.filter(
                      (s) => (!onlyHere || inScope(s.col)) && hitAgent(s.col));
                    const label = labelOf(row);
                    const seat = hereAll[0] || null;
                    return (
                      <View key={row.path}>
                        <View style={styles.wtSlot}>
                          <Pressable
                            accessibilityRole="button"
                            accessibilityLabel={
                              seat
                                ? `${label} — focus ${seat.col.name}`
                                : `${label} — open an agent here`
                            }
                            onPress={() => tapWt(p, row, hereAll)}
                            style={[styles.wt, row.path === sel && styles.wtOn]}>
                            <View
                              style={[
                                styles.wtDot,
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
                            {row.main && label !== 'main' && p.git !== false && <Text style={styles.tag}>main</Text>}
                            {!row.exists && <Text style={styles.tagGone}>gone</Text>}
                            {/* "nothing is running here" is a fact worth seeing --
                                selecting an empty worktree is how you pick a
                                checkout to read guardrails/CI for */}
                            {!hereAll.length && <Text style={styles.tag}>empty</Text>}
                            {wbusy === row.path && (
                              <ActivityIndicator size="small" color={C.faint} />
                            )}
                          </Pressable>
                          <MenuButton
                            accessibilityLabel={`more actions for ${label}`}
                            onOpen={(anchor) =>
                              setWmenu({ key: row.path, anchor, p, row, here: hereAll })
                            }
                            style={styles.dots}
                            glyphStyle={styles.dotsGlyph}
                          />
                        </View>

                        {shownHere.map(({ col, i }) => {
                          const hue = seatHue(col);
                          const on = i === current;
                          // how full this agent's context is, 0..1 -- the same number
                          // the conversation's context bar reads
                          const ctx = Number.isFinite(col.context) ? Math.max(0, Math.min(1, col.context)) : null;
                          return (
                            <View key={i} style={styles.agentSlot}>
                              <Pressable
                                accessibilityRole="button"
                                // left to itself this announces as "midiAIidle" -- the
                                // name and the status run together, same shape as the
                                // tab that answered to "· 0"
                                accessibilityLabel={`${col.name} · ${seatWord(col)}${ctx != null ? ` · context ${Math.round(ctx * 100)}% full` : ''}`}
                                accessibilityState={{ selected: on }}
                                onPress={() => onSeat(i)}
                                style={[styles.agentRow, on && styles.agentRowOn]}>
                                <View style={[styles.dot, { backgroundColor: hue }]} />
                                <Text
                                  style={[styles.name, !on && styles.nameOff]}
                                  numberOfLines={1}>
                                  {col.name}
                                </Text>
                                {/* Codex is the one engine besides Claude Code so far --
                                    a plain "claude" badge next to every other row would
                                    be noise, so only the exception is marked. Absent on
                                    an older server that sends no `engine` field at all,
                                    which reads the same as "claude". */}
                                {col.engine === 'codex' && (
                                  <Text style={styles.engineBadge}>codex</Text>
                                )}
                                {[col.model, col.effort]
                                  .filter(Boolean)
                                  .map((bit, k) => (
                                    <Text key={k} style={styles.meta} numberOfLines={1}>
                                      {k ? `· ${bit}` : bit}
                                    </Text>
                                  ))}
                                {ctx != null && (
                                  <View pointerEvents="none" style={styles.ctxTrack}>
                                    <View style={[styles.ctxFill, { width: `${ctx * 100}%`, backgroundColor: fillHue(ctx) }]} />
                                  </View>
                                )}
                              </Pressable>
                              {/* the four keys are a 268px column's worth on their
                                  own once nested this deep, so only the selected
                                  agent gets them -- everyone else keeps the hue,
                                  the name and the model, same as the tap target
                                  above shows before you have picked one */}
                              {on && (
                                <View style={styles.keys}>
                                  {[
                                    // U+FE0E, or Chrome hands ✎ to the emoji font and
                                    // one key in four comes out in colour beside three
                                    // flat glyphs
                                    ['✎︎', `rename ${col.name}`,
                                      () => setSheet({ mode: 'rename', seatIndex: i })],
                                    ['⊟', `compact ${col.name}'s context`,
                                      () => say(col, '/compact')],
                                    [
                                      '⊘',
                                      armed === col.tid
                                        ? `clear ${col.name}'s context — tap again to confirm`
                                        : `clear ${col.name}'s context`,
                                      () => arm(col),
                                      armed === col.tid,
                                    ],
                                    ['×', `close ${col.name}`,
                                      () => setSheet({ mode: 'close', seatIndex: i }), true],
                                  ].map(([glyph, label2, fn, hot]) => (
                                    <Pressable
                                      key={glyph}
                                      accessibilityRole="button"
                                      accessibilityLabel={label2}
                                      hitSlop={4}
                                      onPress={fn}
                                      style={styles.keyBtn}>
                                      <Text style={[styles.keyGlyph, hot && styles.keyBad]}>
                                        {glyph}
                                      </Text>
                                    </Pressable>
                                  ))}
                                </View>
                              )}
                            </View>
                          );
                        })}
                        {/* new agents start under the branch they will run on, and
                            the sheet is told which one -- there is no "where" to pick */}
                        {row.exists && free > 0 && (
                          <Pressable
                            accessibilityRole="button"
                            accessibilityLabel={`new agent on ${label} in ${p.name}`}
                            onPress={() => setSheet({
                              mode: 'new', seatIndex: null, cwd: row.path, place: `${p.name} › ${label}`,
                            })}
                            style={styles.newHere}>
                            <Text style={styles.newHereText}>＋ new agent</Text>
                          </Pressable>
                        )}
                      </View>
                    );
                  })}
              </View>
            );
          })}
          {/* the answer to "where did the rest go" -- onlyHere hides agents,
              never the worktrees they would otherwise sit under */}
          {hidden > 0 && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`show all agents — ${hidden} hidden`}
              onPress={() => setOnlyHere(false)}
              style={styles.hidden}>
              <Text style={styles.hiddenText}>
                {hidden} elsewhere — show all
              </Text>
            </Pressable>
          )}
          {!!listErr && <Text style={styles.err}>{listErr}</Text>}
          {!!werr && <Text style={styles.err}>{werr}</Text>}
        </View>}
      </ScrollView>

      {/* Fixed footer: the door to MCPS, and the one action that always needs
          to be reachable without scrolling past however many repos there are. */}
      <View style={styles.footer}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={mcpDown > 0 ? `MCPs — ${mcpDown} down` : 'MCPs'}
          onPress={() => onOpenSettings && onOpenSettings('mcps')}
          style={styles.mcpsRow}>
          <Icon name="mcp" size={17} color={C.faint} />
          <Text style={styles.mcpsRowText}>MCPs</Text>
          <View style={styles.spacer} />
          {/* a zero here is a settled fact, not a warning -- a faint "0" and a
              faint "3" would read the same at a glance (MIDI-014) */}
          {mcpDown > 0 && (
            <>
              <Icon name="trouble" size={15} color={C.bad} />
              <Text style={styles.mcpsRowDown}>{mcpDown}</Text>
            </>
          )}
        </Pressable>
      </View>

      {sheet && (
        <SessionSheet
          visible
          mode={sheet.mode}
          base={base}
          tid={activeSeat?.tid}
          name={activeSeat?.name}
          cwd={sheet.cwd ?? activeSeat?.cwd}
          place={sheet.place}
          onClose={() => setSheet(null)}
          onDone={() => {
            setSheet(null);
            changed();
          }}
        />
      )}

      {wsheet && (
        <SessionSheet
          visible
          mode={wsheet.mode}
          base={base}
          cwd={wsheet.cwd}
          onClose={() => setWsheet(null)}
          onDone={() => {
            setWsheet(null);
            refreshProjects();
            // a branch switch changes what the agents in that worktree are
            // sitting on top of, which is the coordinator's business too
            if (wsheet.mode === 'branch') changed();
          }}
        />
      )}

      {wmenu && (
        <Menu
          // remounted per open: Menu measures its own height, and a stale one
          // from the last menu would misplace this one for a frame
          key={wmenu.key}
          anchor={wmenu.anchor}
          head={labelOf(wmenu.row)}
          onClose={() => setWmenu(null)}
          items={[
                  ...(wmenu.here.length
                    ? []
                    : [{ label: '＋ new agent here', onPress: () => openHereWt(wmenu.p, wmenu.row) }]),
                  { label: 'switch branch', onPress: () => setWsheet({ mode: 'branch', cwd: wmenu.row.path }) },
                  ...(wmenu.here.length
                    ? [{
                        label: wmenu.here.length > 1 ? `close ${wmenu.here.length} agents` : 'close agent',
                        danger: true,
                        onPress: () =>
                          run(wmenu.row.path, () => closeHere(wmenu.p, wmenu.row, wmenu.here)),
                      }]
                    : []),
                  // the main checkout cannot be removed -- the server answers
                  // 409 every time, so it is not offered
                  ...(wmenu.row.main
                    ? []
                    : [{
                        label: 'close + delete worktree',
                        danger: true,
                        onPress: () => closeAndRemove(wmenu.p, wmenu.row, wmenu.here),
                      }]),
          ]}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  rail: {
    width: 268,
    borderRightWidth: 1,
    borderRightColor: C.line,
    paddingVertical: S.pad,
    paddingHorizontal: 12,
  },
  headRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  addErr: { color: C.bad, fontSize: 11 },
  spacer: { flex: 1 },
  filter: { color: C.faint, fontSize: 11, paddingHorizontal: 2 },
  filterOn: { color: C.accentText },
  hidden: { minHeight: 26, justifyContent: 'center', paddingHorizontal: 4 },
  hiddenText: { color: C.edge, fontSize: 11 },
  bodyScroll: { flex: 1 },
  scroll: { paddingBottom: 12 },
  find: {
    height: 30,
    marginTop: 8,
    color: C.text,
    backgroundColor: C.panel,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 6,
    paddingHorizontal: 9,
    fontSize: 12,
  },
  list: { gap: S.gap, paddingTop: 10 },

  // -- header row's own add-project key -------------------------------------
  addRow: { minHeight: 30, justifyContent: 'center', paddingHorizontal: 4 },
  addText: { color: C.edge, fontSize: 11 },

  // -- the tree: repo, worktree, agent, three levels in one list ------------
  project: { flexDirection: 'row', alignItems: 'center' },
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
  // smaller and quieter than a card's own ⋮: these rows are 28px and there
  // are one of these per worktree, so at heavier weight they would become
  // the loudest thing in the group
  dotsGlyph: { color: C.edge, fontSize: 13 },
  // a fixed column so the branch names line up whether or not the row has one
  kind: { color: C.edge, fontSize: 11, width: 9 },
  plus: { color: C.faint, fontSize: 13 },
  forgetX: { color: C.edge, fontSize: 14 },
  // collapsed, the repo still needs to answer "is anything blocked in here"
  repoDot: { width: 8, height: 8, borderRadius: 4, marginHorizontal: 2 },
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
  wtDot: { width: 6, height: 6, borderRadius: 3 },
  // a worktree nobody is in still gets the dot's width, so the branch names
  // line up whether or not an agent is living there
  dotOff: { borderWidth: 1, borderColor: C.edge },
  branch: { color: C.dim, fontSize: 12, flexShrink: 1 },
  branchOn: { color: C.text },
  gone: { color: C.faint, textDecorationLine: 'line-through' },
  tag: { color: C.edge, fontSize: 10 },
  tagGone: { color: C.warn, fontSize: 10 },
  err: { color: C.bad, fontSize: 11, paddingHorizontal: 4, paddingTop: 6 },

  // the agent, nested one level past its worktree
  agentSlot: { flexDirection: 'row', alignItems: 'center' },
  agentRow: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 7,
    minHeight: 28,
    paddingLeft: 34,
    paddingRight: 4,
    borderRadius: 4,
  },
  agentRowOn: { backgroundColor: C.raised },
  // under the name, from where the name starts to the row's end
  ctxTrack: { position: 'absolute', left: 34, right: 4, bottom: 2, height: 2, borderRadius: 1, backgroundColor: C.line, overflow: 'hidden' },
  ctxFill: { height: 2, borderRadius: 1 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  name: { color: C.text, fontSize: 13, fontWeight: '600', flexShrink: 1 },
  nameOff: { color: C.dim },
  meta: { color: C.faint, fontSize: 11, flexShrink: 1 },
  engineBadge: {
    color: C.accentText,
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
    borderWidth: 1,
    borderColor: C.accent,
    borderRadius: 3,
    paddingHorizontal: 4,
    paddingVertical: 1,
  },
  keys: { flexDirection: 'row' },
  keyBtn: { paddingHorizontal: 4, paddingVertical: 2 },
  keyGlyph: { color: C.edge, fontSize: 13 },
  keyBad: { color: C.bad },

  // -- fixed footer: MCPs door, then the one action always worth reaching ---
  footer: {
    borderTopWidth: 1,
    borderTopColor: C.line,
    paddingTop: 8,
    gap: 8,
  },
  mcpsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: 38,
    paddingHorizontal: 4,
    borderRadius: S.radius,
  },
  mcpsRowText: { color: C.dim, fontSize: 13, fontWeight: '600' },
  mcpsRowDown: { color: C.bad, fontSize: 11, fontWeight: '600' },

  newHere: { paddingVertical: 3, paddingLeft: 22 },
  newHereText: { color: C.faint, fontSize: 11 },
  empty: { color: C.edge, fontSize: 11, paddingHorizontal: 4, paddingVertical: 8 },
});
