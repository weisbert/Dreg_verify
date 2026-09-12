# -*- coding: utf-8 -*-
"""byte_gate.py — Topout .sv 逐字节闸门（form-driven 重构起的安全网，2026-09-12 起入库不再靠会话重建）。

对两张 mirror 表各渲染 3 档 Topout .sv（min / max / exhaustive，max_tests=100000），
取 sha256(utf-8)[:8] 与基线比对。任何声称「不改 .sv 字节」的改动，改前改后各跑一次，6 个值必须全同。

用法：
    .venv/Scripts/python.exe tools/byte_gate.py            # 比对基线
    .venv/Scripts/python.exe tools/byte_gate.py --print    # 只打印当前 6 值（重录基线时用）

基线历史（改了 .sv 输出的功能 commit 之后要重录，并把旧值留在这里）：
    2026-09-12 HEAD 6859453（880 过）：
        btlp min=4c1410b7 max=f977cc7c exh=26fa5d05
        wl   min=fb34bb65 max=c359e1a1 exh=c129244d
    2026-06 S1 t1 后（记忆 refactor-form-driven-pipeline，已被后续断言标号/dft 门修复等 commit 改掉）：
        btlp 5a0d5d75/3ee55056/d2b7e2da  wl aee1cf80/2315685c/c82bf24e
"""
import hashlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dreg_verify import excel_model as M   # noqa: E402
from dreg_verify import topout as T        # noqa: E402

GOLD = {
    ("btlp", "min"): "4c1410b7", ("btlp", "max"): "f977cc7c", ("btlp", "exh"): "26fa5d05",
    ("wl", "min"): "fb34bb65", ("wl", "max"): "c359e1a1", ("wl", "exh"): "c129244d",
}
FILES = {"btlp": "mirror_btlp_dreg.xlsx", "wl": "mirror_wl_dreg.xlsx"}


def digests():
    out = {}
    for tag, fn in FILES.items():
        wb = M.load_workbook(os.path.join(_ROOT, fn))
        for mode in ("min", "max", "exh"):
            sv = T.render_topout_sv(wb, max_tests=100000,
                                    mode=("max" if mode == "max" else "min"),
                                    exhaustive=(mode == "exh"))
            text = sv if isinstance(sv, str) else str(sv)
            out[(tag, mode)] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return out


def main(argv):
    got = digests()
    if "--print" in argv:
        for k, v in got.items():
            print(f"{k[0]:5s} {k[1]:3s} {v}")
        return 0
    ok = True
    for k, v in got.items():
        hit = v == GOLD[k]
        ok &= hit
        print(f"{k[0]:5s} {k[1]:3s} gold={GOLD[k]} got={v} {'OK' if hit else 'MISMATCH'}")
    print("BYTE-GATE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
