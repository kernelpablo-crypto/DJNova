#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Punto de entrada principal de DJNova."""
import os
import sys

# Asegurar que la raíz del repositorio y binarios empaquetados estén en sys.path / PATH
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if getattr(sys, "frozen", False):
    extra_dirs = [os.path.dirname(sys.executable)]
    if hasattr(sys, "_MEIPASS"):
        extra_dirs.insert(0, sys._MEIPASS)
    os.environ["PATH"] = os.pathsep.join(extra_dirs + [os.environ.get("PATH", "")])

from lisp.main import _report_fatal_error, main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _report_fatal_error(exc)
        sys.exit(1)
