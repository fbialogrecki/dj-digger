#!/usr/bin/env python3
"""Compatibility shim for the pre-0.2 entry point. Use `dj-digger` instead."""

from dj_digger.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
