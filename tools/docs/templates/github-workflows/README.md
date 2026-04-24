# GitHub workflow templates

The YAML files in this directory are **templates** — Claude Code's
commit PAT lacks the `workflow` scope needed to write to
`.github/workflows/`, so these ship as drop-in copies the maintainer
installs with:

```bash
mkdir -p .github/workflows
cp tools/docs/templates/github-workflows/*.yml .github/workflows/
git add .github/workflows
git commit -m "ci: docs drift workflow (#672)"
```

## Contents

| File | Trigger | Purpose |
|---|---|---|
| `docs-drift.yml` | PR + weekly cron | Nudge on calibration-code PRs without doc edits; open a "drift report" PR weekly. |

Add new workflows here first so Claude Code can push them, then the
human operator promotes them to `.github/workflows/` in a separate
commit with the right token.
