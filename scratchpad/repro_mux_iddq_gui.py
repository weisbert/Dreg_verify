"""GUI 端到端：实际 MainWindow.on_load + SignalView._load_signal，看用户真正看到的真值表。
对照 iddq-gated mux(d_bt_lp_lna_itrim) vs iddq-gated logic(d_en_vco_fc_ls，已修)。"""
import sys, os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PATH = "scratchpad/_repro_mux_iddq.xlsx"   # 由 repro_mux_iddq.py 造好(含两个 iddq 门)

from PySide6 import QtWidgets
from dreg_verify import gui

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
win = gui.MainWindow()
win.path_edit.setText(os.path.abspath(PATH))
win.on_load()

# 找 Topout SignalView
sv = None
for attr in dir(win):
    try:
        o = getattr(win, attr)
    except Exception:
        continue
    if o.__class__.__name__ == "SignalView" and getattr(getattr(o, "provider", None), "view_id", "") == "topout":
        sv = o
        break
if sv is None:
    # 退而求其次：遍历所有 SignalView
    svs = [getattr(win, a) for a in dir(win)
           if getattr(getattr(win, a, None), "__class__", type("x", (), {})).__name__ == "SignalView"]
    print("SignalViews found:", [getattr(s.provider, "view_id", "?") for s in svs])
    sv = next((s for s in svs if getattr(s.provider, "view_id", "") == "topout"), svs[0] if svs else None)

def dump(name):
    sv._load_signal(name)
    print("\n=== GUI 真值表 for %s ===" % name)
    print("输入行 e_inputs (%d):" % len(sv.e_inputs))
    for e in sv.e_inputs:
        print("   %-40s editable=%s control=%s dft_gate=%s"
              % (e.get("label"), e.get("editable"), e.get("control"), e.get("is_dft_gate")))
    has_iddq = any(e.get("is_dft_gate") or "iddq" in str(e.get("label", "")).lower()
                   for e in sv.e_inputs)
    print("列数 cur_cols =", len(sv.cur_cols or []))
    print("有 iddq 门输入行 =", has_iddq)
    return has_iddq, len(sv.cur_cols or [])

mux_iddq, mux_cols = dump("d_bt_lp_lna_itrim")
logic_iddq, logic_cols = dump("d_en_vco_fc_ls")

print("\n==================== 总结 ====================")
print("MUX  根 d_bt_lp_lna_itrim : GUI真值表有iddq行=%s 列数=%d" % (mux_iddq, mux_cols))
print("LOGIC根 d_en_vco_fc_ls    : GUI真值表有iddq行=%s 列数=%d (已修=应为True)" % (logic_iddq, logic_cols))
