# -*- coding: utf-8 -*-
"""names.py —— v2 全部可测控件的 objectName 注册表（唯一真相源）。

规则（执行计划 Phase A 结果「架构步须知」：v1 全仓 setObjectName 为 0，v2 每个可测控件必须命名）：
  · 每个会被测试 `ui_harness.find(root, NAME)` 找到的控件，都从这里取名，禁止在视图模块里写裸字符串；
  · 值 = 小写 snake_case，前缀 = 所在区（top_ / filter_ / list_ / hdr_ / cov_ / tabs_ / truth_ /
    flow_ / sv_ / side_ / status_ / export_ / done_ / diag_ / empty_ / loading_ / error_ / dlg_ / win_）；
  · 同一个名字只能出现在一个模块里（tests/test_ui_contracts.py 验唯一）；
  · 动态生成的控件（导出中心 6 行、诊断 3 步）用 `fmt_*` 函数拼名，仍走本模块。

Design 对照：区号 = docs/GUI_v2_Design对齐_20260912.md §1.2 的 ①–⑯。
"""

# ───────── 窗口 / 容器 ─────────
WIN_MAIN = "win_main"                      # QMainWindow（标题「Dreg_verify · 版本 <短HEAD>」C-259）
WIN_WORKBENCH = "win_workbench"            # 载入后的工作台根 QWidget（顶栏以下、状态栏以上）
WIN_STACK = "win_stack"                    # QStackedWidget：空态⑭ / 工作台 **两页**
#   ⚠ B3 阶段这里写的是「三页」。实现定版为两页：载入态⑮不是独立一页，而是工作台详情列顶部的
#     卡片——C-276 / 场景⑧ 要求「分析进行中清单仍然可点」，独占一页就做不到（见 app.py 模块头）。
WIN_SPLIT_MAIN = "win_split_main"          # 清单 | 详情 水平分割（list 320–900）
WIN_SPLIT_SIDE = "win_split_side"          # 主视图 | 右栏 水平分割（side 280–900）
WIN_SPLIT_TRUTH_FLOW = "win_split_truth_flow"   # 真值表 / 电路图 垂直分割（truth 160–820）
WIN_SPLIT_CHAIN_INPUTS = "win_split_chain_inputs"  # 逐层展开 / 输入信号 垂直分割（chain 120–620）
DETAIL_COLUMN = "detail_column"            # 详情列容器（载入态⑮ + 标题栏④ + WIN_SPLIT_SIDE）

# ───────── ① 顶栏 ─────────
TOP_BAR = "top_bar"
TOP_BRAND = "top_brand"                    # 「Dreg_verify」
TOP_TABLE_TAG = "top_table_tag"            # 路径框前的「真表」标签
TOP_EXCEL_PATH = "top_excel_path"          # 只读路径框（未载入「尚未选择文件」）；harness 兼容名见下
TOP_BROWSE_BTN = "top_browse_btn"          # 「浏览… Ctrl+O」C-001
TOP_LOAD_BTN = "top_load_btn"              # 「载入 / 重新载入 Ctrl+L」C-002
TOP_DIAG_BTN = "top_diag_btn"              # 「诊断 Ctrl+Shift+D」
TOP_EXPORT_BTN = "top_export_btn"          # 「导出中心 Ctrl+G」

#: ui_harness._set_excel_path 退回找的 objectName（保持 harness 不改就能起 v2 窗）
HARNESS_EXCEL_PATH_EDIT = "excel_path_edit"

# ───────── ② 筛选行 ─────────
FILTER_BAR = "filter_bar"
FILTER_SCOPE_SEG = "filter_scope_seg"      # 5 段联排容器
FILTER_SCOPE_TOPOUT = "filter_scope_topout"
FILTER_SCOPE_LOGIC = "filter_scope_logic"
FILTER_SCOPE_MUX = "filter_scope_mux"
FILTER_SCOPE_DFT = "filter_scope_dft"
FILTER_SCOPE_IDDQ = "filter_scope_iddq"
FILTER_SCOPE_HINT = "filter_scope_hint"    # scopeHint 小字
FILTER_OWNER_BTN = "filter_owner_btn"      # 「全部 owner ▾」多选（C-024..C-027）
FILTER_OWNER_MENU = "filter_owner_menu"
FILTER_KIND_COMBO = "filter_kind_combo"    # 「全部分类 ▾」C-028
FILTER_STATUS_COMBO = "filter_status_combo"  # 「全部状态 ▾」C-029
FILTER_SEARCH = "filter_search"            # 正则搜索 C-030 / C-047
FILTER_PRESETS_BTN = "filter_presets_btn"  # 「预设 ▾」C-291
FILTER_PASTE_NAMES_BTN = "filter_paste_names_btn"  # 预设菜单里的「粘贴名单勾选…」QAction（C-290）


def fmt_scope_btn(view_id):
    """范围段按钮名：filter_scope_<view_id>。"""
    return "filter_scope_%s" % view_id


# ───────── ③ 清单区 ─────────
LIST_PANEL = "list_panel"
LIST_HEADER_COUNTS = "list_header_counts"  # 「要验信号 10 · 已勾选 7 · 有问题 3」
LIST_COLUMNS_BTN = "list_columns_btn"      # 「列设置…」C-008/C-046
LIST_SORT_BTN = "list_sort_btn"            # 「排序」入口 C-037
LIST_SORT_MENU = "list_sort_menu"          # 排序下拉 QMenu（按状态 / 用例 / owner / 信号名）
LIST_VIEW = "list_view"                    # QTreeView（两级：信号行 + 原因子行）
LIST_REASON = "list_reason"                # 行内原因块 QWidget（setIndexWidget 到子行）
LIST_REASON_TITLE = "list_reason_title"
LIST_REASON_BODY = "list_reason_body"
LIST_REASON_DIAG_BTN = "list_reason_diag_btn"      # 「去诊断 · {reasonAction}」
LIST_REASON_RISKY_BTN = "list_reason_risky_btn"    # 「去诊断 · 缺前缀是否强制生成」（裁决①/③）
LIST_BTN_CHECK_ALL = "list_btn_check_all"          # 「全选」C-032
LIST_BTN_UNCHECK_ALL = "list_btn_uncheck_all"      # 「清空勾选」C-033
LIST_BTN_CHECK_SELECTED = "list_btn_check_selected"  # 「勾选选中行」C-034
LIST_BTN_NEG_ALL = "list_btn_neg_all"              # 「全部加反例」C-035
LIST_BTN_NEG_CLEAR = "list_btn_neg_clear"          # 「清除反例」C-036
LIST_BTN_PASTE_NAMES = "list_btn_paste_names"      # 「粘贴名单勾选…」C-290
LIST_BOTTOM_BAR = "list_bottom_bar"

# ───────── ④ 详情标题栏 ─────────
HDR_BAR = "hdr_bar"
HDR_NAME = "hdr_name"                      # curName Consolas 16/700
HDR_STATUS_BADGE = "hdr_status_badge"
HDR_META = "hdr_meta"                      # 「owner X · 分类 · 用例 N」
HDR_COV_BTN = "hdr_cov_btn"                # 「覆盖度 全面（来自：…）▾」C-156
HDR_PROGRESS_LABEL = "hdr_progress_label"  # 「手填期望 7/25」C-106
HDR_PROGRESS_BAR = "hdr_progress_bar"
HDR_PROGRESS_DIFF = "hdr_progress_diff"    # 「其中 1 条与程序算的不一致」
HDR_RESOLVE_BTN = "hdr_resolve_btn"        # 「解析明细」C-064
HDR_RESOLVE_PANEL = "hdr_resolve_panel"    # 解析明细面板（QPlainTextEdit 只读）
HDR_SIDE_TOGGLE = "hdr_side_toggle"        # 「隐藏右栏 ▶」/「◀ 展开链 · 输入信号」
HDR_ERROR_LABEL = "hdr_error_label"        # 「分析失败（已捕获，未崩）」C-043

# ───────── ⑯ 覆盖度弹层 ─────────
COV_CONTROL = "cov_control"                # 按钮 + 弹层的外壳（CoverageControl 本体）
COV_POPOVER = "cov_popover"
COV_TITLE = "cov_title"                    # 「覆盖度：本信号生效档 = 全面」
COV_CHAIN_BAR = "cov_chain_bar"            # 蓝条：生效来源链 C-150
COV_TABLE = "cov_table"                    # 6 行三列
COV_GLOBAL_COMBO = "cov_global_combo"      # 全局默认 C-142
COV_FORM_COMBO_REGISTER = "cov_form_combo_register"
COV_FORM_COMBO_BOOLEAN = "cov_form_combo_boolean"
COV_FORM_COMBO_SELECT = "cov_form_combo_select"
COV_FORM_COMBO_GATED = "cov_form_combo_gated"
COV_SIG_COMBO = "cov_sig_combo"            # 本信号 C-147
COV_MAXT_SPIN = "cov_maxt_spin"            # 用例数上限 C-143
COV_COUNT_LABEL = "cov_count_label"        # 「本信号当前 25 条」C-133
COV_HELP_BTN = "cov_help_btn"              # 「档位怎么算的？」C-144
COV_HELP_TEXT = "cov_help_text"            # 展开后的内联帮助正文（terms.COV_HELP_TEXT）
COV_CLOSE_BTN = "cov_close_btn"


def fmt_cov_form_combo(form_key):
    """按逻辑类型档下拉名：cov_form_combo_<register|boolean|select|gated>。"""
    return "cov_form_combo_%s" % form_key


# ───────── ⑤ 主视图标签 ─────────
TABS_BAR = "tabs_bar"
TABS_TRUTH = "tabs_truth"                  # 「真值表 + 电路图　25 列 · 手填 7/25」
TABS_SV = "tabs_sv"                        # 「.sv 预览」Ctrl+P
MAIN_VIEW = "main_view"                    # QStackedWidget（truth+flow 页 / sv 页）

# ───────── ⑥ 真值表区 ─────────
TRUTH_PANEL = "truth_panel"
TRUTH_TOOLBAR = "truth_toolbar"
TRUTH_BTN_REGEN = "truth_btn_regen"        # 重新生成 C-085
TRUTH_BTN_ADD_COL = "truth_btn_add_col"    # 加列 C-086
TRUTH_BTN_COPY_COL = "truth_btn_copy_col"  # 复制列 Ctrl+D C-087
TRUTH_BTN_DEL_COL = "truth_btn_del_col"    # 删列 C-088
TRUTH_BTN_RENAME_COL = "truth_btn_rename_col"  # 重命名列… C-091
TRUTH_BTN_CLEAR = "truth_btn_clear"        # 清零… C-089
TRUTH_BTN_ADD_NEG = "truth_btn_add_neg"    # 加反例（选中列）C-096
TRUTH_BTN_DEL_NEG = "truth_btn_del_neg"    # 删反例… C-102
TRUTH_BTN_IMPORT_EXP = "truth_btn_import_exp"  # 导入期望… C-298
TRUTH_BTN_BATCH_FILL = "truth_btn_batch_fill"  # 批量填… C-298
TRUTH_BTN_AUTO_FILL = "truth_btn_auto_fill"    # auto→期望… C-094
TRUTH_BTN_MUX_DATA = "truth_btn_mux_data"      # 设置 mux 数据值（整表）C-110
TRUTH_BTN_EXPORT_CSV = "truth_btn_export_csv"  # 导出 CSV C-136
TRUTH_BTN_MAXIMIZE = "truth_btn_maximize"      # 真值表放大（隐藏右栏 + 电路图）C-297
TRUTH_HINT_SELECTION = "truth_hint_selection"  # 「已选 T13 整列（6 格）」
TRUTH_HINT_PASTE = "truth_hint_paste"
TRUTH_HINT_CONTEXT = "truth_hint_context"
TRUTH_NAMES_VIEW = "truth_names_view"      # 冻结左列（QTableView，NameModel）
TRUTH_GRID_VIEW = "truth_grid_view"        # 网格（QTableView，TruthModel）
TRUTH_LEGEND = "truth_legend"
TRUTH_MUX_HEADER = "truth_mux_header"      # mux 头部条 C-124..C-126
TRUTH_CONTEXT_MENU = "truth_context_menu"
TRUTH_SUPPLEMENT_DOT = "truth_supplement_dot"   # RTL 补充琥珀标记（标题栏名字后，C-215）

# ───────── ⑦ 电路图区 ─────────
FLOW_PANEL = "flow_panel"
FLOW_TOOLBAR = "flow_toolbar"
FLOW_TITLE = "flow_title"                  # 「电路图」「源寄存器在左 · 顶层输出在右」
FLOW_BTN_FULLSCREEN = "flow_btn_fullscreen"  # 全屏 / 退出全屏
FLOW_BTN_FIT = "flow_btn_fit"              # 适应窗口
FLOW_BTN_100 = "flow_btn_100"              # 100%
FLOW_ZOOM_LABEL = "flow_zoom_label"
FLOW_BTN_EXPORT_SVG = "flow_btn_export_svg"
FLOW_BTN_EXPORT_PNG = "flow_btn_export_png"
FLOW_VIEW = "flow_view"                    # QGraphicsView
FLOW_FOOTER = "flow_footer"                # 底栏操作提示文案
FLOW_LEGEND = "flow_legend"

# ───────── ⑧ .sv 预览 ─────────
SV_PANEL = "sv_panel"
SV_TOOLBAR = "sv_toolbar"
SV_TITLE = "sv_title"                      # 「本信号 .sv 片段（不落盘）」/「勾选集 .sv」
SV_BTN_TOGGLE_SCOPE = "sv_btn_toggle_scope"  # 「改看勾选集」/「改看本信号」C-067/C-068
SV_BTN_COPY = "sv_btn_copy"
SV_TEXT = "sv_text"                        # QPlainTextEdit 只读

# ───────── ⑨ 右侧常驻栏 ─────────
SIDE_PANEL = "side_panel"
SIDE_CHAIN_TITLE = "side_chain_title"      # 「逐层展开」
SIDE_CHAIN_HELP = "side_chain_help"        # 「从顶层输出往回到源寄存器 · 每层：Excel 原式 = 代入真实信号名」
SIDE_CHAIN_VIEW = "side_chain_view"        # QTextBrowser / 自绘列表
SIDE_INPUTS_TITLE = "side_inputs_title"    # 「输入信号 6 个」
SIDE_INPUTS_GUESS_BADGE = "side_inputs_guess_badge"  # 橙色虚线小方块 +「名字来自命名约定，表里未查到」
SIDE_INPUTS_VIEW = "side_inputs_view"      # QTableView（InputsModel，每行两行高）

# ───────── ⑩ 状态栏 ─────────
STATUS_BAR = "status_bar"
STATUS_LEFT = "status_left"
STATUS_RIGHT = "status_right"
STATUS_AUTOSAVE = "status_autosave"        # 「编辑自动存盘 · 上次 14:31」

# ───────── ⑪ 导出中心 ─────────
EXPORT_DIALOG = "export_dialog"
EXPORT_TABLE = "export_table"
EXPORT_SUMMARY = "export_summary"          # 「勾选 3 项 · 覆盖 7 个信号 · 2 个信号会被跳过」
EXPORT_SUMMARY_SKIPPED = "export_summary_skipped"  # 可点开：名字 + 原因（裁决⑯）
EXPORT_BTN_CANCEL = "export_btn_cancel"
EXPORT_BTN_RUN = "export_btn_run"          # 「导出勾选的 N 项」/「先勾选要导出的交付物」
EXPORT_BTN_IMPORT_CONFIG = "export_btn_import_config"  # 「导入配置…」C-192
EXPORT_OPTIONS_POPOVER = "export_options_popover"

#: 交付物行键（顺序 = Design ER 六行）
EXPORT_KINDS = ("sv", "report", "fortest", "nets", "claims", "config")


def fmt_export_check(kind):
    return "export_check_%s" % kind


def fmt_export_scope(kind):
    """范围两段控件容器名（对象范围 × scope）。"""
    return "export_scope_%s" % kind


def fmt_export_scope_objects(kind):
    """对象范围下拉：勾选的 N 个 / 全部 M 个。"""
    return "export_scope_objects_%s" % kind


def fmt_export_scope_sv(kind):
    """.sv 的 scope 下拉：全部（正向+反例）/ 仅正向 / 仅反例 / 正向+反例分文件。"""
    return "export_scope_sv_%s" % kind


def fmt_export_options(kind):
    return "export_options_%s" % kind


def fmt_export_last(kind):
    return "export_last_%s" % kind


# ───────── ⑫ 导出完成 ─────────
DONE_DIALOG = "done_dialog"
DONE_SKIPPED_BLOCK = "done_skipped_block"  # 琥珀块「N 个信号没有进 .sv —— 名字和原因：」
DONE_WRITTEN_BLOCK = "done_written_block"  # 「已写出」
DONE_BTN_OPEN_DIR = "done_btn_open_dir"    # 打开输出目录 N6
DONE_BTN_GO_FIX = "done_btn_go_fix"        # 去处理这 N 个信号
DONE_BTN_OK = "done_btn_ok"                # 知道了

# ───────── ⑬ 诊断抽屉 ─────────
DIAG_DRAWER = "diag_drawer"
DIAG_TITLE = "diag_title"
DIAG_BTN_CLOSE = "diag_btn_close"
DIAG_INTRO = "diag_intro"                  # 「按症状选，不是按按钮选。」
DIAG_CUVUNF_BOX = "diag_cuvunf_box"        # 主症状蓝框
DIAG_CUVUNF_TITLE = "diag_cuvunf_title"
DIAG_STEP1_BTN = "diag_step1_btn"          # 导出 nets.txt…
DIAG_STEP1_BOX = "diag_step1_box"
DIAG_STEP2_BTN = "diag_step2_btn"          # 复制这行命令
DIAG_STEP2_BOX = "diag_step2_box"
DIAG_STEP3_BTN = "diag_step3_btn"          # 导入 probe_prefixes.txt…
DIAG_STEP3_BOX = "diag_step3_box"
DIAG_PREFIX_EDIT_BTN = "diag_prefix_edit_btn"  # 编辑前缀映射…
DIAG_SYM_SUPPLEMENT = "diag_sym_supplement"    # otherSymptoms ①
DIAG_SYM_FORCE = "diag_sym_force"              # ②
DIAG_SYM_COVERAGE = "diag_sym_coverage"        # ③（跳到覆盖度弹层）
DIAG_SYM_RISKY = "diag_sym_risky"              # ④
DIAG_SYM_LEGACY = "diag_sym_legacy"            # ⑤ C-302
DIAG_RISKY_TOGGLE = "diag_risky_toggle"        # 缺前缀是否强制生成 开关（默认 True）C-217
DIAG_LEGACY_IMPORT_BTN = "diag_legacy_import_btn"
DIAG_FOOTER = "diag_footer"                # 「这里的每一项都是逃生阀…」
# 探针前缀编辑器
DIAG_PREFIX_DIALOG = "diag_prefix_dialog"
DIAG_PREFIX_TEXT = "diag_prefix_text"
DIAG_PREFIX_IMPORT_BTN = "diag_prefix_import_btn"
DIAG_PREFIX_EXPORT_BTN = "diag_prefix_export_btn"
DIAG_PREFIX_SAVE_BTN = "diag_prefix_save_btn"
DIAG_PREFIX_IMPACT = "diag_prefix_impact"  # 「共 N 条映射 · 影响 M 个信号」C-204
# 强制 force 编辑器
DIAG_FORCE_DIALOG = "diag_force_dialog"
DIAG_FORCE_TEXT = "diag_force_text"
DIAG_FORCE_IMPORT_BTN = "diag_force_import_btn"
DIAG_FORCE_EXPORT_BTN = "diag_force_export_btn"
DIAG_FORCE_SAVE_BTN = "diag_force_save_btn"
# RTL 补充逻辑编辑器
DIAG_SUPP_DIALOG = "diag_supp_dialog"
DIAG_SUPP_TEXT = "diag_supp_text"
DIAG_SUPP_TEMPLATE_BTN = "diag_supp_template_btn"
DIAG_SUPP_IMPORT_BTN = "diag_supp_import_btn"
DIAG_SUPP_SAVE_BTN = "diag_supp_save_btn"
DIAG_SUPP_ERRORS = "diag_supp_errors"
# 旧版编辑迁移
DIAG_LEGACY_DIALOG = "diag_legacy_dialog"
DIAG_LEGACY_PREVIEW = "diag_legacy_preview"
DIAG_LEGACY_RUN_BTN = "diag_legacy_run_btn"

# ───────── ⑭ 空态 ─────────
EMPTY_PANEL = "empty_panel"
EMPTY_CARD = "empty_card"                  # 620px 居中卡片
EMPTY_ICON = "empty_icon"                  # 88px「xlsx」虚线方块
EMPTY_TITLE = "empty_title"                # 「先载入 Dreg 核心 Excel」
EMPTY_DESC = "empty_desc"
EMPTY_BTN_OPEN = "empty_btn_open"          # 「选择 Excel… Ctrl+O」
EMPTY_BTN_IMPORT_CONFIG = "empty_btn_import_config"
EMPTY_RECENT_TITLE = "empty_recent_title"  # 「最近打开」小标题
EMPTY_RECENT_LIST = "empty_recent_list"    # 最近打开 C-003 / N4
EMPTY_RECENT_NONE = "empty_recent_none"    # 「还没有最近打开的表」（裁决⑫）

# ───────── ⑮ 载入态 ─────────
LOADING_PANEL = "loading_panel"
LOADING_CARD = "loading_card"              # 520px 居中卡片（LOADING_PANEL 是它的外壳）
LOADING_TITLE = "loading_title"            # 「正在展开 Topout 信号 137 / 211」
LOADING_BAR = "loading_bar"
LOADING_CURRENT = "loading_current"        # 「当前：xxx　15 路 mux」
LOADING_HINT = "loading_hint"              # 「清单已可点，展开完的信号先出现…」
LOADING_BTN_STOP = "loading_btn_stop"      # 停止分析

# ───────── 错误条（C-005 / C-272）─────────
ERROR_BAR = "error_bar"
ERROR_TEXT = "error_text"
ERROR_CLOSE = "error_close"

# ───────── 对话框（dialogs.py）─────────
DLG_CONFIRM = "dlg_confirm"                # 三处确认（清零 / 删反例 / auto→期望）
DLG_CONFIRM_TEXT = "dlg_confirm_text"
DLG_RENAME_COL = "dlg_rename_col"
DLG_RENAME_COL_EDIT = "dlg_rename_col_edit"
DLG_RENAME_COL_ERROR = "dlg_rename_col_error"
DLG_MUX_DATA = "dlg_mux_data"              # mux 数据值整表 C-110
DLG_MUX_DATA_TABLE = "dlg_mux_data_table"
DLG_COLUMNS = "dlg_columns"                # 列设置
DLG_COLUMNS_LIST = "dlg_columns_list"
DLG_PASTE_NAMES = "dlg_paste_names"        # 粘贴名单勾选
DLG_PASTE_NAMES_TEXT = "dlg_paste_names_text"
DLG_PASTE_NAMES_RESULT = "dlg_paste_names_result"
DLG_PRESETS = "dlg_presets"                # 预设存取
DLG_PRESETS_LIST = "dlg_presets_list"
DLG_PRESETS_NAME = "dlg_presets_name"
DLG_DUP_LABELS = "dlg_dup_labels"          # 重复 assert 标号确认 C-164
DLG_DUP_LABELS_TEXT = "dlg_dup_labels_text"
DLG_IMPORT_REPORT = "dlg_import_report"    # 导入配置结果（缺段 / 表不一致 / 找不到的信号）
DLG_IMPORT_REPORT_TEXT = "dlg_import_report_text"
DLG_BATCH_FILL = "dlg_batch_fill"          # 批量填期望
DLG_BATCH_FILL_VALUE = "dlg_batch_fill_value"


# ───────── 动态名（组合根）─────────
#: 前缀带 `_` = 不进 `all_names()`（值里有 `%s` / `%d`，不是合法 objectName）
_SHORTCUT_FMT = "shortcut_%s"
_RECENT_ROW_FMT = "empty_recent_row_%d"


def fmt_shortcut(key):
    """窗口级 QShortcut 的名字：shortcut_<contracts.SHORTCUTS 的键>。"""
    return _SHORTCUT_FMT % key


def fmt_recent_row(i):
    """空态「最近打开」第 i 行：empty_recent_row_<i>。"""
    return _RECENT_ROW_FMT % int(i)


def all_names():
    """{常量名: objectName}——测试与自查用（不含 fmt_* 函数产出）。"""
    return {k: v for k, v in globals().items()
            if k.isupper() and isinstance(v, str) and not k.startswith("_")}
