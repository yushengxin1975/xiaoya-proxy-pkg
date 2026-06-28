#!/usr/bin/env python3
"""
读取仓库根目录 VERSION 文件,作为运行时 __version__。
保证运行时版本和 git tag / release 一致。
"""
import os

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")


def _load():
    try:
        with open(_VERSION_FILE) as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


__version__ = _load()