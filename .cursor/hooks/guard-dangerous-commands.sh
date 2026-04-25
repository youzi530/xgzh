#!/bin/bash
# XGZH project guard: block destructive commands; ask for sensitive ones.
# stdin/stdout: Cursor hook JSON protocol.

set -euo pipefail

input=$(cat)

# Try to read .command (newer schema) and .input.command (defensive fallback)
command=$(printf '%s' "$input" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('')
    sys.exit(0)
cmd = d.get('command') or (d.get('input') or {}).get('command') or ''
print(cmd)
" 2>/dev/null || true)

if [ -z "${command:-}" ]; then
  printf '{"permission":"allow"}'
  exit 0
fi

# ── Hard deny patterns (irreversible / dangerous) ──────────────────
deny_patterns=(
  'rm[[:space:]]+-rf?[[:space:]]+/[^a-zA-Z]?'
  'rm[[:space:]]+-rf?[[:space:]]+\$HOME'
  'rm[[:space:]]+-rf?[[:space:]]+~'
  ':\(\)\{[[:space:]]*:\|:&[[:space:]]*\};:'
  'mkfs\.[a-z0-9]+'
  'dd[[:space:]]+if=.*of=/dev/'
  '>[[:space:]]*/dev/sd[a-z]'
  'chmod[[:space:]]+-R[[:space:]]+777[[:space:]]+/'
  'git[[:space:]]+push[[:space:]].*--force.*\b(main|master|prod|production)\b'
  'git[[:space:]]+push[[:space:]]+.*-f[[:space:]].*\b(main|master)\b'
  'DROP[[:space:]]+(DATABASE|SCHEMA|TABLE)[[:space:]]+(IF[[:space:]]+EXISTS[[:space:]]+)?(prod|production|main)'
  'TRUNCATE[[:space:]]+TABLE[[:space:]]+(prod|production)'
  'alembic[[:space:]]+downgrade[[:space:]]+base'
)

for p in "${deny_patterns[@]}"; do
  if printf '%s' "$command" | grep -E -i "$p" >/dev/null 2>&1; then
    cat <<EOF
{
  "permission": "deny",
  "agent_message": "BLOCKED by XGZH guard: command matches dangerous pattern '${p}'. If this is intentional, ask the user to run it manually outside the agent.",
  "user_message": "🛑 已拦截危险命令 (匹配规则: ${p})"
}
EOF
    exit 0
  fi
done

# ── Soft ask patterns (sensitive, ask for confirmation) ────────────
ask_patterns=(
  'rm[[:space:]]+-rf?'
  'git[[:space:]]+push[[:space:]]+.*--force'
  'git[[:space:]]+reset[[:space:]]+--hard'
  'git[[:space:]]+clean[[:space:]]+-fd'
  'docker[[:space:]]+system[[:space:]]+prune'
  'kubectl[[:space:]]+delete'
  'aws[[:space:]]+s3[[:space:]]+rm[[:space:]]+.*--recursive'
)

for p in "${ask_patterns[@]}"; do
  if printf '%s' "$command" | grep -E -i "$p" >/dev/null 2>&1; then
    cat <<EOF
{
  "permission": "ask",
  "agent_message": "Sensitive command detected (matches '${p}'). Asking user to confirm.",
  "user_message": "⚠️ 即将执行敏感命令 (匹配规则: ${p}), 请确认后再继续"
}
EOF
    exit 0
  fi
done

# Default: allow
printf '{"permission":"allow"}'
exit 0
