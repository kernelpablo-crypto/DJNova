#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Punto de entrada principal de DJNova."""
import os
import sys

# Asegurar que la raíz del repositorio esté en sys.path al ejecutar como script
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lisp.main import main

if __name__ == "__main__":
    sys.exit(main())
