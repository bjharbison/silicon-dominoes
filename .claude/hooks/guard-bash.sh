#!/usr/bin/env bash
# PreToolUse guard for the Bash tool (Silicon Dominoes).
# Blocks any command that could reach the homelab or execute SQL against CT 109.
# Exit 2 = block; stderr is returned to the agent as the reason. Exit 0 = allow.
#
# Deliberately NOT blocked: SQL text (CREATE/ALTER/DROP) in a command. Writing
# numbered .sql files is the agent's job; executing them is Brian's, as postgres
# inside CT 109. This hook enforces the second half of that rule.
set -u
input="$(cat)"

pattern='psql|pg_dump|pg_restore|su +(- +)?postgres|su +(- +)?dominoes|pct +[a-z]+|192\.168\.1\.[0-9]+|ssh +[a-z]+@|sd-deploy|systemctl +(start|stop|restart)|collector\.env'

if printf '%s' "$input" | grep -Eiq "$pattern"; then
  echo "BLOCKED by .claude/hooks/guard-bash.sh: this command would reach the homelab (CT 109 / Proxmox / LiteLLM) or execute SQL. Agents never run psql, pct, ssh, or touch 192.168.1.x. Write DDL to a numbered file under collection/sql/ and stop; Brian applies it as postgres inside CT 109." >&2
  exit 2
fi
exit 0
