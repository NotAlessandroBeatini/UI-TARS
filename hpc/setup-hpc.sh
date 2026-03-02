#!/usr/bin/env bash
# setup-hpc.sh — One-time HPC environment setup for UI-TARS
# Usage: bash hpc/setup-hpc.sh [--hf-home /path/to/large/storage]
set -euo pipefail

CONDA_ENV_NAME="ui-tars"
PYTHON_VERSION="3.11"
MODEL_ID="ByteDance-Seed/UI-TARS-1.5-7B"

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
HF_HOME_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --hf-home) HF_HOME_ARG="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve HF_HOME: arg > $SCRATCH > ~/.cache/huggingface
# ---------------------------------------------------------------------------
if [[ -n "$HF_HOME_ARG" ]]; then
    export HF_HOME="$HF_HOME_ARG"
elif [[ -n "${SCRATCH:-}" ]]; then
    export HF_HOME="$SCRATCH/huggingface"
else
    export HF_HOME="$HOME/.cache/huggingface"
fi
mkdir -p "$HF_HOME"
echo "==> HF_HOME set to: $HF_HOME"

# ---------------------------------------------------------------------------
# Install or detect miniconda
# ---------------------------------------------------------------------------
CONDA_DIR="$HOME/miniconda3"

if command -v conda &>/dev/null; then
    echo "==> conda already available: $(conda --version)"
elif [[ -f "$CONDA_DIR/bin/conda" ]]; then
    echo "==> Found conda at $CONDA_DIR, activating..."
    eval "$("$CONDA_DIR/bin/conda" shell.bash hook)"
else
    echo "==> Installing Miniconda to $CONDA_DIR ..."
    INSTALLER="/tmp/Miniconda3-latest-Linux-x86_64.sh"
    curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o "$INSTALLER"
    bash "$INSTALLER" -b -p "$CONDA_DIR"
    rm -f "$INSTALLER"
    eval "$("$CONDA_DIR/bin/conda" shell.bash hook)"
    conda init bash
    echo "==> Miniconda installed. You may need to restart your shell."
fi

# Make sure conda is in PATH for the rest of this script
eval "$(conda shell.bash hook)"

# ---------------------------------------------------------------------------
# Create conda environment
# ---------------------------------------------------------------------------
if conda env list | grep -q "^${CONDA_ENV_NAME} "; then
    echo "==> Conda env '$CONDA_ENV_NAME' already exists, activating..."
else
    echo "==> Creating conda env '$CONDA_ENV_NAME' (Python $PYTHON_VERSION)..."
    conda create -y -n "$CONDA_ENV_NAME" python="$PYTHON_VERSION"
fi

conda activate "$CONDA_ENV_NAME"

# ---------------------------------------------------------------------------
# Install Python packages
# ---------------------------------------------------------------------------
echo "==> Installing Python packages..."
pip install --upgrade pip

# PyTorch (CUDA 12.1 — adjust if your HPC uses a different CUDA)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# Transformers + model loading
pip install transformers accelerate bitsandbytes

# Qwen2.5-VL dependencies (UI-TARS is based on Qwen2.5-VL)
pip install qwen-vl-utils

# UI-TARS action parser
pip install ui-tars

# For HF endpoint testing (optional)
pip install openai

# Image handling
pip install Pillow

echo "==> Packages installed."

# ---------------------------------------------------------------------------
# Persist HF_HOME in conda env activation
# ---------------------------------------------------------------------------
ACTIVATE_DIR="$CONDA_PREFIX/etc/conda/activate.d"
mkdir -p "$ACTIVATE_DIR"
cat > "$ACTIVATE_DIR/hf_home.sh" <<ENVEOF
export HF_HOME="$HF_HOME"
ENVEOF
echo "==> HF_HOME will auto-set on 'conda activate $CONDA_ENV_NAME'"

# ---------------------------------------------------------------------------
# Pre-download model
# ---------------------------------------------------------------------------
echo "==> Downloading model: $MODEL_ID (this may take a while)..."
huggingface-cli download "$MODEL_ID"
echo "==> Model downloaded."

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
echo "  UI-TARS HPC Setup Complete"
echo "========================================"
echo "  Conda env:    $CONDA_ENV_NAME"
echo "  Python:       $(python --version 2>&1)"
echo "  Torch:        $(python -c 'import torch; print(torch.__version__)' 2>/dev/null || echo 'N/A')"
echo "  CUDA avail:   $(python -c 'import torch; print(torch.cuda.is_available())' 2>/dev/null || echo 'N/A')"
echo "  GPU info:     $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'no nvidia-smi')"
echo "  HF_HOME:      $HF_HOME"
echo "  Model:        $MODEL_ID"
echo "========================================"
echo ""
echo "To activate:  conda activate $CONDA_ENV_NAME"
echo "To run:       ./hpc/run.sh python hpc/inference.py --image screenshot.png --task 'click search bar'"
