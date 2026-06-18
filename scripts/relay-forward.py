#!/usr/bin/env python3
"""Backward-compatible shim: delegates to relay_forward.py (v4)."""
import sys, os
d = os.path.dirname(os.path.abspath(__file__))
if d not in sys.path: sys.path.insert(0, d)
from relay_forward import main
main()
