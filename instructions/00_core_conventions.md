<!-- BEGIN managed-from-template:v1 (do not edit between markers; run sync-templates.sh) -->
# 00 — Core conventions

Non-negotiable conventions inherited from the env template.

## Commit hygiene
- Imperative mood ("Add", not "Added")
- ≤72 chars subject line
- Body when context matters
- No `git commit --no-verify` without first reading what the hook flagged

## File operations
- Use `rg`/Grep tool for search, not `grep -r`
- Use the dedicated read/write/edit tools, not `cat`/`echo`/`sed`
- Quote paths with spaces (though our naming convention rejects spaces)

## Knowledge stewardship
- When a lesson is worth remembering, propose `~/Dev/_shared/_scripts/promote-lesson.sh`
- Don't unilaterally promote
- Read `_shared/playbooks/specstory-promotion-workflow.md` for full guidance

## Hardlinks
- Files in `.cursor/rules/`, `.cursor/hooks/`, and `.cursor/hooks.json` are HARD LINKS to the env template
- Editing them in place breaks the link (creates a divergent inode)
- Always edit at the env template path: `~/Dev/<env>/_shared/_templates/.cursor/...`
- `sessionStart` hook detects breakage and self-repairs (backing up divergent versions to `.divergent-*.bak`)
<!-- END managed-from-template -->

## Project-specific conventions

> Add overrides or additions specific to this project.
