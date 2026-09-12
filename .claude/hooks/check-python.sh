#!/usr/bin/env bash
# PostToolUse hook (Edit|Write): after Claude touches a Python file of this
# project, type-check the whole project with Pyright and lint it with pyflakes.
# Exit 2 hands the diagnostics back to Claude, which then fixes them in the same
# turn instead of leaving them for the next `pyright` run. Non-Python files and
# files outside the project exit 0 immediately.
set -u

file=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' 2>/dev/null)
case "$file" in *.py) ;; *) exit 0 ;; esac

project="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$project" || exit 0
case "$file" in "$project"/*) ;; *) exit 0 ;; esac
[ -f "$file" ] || exit 0

# Hooks may run with a minimal PATH (desktop app): uv and pyright live in
# ~/.local/bin, jq comes from Homebrew.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
export PYRIGHT_PYTHON_IGNORE_WARNINGS=1

problems=""
if command -v pyright >/dev/null; then
    # [tool.pyright] in pyproject.toml picks the venv and the packages to check.
    report=$(pyright --outputjson 2>/dev/null | jq -r --arg root "$project/" '
        .generalDiagnostics[]
        | select(.severity == "error")
        | "\(.file | ltrimstr($root)):\(.range.start.line + 1):\(.range.start.character + 1) \(.message | split("\n")[0]) [\(.rule // "-")]"')
    [ -n "$report" ] && problems="pyright:
$report"
else
    problems="pyright introuvable (uv tool install pyright)"
fi

lint=$(uv run --no-sync pyflakes jobhunt accounts tracker rls jobhunt_ai 2>&1)
if [ -n "$lint" ]; then
    problems="${problems:+$problems

}pyflakes:
$lint"
fi

if [ -n "$problems" ]; then
    printf '%s\n' "Contrôle Python après l'édition de ${file#"$project"/} — à corriger avant de rendre la main :" "$problems" >&2
    exit 2
fi
exit 0
