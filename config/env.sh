#!/usr/bin/env bash

# ======================================
# GLOBAL PATHS
# ======================================
PROJECT_ROOT="/home/ytscan/yt-autoscanner"
VENV_DIR="$PROJECT_ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"
REQUIREMENTS_DEV="$PROJECT_ROOT/requirements-dev.txt"


# ======================================
# Ensure virtual environment exists
# ======================================
check_venv() {
  if [ ! -x "$VENV_PY" ]; then
    echo "[FATAL] Python virtual environment not found at: $VENV_PY"
    echo "-> To set it up, run:"
    echo "   python3 -m venv $VENV_DIR"
    echo "   source $VENV_DIR/bin/activate"
    echo "   pip install -r requirements-dev.txt"
    exit 1
  fi
}


# ======================================
# Install development dependencies
# ======================================
install_dev_requirements() {
  if [ ! -f "$REQUIREMENTS_DEV" ]; then
    echo "[WARN] requirements-dev.txt not found at: $REQUIREMENTS_DEV"
    return
  fi

  echo "[INFO] Installing development dependencies from requirements-dev.txt..."
  "$VENV_PY" -m pip install -r "$REQUIREMENTS_DEV"
  echo "[INFO] Development dependencies installed."
}


# ======================================
# Ensure a Python module exists in venv
# If missing -> install dev requirements
# ======================================
ensure_module() {
  local module_name="$1"

  if ! "$VENV_PY" -c "import ${module_name}" >/dev/null 2>&1; then
    echo "[WARN] Python module '${module_name}' is missing."
    echo "[INFO] Attempting to install requirements-dev.txt..."
    install_dev_requirements

    # Retry import after installation
    if ! "$VENV_PY" -c "import ${module_name}" >/dev/null 2>&1; then
      echo "[FATAL] Module '${module_name}' is still missing even after installing requirements-dev.txt."
      exit 1
    fi
  fi
}
