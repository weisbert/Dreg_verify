# -*- coding: utf-8 -*-
"""persist.py —— ui 状态层的【文件 IO 唯一入口】（架构 §2.1 / §6.2；不变量 I-01、I-05、I-08）。

两个文件，两种语义（A3 §3.1 / §3.2）：
  · `SETTINGS_PATH = ~/.dreg_verify_gui.json`  —— 界面偏好 + 诊断配置，平坦 dict、无版本号、宽容读；
  · `EDITS_PATH    = ~/.dreg_verify_edits.json`—— 劳动成果（designer 手填期望/负向/自定义列/勾选），
    顶层按 **Excel 全路径**分桶，一个桶里同时住着 legacy 段与 v2 段。

本模块只做三件事，且都不自己实现格式：
  ① 路径（模块级常量，**可 monkeypatch** —— `tests/ui_harness.isolate_settings` 按名字 patch 这两个，
     与 `gui.py` 同名故同一个 helper 能同时隔离两套门面）；
  ② 落盘策略（I-08 / C-246：路径 == 默认路径时 pytest 下一律 no-op，绝不污染用户真机的配置与编辑；
     测试把路径 patch 到 tmp 后写入照常生效，持久化行为才测得到）；
  ③ **桶合并写**（I-05 / C-300）——见 `write_edits_bucket` 的长注释，这是本模块存在的头号理由。

格式与序列化一律走 `session` / `edits` / `exports`：多一份实现就多一处会漂的口径。

依赖方向（§1.2）：只 import Qt-free 层（session / edits / exports），不 import PySide6、不 import 视图。
"""

import os
import sys

from dreg_verify import edits as ED
from dreg_verify import exports as X
from dreg_verify import session

from . import contracts

__all__ = [
    "SETTINGS_PATH", "EDITS_PATH", "LEGACY_SEGMENTS", "V2_SEGMENTS",
    "NO_PERSIST_ENV", "no_persist_env", "is_isolated", "may_read_machine_settings",
    "load_settings", "save_settings", "patch_settings",
    "load_edits_all", "save_edits_all", "load_edits_bucket", "write_edits_bucket",
    "take_corrupt_backup",
    "load_legacy_bucket", "legacy_bucket_counts",
    "list_columns_get", "list_columns_set", "presets_get", "presets_set",
    "push_recent", "recent_excels", "update_recent_count",
    "last_export", "record_last_export",
    "path_map_of", "save_path_map", "load_export_options", "store_export_options",
]

#: 界面偏好 / 诊断配置（可 monkeypatch；与 `gui.SETTINGS_PATH` 同名同义）
SETTINGS_PATH = session.DEFAULT_SETTINGS_PATH
#: 测试项编辑（可 monkeypatch；与 `gui.EDITS_PATH` 同名同义）
EDITS_PATH = ED.DEFAULT_EDITS_PATH

#: 出厂默认路径——**只用来判断「现在写的是不是用户真机那份」**（I-08），不参与读写
_SETTINGS_DEFAULT = SETTINGS_PATH
_EDITS_DEFAULT = EDITS_PATH

#: 一个 Excel 桶里 legacy（『排查(旧)』门面）写的九段 —— v2 **不读不删**（C-235），写时逐字节保留
LEGACY_SEGMENTS = ("edits", "neg_only", "mux_expected", "mux_neg", "mux_data",
                   "mux_dropped", "mux_cleared", "mux_user_vecs", "signals_checked")
#: v2 自己管的三段（C-234；`view_mux_data` 是 R2-01 加的，形状同 legacy 的 `mux_data`
#: 段但多一层 view_id：`{view_id: {信号名low: {物理基名low: int}}}`）
V2_SEGMENTS = ("view_edits", "view_checks", "view_mux_data")

#: 这两段**按信号逐条合并**（I-05 / R2-04 / R2-06）：同一个 view_id 的子桶里同时住着
#: 「本会话改过的信号」「本会话恢复不出来的信号」「另一个窗口刚存进去的信号」——
#: 整段替换会把后两类静默抹掉。`view_checks` 不在此列：勾选集是**整个视图**一个值。
PER_SIGNAL_SEGMENTS = ("view_edits", "view_mux_data")

#: settings 里 v2 新增的两个键（清单列设置 / 筛选预设），其余键名一律沿用 A3 §3.1 的旧名
PRESETS_KEY = contracts.SETTINGS_PRESETS
INCLUDE_RISKY_KEY = "include_risky"

#: 「这一趟绝不碰我真机那两份文件」的环境变量（I-08 的第二把锁，值 = "1"）
NO_PERSIST_ENV = "DREG_VERIFY_NO_PERSIST"


# ═════════════════════ ① settings（界面偏好 + 诊断配置）═════════════════════
def no_persist_env():
    """`DREG_VERIFY_NO_PERSIST=1` 有没有设。

    I-08 原本只有一把锁：「pytest + 出厂默认路径」。**pytest 外起窗的脚本**（review /
    迁移 / 手工复现脚本）两个条件都不满足，于是它们打开的每张镜像表都被写进了用户真机
    `~/.dreg_verify_gui.json` 的 `recent_excels`（D1 对抗 review 实证，P-22）。
    这把锁给那类脚本用：设上它，`save_settings` / `save_edits_all` 对真机那两份一个字节都不写。"""
    return str(os.environ.get(NO_PERSIST_ENV, "")).strip() == "1"


def is_isolated():
    """现在的读写是不是已经与用户真机那份**隔开**了。

    两条都算隔开：① 路径被指到临时目录（`tests/ui_harness.isolate_settings` 干的）；
    ② `DREG_VERIFY_NO_PERSIST=1`（pytest 外起窗的脚本，见 `no_persist_env`）。

    读设置这件事在 pytest 下本身是安全的，但**读用户真机那份**会让默认值断言随机红
    （同事机上存着 `include_risky: false` / `maxt_topout: 512`…）。凡是「只在隔离后才该
    生效」的恢复动作都问它一句，判据只此一处，不在各模块各写各的 `"pytest" in sys.modules`。"""
    return SETTINGS_PATH != _SETTINGS_DEFAULT or no_persist_env()


def may_read_machine_settings():
    """现在从盘上恢复「界面偏好」安不安全。

    只有 **pytest + 出厂默认路径** 这一种组合要躲开——那读的是用户真机的偏好
    （同事机上可能存着 `maxt_topout: 512` / `cov_topout: 穷举`），默认值断言会随机红。
    真机运行、或测试已经把路径 patch 到 tmp，都照常读。"""
    return is_isolated() or "pytest" not in sys.modules


def load_settings():
    """读设置（I-01 / C-227：未知键原样留着、坏文件 → `{}`，**绝不收紧**）。

    同事机上的旧文件里有一堆 v2 已经不认识的键（cascade_* / append_to_* / suffix_override…），
    它们必须能原样载入、原样写回去——升级工具不该把同事的设置吃掉。"""
    return session.load_settings(SETTINGS_PATH)


def save_settings(d):
    """写设置。返回是否真落了盘。

    I-08 / C-246：`SETTINGS_PATH` 还是出厂默认值时，pytest 下 no-op；
    测试把它 patch 到 tmp 之后照常写（否则持久化这件事根本测不到）。
    `DREG_VERIFY_NO_PERSIST=1` 且路径仍是真机那份（= 隔离来自环境变量）→ 同样一个字节不写（P-22）。"""
    if SETTINGS_PATH == _SETTINGS_DEFAULT and no_persist_env():
        return False
    return session.save_settings(d, SETTINGS_PATH,
                                 skip_under_pytest=(SETTINGS_PATH == _SETTINGS_DEFAULT))


def patch_settings(patch):
    """读-改-写：只覆盖 patch 里的键，其余键（含不认识的旧键）原样保留。返回合并后的 dict。"""
    st = load_settings()
    st.update(patch or {})
    save_settings(st)
    return st


def list_columns_get():
    """清单列设置 `{列键: 可见}`（C-008 / C-046）。没存过 → 按 `contracts.LIST_DEFAULT_VISIBLE` 造。"""
    st = load_settings().get(contracts.SETTINGS_LIST_COLUMNS)
    if isinstance(st, dict):
        return {str(k): bool(v) for k, v in st.items()}
    vis = set(contracts.LIST_DEFAULT_VISIBLE)
    return {key: (col in vis) for col, key in contracts.LIST_COL_KEYS.items()}


def list_columns_set(mapping):
    """写清单列设置。"""
    return patch_settings({contracts.SETTINGS_LIST_COLUMNS:
                           {str(k): bool(v) for k, v in (mapping or {}).items()}})


#: 一条预设的定版结构（C-291；C2-int 主控裁决：**三键**，写的人读的人都按这一份）
PRESET_KEYS = ("scope", "checks", "filters")


def preset_spec(scope="", checks=None, filters=None):
    """一条预设的定版形状 `{scope, checks, filters}` —— `filter_bar` / `dialogs` / 本模块共用。

    · `checks=None` = 「这条预设没记勾选」（取回时不动勾选）；给了序列就是那一批名字。
    · `filters["owners"]` 收进来可能是 **set**（筛选行的口径），这里一律转成**排序后的列表**：
      settings 是 JSON，`json.dump` 遇到 set 会抛 —— 而 `session.save_settings` 是先开文件
      再序列化、抛了还把异常吞掉，结果是**整份 settings 被清空**（存一次预设，用户的全部偏好
      连同诊断配置一起没）。已用 mirror wl 端到端复现过：选中 5 个 owner 存预设 →
      settings.json 变成 `{}`。取回时 `filter_bar.set_filters` 本来就 `set(...)` 收，不受影响。
    · 旧的两键文件（`{checks, filters}`，没有 scope）照常读：缺的键补默认，不报错。
    """
    f = dict(filters or {})
    if "owners" in f:
        f["owners"] = sorted(str(x) for x in (f.get("owners") or ()))
    return {"scope": str(scope or ""),
            "checks": (None if checks is None else [str(x) for x in checks]),
            "filters": f}


def presets_get():
    """筛选/勾选预设 `{名字: {"scope", "checks", "filters"}}`（脏数据丢掉，不抛）。"""
    raw = load_settings().get(PRESETS_KEY)
    out = {}
    for name, spec in (raw if isinstance(raw, dict) else {}).items():
        if isinstance(spec, dict):
            out[str(name)] = preset_spec(spec.get("scope"), spec.get("checks"), spec.get("filters"))
    return out


def presets_set(presets):
    """整体替换预设段（逐条过 `preset_spec`，落盘的一律是定版三键 + 可 JSON 化的值）。"""
    got = {}
    for name, spec in (presets or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        got[str(name)] = preset_spec(spec.get("scope"), spec.get("checks"), spec.get("filters"))
    return patch_settings({PRESETS_KEY: got})


# ── N4 / N5 / 按路径分桶的诊断配置：薄包一层 session，统一把本模块的 load/save 注进去 ──
def push_recent(path, n_signals=None):
    """载表成功 → MRU 置顶（C-003 / C-228）。`last_excel` 由 session 一并照写。"""
    return session.push_recent_excel(path, n_signals=n_signals,
                                     load=load_settings, save=save_settings)


def recent_excels():
    """最近打开的表（新→旧，≤ `session.RECENT_MAX`）。"""
    return session.recent_excels(load=load_settings)


def update_recent_count(path, n):
    """清单分析完 → 给 MRU 条目补上信号数。"""
    return session.update_recent_count(path, n, load=load_settings, save=save_settings)


def last_export(kind):
    """该交付物上次导出到哪 → `{"path","ts"}`；没导过 → None（N5）。"""
    return session.last_export(kind, load=load_settings)


def record_last_export(kind, path):
    """导出成功 → 记下这一格（N5）。"""
    return session.record_last_export(kind, path, load=load_settings, save=save_settings)


def path_map_of(key, excel_path):
    """读【按 Excel 全路径分桶】的配置段（I-04 / C-233：探针前缀 / 强制 force / RTL 补充）。"""
    return session.path_map_of(key, excel_path, load=load_settings)


def save_path_map(key, excel_path, value):
    """写同上；value 为空 = 清掉这张表的这一段。"""
    return session.save_path_map(key, excel_path, value, load=load_settings, save=save_settings)


def load_export_options():
    """导出选项（I-03 / C-231/C-232：四个 `export_*` 键统一默认值走 `exports`，不另写一套）。"""
    return X.load_export_options(load_settings())


def store_export_options(opt):
    """写回导出选项。"""
    return patch_settings(X.store_export_options({}, opt or {}))


# ═════════════════════ ② edits 文件（劳动成果）═════════════════════
#: R2-07：上一次读 edits 文件时发现【内容坏了】并另存出来的备份路径（`take_corrupt_backup`
#: 取走就清空）。放模块级而不改 `load_edits_all` 的签名：它有五个调用点，而「坏文件」
#: 一张表只发生一次（原文件已被改名，下一次读就是干净的空文件了）。
_corrupt_backup = ""


def load_edits_all():
    """整份 edits 文件 `{Excel 全路径: 桶}`（每次载表都跑、绝不能崩）。

    R2-07：内容坏掉的文件会被 `edits.load_edits_file` 改名另存，备份路径记在
    `_corrupt_backup` 里等 `take_corrupt_backup()` 取走点名（此前是静默 `{}`）。"""
    def _on_corrupt(bak):
        global _corrupt_backup
        _corrupt_backup = str(bak or "")
    return ED.load_edits_file(EDITS_PATH, on_corrupt=_on_corrupt)


def take_corrupt_backup():
    """取走并清掉「上一次读到坏文件」的备份路径（`state.load` 拿去报给用户）。没有 → `""`。"""
    global _corrupt_backup
    bak, _corrupt_backup = _corrupt_backup, ""
    return bak


def save_edits_all(d):
    """整份写回。I-08 / C-246 的 no-op 规则与 settings 同（由 `edits.save_edits_file` 判）；
    `DREG_VERIFY_NO_PERSIST=1` 且路径仍是真机那份时同样不写（P-22）。

    R2-07：文件**在**、却还读不出来（占用 / 权限 / 备份也改名失败）时**不写**。
    整份覆盖以一份空 `{}` 为基底，写下去就是把别的表的桶与 legacy 九段一起销毁。"""
    if EDITS_PATH == _EDITS_DEFAULT and no_persist_env():
        return False
    if ED.edits_file_unreadable(EDITS_PATH):
        return False
    return ED.save_edits_file(EDITS_PATH, d, _EDITS_DEFAULT)


def load_edits_bucket(excel_path):
    """这张表的桶（不存在 → `{}`）。legacy 段与 v2 段都在里面，各读各的（A3 §3.2 C）。

    R2-05：同一个文件被写成 `C:\\x\\t.xlsx` / `C:/x/t.xlsx` / 相对路径 / 大小写不同时
    分成了两个桶 —— 用户从「最近打开」进来看到手填期望都在，自己敲一遍路径进来就「全没了」。
    这里按 `session.norm_path_key` 把同指一个文件的几个桶**合并读出**（规则见
    `session.merge_buckets`：dict 逐键往下合、条目多的赢、一样多取靠后的）。"""
    b = session.merge_path_buckets(load_edits_all(), excel_path)
    return dict(b) if isinstance(b, dict) else {}


def write_edits_bucket(excel_path, patch):
    """**桶合并写**（I-05 / C-300 / R2-04 / R2-06）——v2 写 edits 文件的唯一出口。
    返回是否真落了盘。

    为什么不能整桶重建：一个桶里同时住着
      · legacy 九段（『排查(旧)』门面的劳动成果，C-235 规定 v2 不读不删）；
      · `view_edits` / `view_checks` 里**别的 view_id** 的子桶（同事可能只在 logic 视图里干活）。
    v2 只认识自己那几格，整桶重建 = 把上面两类静默抹掉。手填期望没了、而且没有任何报错，
    这类丢失要等到下次导出时才被发现——所以写入一律是「先读旧桶，再只覆盖自己那几格」。

    **为什么 `view_edits` 还要再细到逐信号**（R2-04 / R2-06）：同一个 view_id 的子桶里
    还混着两类本会话**根本不该动**的信号——
      · 这一趟恢复不出来的（引擎瞬时异常 / 名字暂时不在清单 / `editable == ""`）：
        整段替换之后，用户接下来随便改一格，这些信号存在文件里的手填期望就被永久抹掉；
      · 另一个窗口开着同一张表刚存进去的：后写的整段盖掉先写的，零提示。
    所以这两段（`PER_SIGNAL_SEGMENTS`）按**信号名**逐条合并，删要显式走 `drop`。

    patch 形状（三段都可选，按 **view_id 逐格**）：
        {"view_edits":    {view_id: {信号名low: 序列化记录 或 None（删这一条）}}  ← 逐信号合并
         "view_checks":   {view_id: [勾选名] 或 None（None = 全勾 = 默认态）},
         "view_mux_data": {view_id: {信号名low: {基名low: int} 或 None}}}        ← 逐信号合并

    **patch 里没提到的 view_id、以及逐信号段里没提到的信号，一个字节都不动。**
    段空了就把段删掉；整个桶空了就把桶删掉（与 v1 `_persist_edits` 同语义）。

    ⚠ 三种「空」语义各不相同，别混：
      · 逐信号段的 `{}` = 「这一趟没有要写的信号」= **什么都不做**（此前是「删掉这一格」，
        那正是 R2-04 的洞：恢复了 0 个信号就把整段清了）；**某个信号写成 `None`** 才是
        「把文件里那一条删掉」；整个 view_id 写成 `None` 才是「整格删掉」。
      · `view_checks` 的 `[]` 是「一个都没勾」——用户真实的选择，得照写；当成空丢掉的话
        下次开工具会变回全勾，他一导出就是整表都出来了。`None` 才是「全勾 = 默认态」（C-243）。
    """
    allb = load_edits_all()
    # R2-05：同一个文件的几种拼法先合成一个桶，写回**盘上那个拼法**（`canonical_path_key`），
    # 其余同指一个文件的旧键删掉。刻意不把键改写成归一形式：那会让『排查(旧)』门面按它
    # 自己的拼法 `.get(path)` 时找不到桶（C-235）。
    dup = session.path_bucket_keys(allb, excel_path)
    key = session.canonical_path_key(allb, excel_path)
    bucket = session.merge_path_buckets(allb, excel_path)
    bucket = dict(bucket) if isinstance(bucket, dict) else {}
    for k in dup:
        allb.pop(k, None)
    excel_path = key
    for seg in V2_SEGMENTS:
        if seg not in (patch or {}):
            continue                                  # 没提这一段 → 一个字节都不碰
        cur = dict(bucket.get(seg) or {})
        per_sig = seg in PER_SIGNAL_SEGMENTS
        for vid, val in (patch[seg] or {}).items():
            vid = str(vid)
            if val is None:                           # 显式整格删（两种段同义）
                cur.pop(vid, None)
            elif not per_sig:
                if isinstance(val, dict) and not val:
                    cur.pop(vid, None)                # 默认态 / 没有编辑 → 不留空壳
                else:
                    cur[vid] = val
            elif isinstance(val, dict) and val:
                sub = dict(cur.get(vid) or {})
                for k, v in val.items():
                    if v is None:
                        sub.pop(str(k), None)         # 用户删掉的那一条 → 文件里也删
                    else:
                        sub[str(k)] = v
                if sub:
                    cur[vid] = sub
                else:
                    cur.pop(vid, None)                # 一个信号都不剩 → 不留空壳
        if cur:
            bucket[seg] = cur
        else:
            bucket.pop(seg, None)
    if bucket:
        allb[excel_path] = bucket
    else:
        allb.pop(excel_path, None)
    return save_edits_all(allb)


def load_legacy_bucket(excel_path):
    """这张表桶里的 legacy 九段（**只读**，给诊断抽屉的「旧版编辑迁移」用，C-302）。

    C-235 的「不读不删」说的是**自动**恢复流程不碰它；显式迁移动作当然要读——
    读完也不删（迁移失败/回退旧版本时同事的活还在）。"""
    b = load_edits_bucket(excel_path)
    return {k: b[k] for k in LEGACY_SEGMENTS if k in b}


def legacy_bucket_counts(excel_path, kind_of=None):
    """legacy 段的条数统计，给诊断抽屉的 `DiagnosticsSnapshot.legacy_counts` 用（C-302）。

    → `{"edits","logic","register","mux","checks"}`。

    ⚠ `kind_of` 的存在理由：legacy `edits` 段按信号名键、**段里没有 kind 字段**，
    单看文件分不出 logic 根还是 register 根——那要 `topout.resolve_root` 查当前表才知道。
    不给 `kind_of` 时全部记在 `logic` 格（并非「这些都是 logic」，而是「还没分类」）；
    调用方有 wb 时传 `kind_of(name) -> "logic"/"register"/None` 就能拿到真的两格。"""
    b = load_edits_bucket(excel_path)
    names = list((b.get("edits") or {}).keys())
    n_logic, n_reg = len(names), 0
    if kind_of is not None:
        n_logic = n_reg = 0
        for nm in names:
            n_reg += 1 if kind_of(nm) == "register" else 0
            n_logic += 0 if kind_of(nm) == "register" else 1
    mux_names = set()
    for seg in ("mux_expected", "mux_data", "mux_dropped", "mux_user_vecs"):
        mux_names |= set((b.get(seg) or {}).keys())
    for seg in ("mux_neg", "mux_cleared"):
        mux_names |= set(b.get(seg) or ())
    return {"edits": len(names), "logic": n_logic, "register": n_reg,
            "mux": len(mux_names), "checks": len(b.get("signals_checked") or ())}
