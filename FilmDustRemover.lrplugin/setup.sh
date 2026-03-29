#!/usr/bin/env bash
# Film Dust Remover — dependency installer
set -e

echo ""
echo "Film Dust Remover — Setup"
echo "─────────────────────────"

# Find Python
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo ""
    echo "✗ Python not found."
    echo "  Install Python 3 from https://www.python.org/downloads/"
    echo "  Then run this script again."
    exit 1
fi

echo "✓ Python found: $PYTHON ($($PYTHON --version))"

# Install dependencies
echo ""
echo "Installing dependencies..."
$PYTHON -m pip install --quiet --upgrade opencv-python-headless numpy mediapipe

# Verify
if $PYTHON -c "import cv2, numpy, mediapipe" 2>/dev/null; then
    echo "✓ opencv-python-headless installed"
    echo "✓ numpy installed"
    echo "✓ mediapipe installed (subject protection enabled)"
    echo ""
    echo "All dependencies ready."
    echo "You can now add FilmDustRemover.lrplugin to Lightroom Classic via:"
    echo "  File → Plug-in Manager → Add"
else
    echo ""
    echo "✗ Installation failed. Try running manually:"
    echo "  pip3 install opencv-python-headless numpy"
    exit 1
fi
