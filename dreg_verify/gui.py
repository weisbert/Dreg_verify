# -*- coding: utf-8 -*-
"""gui.py —— v2 工作台入口（I-17 / C-251：`python -m dreg_verify.gui` 不变）；旧门面在 `legacy_gui`。"""

from .ui.app import MainWindow, build_window, main

__all__ = ["main", "MainWindow", "build_window"]


if __name__ == "__main__":                            # pragma: no cover
    import sys

    sys.exit(main(sys.argv[1:]))
