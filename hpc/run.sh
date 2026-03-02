#!/usr/bin/env bash
# run.sh — Wrapper that tees all output to terminal + timestamped log file
# Usage: ./hpc/run.sh python hpc/inference.py --image test.png --task "click search"
set -uo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: ./hpc/run.sh <command> [args...]"
    echo "Example: ./hpc/run.sh python hpc/inference.py --image test.png --task 'click search bar'"
    exit 1
fi

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/run_${TIMESTAMP}.log"
START_EPOCH="$(date +%s)"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
{
    echo "================================================================"
    echo "  UI-TARS Run Log"
    echo "================================================================"
    echo "  Timestamp:    $(date -Iseconds)"
    echo "  Hostname:     $(hostname)"
    echo "  Command:      $*"
    echo "  Git branch:   $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'N/A')"
    echo "  Git commit:   $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'N/A')"
    echo "  Python:       $(python --version 2>&1 || echo 'N/A')"
    echo "  Conda env:    ${CONDA_DEFAULT_ENV:-N/A}"
    echo "  GPU:          $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'no nvidia-smi')"
    echo "  HF_HOME:      ${HF_HOME:-not set}"
    echo "  CUDA_DEVICES: ${CUDA_VISIBLE_DEVICES:-not set}"
    echo "================================================================"
    echo ""
} | tee "$LOG_FILE"

# ---------------------------------------------------------------------------
# Run command, tee to log
# ---------------------------------------------------------------------------
"$@" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE="${PIPESTATUS[0]}"

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
END_EPOCH="$(date +%s)"
DURATION=$(( END_EPOCH - START_EPOCH ))
MINS=$(( DURATION / 60 ))
SECS=$(( DURATION % 60 ))

{
    echo ""
    echo "================================================================"
    echo "  Run Complete"
    echo "================================================================"
    echo "  Exit code:    $EXIT_CODE"
    echo "  Duration:     ${MINS}m ${SECS}s"
    echo "  Finished at:  $(date -Iseconds)"
    echo "================================================================"
} | tee -a "$LOG_FILE"

echo ""
echo "Log saved to: $LOG_FILE"

exit "$EXIT_CODE"
