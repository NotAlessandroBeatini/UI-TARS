#!/usr/bin/env bash
# sync.sh — Two-way git sync helper for HPC workflow
# Usage:
#   ./hpc/sync.sh pull   — pull latest code from origin
#   ./hpc/sync.sh push   — commit logs + push to origin
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ACTION="${1:-}"

case "$ACTION" in
    pull)
        echo "==> Pulling latest from origin..."
        git pull --rebase origin "$(git rev-parse --abbrev-ref HEAD)"
        echo "==> Done."
        ;;

    push)
        # Find the latest log file
        LATEST_LOG="$(ls -t logs/run_*.log 2>/dev/null | head -1 || true)"

        if [[ -z "$LATEST_LOG" ]]; then
            echo "No log files found in logs/. Nothing to push."
            exit 0
        fi

        # Extract exit code from the latest log's footer
        LOG_EXIT="$(grep -oP 'Exit code:\s+\K\d+' "$LATEST_LOG" 2>/dev/null | tail -1 || echo '?')"
        LOG_BASENAME="$(basename "$LATEST_LOG")"

        COMMIT_MSG="[hpc] $(date -Iseconds) $(hostname): ${LOG_BASENAME} (exit ${LOG_EXIT})"

        echo "==> Staging logs..."
        git add logs/

        # Check if there's anything to commit
        if git diff --cached --quiet; then
            echo "No new log changes to commit."
            exit 0
        fi

        echo "==> Committing: $COMMIT_MSG"
        git commit -m "$COMMIT_MSG"

        echo "==> Pushing to origin..."
        git push origin "$(git rev-parse --abbrev-ref HEAD)"
        echo "==> Done."
        ;;

    *)
        echo "UI-TARS HPC Sync Helper"
        echo ""
        echo "Usage:"
        echo "  ./hpc/sync.sh pull    Pull latest code from origin"
        echo "  ./hpc/sync.sh push    Commit log files and push to origin"
        echo ""
        echo "Examples:"
        echo "  # On HPC: get latest code changes"
        echo "  ./hpc/sync.sh pull"
        echo ""
        echo "  # On HPC: push experiment logs back"
        echo "  ./hpc/sync.sh push"
        exit 1
        ;;
esac
