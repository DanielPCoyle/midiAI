import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {
  closeAgent,
  forgetProject,
  listMcps,
  listProjects,
  mcpDo,
  mcpHealth,
  openWorktree,
  promptAgent,
  removeWorktree,
} from './api';
import Icon from './Icon';
import { Menu, MenuButton } from './Menu';
import { inside } from './Projects';
import PushButton from './PushButton';
import SessionSheet from './SessionSheet';
import { C, S, SEAT_HEX, seatHue, seatWord } from './theme';

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

// One worktree's name: the branch, or a short sha when it is detached. The
// same rule Projects.js uses for its own rows -- not imported, because only
// `inside` is exported from there and four lines here beat a second file
// this task is not allowed to touch.
const labelOf = (row) =>
  row.detached ? (row.head || '').slice(0, 7) : row.branch || '(no branch)';

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

// The left rail: a fixed header, one scrolling body that is either the tree
// or the MCPs, and a fixed footer that switches between them.
//
// The tree is repos over their worktrees over the agents living in each --
// and, until you press the footer's MCPs row, the whole body is it. PROJECTS
// and AGENTS used to be two capped scrolls stacked in one rail scroll, and the
// fact that mattered most -- which worktree a given agent is living in -- was
// not drawn anywhere; you read a card's cwd and matched it against the tree
// above by eye. Placement is now the tree's own structure.
//
// Placement is longest-match: this repo keeps worktrees under
// `.claude/worktrees/` INSIDE the main checkout, so a first-match rule would
// file every one of them under the checkout instead. `inside()` from
// Projects.js is the one place that predicate is defined; App.js's own
// `place` follows the same rule for the same reason -- this file follows it
// a third time rather than inventing a fourth version that could disagree.
//
// MCPS used to be a tab beside AGENTS; the tab pair is gone now that AGENTS
// is the tree and always on screen. The footer's MCPs row is the only door
// into it -- pressed once it swaps the body, pressed again it swaps back. Its
// own content (the scope tabs, the health checking, the add form, every
// endpoint) is untouched; only how you get to it moved.
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
export default function Rail({
  cols,
  current,
  onSeat,
  base,
  onChanged,
  onPick,
  subs = [],
  scopePath = '',
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
  const [werr, setWerr] = useState('');
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
    setProjLoading(true);
    listProjects(base)
      .then((rows) => live && setProjects(rows))
      .catch((e) => live && setWerr(e.message || String(e)))
      .finally(() => live && setProjLoading(false));
    return () => { live = false; };
  }, [base, projKey, beat]);

  const refreshProjects = () => listProjects(base).then(setProjects).catch(() => {});

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
  // Which body the scroll is showing -- 'agents' is the tree, 'mcps' is the
  // MCP list. There is no tab pair to click any more; the footer's MCPs row
  // is the only thing that flips this.
  const [tab, setTab] = useState('agents');
  const [mcps, setMcps] = useState([]);
  const [mcpsBusy, setMcpsBusy] = useState(false);
  const [health, setHealth] = useState({ rows: {}, checking: false });
  const [open, setOpen] = useState('');      // the MCP whose actions are showing
  const [busy, setBusy] = useState('');
  const [said, setSaid] = useState('');
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: '', scope: 'local', transport: 'stdio', target: '' });
  const [mcpTab, setMcpTab] = useState('global');
  // One box for both. The tree and a dozen MCPs are two short lists, not two
  // search problems, and a field that emptied itself every time you looked at
  // the other body would read as two.
  // There is no height cap on either body any more. Both used to be capped at
  // 30% of the window because they shared one scroll and thirty-one worktrees
  // would push the other off the bottom of the screen. They no longer share
  // anything -- the footer's MCPs row swaps one for the other -- so whichever
  // is showing takes the whole body scroll. Capping it now just clipped the
  // list with the rest of the rail sitting empty beneath it.
  const [q, setQ] = useState('');
  const find = q.trim().toLowerCase();
  const hitAgent = (col) =>
    !find ||
    `${col.name} ${col.model || ''} ${col.effort || ''} ${col.cwd || ''}`
      .toLowerCase()
      .includes(find);
  const hitMcp = (mcp) =>
    !find ||
    `${mcp.name} ${mcp.scope} ${mcp.transport || ''} ${mcp.endpoint || ''}`
      .toLowerCase()
      .includes(find);
  const inScope = (col) => !!scopePath && !!col?.cwd && inside(col.cwd, scopePath);
  const hidden = onlyHere ? cols.filter((c) => c && !inScope(c)).length : 0;
  const mcpCwds = [...new Set(cols.filter(Boolean).map((c) => c.cwd).filter(Boolean))];
  const mcpCwdKey = mcpCwds.join('\u0000');

  // Read whether or not their tab is showing. They used to be fetched only
  // when it was, which was right until the footer's row started carrying a
  // count of what is broken -- a number whose whole job is to make you look
  // cannot wait until you have looked. One file read per live working
  // directory, and identical user-wide entries collapse.
  useEffect(() => {
    const cwds = mcpCwds.length ? mcpCwds : [''];
    let live = true;
    setMcpsBusy(true);
    Promise.all(cwds.map((cwd) => listMcps(base, cwd).then((rows) => ({ cwd, rows }))))
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
  }, [tab, mcpCwdKey, base, beat]);

  // The card's corner used to be one ⋮ over a menu. It is four keys now: the
  // three things you actually do to a running agent are one tap each rather
  // than a tap, a read and a tap.
  //
  // compact and clear are typed, not called -- /compact and /clear are Claude
  // Code's own commands and there is no API behind them, so this sends the
  // words down the same pty a pad fires into.
  const say = (col, text) => promptAgent(base, text, true, col.tid).catch(() => {});
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
  // The health check is nine seconds and spawns every stdio server, so it is
  // asked for only while this body is open, and the server caches it for two
  // minutes behind that. Polling is how the answer arrives, not how often it
  // is computed.
  const healthCwd = mcpCwds[0] || '';
  useEffect(() => {
    if (!base) return undefined;
    let live = true;
    const ask = () =>
      mcpHealth(base, healthCwd)
        .then((got) => live && setHealth(got))
        .catch(() => {});
    ask();
    // The server caches for two minutes behind this, so polling is how the
    // answer arrives and not how often it is computed. Off-body it is only
    // feeding the footer's count, so it asks a good deal less often.
    const timer = setInterval(ask, tab === 'mcps' ? 4000 : 30000);
    return () => { live = false; clearInterval(timer); };
  }, [tab, base, healthCwd]);

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
  // The one number the footer's row is worth interrupting you for. A zero
  // stays unshown rather than drawn faint -- a faint "0" and a faint "3" read
  // the same at a glance, and that exact confusion is MIDI-014.
  const mcpDown = mcps.filter((mcp) => health.rows?.[mcp.name]?.state === 'down').length;

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
      return { p, at, rows };
    })
    .filter(({ p, rows }) => !find || hitProject(p) || rows.length > 0);

  return (
    <View style={styles.rail}>
      {/* Fixed chrome: search, the scope filter and adding a project. Above
          the body scroll, not merely first inside it -- a control that slides
          away the longer the tree gets is a control you stop finding. */}
      <View style={styles.headRow}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="add a project"
          onPress={() => setWsheet({ mode: 'project' })}
          style={styles.addRow}>
          <Text style={styles.addText}>＋ add project</Text>
        </Pressable>
        <View style={styles.spacer} />
        {/* MCPs are configuration, not work in progress: scoping them to one
            worktree is what the three scope tabs below already do, so this
            would be a second, quieter answer to the same question -- it is
            offered whenever there is a "here" to mean, whichever body is
            showing. */}
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
      <TextInput
        value={q}
        onChangeText={setQ}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
        accessibilityLabel={tab === 'agents' ? 'search agents & repos' : 'search MCPs'}
        placeholder={tab === 'agents' ? 'search agents & repos' : 'search MCPs'}
        placeholderTextColor={C.faint}
        style={styles.find}
      />

      <ScrollView style={styles.bodyScroll} contentContainerStyle={styles.scroll}>
        {tab === 'agents' ? (
        /* The tree: repos, their worktrees, and the agents in each. There is
            no capped inner scroll left inside it -- the whole thing rides
            this one body scroll now. */
        <View style={styles.list}>
          {!projects.length && !projLoading && (
            <Text style={styles.empty}>no projects yet</Text>
          )}
          {!!find && projects.length > 0 && visibleProjects.length === 0 && (
            <Text style={styles.empty}>no matches for “{q.trim()}”</Text>
          )}
          {visibleProjects.map(({ p, at, rows }) => {
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
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`new worktree in ${p.name}`}
                    hitSlop={6}
                    onPress={() => setWsheet({ mode: 'worktree', cwd: p.path })}
                    style={styles.dots}>
                    <Text style={styles.plus}>＋</Text>
                  </Pressable>
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
                            style={[styles.wt, seat && styles.wtOn]}>
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
                            {row.main && label !== 'main' && <Text style={styles.tag}>main</Text>}
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
                          return (
                            <View key={i} style={styles.agentSlot}>
                              <Pressable
                                accessibilityRole="button"
                                // left to itself this announces as "midiAIidle" -- the
                                // name and the status run together, same shape as the
                                // tab that answered to "· 0"
                                accessibilityLabel={`${col.name} · ${seatWord(col)}`}
                                accessibilityState={{ selected: on }}
                                onPress={() => onSeat(i)}
                                style={[styles.agentRow, on && styles.agentRowOn]}>
                                <View style={[styles.dot, { backgroundColor: hue }]} />
                                <Text
                                  style={[styles.name, !on && styles.nameOff]}
                                  numberOfLines={1}>
                                  {col.name}
                                </Text>
                                {[col.model, col.effort]
                                  .filter(Boolean)
                                  .map((bit, k) => (
                                    <Text key={k} style={styles.meta} numberOfLines={1}>
                                      {k ? `· ${bit}` : bit}
                                    </Text>
                                  ))}
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
          {!!werr && <Text style={styles.err}>{werr}</Text>}
        </View>
        ) : (<>
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
        </View></>)}
      </ScrollView>

      {/* Fixed footer: the door to MCPS, and the one action that always needs
          to be reachable without scrolling past however many repos there are. */}
      <View style={styles.footer}>
        {/* Pinned rather than scrolled: installing one is what you came here
            to do when the list has not got it, so it cannot sit underneath
            however many servers the list happens to have. It used to be kept
            above the fold by capping the list's height instead, which clipped
            the list and left the rest of the rail empty beneath it. */}
        {tab === 'mcps' && (
          <>
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
          </>
        )}
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ selected: tab === 'mcps' }}
          accessibilityLabel={mcpDown > 0 ? `MCPs — ${mcpDown} down` : 'MCPs'}
          onPress={() => setTab((t) => (t === 'mcps' ? 'agents' : 'mcps'))}
          style={[styles.mcpsRow, tab === 'mcps' && styles.mcpsRowOn]}>
          <Icon name="mcp" size={17} color={C.faint} />
          <Text style={[styles.mcpsRowText, tab === 'mcps' && styles.mcpsRowTextOn]}>MCPs</Text>
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
        {free > 0 && (
          <PushButton
            colour="transparent"
            accessibilityLabel="new agent"
            onPress={() => setSheet({ mode: 'new', seatIndex: null })}
            style={styles.free}>
            <Text style={styles.freeText}>＋ new agent</Text>
          </PushButton>
        )}
      </View>

      {sheet && (
        <SessionSheet
          visible
          mode={sheet.mode}
          base={base}
          tid={activeSeat?.tid}
          name={activeSeat?.name}
          cwd={activeSeat?.cwd}
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
  dot: { width: 8, height: 8, borderRadius: 4 },
  name: { color: C.text, fontSize: 13, fontWeight: '600', flexShrink: 1 },
  nameOff: { color: C.dim },
  meta: { color: C.faint, fontSize: 11, flexShrink: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 7 },
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
  mcpsRowOn: { backgroundColor: C.raised },
  mcpsRowText: { color: C.dim, fontSize: 13, fontWeight: '600' },
  mcpsRowTextOn: { color: C.text },
  mcpsRowDown: { color: C.bad, fontSize: 11, fontWeight: '600' },

  free: {
    minHeight: 44,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: C.line,
    borderTopColor: C.line, // PushButton's own key style tints the top edge -- flatten it back to the dash
    borderRadius: S.radius,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'transparent',
    paddingBottom: 0,
  },
  freeText: { color: C.edge, fontSize: 11 },
  mcpCard: { borderWidth: 1, borderColor: C.line, borderRadius: S.radius, padding: 12, gap: 6 },
  mcpTabs: { flexDirection: 'row', flexWrap: 'wrap', gap: 9, paddingTop: 10, paddingHorizontal: 4 },
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
  empty: { color: C.edge, fontSize: 11, paddingHorizontal: 4, paddingVertical: 8 },
});
