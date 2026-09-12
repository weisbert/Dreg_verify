# -*- coding: utf-8 -*-
"""session.py — GUI 的【会话状态层】：设置文件 IO、覆盖度三层状态、配置导入导出、诊断配置校验。

为什么单独一层（GUI v2「换包不换引擎」的地基）：
  现在这套界面(gui.py)与 v2 新界面要共用同一份「会话状态」——上次开的表、每个视图的覆盖度档、
  整份配置的导出/导入、探针前缀/强制force/RTL 补充这三套诊断配置。把它们从 Qt 控件里剥出来后，
  两套门面都只是「读写同一层状态 + 各自画自己的控件」，口径不可能再分叉。

本模块【不】import PySide6，也不碰任何控件：
  · 入参出参全是 dict/list/str 等朴素数据；需要控件当前值的，由调用方读了再传进来。
  · gui.py 里对应的方法改成薄委托（现有 ≈420 条 GUI 测试即等价性证明）。
  · 测试项编辑(EDITS_PATH 那套 rows/mux 向量序列化)不在本层——见 gui.py 的 edits 相关函数；
    本层把 edits/view_edits/view_checks 当【不透明数据】原样进出。

覆盖度口径（重要）：三层优先级 单点(sig_cov) > 逻辑类型(form_cov) > 全局默认，
与 topout._effective_cov_str / topout._cov_for 同源——档位字符串→(mode, exhaustive) 一律走
generator._decompose_cov，不另写一份换算，否则清单用例数与真值表列数会对不上(R25/#3 实证)。
"""

import json
import os
import sys

from . import excel_model
from . import expr as E
from . import generator

# ───────────────────────── 设置文件 IO ─────────────────────────
# 记住上次加载的 Excel、各视图覆盖档、各表的探针前缀/force/补充逻辑等「界面偏好 + 诊断配置」。
# ⚠ 与测试项编辑(EDITS_PATH)分开存：编辑数据大且语义是「劳动成果」，不是偏好。
DEFAULT_SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".dreg_verify_gui.json")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_settings(path=None):
    """读取持久化配置(上次的 Excel、上次的导出选项等)。读不到/坏文件 → {}（绝不抛）。"""
    try:
        with open(path or DEFAULT_SETTINGS_PATH, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_settings(d, path=None, skip_under_pytest=True):
    """写持久化配置。测试环境(pytest)下不落盘，避免把临时状态污染到用户的真实配置。
    返回 True=真写了盘。写失败静默吞掉（配置存不下不该影响正在干的活）。"""
    if skip_under_pytest and "pytest" in sys.modules:
        return False
    try:
        with open(path or DEFAULT_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
        return True
    except Exception:  # noqa: BLE001
        return False


def load_last_excel(path=None):
    """上次加载的 Excel 路径（没有 → None）。"""
    return load_settings(path).get("last_excel")


def save_last_excel(excel, path=None):
    """记住这次加载的 Excel，下次启动自动恢复。"""
    d = load_settings(path)
    d["last_excel"] = excel
    save_settings(d, path)


def save_path_map(key, excel_path, value, load=None, save=None, skip_under_pytest=False):
    """把【按 Excel 路径分桶】的配置段写进 settings：settings[key][excel_path] = value。
    value 为空 → 删掉这张表的条目(= 清除配置)。探针前缀/强制force/RTL 补充三套共用。

    load/save 可注入（gui.py 传自己的模块级 _load_settings/_save_settings，测试 monkeypatch 它们
    时仍然生效——绑定发生在调用时刻，不是构造时刻）。
    """
    if skip_under_pytest and "pytest" in sys.modules:
        return False
    st = (load or load_settings)()
    all_maps = st.get(key, {})
    if value:
        all_maps[excel_path] = value
    else:
        all_maps.pop(excel_path, None)
    st[key] = all_maps
    (save or save_settings)(st)
    return True


def path_map_of(key, excel_path, load=None):
    """读回 settings[key][excel_path]（缺省 {}）——与 save_path_map 对称。"""
    st = (load or load_settings)()
    seg = st.get(key, {})
    return seg.get(excel_path, {}) if isinstance(seg, dict) else {}


def code_version(root=None):
    """工具代码版本（git 短 HEAD，拿不到给空）——进窗口标题。

    2026-06-10 实地教训：用户机器上可能同时存在旧拷贝/旧进程，「改了却看不到」排查
    了一整轮才怀疑到版本——标题带 HEAD 后一眼可辨跑的是哪份代码。"""
    try:
        import subprocess
        return subprocess.run(
            ["git", "-C", root or _repo_root(), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


# ───────────────────────── 覆盖度三层状态 ─────────────────────────
COV_LABELS = ("精简", "全面", "穷举")            # 界面上的三档中文名
COV_MODES = ("min", "max", "exhaustive")        # 存档/接口用的档位字符串
LABEL_TO_COV = {"精简": "min", "全面": "max", "穷举": "exhaustive"}
COV_TO_LABEL = {v: k for k, v in LABEL_TO_COV.items()}
DEFAULT_COV_LABEL = "全面"
DEFAULT_MAX_TESTS = 256
MAX_TESTS_RANGE = (1, 100000)

# 『覆盖度·按逻辑类型』每行：(形态键, 显示名, 例子表达式, 各档说明 tooltip)。
# 形态键 = topout._form_cov_key 的产物(register/boolean/select/gated)，覆盖方案据【展开后表达式
# 形态 F0-F4】派发(用户第一性原理)。这里是纯数据，两套门面各自画自己的控件。
FORM_COV_ROWS = [
    ("register", "直连寄存器 (F0)", "out = d_reg[3:0]",
     "单字段透传：精简/全面/穷举均验【全0 + 各位异值】(约 2 条，位宽取字段全宽)。"),
    ("boolean", "布尔/位运算 (F1)", "out = (A & ~B) | C",
     "精简=控制位关键组合各 1 组数据；全面=控制位全组合 × 多组数据(全0/全1/反码/走步)；"
     "穷举=所有输入位全组合(≤10 位，否则退『全面』)。"),
    ("select", "选路 (F2)", "out = sel ? A : B",
     "精简=每条路 1 个代表值；全面=精简 + x 位展开 + 每路反码数据轮(抓数据通路坏位)；"
     "穷举=全面 + 换另一条物理控制路径(line/local)全扫。mux 八选一同理。"),
    ("gated", "门控·iddq (F3/F4)", "out = iddq ? 0 : (sel ? A : B)",
     "= 内层形态(选路/布尔)覆盖 × iddq 透传(功能向量门=0) + 追加 1 条 DFT 漏电拍(门=1 压输出到 0)。"
     "档位作用于内层，DFT 拍恒 1 条。"),
]
FORM_LABELS = {key: label for key, label, _ex, _tip in FORM_COV_ROWS}


def decompose_label(label):
    """覆盖度中文档名 → (mode, exhaustive)。非三档之一 → 按『全面』(旧 GUI 下拉的兜底行为)。"""
    return generator._decompose_cov(LABEL_TO_COV.get(label, "max"))


def form_key_of(models, name):
    """在清单 view_models 里找这个信号的【形态键】(m['form'])；找不到 → None(跟随全局)。"""
    low = str(name).lower()
    m = next((mm for mm in (models or []) if str(mm.get("name", "")).lower() == low), None)
    return m.get("form") if m else None


def cov_hint_text(cols, customized=False):
    """覆盖度提示文案：『→ 当前信号 N 条[，含 M 负向][（已自定义）]』；无列 → 空串。"""
    if not cols:
        return ""
    n = len(cols)
    nn = sum(1 for c in cols if c["neg"])
    tag = "，含 %d 负向" % nn if nn else ""
    tag += "（已自定义）" if customized else ""
    return "→ 当前信号 %d 条%s" % (n, tag)


class CoverageState:
    """一个视图(view_id)的覆盖度三层状态：全局默认档 + 上限 + 逻辑类型档 + 单点档。

    优先级：单点(sig_cov) > 逻辑类型(form_cov) > 全局默认——与 topout._effective_cov_str 同口径。
    存盘策略(R25 教训)：
      · 全局档 cov_<view_id> / 上限 maxt_<view_id> 存盘，下次打开恢复；
      · 单点档、逻辑类型档【只在会话内】，不存盘不导出——否则上次留的单点档会静默盖过全局下拉，
        用户表现为「刚开 GUI 改全局对某些信号无效」。
    load/save 可注入，见 save_path_map 的说明。
    """

    def __init__(self, view_id, global_label=DEFAULT_COV_LABEL, max_tests=DEFAULT_MAX_TESTS,
                 load=None, save=None):
        self.view_id = str(view_id)
        self.global_label = global_label if global_label in COV_LABELS else DEFAULT_COV_LABEL
        self.max_tests = int(max_tests)
        self.sig_cov = {}     # 信号名低 -> min/max/exhaustive（会话内临时档）
        self.form_cov = {}    # 形态键   -> min/max/exhaustive（会话内临时档）
        self._load = load
        self._save = save

    # ── settings 键名（按 view_id 分桶：Topout 与各子视图各存各的）──
    @property
    def cov_key(self):
        return "cov_%s" % self.view_id

    @property
    def maxt_key(self):
        return "maxt_%s" % self.view_id

    def _settings(self):
        return (self._load or load_settings)()

    def _write(self, st):
        (self._save or save_settings)(st)

    # ── 存盘/恢复（全局档 + 上限）──
    def restore_global_label(self, default=DEFAULT_COV_LABEL):
        """从 settings 恢复本视图的全局默认档并记进状态；没存过/值非法 → default。"""
        v = self._settings().get(self.cov_key)
        self.global_label = v if v in COV_LABELS else default
        return self.global_label

    def restore_max_tests(self, skip_under_pytest=True):
        """从 settings 恢复本视图的用例上限并记进状态；没存过/越界 → None(调用方保持默认 256)。
        pytest 下一律不恢复(与 save_settings 的 no-op 对称，防真机配置污染测试基线)。"""
        if skip_under_pytest and "pytest" in sys.modules:
            return None
        v = self._settings().get(self.maxt_key)
        lo, hi = MAX_TESTS_RANGE
        if isinstance(v, int) and lo <= v <= hi:
            self.max_tests = v
            return v
        return None

    def persist_global_label(self, label):
        """全局默认档改动 → 记进状态 + 存盘（按 view_id）。"""
        self.global_label = label if label in COV_LABELS else DEFAULT_COV_LABEL
        st = self._settings()
        st[self.cov_key] = label
        self._write(st)
        return self.global_label

    def persist_max_tests(self, n):
        """用例上限改动 → 记进状态 + 存盘（按 view_id）。"""
        self.max_tests = int(n)
        st = self._settings()
        st[self.maxt_key] = int(n)
        self._write(st)
        return self.max_tests

    # ── 单点档 / 逻辑类型档 ──
    def sig_cov_of(self, name):
        """该信号的单点档；没单设 → ""（给下拉回显用）。"""
        return self.sig_cov.get(str(name).lower(), "") if name is not None else ""

    def set_sig_cov(self, name, value):
        """设/清该信号的单点档（空值 = 跟随全局，直接删掉不留痕）。返回生效后的档(或 "")。"""
        low = str(name).lower()
        if value in COV_MODES:
            self.sig_cov[low] = value
            return value
        self.sig_cov.pop(low, None)
        return ""

    def set_form_cov(self, mapping):
        """整体替换逻辑类型档（空值 = 跟随全局，不存）。返回新的 form_cov。"""
        self.form_cov = {k: v for k, v in (mapping or {}).items() if v}
        return self.form_cov

    # ── 生效档查询（与 topout._effective_cov_str 同口径）──
    def mode(self):
        """全局默认档 → (mode, exhaustive)。"""
        return decompose_label(self.global_label)

    def effective_cov(self, name, models=None, form_key=None):
        """该信号的生效档字符串(min/max/exhaustive)，都没命中 → None(跟随全局)。
        form_key 可直接给；没给就用 models(清单 view_models)现查。"""
        c = self.sig_cov.get(str(name).lower())
        if c in COV_MODES:
            return c
        if form_key is None and models is not None:
            form_key = form_key_of(models, name)
        fc = self.form_cov.get(form_key) if form_key else None
        return fc if fc in COV_MODES else None

    def mode_for(self, name, models=None, form_key=None, global_mode=None):
        """本信号有效覆盖度 → (mode, exhaustive)。优先级 单点 > 逻辑类型 > 全局。
        global_mode 可传调用方自己的全局档(gui 的下拉控件才是那边的真相源)；不传则用本状态的。"""
        c = self.effective_cov(name, models=models, form_key=form_key)
        if c:
            return generator._decompose_cov(c)
        return global_mode if global_mode is not None else self.mode()

    def effective_label(self, name, models=None, form_key=None):
        """给界面用的『生效档 + 来源』：返回 (档中文名, 来源说明)。
        例：("穷举", "本信号") / ("精简", "逻辑类型·选路 (F2)") / ("全面", "全局默认")。"""
        c = self.sig_cov.get(str(name).lower())
        if c in COV_MODES:
            return COV_TO_LABEL[c], "本信号"
        if form_key is None and models is not None:
            form_key = form_key_of(models, name)
        fc = self.form_cov.get(form_key) if form_key else None
        if fc in COV_MODES:
            return COV_TO_LABEL[fc], "逻辑类型·%s" % FORM_LABELS.get(form_key, form_key)
        return self.global_label, "全局默认"

    def effective_chain(self, name, models=None, form_key=None):
        """N7（GUI v2 冲突⑧）：整条**三层继承链**，给覆盖度弹层的蓝条与「生效行高亮」用。

        `effective_label` 只回赢的那一层，画不出 Design 要的
        「本信号「跟随上级」→ 逻辑类型「选路 (F2)」= 全面 → 全局默认 = 全面」——它不动，本方法另给。

        返回**恒定三行**（次序 = 优先级从高到低：本信号 > 逻辑类型 > 全局默认），每行：
            {"level": 层名(中文),        # "本信号" / "逻辑类型·选路 (F2)" / "全局默认"
             "label": 该层的档(中文),    # 没单设 → "跟随上级"(本信号) / "跟随全局"(逻辑类型)
             "active": 该层是否参与生效,  # 见下
             "key":   该层的档位键}       # min/max/exhaustive；没单设 → ""

        `active` 规则（冲突⑧原文：「本信号『跟随上级』时，逻辑类型层与全局层**都**算参与了生效」）：
            本信号有单点档 → (True, False, False)   本层一锤定音，下面两层被跨过
            本信号跟随上级 → (False, True, True)    逻辑类型层与全局层都参与（Design 的两行高亮）
        即 active 只由「本信号这层有没有单设」决定，不看逻辑类型层设没设——
        逻辑类型层没设时它把全局档透传下去，两层仍都在生效链上。

        form_key 可直接给；没给就用 models(清单 view_models)现查——与 effective_label 同口径。
        """
        sig = self.sig_cov.get(str(name).lower())
        sig_set = sig in COV_MODES
        if form_key is None and models is not None:
            form_key = form_key_of(models, name)
        fc = self.form_cov.get(form_key) if form_key else None
        form_set = fc in COV_MODES
        form_level = ("逻辑类型·%s" % FORM_LABELS.get(form_key, form_key)) if form_key else "逻辑类型"
        return [
            {"level": "本信号", "active": sig_set,
             "label": COV_TO_LABEL[sig] if sig_set else "跟随上级",
             "key": sig if sig_set else ""},
            {"level": form_level, "active": not sig_set,
             "label": COV_TO_LABEL[fc] if form_set else "跟随全局",
             "key": fc if form_set else ""},
            {"level": "全局默认", "active": not sig_set,
             "label": self.global_label,
             "key": LABEL_TO_COV.get(self.global_label, "max")},
        ]


# ───────────────────────── 配置导入导出 ─────────────────────────
CONFIG_VERSION = 2
CONFIG_BAD_FILE_MSG = "不是 dreg_verify 配置/编辑文件(缺少 dreg_verify_config 或 edits/mux_* 段)。"
# 「用户配置层」的全部可编辑状态字段（导入完整配置前先清空 = 加载这份工作状态，而非叠加）。
# 值是构造器，取用时各造一份新的空容器。
_BLANK_CONFIG_STATE = (
    ("_edited", dict), ("_customized", set), ("_neg_only", dict),
    ("_mux_expected", dict), ("_mux_neg", set), ("_mux_data", dict),
    ("_mux_dropped", dict), ("_mux_cleared", set), ("_mux_user_vecs", dict),
    ("_sig_cov", dict), ("_sig_cascade", dict),
    ("_probe_prefixes", dict), ("_force_signals", set), ("_suffix_override", dict),
    ("_logic_overrides", dict),
)


def blank_config_state():
    """导入前要清空的全部用户配置状态 → {属性名: 空容器}。不碰已加载的 wb/signals/解析画像。"""
    return {name: factory() for name, factory in _BLANK_CONFIG_STATE}


def default_config_filename(excel):
    """导出配置的默认文件名：<源表名>_config.json（没表名 → dreg_config.json）。"""
    return os.path.splitext(os.path.basename(excel or "") or "dreg")[0] + "_config.json"


def collect_config(excel_path, global_settings, signals_checked=None, probe_prefixes=None,
                   force_signals=None, suffix_override=None, logic_overrides=None,
                   edits=None, neg_only=None, mux_expected=None, mux_neg=None, mux_data=None,
                   mux_dropped=None, mux_cleared=None, mux_user_vecs=None,
                   view_edits=None, view_checks=None):
    """收集【完整配置】(第二十六轮，用户拍板「不只mux，logic/勾选/全局/测试编辑等所有配置一键带走」)：
    信号勾选 + 全局工具栏设置 + 探针前缀 + 强制force + 全部 per-signal 测试编辑(含 mux 删除/清空)。
    单点覆盖度【不含】——会话内临时档。
    excel/excel_path：记下这份配置对应的源表(全路径+文件名)，导入时按文件名核对、配错表给提示。

    edits / mux_user_vecs / view_edits / view_checks 是【不透明数据】：调用方先序列化好再传进来，
    本层只做归一化(空桶剔除/排序)，不碰它们的内部结构。
    """
    excel = (excel_path or "").strip()
    return {
        "dreg_verify_config": CONFIG_VERSION,
        "excel": os.path.basename(excel),     # 文件名（跨机器比对用，路径常不同）
        "excel_path": excel,                  # 全路径（本机来源参考）
        "signals_checked": list(signals_checked or []),
        "global": dict(global_settings or {}),
        "probe_prefixes": dict(probe_prefixes or {}),
        "force_signals": sorted(force_signals or []),
        "suffix_override": dict(suffix_override or {}),
        # RTL 补充逻辑：Excel 缺级时手工补的等价 logic 式（Claude 代写、用户导入复核）
        "logic_overrides": {k: dict(v) for k, v in (logic_overrides or {}).items()},
        "edits": dict(edits or {}),
        "neg_only": dict(neg_only or {}),
        "mux_expected": {k: dict(v) for k, v in (mux_expected or {}).items() if v},
        "mux_neg": sorted(mux_neg or []),
        "mux_data": {k: dict(v) for k, v in (mux_data or {}).items() if v},
        "mux_dropped": {k: sorted(v) for k, v in (mux_dropped or {}).items() if v},
        "mux_cleared": sorted(mux_cleared or []),
        "mux_user_vecs": {k: v for k, v in (mux_user_vecs or {}).items() if v},
        # SignalView（Topout + 子视图）逐信号编辑/勾选——让 Topout 视图的手填期望随配置跨机器迁移
        "view_edits": view_edits,
        "view_checks": view_checks,
    }


def write_config_file(path, payload):
    """把配置写成 .json（给同事/版本库/跨机器）。IO 失败抛 OSError，由调用方弹框。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def read_config_file(path):
    """读一份配置 .json。读不了/不是 JSON → 抛 OSError/ValueError，由调用方弹框。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def classify_config(payload):
    """认版本：返回 (是完整配置 v2, 是旧【测试项编辑】文件 v1)。两者都不是 → (False, False)。"""
    if not isinstance(payload, dict):
        return False, False
    is_full = bool(payload.get("dreg_verify_config"))
    is_legacy = bool(payload.get("dreg_verify_edits")
                     or any(k in payload for k in ("edits", "mux_expected", "mux_data")))
    return is_full, is_legacy


def normalize_global_settings(g):
    """导入配置的 global 段 → 规范化目标值（纯数据，GUI 照单套控件）。键兼容规则：
      · coverage_logic/coverage_mux：非三档之一 → None(保持当前)
      · max_tests：越界/非整数 → None(保持当前)
      · cascade_logic/cascade_mux：缺新键回退旧 cascade_mode；都缺 = cone
      · append_to_logic：缺键复位默认 True（生产默认勾，2026-06-11 翻回）
      · append_to_mux：缺键复位默认 False（mux 默认探裸名）
      · include_risky：缺键 → None(【保持当前】而非复位)——较新字段，旧配置/pytest 基线本就无此键，
        不该翻动用户/测试基线刻意设的本机选择；present 即套用(它会改产物)。
    """
    g = g if isinstance(g, dict) else {}
    out = {}
    for key in ("coverage_logic", "coverage_mux"):
        v = g.get(key)
        out[key] = v if v in COV_LABELS else None
    mt = g.get("max_tests")
    lo, hi = MAX_TESTS_RANGE
    out["max_tests"] = mt if isinstance(mt, int) and lo <= mt <= hi else None
    cm = g.get("cascade_mode")
    for key in ("cascade_logic", "cascade_mux"):
        out[key] = "force" if g.get(key, cm) == "force" else "cone"
    atl = g.get("append_to_logic")
    out["append_to_logic"] = atl if isinstance(atl, bool) else True
    atm = g.get("append_to_mux")
    out["append_to_mux"] = atm if isinstance(atm, bool) else False
    ir = g.get("include_risky")
    out["include_risky"] = ir if isinstance(ir, bool) else None
    return out


def persist_global_settings(values, load=None, save=None):
    """把全局工具栏设置(级联/输出引用尾缀/缺前缀强制生成)写进 settings，下次启动恢复。
    只写 values 里【出现过】的键(覆盖档与上限另由调用方的 _persist_coverage 负责)。"""
    st = (load or load_settings)()
    for k in ("cascade_logic", "cascade_mux", "append_to_logic", "append_to_mux", "include_risky"):
        if k in values:
            st[k] = values[k]
    (save or save_settings)(st)


def normalize_probe_prefixes(pp):
    """配置里的 probe_prefixes 段 → {信号名低: 路径}；不是 dict → None(= 不套用，保持当前)。"""
    if not isinstance(pp, dict):
        return None
    return {str(k).strip().lower(): str(v).strip() for k, v in pp.items() if v and str(v).strip()}


def normalize_force_signals(fs):
    """配置里的 force_signals 段 → {基名低}；不是 list → None(= 不套用)。"""
    if not isinstance(fs, list):
        return None
    return {str(x).strip().lower() for x in fs if str(x).strip()}


def normalize_suffix_override(so):
    """配置里的 suffix_override 段 → {信号名低: bool}；不是 dict → None(= 不套用)。"""
    if not isinstance(so, dict):
        return None
    return {str(k).strip().lower(): bool(v) for k, v in so.items() if str(k).strip()}


def normalize_logic_overrides(lo):
    """配置里的 logic_overrides 段 → {基名低: spec}；不是 dict → None(= 不套用)。"""
    if not isinstance(lo, dict):
        return None
    return {str(k).strip().lower(): dict(v) for k, v in lo.items()
            if str(k).strip() and isinstance(v, dict)}


def apply_config(payload, current_excel=""):
    """解析一份配置 payload → 规范化的「套用计划」(纯数据)，供 GUI 照单套控件/状态。

    返回 dict：
      ok / error           —— 不认这个文件时 ok=False，error 是提示文案(调用方前面拼上文件名)
      is_full / is_legacy  —— v2 完整配置 / v1 旧测试项编辑文件
      cfg_excel / cur_excel / excel_mismatch —— 源表核对(按【文件名】比，跨机器路径不同是正常用法)
      global / probe_prefixes / force_signals / suffix_override / logic_overrides
                           —— 仅完整配置才有；值为 None 表示该段不套用(缺段/类型不对，保持当前)
    测试项编辑(edits/mux_*/view_*)不在这里——调用方自己从 payload 取，本层不碰其序列化。
    """
    is_full, is_legacy = classify_config(payload)
    out = {"ok": bool(is_full or is_legacy), "error": None,
           "is_full": is_full, "is_legacy": is_legacy,
           "cfg_excel": "", "cur_excel": "", "excel_mismatch": False,
           "global": None, "probe_prefixes": None, "force_signals": None,
           "suffix_override": None, "logic_overrides": None}
    if not out["ok"]:
        out["error"] = CONFIG_BAD_FILE_MSG
        return out
    cfg_excel = str(payload.get("excel")
                    or os.path.basename(str(payload.get("excel_path") or ""))).strip()
    cur_excel = os.path.basename((current_excel or "").strip())
    out["cfg_excel"] = cfg_excel
    out["cur_excel"] = cur_excel
    out["excel_mismatch"] = bool(cfg_excel and cur_excel and cfg_excel.lower() != cur_excel.lower())
    if is_full:
        out["global"] = normalize_global_settings(payload.get("global") or {})
        out["probe_prefixes"] = normalize_probe_prefixes(payload.get("probe_prefixes"))
        out["force_signals"] = normalize_force_signals(payload.get("force_signals"))
        out["suffix_override"] = normalize_suffix_override(payload.get("suffix_override"))
        out["logic_overrides"] = normalize_logic_overrides(payload.get("logic_overrides"))
    return out


# ───────────────────────── 诊断配置：探针前缀 / 强制 force / RTL 补充逻辑 ─────────────────────────
def read_text_file(path):
    """读一个纯文本配置文件(探针前缀映射 .txt / 补充逻辑 .json)。IO 失败抛 OSError。"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_mapping_text(path, text):
    """把映射编辑框文本存成 .txt（尾部规范成单个换行）。IO 失败抛 OSError。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def parse_probe_prefix_text(text):
    """探针前缀编辑框文本 → {信号名: 层级路径}（合并/扁平两种写法，解析在 generator 里）。"""
    return generator.parse_probe_prefix_lines(text)


def render_probe_prefix_text(mapping):
    """{信号名: 层级路径} → 编辑框文本（合并格式：同路径的信号归一行）。"""
    return generator.render_probe_prefix_grouped(mapping)


def merge_probe_prefix_text(cur_text, new_text):
    """导入映射文件：与编辑框现有内容合并，同名以导入为准 → 渲染回编辑框文本。"""
    merged = parse_probe_prefix_text(cur_text)
    merged.update(parse_probe_prefix_text(new_text))
    return render_probe_prefix_text(merged)


def parse_force_signal_text(text):
    """强制 force 编辑框文本 → {基名低}。每行一个基名；# 开头/行尾 = 注释；空行忽略。"""
    names = set()
    for line in (text or "").splitlines():
        s = line.split("#", 1)[0].strip().lower()
        if s:
            names.add(s)
    return names


def render_force_signal_text(names):
    """{基名} → 编辑框文本（每行一个，排序稳定）。"""
    return "\n".join(sorted(names or []))


def supplement_template(sig=None):
    """为【当前编辑器里的 logic 信号】生成一份补充 spec 模板：预填原表达式+原输入映射，
    用户/Claude 只需把 ECO 级包在外面、加新输入。无选中信号(或选的是 mux 组) → 通用空模板。"""
    if sig is not None and not isinstance(sig, excel_model.MuxGroup):
        name = sig.out_base.lower()
        inputs = [{"var": k, "raw": info.get("raw", "")}
                  for k, info in sig.inputs.items()]
        return {name: {
            "_提示": "把 ECO 级(如 2:1 mux/二级 iddq)包在原表达式外面，并在 inputs 里加新输入；理由填 note",
            "enabled": True,
            "note": "（填理由：SE 说 RTL 顶层口后多了什么级）",
            "expr": sig.expr,
            "inputs": inputs,
        }}
    return {"<信号基名>": {
        "_提示": "var=表达式里的变量名(大小写无关)，raw=真实网名(可带[msb:lsb])；理由填 note",
        "enabled": True, "note": "（理由）",
        "expr": "ECO_IDDQ ? 1'b0 : (VCO_FC_SEL ? VCO_EN_FASTON : (EN & ~IDDQ))",
        "inputs": [{"var": "EN", "raw": "<原使能寄存器>"},
                   {"var": "IDDQ", "raw": "iddq"},
                   {"var": "VCO_FC_SEL", "raw": "d_vco_fc_sel_ls[0]"},
                   {"var": "VCO_EN_FASTON", "raw": "d_vco_en_faston"},
                   {"var": "ECO_IDDQ", "raw": "<ECO 二级 iddq 网>"}]}}


def validate_supplements(data):
    """校验 {信号: spec} 映射：返回 (规范化后的 dict, [错误串])。错误非空时不应保存。"""
    if not isinstance(data, dict):
        return {}, ["顶层必须是 JSON 对象 {信号基名: {expr, inputs, ...}}"]
    out, errs = {}, []
    for raw_name, spec in data.items():
        name = str(raw_name).strip().lower()
        if not name or name.startswith("<"):
            errs.append("信号名 %r 无效(占位符未替换?)" % raw_name); continue
        if not isinstance(spec, dict):
            errs.append("%s: spec 必须是对象" % name); continue
        expr = str(spec.get("expr", "") or "").strip()
        if not expr:
            errs.append("%s: 缺 expr" % name); continue
        ins = spec.get("inputs")
        if not isinstance(ins, list) or not ins:
            errs.append("%s: inputs 应为非空列表 [{var,raw},...]" % name); continue
        try:
            node = E.parse(expr)
        except Exception as ex:  # noqa: BLE001
            errs.append("%s: 表达式解析失败 — %s" % (name, ex)); continue
        try:
            sigobj = generator.make_supplement_signal(name, spec, None)
        except Exception as ex:  # noqa: BLE001
            errs.append("%s: inputs 解析失败 — %s" % (name, ex)); continue
        missing = set(E.collect_vars(node)) - set(sigobj.inputs.keys())
        if missing:
            errs.append("%s: 表达式用到的变量没有 input 映射: %s"
                        % (name, ", ".join(sorted(missing))))
            continue
        out[name] = spec
    return out, errs


def render_supplements_json(d):
    """{信号: spec} → 编辑框 JSON 文本；空 → 空串(编辑框显示 placeholder)。"""
    return json.dumps(d, ensure_ascii=False, indent=2) if d else ""


def parse_supplements_json(text):
    """编辑框 JSON 文本 → 数据；空文本 → None(= 清空补充)。非法 JSON 抛 ValueError。"""
    txt = (text or "").strip()
    if not txt:
        return None
    return json.loads(txt)


def merge_supplements_text(text, tmpl):
    """把模板并进编辑框现有 JSON。返回 (新文本, ok)；ok=False = 现有内容不是合法 JSON(不动它)。"""
    txt = (text or "").strip()
    if not txt:
        return render_supplements_json(tmpl), True
    try:
        cur = json.loads(txt)
    except ValueError:
        return text, False
    if isinstance(cur, dict):
        cur.update(tmpl)
        return render_supplements_json(cur), True
    return text, True
