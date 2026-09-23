// Scaffolds for a new workflow, in the shape the repos here already use.
//
// These are not GitHub's starter templates. Every one of the seven workflows
// in simplerdevelopment2026 opens with the same banner — what it does, what
// secret it needs, and what happens on a fork that has not got it — because a
// workflow is read months later by someone deciding whether it is safe to
// change. A blank file does not ask for that paragraph and so does not get it.
//
// Three habits are baked in for the same reason:
//   - `concurrency` on anything a rapid push can start twice, so three runs of
//     the same ref are not racing for a runner.
//   - a `check-secret` gate ahead of any job that needs a secret, so a fork
//     skips cleanly rather than showing a red required check nobody can fix.
//   - action majors pinned (checkout@v7, setup-node@v7, setup-bun@v2) rather
//     than floating, and every job and step named.

// 'publish-cli' -> 'Publish Cli'. Wrong for acronyms, and the field is right
// there to fix it -- a name the author retypes is better than one they have to
// notice is wrong.
const title = (slug) =>
  slug.split('-').filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');

const banner = (slug, body) =>
  `# =============================================================================
# ${slug}.yml — ${title(slug)}
#
${body}
# =============================================================================
`;

export const TEMPLATES = [
  {
    key: 'check',
    label: 'PR gate',
    what: 'Install, typecheck, test on every PR to main. No secret.',
    body: (slug) => `${banner(slug, `# WHAT IT DOES
#   Runs on every PR to main and on main itself: install, typecheck, test.
#   Superseded runs on the same ref are cancelled, so a rapid push does not
#   leave three of these racing for a runner.
#
# COST
#   One ubuntu-latest runner per push. If this grows past a few minutes, add a
#   changed-paths job ahead of it rather than making every PR wait.`)}
name: ${title(slug)}

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: ${slug}-\${{ github.ref }}
  cancel-in-progress: true

jobs:
  ${slug}:
    name: ${title(slug)}
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Checkout repository
        uses: actions/checkout@v7

      - name: Set up Bun
        uses: oven-sh/setup-bun@v2

      - name: Install dependencies
        run: bun install --frozen-lockfile

      - name: Typecheck
        run: bun run typecheck

      - name: Test
        run: bun run test --run
`,
  },
  {
    key: 'publish',
    label: 'Tagged publish',
    what: 'Publish to npm on a version tag, gated on NPM_TOKEN.',
    body: (slug) => `${banner(slug, `# REQUIRES
#   Repo secret: NPM_TOKEN
#     An npm automation token with publish rights. Settings → Secrets and
#     variables → Actions → Repository secrets.
#
# HOW TO RELEASE
#   1. Bump the version in package.json.
#   2. Tag the commit \`${slug}-v<version>\` and push the tag, OR trigger this
#      workflow manually from the Actions tab (workflow_dispatch).
#   The publish step is idempotent-safe: npm rejects re-publishing a version
#   that exists, so a re-run of an already-published tag fails loudly rather
#   than silently overwriting.
#
# WITHOUT THE SECRET
#   The gate job below sees the missing token and skips the publish cleanly,
#   so forks and a pre-setup repo do not see a red required check.`)}
name: ${title(slug)}

on:
  push:
    tags:
      - '${slug}-v*'
  workflow_dispatch:

jobs:
  check-secret:
    name: Check npm token
    runs-on: ubuntu-latest
    outputs:
      configured: \${{ steps.check.outputs.configured }}
    steps:
      - id: check
        env:
          NPM_TOKEN: \${{ secrets.NPM_TOKEN }}
        run: |
          if [ -n "$NPM_TOKEN" ]; then
            echo "configured=true" >> "$GITHUB_OUTPUT"
          else
            echo "configured=false" >> "$GITHUB_OUTPUT"
            echo "NPM_TOKEN secret not set — skipping publish."
          fi

  publish:
    name: Build and publish
    needs: check-secret
    if: needs.check-secret.outputs.configured == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write # required for npm provenance
    steps:
      - name: Checkout repository
        uses: actions/checkout@v7

      - name: Set up Node (with npm registry auth)
        uses: actions/setup-node@v7
        with:
          node-version: 22
          registry-url: https://registry.npmjs.org

      - name: Set up Bun
        uses: oven-sh/setup-bun@v2

      # Install from the repo root -- a workspace member resolves against the
      # root lockfile, not a stray one of its own.
      - name: Install workspace dependencies
        run: bun install --frozen-lockfile

      - name: Build
        run: bun run build

      - name: Publish to npm
        run: npm publish --provenance
        env:
          NODE_AUTH_TOKEN: \${{ secrets.NPM_TOKEN }}
`,
  },
  {
    key: 'nightly',
    label: 'Nightly + on demand',
    what: 'Too expensive for every PR: runs on a cron, and on request.',
    body: (slug) => `${banner(slug, `# WHY NIGHTLY, NOT PER-PR
#   Say here what one run costs and why the PR path does not pay it -- the
#   next person's first question is why this is not a merge gate, and the
#   answer only exists in somebody's head until it is written down.
#   To promote it: add \`pull_request:\` to \`on:\` below and mark the check
#   required in branch protection.
#
# ON DEMAND
#   workflow_dispatch -- run it from the Actions tab without waiting for 03:00.`)}
name: ${title(slug)}

on:
  schedule:
    - cron: '0 3 * * *'   # 03:00 UTC daily
  workflow_dispatch:

concurrency:
  group: ${slug}-\${{ github.ref }}
  cancel-in-progress: true

jobs:
  ${slug}:
    name: ${title(slug)}
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout repository
        uses: actions/checkout@v7

      - name: Set up Bun
        uses: oven-sh/setup-bun@v2

      - name: Install dependencies
        run: bun install --frozen-lockfile

      - name: Run
        run: echo 'replace me'
`,
  },
  {
    key: 'blank',
    label: 'Manual only',
    what: 'One job, run by hand. The least a workflow can be.',
    body: (slug) => `${banner(slug, `# WHAT IT DOES
#   One paragraph, written before the jobs are. The header is the first thing
#   the next person reads and the only place the WHY survives a rewrite.`)}
name: ${title(slug)}

on:
  workflow_dispatch:

jobs:
  ${slug}:
    name: ${title(slug)}
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Checkout repository
        uses: actions/checkout@v7

      - name: Run
        run: echo 'replace me'
`,
  },
];

// The server's own rule, so the field can say no before the round trip does.
export const WORKFLOW_NAME_RE = /^[a-z][a-z0-9-]{0,63}$/;

// The files that govern a pull request without being one. Same idea as the
// workflow templates: a starter that already says the thing the file exists to
// say, because an empty CODEOWNERS is not a head start on anything.
export const GOVERN_STARTERS = {
  'pr-template': `## What changed

<!-- One paragraph. What a reviewer needs before reading the diff. -->

## Why

<!-- The problem, not the solution. Link the issue. -->

## How to verify

- [ ] <the command a reviewer runs, or the thing they click>

## Risk

- [ ] Reversible, no migration, no auth or billing path touched
- [ ] Needs a staged rollout / has a rollback plan (say which below)
`,
  codeowners: `# Who gets asked for review, by path. Last matching line wins, so the
# broadest rule goes first and the sharp exceptions go last.
#
# A path with no owner is a path where "somebody should look at this" is
# nobody's job -- which is how the risky directories end up unreviewed.

*                       @your-org/engineering

# Tighter than the default: the paths where a bad merge is expensive.
/.github/workflows/     @your-org/platform
/infra/                 @your-org/platform
`,
  dependabot: `# What opens the dependency PRs, and how often. Grouping matters more than
# it looks: twenty separate patch PRs a week get rubber-stamped, one grouped
# PR a week gets read.
version: 2
updates:
  - package-ecosystem: npm
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    groups:
      minor-and-patch:
        update-types: [minor, patch]

  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
`,
};
