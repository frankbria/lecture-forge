#!/bin/bash
set -e

# Check if this is a Python project
if [ ! -f "pyproject.toml" ] && [ ! -f "setup.py" ]; then
    echo "✓ Not a Python project, skipping coverage check"
    exit 0
fi

# Run coverage test
echo "Running coverage check (minimum 85%)..."
if uv run pytest -m "not integration" --cov --cov-report=term-missing --cov-fail-under=85; then
    echo "✓ Coverage check passed"
    exit 0
else
    echo ""
    echo "❌❌❌ COVERAGE BELOW 85% ❌❌❌"
    echo ""
    echo "To see detailed coverage report:"
    echo "  uv run pytest -m "not integration" --cov --cov-report=html"
    echo "  open htmlcov/index.html"
    echo ""
    exit 1
fi
