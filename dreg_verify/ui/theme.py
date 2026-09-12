# -*- coding: utf-8 -*-
"""theme.py —— Design 色板 / 字号 / 行高 / 尺寸默认值（唯一定义；视图模块禁止写裸十六进制）。

来源：docs/GUI_v2_Design对齐_20260912.md §1.1（state 默认值与拖拽 clamp）、§1.2（逐区颜色）、
§1.4（单元格 6 态 + 列级底色 + 列头）、§6.1（电路图五色）；主控裁决⑭（iddq 淡蓝）、⑮（7 态：
Design 6 态 + 手编列只做列头标记不占底色）。

约定：颜色常量一律小写 `#rrggbb`；唯一的 rgba 是遮罩 MASK。字号单位 px（Qt 里用 setPixelSize）。
"""

# ═════════ 基础色（§1.2）═════════
INK = "#12161a"            # 主文字
TEXT = "#3d4752"           # 正文灰
MUTE = "#5d6773"           # 次要文字
BLUE = "#2f6fd0"           # 主按钮 / 勾选框 / 选中列描边
BLUE_DARK = "#1d4f9c"      # 导出中心按钮字色 / 驱动串 / iddq 字
PANEL_BG = "#f1f3f5"       # 顶栏 / 状态栏 / 普通列头
HINT_BG = "#fafbfc"        # 提示条 / 驱动串行底 / 只读格底
ZEBRA = "#fcfcfd"          # 斑马纹
SELECTED_BG = "#dbe7f8"    # 选中行 / 当前线网行
BORDER = "#d0d5da"
BORDER_LIGHT = "#e3e7eb"
BORDER_SEG = "#ccd2d8"     # 分段按钮竖分隔
HANDLE = "#cfd5db"         # 分割条手柄
CHECK_OFF_BORDER = "#a9b2bb"
DISABLED_TEXT = "#9aa3ad"
DISABLED_BG = "#eef0f3"
WHITE = "#ffffff"
MASK = "rgba(24,30,38,0.38)"   # 导出中心 / 完成弹层遮罩

# ═════════ 四档状态色（清单 stS）═════════
OK_FG = "#1d7a4c"
OK_BG = "#eaf6ee"
WARN_FG = "#8a6412"
WARN_BG = "#fdf3e0"
BAD_FG = "#a9302a"
BAD_BG = "#fdecea"
NOTE_FG = "#3d4752"
NOTE_BG = "#eef0f3"
TONE_COLORS = {"ok": (OK_FG, OK_BG), "warn": (WARN_FG, WARN_BG),
               "bad": (BAD_FG, BAD_BG), "note": (NOTE_FG, NOTE_BG)}

# 反例勾选 / 琥珀家族
AMBER_CHECK = "#b4690e"    # 反例勾选框
AMBER_FG = "#8a6412"       # 猜名行字 / 反例列头字
AMBER_BG = "#fdf3e0"       # 反例列头底 / 反例格底
AMBER_BORDER = "#e6c98a"   # 反例格边框 / 原因块左边条
AMBER_STROKE = "#c9922a"   # auto→期望 描边 / 猜名虚下划线
REASON_BG = "#fffdf6"      # 行内原因块底
GUESS_BG = "#fffaf0"       # 猜名行底（输入表）
RED_DARK = "#8c2d27"       # V2Spec §3 状态设计红字

# 淡蓝家族（iddq 拍 / 载入态 / 覆盖度生效行）
LIGHT_BLUE_BG = "#eef4fd"
LIGHT_BLUE_BORDER = "#a9c4ea"
LIGHT_BLUE_FG = "#1d4f9c"

# ═════════ 真值表单元格 7 态（§1.4 + 裁决⑮）═════════
# 键 = edits.expected_cell_state / input_cell_state 的返回值 + 两个视图态；值 = (底, 边框, 字, 边框线型)
CELL_STATES = {
    "match":    ("#eaf6ee", "#a9d6ba", "#14603b", "solid"),    # 手填 · 与 auto 一致
    "diff":     ("#fdecea", "#e5a9a4", "#a9302a", "solid"),    # 手填 · 与 auto 不一致（特性不是错误）
    "unfilled": ("#ffffff", "#c3c9cf", "#c9ced4", "dashed"),   # 待手填（生成时 auto 兜底）
    "neg":      ("#fdf3e0", "#e6c98a", "#8a6412", "solid"),    # 反例（故意填错）
    "dft":      ("#eef4fd", "#a9c4ea", "#1d4f9c", "solid"),    # iddq=1 漏电态自检拍（裁决⑭：淡蓝）
    "readonly": ("#fafbfc", "#e3e7eb", "#5d6773", "solid"),    # 只读（auto_out 行 / 只读输入行）
    "editable": ("#ffffff", "#e3e7eb", "#12161a", "solid"),    # 可编辑输入格（普通）
}
#: 手编列（user=True）不占底色，只在列头标 U<n>/自定义名（裁决⑮）
USER_COL_HEADER_FG = "#1d4f9c"

# 列级底色（colBg）
COL_BG_CURRENT = "#eef5fd"
COL_BG_NEG = "#fdfaf3"
COL_BG_IDDQ = "#f6faff"
COL_BG_NORMAL = "#ffffff"
# 列头
COL_HDR_BG = "#f1f3f5"
COL_HDR_FG = "#3d4752"
COL_HDR_CURRENT_BG = "#c9dcf5"
COL_HDR_CURRENT_FG = "#12161a"
COL_HDR_NEG_BG = "#fdf3e0"
COL_HDR_NEG_FG = "#8a6412"
COL_HDR_IDDQ_BG = "#eef4fd"
COL_HDR_IDDQ_FG = "#1d4f9c"
CURRENT_COL_OUTLINE = "#2f6fd0"   # 当前列左右 1px + 期望格 outline 2px

# ═════════ 电路图五色（§6.1 SigFlowDiagram）═════════
FLOW_INK = "#1f2933"
FLOW_MUTE = "#5d6773"
FLOW_HL = "#1d4f9c"
FLOW_AMB = "#a8710f"
FLOW_BAD = "#b3302a"
FLOW_REG_BAR = "#93a1b0"      # 寄存器盒左侧 5px 色条（高亮时 FLOW_HL）
FLOW_TOP_BG = "#e8f0fb"       # TOP 端子底
FLOW_GUESS_BG = "#fffbf3"     # 猜名盒底
FLOW_HL_BG = "#eef4fd"        # 选中盒底
FLOW_WIRE_W = 1.2
FLOW_BUS_W = 2.4
FLOW_HL_W = 2.2
FLOW_DASH_BAD = "6 3"
FLOW_DASH_GHOST = "5 3"

# ═════════ 字体 / 字号 ═════════
FONT_UI = "Microsoft YaHei"
FONT_UI_FALLBACKS = ("Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC", "sans-serif")
FONT_MONO = "Consolas"
FONT_MONO_FALLBACKS = ("Consolas", "Cascadia Mono", "Menlo", "DejaVu Sans Mono", "monospace")
FS_UI = 12                 # 界面正文
FS_UI_SMALL = 11.5         # 次要
FS_UI_TITLE = 13           # 标题
FS_MONO = 11.5             # 信号名 / 地址 / 数值 / 表达式
FS_MONO_NAME_BIG = 16      # 详情标题栏 curName（700）
FS_MONO_SV = 12            # .sv 预览
FS_BRAND = 13
FS_STATUS = 11.5
FS_PAGE_TAG = 11           # 展开链每层的页标签（微软雅黑灰）

# ═════════ 尺寸默认值（state 键，§1.1）═════════
WIN_W, WIN_H = 1920, 1080
WIN_MIN_W, WIN_MIN_H = 1366, 768
LIST_W = 560
LIST_W_NARROW = 420        # 1366×768 时
SIDE_W = 470
CHAIN_H = 300
FROZEN_W = 288
TRUTH_H = 420
HANDLE_W = 6
# 拖拽 clamp（min, max）
CLAMP_LIST = (320, 900)
CLAMP_SIDE = (280, 900)
CLAMP_FROZEN = (160, 600)
CLAMP_TRUTH = (160, 820)
CLAMP_CHAIN = (120, 620)
CLAMP_WIN = ((1100, 2600), (700, 1600))
# 行高 / 表头 / 列宽
ROW_H = 26
HEADER_H = 24
TRUTH_HEADER_H = 28
TRUTH_ROW_H = 26
TRUTH_COL_W = 34
BTN_H = 24
INPUTS_ROW_H = 2 * ROW_H   # 输入表每行两行高（第二行常驻驱动串）
LIST_COL_W = {"check": 30, "neg": 34, "status": 146, "ntest": 44, "owner": 76}
INPUTS_COL_W = {"letter": 76, "role": 112, "rw": 40}
COV_POP_W = 760
COV_TABLE_COLS = (150, None, 128)
EXPORT_W = 1080
EXPORT_COLS = (40, 206, 190, None, 240)
DONE_W = 820
DIAG_W = 620
EMPTY_W = 620
LOADING_W = 520
PROGRESS_W, PROGRESS_H = 90, 6
LOADING_BAR_H = 8

# ═════════ 电路图缩放规格（§5 第 22 条）═════════
ZOOM_MIN = 0.3
ZOOM_MAX = 3.0
ZOOM_STEP = 1.12
ZOOM_FIT = 0.82

# ═════════ 预览截断 ═════════
SV_PREVIEW_MAX_LINES = 600      # C-069
IMPORT_MISSING_LIST_MAX = 30    # C-194 / C-239
RECENT_MAX = 5                  # N4


def all_colors():
    """{常量名: 颜色串}——测试用：所有 `#` 开头的模块级常量。"""
    out = {}
    for k, v in globals().items():
        if k.isupper() and isinstance(v, str) and v.startswith("#"):
            out[k] = v
    return out
