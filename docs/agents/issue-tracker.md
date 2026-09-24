# Issue tracker: SimplerDevelopment portal

Issues for this repo are kanban cards on the portal board **midiAI**
(`projectId 216`, `clientId 104`), reached through the Simpler Development MCP
(`mcp__claude_ai_Simpler_Development__*`).

**Pass `clientId: 104` on every call.** Never omit it and never let it default:
the portal's default client is 117 (W.H. Peters Outdoor Adventures), which is
not this repo. `whoami` lists the roster if you need it.

The board exists because board 153 says so in as many words — a separate repo
gets its own board, like bugcast (215), Vanta (194) and Cookoojobs (195). The
test is where a change **lands**, not what it is about. Podium is its own repo,
so Podium work does not go on 153.

## Conventions

| Operation | Tool |
|---|---|
| Create | `kanban_create_card` — `projectId`, `columnId`, `title`, `description` |
| Read | `kanban_get_card`, `kanban_card_list_comments` |
| List | `kanban_list_board` — pass `column` and `limit`; a mature board runs to tens of thousands of tokens unfiltered |
| Search | `kanban_cards_search` |
| Comment | `kanban_card_add_comment` |
| Label | `kanban_labels_list` / `kanban_labels_create` / `kanban_card_attach_label` |
| Close | `kanban_update_card` with `workflowState: "done"`, then `kanban_move_card` to Shipped |

Lanes, and their column ids:

| Lane | id | |
|---|---|---|
| Backlog | 942 | |
| Planned | 943 | |
| In Progress | 944 | |
| Validating | 945 | code complete on a branch, awaiting QA + merge |
| Approved | 946 | |
| Shipped | 947 | `isDone` |

Cards carry a SKU in the title: **`MIDI-###`**, assigned once, never renumbered
and never reused.

## When a skill says "publish to the issue tracker"

Create a card in Backlog (column 942).

## When a skill says "fetch the relevant ticket"

`kanban_get_card`, then `kanban_card_list_comments` — the answer to a resolved
wayfinder ticket lives in its comments, not its body.

## Wayfinding operations

Used by `/wayfinder`. The **map** is one card; its tickets are child cards.

- **Map** — a card with `cardType: "epic"` and the label `wayfinder:map`,
  holding Destination / Notes / Decisions-so-far / Not-yet-specified /
  Out-of-scope. Lives in Planned (943).
- **Child ticket** — a card with `parentCardId` set to the map's id. This is
  native hierarchy, so the tree renders in the portal without a task list to
  keep in sync. Label `wayfinder:<type>` — `research`, `prototype`,
  `grilling`, or `task`.
- **Blocking** — native: `kanban_card_add_blocker(cardId, blockerCardId)`,
  read back with `kanban_card_dependencies_list`, removed with
  `kanban_card_remove_blocker`. Both cards must be in the same project. A
  ticket is unblocked when every blocker is `done`.
- **Frontier** — the map's children that are open (`workflowState` neither
  `done` nor `canceled`), carry no open blocker, and have no assignee. First
  in board order wins.
- **Claim** — `kanban_card_assign`, the session's first write, before any
  work. That assignee *is* the claim; an open unassigned child is unclaimed.
- **Resolve** — `kanban_card_add_comment` with the answer, then
  `kanban_update_card` to `workflowState: "done"` and `kanban_move_card` to
  Shipped (947), then append a one-line gist plus card link to the map's
  Decisions-so-far.

Create cards first and wire `parentCardId` / blockers in a second pass — a card
needs an id before anything can reference it.

## Note on labels

This board was cloned from bugcast (215) for its lanes, which dragged roughly
forty labels across from other projects (`VANTA`, `LFQA`, `COOK`, `SEO`, the
`*79` set). They are noise here; ignore them, or delete them. `wayfinder:map`
came across and is genuine.
