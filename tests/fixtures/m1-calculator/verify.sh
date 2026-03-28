#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pytest src/tests/test_calc.py -v --tb=short 2>&1
