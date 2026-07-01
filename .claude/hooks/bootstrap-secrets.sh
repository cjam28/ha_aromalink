#!/usr/bin/env bash
# bootstrap-secrets.sh — committed, surface-agnostic, FAIL-OPEN secret materializer.
#
# This is the ONLY `op inject` in the project (`cjam28/dev`) domain — INVARIANT 1: no chezmoi or
# cloud logic ever enters a scaffolding script; the runtime delivery lives here, in a committed hook.
#
# Runs at Claude Code SessionStart (local AND cloud). When a 1Password Service-Account token is
# present (the Claude cloud Environment / CI-like surfaces) AND `op` is installed AND a committed
# `.env.op` exists, it resolves that file's op:// references into `KEY=value` export lines written
# to $CLAUDE_ENV_FILE — a documented Claude Code primitive that Claude auto-sources into the session
# env, so the ${VAR:-} placeholders in the generated root .mcp.json resolve for later Bash calls,
# subprocesses, and MCP servers that connect after the hook.
#
# LOCAL has NO token (it uses biometric `op` via chezmoi->launchctl, which already populated the
# env), so this no-ops there. There is NO gitignored-.env fallback (plan §0 D1): CLAUDE_ENV_FILE is
# real, so if it is somehow unset the hook simply does nothing rather than writing a stray .env.
#
# ALWAYS exits 0 (fails open): a missing token / missing op / missing .env.op / op error must never
# block a session — ${VAR:-} degrades a single MCP server gracefully, it never crashes parsing.
# Never echoes a secret value (.specstory captures terminal output).
set -uo pipefail

if [ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ] \
   && [ -n "${CLAUDE_ENV_FILE:-}" ] \
   && command -v op >/dev/null 2>&1 \
   && [ -f .env.op ]; then
  op inject -i .env.op -o "$CLAUDE_ENV_FILE" 2>/dev/null || true
fi

exit 0
