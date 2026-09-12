# GUI v2 删除安全 + 持久化键值审计（2026-09-12，Phase A3）

> 承 `docs/GUI_v2_执行计划_20260912.md` Phase A3、`docs/推进方案_GUI_v2_20260912.md` §4.3/§4.4（删除清单）与 §7 护栏「旧 CLI + generator 逐字节不变」。
> **只读审计**：本轮没有改任何 `.py`，没有 commit / push。
> 基线：main `169eb40`。实证解释器 `.venv/Scripts/python.exe`（Python 3.13.7 + PySide6 6.11.1）；GUI 实证一律 `QT_QPA_PLATFORM=offscreen`，持久化路径全部重定向到 scratchpad（**没有碰用户真机的 `~/.dreg_verify_gui.json` / `~/.dreg_verify_edits.json`**）。
> 公开仓：以下所有信号名均为 `make_mirror_btlp.py` / `make_mirror_excel.py` 造出来的 **mirror 名**。

---

## 第一部分 · 删除安全

### 0. 总表（一眼看完）

| # | 开关 | GUI 入口（gui.py 方法/行） | settings 键 | 后端读取点 | 旧 CLI 默认路径依赖？ | Topout / 子视图路径读？ | 结论 |
|---|---|---|---|---|---|---|---|
| 1 | 仅 top_output=1 筛 | `MainWindow._build` 建 `self.top_only`（2528）→ `apply_filter`（3708/3745） | **无**（不存盘，每次启动默认不勾） | 纯 GUI 行隐藏，从不进 `GenOptions`；后端同名字段 `GenOptions.top_output_only`（generator:49/615/627）由 CLI 独立喂 | **依赖**：`cli.py:1366 top_output_only=not args.include_internal` → 默认 **True**（`--include-internal` 才翻） | **不读**（topout/pageviews 从不传，恒用 `GenOptions` 默认 `False`） | **GUI 入口可删，后端参数必须保留**（CLI 默认就靠它） |
| 2 | logic 加尾缀 / mux 加尾缀 | `append_to_logic_chk`（2725）/ `append_to_mux_chk`（2738）；handler 3295/3310；读取 `_append_to_logic_on()` 3282、`_append_to_mux_on()` 3306 | `append_to_logic`(bool) / `append_to_mux`(bool) | `Resolver.__init__`（resolver:96/132-147 → 回填 `sig._append_to_logic`）→ `excel_model.LogicSignal.rtl_base`(81) / `MuxGroup.rtl_base`(275)；`generator.GenOptions`(27/29/109/110) 转发到 resolver(1168/1741/2210) | **依赖**：`cli.py:139 --no-ref-suffix` / `145 --mux-ref-suffix` → `cli:1389/1390`。默认 True/False | **不读**（provider 只传 `probe_prefixes`/`force_overrides`；实测 Topout 路径 Resolver 恒 `append_to_logic=True, append_to_mux=False`） | **GUI 入口可删，后端参数必须保留**（Hi1108 类设计靠 CLI 旗标） |
| 3 | 本信号探尾缀网 | `suffix_chk`（2894）；`on_suffix_changed`(3142)、`_set_suffix_chk`(3126)、`_save_suffix_override`(3160) | `suffix_override`：`{excel全路径: {信号名低: bool}}` | `Resolver.__init__` 的 `suffix_override`（resolver:96/136-147 的 `_atl`）；`GenOptions.suffix_override`(29/114) 转发(1170/1743/2211) | **依赖**：`cli.py:149 --no-suffix-signals` / `152 --suffix-signals` → `cli:1391` 合成 dict | **不读**（实测 Topout 路径 Resolver 恒 `suffix_override={}`） | **GUI 入口可删，后端参数必须保留** |
| 4 | 级联模式 logic/mux/本信号 三处下拉 | `cascade_logic_combo`(2702)、`cascade_mux_combo`(2708)、`sig_cascade_combo`(2911)；`_logic_cascade`(3208)/`_mux_cascade`(3214)/`_cascade_for`(3220)/`on_cascade_mode_changed`(3228)/`on_sig_cascade_changed`(3263) | `cascade_logic` / `cascade_mux`（旧键 `cascade_mode` 只读回退） | `Resolver.cascade_mode`(resolver:96/119/299)、`cone.py:63/145`、`mux_gen.py:350/462`；`GenOptions.cascade_mode/logic_cascade/mux_cascade`(26/81-83/189-194) + `generator` 逐信号 `resolver.cascade_mode = opts.cascade_for(...)`(1191/1335/1747/1896/2092/2235) | **依赖**：`cli.py:156/161/163 --cascade-mode/--logic-cascade/--mux-cascade` → `cli:1387` | Topout **不读**（恒 `cone`）；**页本地视图写死 `force`**（`pageviews.py:61` `_page_resolver(cascade_mode="force")` + `pageviews.py:298` `logic_cascade="force", mux_cascade="force"`）——「不跨页」就是靠它实现的 | **GUI 三处下拉可删，后端参数必须保留**（页本地视图的「范围」能力就是 `cascade_mode=force`，删了参数=子视图失能） |
| 5 | 缺前缀强制生成 | `include_risky_chk`(2751)；`_include_risky_on()`(3321)、`on_include_risky_changed`(3325) | `include_risky`(bool) | `GenOptions.include_risky`(23/53) → generator 1215/1338/1352/1479/1911/1913/2178 | **依赖**：`cli.py:132 --include-risky`（默认 False）+ `135 --match-fortest`（`cli:1350` 强制置 True） | **写死 True**：`topout.py:989`（report_for_topout）、`topout.py:1342`（build_for_topout）、`pageviews.py:295`（`_page_gen_opts`）。GUI 勾不勾对 Topout/子视图**完全无效** | **不能删**：①后端参数是 CLI 默认路径的承重墙；②按 §4.3 这条要**从写死 True 升级成诊断抽屉里的可见开关**——但 ⚠ 实测「关掉」会改 .sv 字节（见 1.5），所以新开关的默认值**必须仍是 True** |
| 6 | R / K / top / type 列头 | `HEADERS`（gui.py:207）→ `MainWindow._build` 2541-2542 | 无 | 无 | 无 | 无 | **后端也可删**（纯显示串；实测改名后全功能正常，见 1.6）。它随『排查(旧)』整体消失，新视图已用 `TOPO_HEADERS`(213) 的工程师语言列名 |
| 保留① | `force_overrides`（强制 force 信号） | `on_set_force_signals`(4178)、`_save_force_signals`(4166) | `force_signals`：`{excel全路径: [基名]}` | `Resolver.force_overrides`(resolver:114/223/376)、`GenOptions.force_overrides`(generator:21/46/1162/1735/2203) | 依赖 `cli.py:126 --force-signals` | **读**（轨 0-⑥ 已接通，见 1.7） | **保留，改归属**：入口挪进「诊断」抽屉；后端**一个参数都不用加**（链路已全通） |
| 保留② | `probe_prefixes`（探针前缀映射） | `on_set_probe_prefix`(3955)、`_save_probe_prefixes`(3943) | `probe_prefixes`：`{excel全路径: {信号名低: 层级路径}}` | `Resolver.wire_prefixes`(resolver:118/361)、`GenOptions.probe_prefixes`(generator:24/60/839)、`topout._probe_prefix_for_name`(1215)、`pageviews._prefix_for`(264) | 依赖 `cli.py:166/170 --probe-prefix / --probe-prefix-file` | **读**（全路径贯通，见 1.8） | **保留，改归属**：入口挪进「诊断」抽屉；后端链路已全通 |

**总判据（实证 [7]）**：`cli.py / generator.py / topout.py / pageviews.py / resolver.py / rtl_scan.py / mux_gen.py / cone.py` **没有任何一处 import gui**——单向依赖，删 GUI 控件在编译期/运行期都到不了 CLI。`redzone_tools/*.py` 对这 6 组开关**零引用**（只在文档串里提到 `probe_prefixes.txt` 文件名）。

---

### 1. 逐条实证

所有脚本在 `scratchpad/a3/`（不入库）：`probe_flags.py` / `sv_sensitivity.py` / `suffix_effect.py` / `risky_effect.py` / `dump_persist.py` / `dump_legacy_bucket.py` / `compare_keys.py` / `delete_sim.py` / `header_rename.py` / `file_formats.py`。

#### 1.0 公共实证：CLI 默认值 + 后端形参表 + 运行时实参录音

`probe_flags.py`（`.venv/Scripts/python.exe`）：

```
[1] argparse 默认值（旧 CLI 不带任何旗标）
  args.include_internal     = False      args.append_to_logic      = True
  args.top_output_only      = False      args.append_to_mux        = False
  args.cascade_mode         = 'cone'     args.no_suffix_signals    = None
  args.logic_cascade        = None       args.suffix_signals       = None
  args.mux_cascade          = None       args.include_risky        = False
  args.force_signals        = None       args.probe_prefix         = []
  args.topout               = False      args.page                 = None

[2] 旧 CLI 默认路径实际构造出的 GenOptions（复刻 cli.main 的构造块）
  opts.top_output_only    = True         opts.append_to_logic    = True
  opts.append_to_mux      = False        opts.suffix_override    = {}
  opts.cascade_mode       = 'cone'       opts.include_risky      = False
  opts.force_overrides    = []           opts.probe_prefixes     = {}

[3] GenOptions 类默认值（不传任何参数）—— GUI 入口删掉后后端参数仍在
  GenOptions.__init__ 形参个数 = 46
    形参 top_output_only    默认=False       形参 append_to_logic    默认=True
    形参 append_to_mux      默认=False       形参 suffix_override    默认=None
    形参 cascade_mode       默认='cone'      形参 include_risky      默认=False

[4] Resolver.__init__ 默认值
  cascade_mode='cone'  append_to_logic=True  append_to_mux=False  suffix_override=None
  wire_prefixes=None   force_overrides=None
```

**读法**：CLI 的默认值**全部来自 argparse + cli.main 的构造块**，与 GUI 无关；GUI 删控件后这两张表一个字节都不会变。

`[5]` 逐函数形参表（节选）——Topout / pageviews 的**每一个对外入口**都没有这 6 个形参：

```
topout.render_topout_sv     形参=['wb','mode','max_tests','exhaustive','comments','sv_summary',
                                 'owner_in_msg','only','edit_overrides','probe_prefixes','scope',
                                 'sig_cov','logic_overrides','neg_all','neg_signals','neg_which',
                                 'neg_mode','neg_value','form_cov','force_overrides']
    → 待删开关出现在形参里的: 无
topout.topout_view_models   → 待删开关出现在形参里的: 无
topout.topout_report        → 待删开关出现在形参里的: 无
pageviews.page_view_models  → 待删开关出现在形参里的: 无
pageviews.build_page_sv     → 待删开关出现在形参里的: 无
pageviews._page_resolver    形参=['wb','probe_prefixes','force_overrides'] → 无
```

`[6]` 运行时把 `Resolver.__init__` / `GenOptions.__init__` 打桩录音，跑完 `render_topout_sv + topout_view_models + topout_report`（mirror WL）：

```
--- Topout 路径 --- （Resolver 被调 10 次）
   Resolver kwarg append_to_logic  取值集合 = ['<未传>', True]
   Resolver kwarg append_to_mux    取值集合 = ['<未传>', False]
   Resolver kwarg suffix_override  取值集合 = ['<未传>', {}]
   Resolver kwarg cascade_mode     取值集合 = ['<未传>', 'cone']
   GenOptions kwarg top_output_only 取值集合 = [None]      ← 从不传
   GenOptions kwarg include_risky   取值集合 = [True]      ← 写死
--- 页本地(子视图)路径 --- （Resolver 被调 6 次）
   Resolver kwarg cascade_mode     取值集合 = ['cone', 'force']   ← 'force' = pageviews 写死
   GenOptions kwarg logic_cascade  取值集合 = ['force']
   GenOptions kwarg mux_cascade    取值集合 = ['force']
   GenOptions kwarg include_risky  取值集合 = [True]
```

**结论**：这 5 个开关（不含列头）在 Topout/子视图路径上取值恒为默认或写死值，GUI 上的控件对它们**本来就不生效**——用户拍板「删」与现实一致。

---

#### 1.1 仅 top_output=1 筛（`top_output_only`）

- **GUI 里在哪设**：`gui.py:2528` `self.top_only = QCheckBox("仅 top_output=1")`（默认不勾），信号接 `apply_filter`（2529）。唯一消费点 `apply_filter` 的 `gui.py:3708` 读、`gui.py:3745` 用 `setRowHidden` 隐藏行。**从不进入任何 GenOptions**；GUI 生成路径反而在 `gui.py:7102` 明写 `top_output_only=False,   # GUI 已按表勾选，不再二次过滤`。
- **settings 键**：**没有**（不存盘）。
- **后端在哪读**：`generator.py:49` `self.top_output_only`；`generator.py:615` `if opts.top_output_only and not is_top_output(sig.top_output): …`（过滤）；`generator.py:622-627` `filtered_internal` 记账（被静默过滤掉的 logic 内部节点在账目里列名字+原因）。`topout.py:848 / 1670` 的注释明写「**不套** `top_output_only` 过滤（真表 N/I 全 0 → 旧过滤会选 0 个）」。
- **旧 CLI 默认路径**：**依赖**。`cli.py:1366` `top_output_only=not args.include_internal`，`--include-internal` 默认 False ⇒ **默认 True**。
- **Topout 路径**：**不读**（录音 [6]：`GenOptions kwarg top_output_only 取值集合 = [None]`）。
- **实证（`sv_sensitivity.py` [A]）**——在旧 CLI 默认路径上翻转它，字节会变，所以后端参数是承重墙：

```
  --- wl ---
    baseline（旧 CLI 默认）                          sha=656ef194 块=  7
    top_output_only=False（= --include-internal）  sha=20a134d1 块= 11  ≠baseline ★字节变了
  --- btlp ---
    baseline（旧 CLI 默认）                          sha=a0808517 块=  1
    top_output_only=False（= --include-internal）  sha=e7d287c7 块=  9  ≠baseline ★字节变了
```

- **结论**：**GUI 入口可删，后端参数保留。** 删的时候要连 `gui.py:3708` 与 `3745` 两处一起删（见 §2 的坑①）。
- 附带的能力损失：`top_only` 勾上时「全选输出(可见)」只勾可见行。v2 的正则搜索 + 排序已覆盖这个用法（执行计划 §5 待拍板 4 也把 type 筛并进正则搜索）。

---

#### 1.2 logic 加尾缀 / mux 加尾缀（`append_to_logic` / `append_to_mux`）

- **GUI 里在哪设**：`append_to_logic_chk`（`gui.py:2725`，默认勾）、`append_to_mux_chk`（`2738`，默认不勾）。handler `on_append_to_logic_changed`(3295)/`on_append_to_mux_changed`(3310)，读值 `_append_to_logic_on()`(3282)/`_append_to_mux_on()`(3306)（**都有 `hasattr` 兜底**，返回 True/False）。消费点：`gui.py:3484/4412/5585/7122-7123` 建 Resolver / GenOptions。
- **settings 键**：`append_to_logic`(bool, 缺省 True) / `append_to_mux`(bool, 缺省 False)；写在 3299/3314、5477-5478；读在 2734/2747、5456-5466。
- **「三处默认 True」的全部落点**（实际是 8 处，v2 只需保证最后 3 处不变）：

| 位置 | 内容 |
|---|---|
| `generator.py:27` | `GenOptions(append_to_logic=True, …)` 形参默认 |
| `generator.py:106-110` | 注释 + `self.append_to_logic = bool(append_to_logic)` |
| `resolver.py:96/132` | `Resolver(append_to_logic=True)` 形参默认 |
| `excel_model.py:50` | `LogicSignal._append_to_logic = True`（未建 Resolver 时的兜底态） |
| `excel_model.py:81` | `rtl_base` 里 `getattr(self, "_append_to_logic", True)` |
| `gui.py:2726` | 控件 `setChecked(True)`（生产/pytest 基线） |
| `gui.py:2734` | `_load_settings().get("append_to_logic", True)` |
| `gui.py:3284` | `_append_to_logic_on()` 的 `hasattr` 兜底 `True` |
| `gui.py:5459` | 导入配置缺键时 `atl = atl if isinstance(atl,bool) else True` |

`append_to_mux` 对应的默认 False 在 `generator.py:29/110`、`resolver.py:96/133`、`excel_model.py:226`（`MuxGroup._append_to_logic = False`）、`excel_model.py:275`、`gui.py:2739/2747/3308/5464`。

- **后端在哪读**：`Resolver.__init__` 的 `_atl()`（`resolver.py:143-147`）把开关回填成每个信号的 `_append_to_logic`，之后 `excel_model.LogicSignal.rtl_base`(81) / `MuxGroup.rtl_base`(275) 据此决定探针网名补不补 `_to_logic` / `_to_mux`。`rtl_scan.py:58/217/304` **强制置 True**（找 RTL 真名，不经此开关）。
- **旧 CLI 默认路径**：**依赖**。`cli.py:139 --no-ref-suffix/--no-to-logic-suffix`（`dest=append_to_logic, store_false`）、`cli.py:145 --mux-ref-suffix`（`store_true`），`cli.py:1389/1390` 传进 GenOptions。
- **Topout 路径**：**不读**（录音 [6]）。
- **实证（`suffix_effect.py`）**——开关确实改探针网名，只是 mirror 表上这些信号是 top_output=0 内部节点、旧 CLI 默认已被 `top_output_only` 滤掉，所以 .sv sha 不变；**影响面真实存在，参数不能删**：

```
wl  带 ref_suffix 的 logic 行: 4 ；带 ref_suffix 的 mux 组: 8
  atl=True  atm=False  logic rtl_base={'d_wl_rf_linectrl_band_sel': 'd_wl_rf_linectrl_band_sel_to_mux', …}
                       mux   rtl_base={'d_wl_rf_temp_code': 'd_wl_rf_temp_code', …}
  atl=False atm=False  logic rtl_base={'d_wl_rf_linectrl_band_sel': 'd_wl_rf_linectrl_band_sel', …}
  atl=True  atm=True   mux   rtl_base={'d_wl_rf_temp_code': 'd_wl_rf_temp_code_to_mux',
                                       'd_wl_rf_freq_sel': 'd_wl_rf_freq_sel_to_logic', …}
```

`sv_sensitivity.py [A]` 上 `append_to_mux=True` 在 WL 直接改字节：`sha=656ef194 → f63a7fb7 ★字节变了`。

- **结论**：**GUI 入口可删，后端参数保留。** ⚠ 删控件时 `_collect_config`（5413-5414）与 `_apply_global_settings`（5460-5466/5477-5478）**没有 hasattr 兜底**，必须同步处理（坑②）。

---

#### 1.3 本信号探尾缀网（`suffix_override`）

- **GUI 里在哪设**：`suffix_chk`（`gui.py:2894`）；`_set_suffix_chk`(3127，有 hasattr 兜底) / `on_suffix_changed`(3142) / `_save_suffix_override`(3160)。语义 = 单点覆盖，**等于类型默认就删掉该条**（只留「偏离项」）。
- **settings 键**：`suffix_override` = `{excel 全路径: {信号名(小写): bool}}`。实测样例：`{"C:\\code\\Dreg_verify\\mirror_btlp_dreg.xlsx": {"clk_force_on": false}}`。
- **后端在哪读**：`resolver.py:136-147`——`ov` 先按 `out_name.lower()` 再按 `out_base.lower()` 匹配，命中就压过 `append_to_logic/append_to_mux` 的类型默认；`generator.py:114-115` 归一化 + `1170/1743/2211` 转发给 Resolver。
- **旧 CLI 默认路径**：**依赖**。`cli.py:149 --no-suffix-signals`（单点探裸名）、`cli.py:152 --suffix-signals`（单点探尾缀网），在 `cli.py:1391-1392` 合成 `{名: False} ∪ {名: True}`。
- **Topout 路径**：**不读**（录音 [6]：`suffix_override` 恒 `{}`）。
- **实证**：`suffix_effect.py` 末行——`单点 suffix_override={d_wl_rf_linectrl_band_sel:False} → rtl_base=d_wl_rf_linectrl_band_sel`（不带 `_to_mux`），证明后端参数活着且有效。
- **结论**：**GUI 入口可删，后端参数保留。** settings 里的 `suffix_override` 键按「随入口删，但载入时忽略不报错」处理。

---

#### 1.4 级联模式 logic / mux / 本信号 三处下拉（`cascade_mode`）

- **GUI 里在哪设**：三个下拉
  - `cascade_logic_combo`（`gui.py:2702`，两项「展开上游(推荐)/force级联网」）
  - `cascade_mux_combo`（`gui.py:2708`）
  - `sig_cascade_combo`（`gui.py:2911`，「跟随全局/展开上游/force级联网」，**会话内临时档不存盘**）
  - 读值 `_logic_cascade()`(3208)/`_mux_cascade()`(3214)（有 hasattr 兜底 → `"cone"`）/`_cascade_for(sig)`(3220)；handler `on_cascade_mode_changed`(3228)/`on_sig_cascade_changed`(3263)；逐信号写进 `self._resolver.cascade_mode`（4392/4697/4776）。
- **settings 键**：`cascade_logic` / `cascade_mux`（str，`"cone"`/`"force"`）。**旧键 `cascade_mode` 只读不写**（`gui.py:2705/2714/5449` 作缺键回退）。
  > 注：任务书里写的 `cascade_overrides` **在代码里不存在**——单点档是内存字段 `self._sig_cascade`（不存盘），批量档在 `GenOptions` 里叫 `logic_cascade` / `mux_cascade` / `sig_cascade`。
- **后端在哪读**：
  - `resolver.py:119` `self.cascade_mode`；`resolver.py:299` `if self.cascade_mode == "cone"` 决定输入引用上游网时展开还是 force
  - `cone.py:63 / 145`：`getattr(resolver,"cascade_mode","cone") == "cone"` 决定 mux 输出算不算可展开
  - `mux_gen.py:350`：`if resolver.cascade_mode == "force"`；`mux_gen.py:462` 注释
  - `generator.py:81-83/189-194` `cascade_for()`；逐信号赋值 `generator.py:1191/1335/1747/1896/2092/2235`
- **旧 CLI 默认路径**：**依赖**。`cli.py:156 --cascade-mode`（默认 `cone`）、`161 --logic-cascade`、`163 --mux-cascade`；`cli.py:1387-1388`。
- **Topout 路径**：**不读**（`topout.py:1313` `R.Resolver(wb, wire_prefixes=…, force_overrides=…)` → `cascade_mode` 走默认 `cone`；`topout.py:1298` 注释明写「cone 默认级联…逃生阀属『排查(旧)』」）。
- **⚠ 页本地子视图路径：写死 `force`**，而且这是子视图的立身之本：
  - `pageviews.py:61-62` `R.Resolver(wb, cascade_mode="force", …)`
  - `pageviews.py:298` `logic_cascade="force", mux_cascade="force"`
  - `pageviews.py:57` 注释：「页本地的『不跨页』靠 `cascade_mode=force` 实现」
- **实证（`sv_sensitivity.py [A]`）**——旧 CLI 路径上翻成 force 直接改字节，且块数腰斩（需要前缀的信号被跳过）：

```
  --- wl ---
    cascade_mode='force'（= --cascade-mode force）  sha=ec3cb12d 块=  3  ≠baseline ★字节变了  (baseline 块=7)
```

- **结论**：**GUI 三处下拉可删，后端参数必须保留**——`cascade_mode` 不但是旧 CLI 的旗标，还是 `pageviews` 实现「页本地不跨页」的机制。v2 的「范围切换（全链 / logic / mux / dft / iddq）」就是它，只是不再以「级联模式」的术语暴露给用户。

---

#### 1.5 缺前缀强制生成（`include_risky`）

- **GUI 里在哪设**：`include_risky_chk`（`gui.py:2751`）；`_include_risky_on()`(3321，有 hasattr 兜底 → False)、`on_include_risky_changed`(3325)。生产默认**勾**（2761：`_load_settings().get("include_risky", True)`），**pytest 下不勾**（保持 LPBT 基线）。消费点：`gui.py:3620`（状态列显示「已强制生成」）、`4399`（mux 组分析）、`7128`（生成 .sv 的 GenOptions）。
- **settings 键**：`include_risky`(bool)，生产缺省 True。
- **后端在哪读**：`generator.py:53` `self.include_risky`；判定点 `generator.py:1215`（override 为空且不放行 → 跳过）、`1338`/`1352`（mux 风险/blocker）、`1479`（放行时块顶留 ⚠）、`1911/1913`（report 侧状态）、`2178`。`generator.py:1362` 明确「确定验证无效」这一类 **`include_risky` 也不放行**。
- **旧 CLI 默认路径**：**依赖**。`cli.py:132 --include-risky`（默认 False = 跳过 risky，与 VBA 一致）；`cli.py:135 --match-fortest` 在 `cli.py:1350` 把它强制置 True。
- **Topout / 子视图路径：写死 True，GUI 开关完全不生效**：
  - `topout.py:989-990`（`report_for_topout` 里 `G.report(… include_risky=True …)`）
  - `topout.py:1342`（`build_for_topout` 的 `G.GenOptions(… include_risky=True …)`）
  - `pageviews.py:295`（`_page_gen_opts` 的 `include_risky=True`）
- **实证（`risky_effect.py`）**——把写死的 True 改成 False，**Topout .sv 字节会变**，页本地 dft 甚至全空（`01ba4719` = sha256("\n")）：

```
wl    Topout  include_risky=True(现状) sha=54c06776 块=9 | =False sha=7e378f1c 块=9  ★不同
      页本地 logic  True sha=195bb1e5 | False sha=b836adc0  ★不同
      页本地 mux    True sha=7eb2a7f9 | False sha=ec3cb12d  ★不同
      页本地 dft    True sha=62b1de91 | False sha=01ba4719  ★不同   ← 整页空掉
btlp  Topout  include_risky=True(现状) sha=61a3cc83 块=12 | =False sha=61a3cc83 块=12  相同
      页本地 dft    True sha=a5a272d2 | False sha=01ba4719  ★不同
```

- **结论**：**不能删**（后端参数是 CLI 默认路径的承重墙）。按推进方案 §4.3，v2 把它做成「诊断」抽屉里的**可见开关**替代写死 True；护栏是：
  1. 给 `topout.render_topout_sv/topout_report` 与 `pageviews.build_page_sv/page_report/page_fortest` 加 `include_risky=True` 形参（**默认必须是 True**），provider 再接上去；
  2. 加完形参、不改默认值 → byte-gate 6 sha 必须不动（当前值见 §2）；
  3. GUI 上把它关掉会改产物，属于用户显式动作，界面要按 gui-feedback 原则**点名列出被跳过的信号 + 原因**（`_skipped_detail_text`，gui.py:191 已有这段文案可复用）。

---

#### 1.6 R / K / top / type 列头（纯显示）

- **在哪定义**：`gui.py:207`
  `HEADERS = ["选", "负向", "R", "输出名(K)", "owner", "type", "top", "状态", "探针前缀", "表达式"]`
  索引常量在 `gui.py:205-206`：`(COL_SEL, COL_NEG, COL_R, COL_K, COL_OWNER, COL_TYPE, COL_TOP, COL_STATUS, COL_PREFIX, COL_EXPR) = range(10)`。
- **谁用它**：只有 `gui.py:2541`（`QTableWidget(0, len(HEADERS))`）和 `2542`（`setHorizontalHeaderLabels(HEADERS)`）。
- **有没有代码按列名取数据**：**没有**。全仓 `HEADERS.index` / `HEADERS[` 的命中数 = 0；`horizontalHeaderItem()` 只出现 2 处，都是**按整数列号**设 tooltip（`gui.py:3691` 信号表、`gui.py:6103` 真值表列头）。筛选也不读单元格文字：`apply_filter` 的 type 筛走的是 `sig.suffix`（`gui.py:3718` `if typ != "全部 type" and sig.suffix != typ`），行→信号走 `_idx_of_row`（`gui.py:3754`，读 `COL_R` 单元格的 `Qt.UserRole` 数据而不是文字）。
- **实证（`header_rename.py`）**——运行时把四个列头改成工程师语言，GUI 全功能正常：

```
改名前 HEADERS = ['选','负向','R','输出名(K)','owner','type','top','状态','探针前缀','表达式']
改名后 HEADERS = ['选','反例','断言号','信号','owner','去向','顶层口','状态','探针前缀','表达式']
COL_* 常量（整数索引，不随名字变）: SEL=0 NEG=1 R=2 K=3 OWNER=4 TYPE=5 TOP=6 STATUS=7 PREFIX=8 EXPR=9
表头实际显示 = ['选','反例','断言号','信号','owner','去向','顶层口','状态','探针前缀','表达式']
行数 = 9      _sig_of_row(0) = d_logic_bt_lp_rx_en
type 筛选(最后一项='to_mux,to_dft')后可见行 = 1
真值表可载入（行数）= 4
Topout .sv 仍可渲染，长度 = 32600
```

- **结论**：**后端也可删**（本来就没有后端）。新视图已经用 `TOPO_HEADERS`（`gui.py:213`）：`["选","负向","信号","断言号","owner","分类","逻辑类型","状态","探针前缀","用例"]`，`SV_HEADERS = TOPO_HEADERS`（392）。v2 直接沿用，Excel 列字母不再露脸。

---

#### 1.7 保留①：`force_overrides`（强制 force 信号）——完整穿透链

```
GUI  MainWindow._force_signals（set[基名低]）
      ├ 存盘 _save_force_signals (gui.py:4166) → settings["force_signals"][excel全路径] = sorted([...])
      ├ 载入 gui.py:3476  self._force_signals = set(_load_settings().get("force_signals",{}).get(path,[]))
      ├ 编辑 on_set_force_signals (gui.py:4178)  每行一个基名，`#` 起注释
      ├ 随配置带走 _collect_config (gui.py:5420 "force_signals") / on_import_edits (gui.py:5563-5567)
      │
      ├─【排查(旧)】R.Resolver(force_overrides=self._force_signals)  gui.py:3482 / 4410 / 5583
      │              generator.GenOptions(force_overrides=…)         gui.py:7120 附近
      ├─【Topout】 _TopoutProvider._fo() (gui.py:522)
      │      → topout.topout_view_models(force_overrides=)  topout.py:1554-1563 → _core:1580 R.Resolver(force_overrides=)
      │      → topout.analyze_signal 用 provider 自建的 R.Resolver(force_overrides=)   gui.py:568
      │      → topout.render_topout_sv(force_overrides=)    topout.py:1226-1242 → 1285 → 1313 R.Resolver + 1348 GenOptions
      │      → topout.topout_report(force_overrides=)       topout.py:1649-1662 → 1678 R.Resolver + 1689 转发
      │      → topout.report_for_topout(resolver=…)         topout.py:990 从 resolver 反取 force_overrides
      └─【子视图】 _PageProvider._fo() (gui.py:692)
             → pageviews.page_view_models / build_page_sv / page_report / page_fortest 全带 force_overrides
             → pageviews._page_resolver (61-62) R.Resolver(force_overrides=)
             → pageviews._page_gen_opts (299) GenOptions(force_overrides=)

后端落点  resolver.py:114 self.force_overrides = {s.lower() …}
          resolver.py:223 overridden = low in self.force_overrides or low in self.rfwrite_overrides
          resolver.py:376 if low in self.force_overrides: → 按 RO 处理，直接 force 顶层基名网、跳过 cone
          generator.py:46 / 1162 / 1735 / 2203 R.Resolver(force_overrides=opts.force_overrides)
          generator.py:1055-1060 cone 成环时临时扩充 resolver.force_overrides（save/restore）
CLI       cli.py:126 --force-signals → cli.py:1381 GenOptions(force_overrides=_split(args.force_signals))
```

- **v2 只需要接的参数**：**0 个新参数**。轨 0-⑥ 之后链路已经全通，v2 只要把「强制 force 信号」的编辑器从『排查(旧)』的按钮挪进「诊断」抽屉，继续读写 `main._force_signals` + `settings["force_signals"]` 即可；顺带按契约补「导入/导出 .txt」（现在只有探针前缀有导入导出，force 没有）。

#### 1.8 保留②：`probe_prefixes`（探针前缀映射）——完整穿透链

```
GUI  MainWindow._probe_prefixes（dict{信号名低: 层级路径}）
      ├ 存盘 _save_probe_prefixes (gui.py:3943) → settings["probe_prefixes"][excel全路径] = {...}
      ├ 载入 gui.py:3475
      ├ 编辑/导入/导出 on_set_probe_prefix (gui.py:3955)；文件解析 generator.parse_probe_prefix_lines(934)
      │                                    文件排版 generator.render_probe_prefix_grouped(972)
      ├ 随配置带走 _collect_config (gui.py:5417) / on_import_edits (gui.py:5553-5557)
      │
      ├─【排查(旧)】R.Resolver(wire_prefixes=self._probe_prefixes)  gui.py:3481/4409/5582
      │              GenOptions(probe_prefixes=…)                    gui.py:7126 附近
      ├─【Topout】 _TopoutProvider._pp() (gui.py:517) → topout_view_models / analyze_signal /
      │              render_topout_sv / topout_report / report_for_topout 全带 probe_prefixes
      │              → topout.py:1313/1580/1678 R.Resolver(wire_prefixes=)
      │              → topout.py:1215 _probe_prefix_for_name()：顶层探针名的层级前缀（1401/1468 用）
      └─【子视图】 _PageProvider._pp() (gui.py:688) → pageviews 四个入口全带
                     → pageviews.py:61 R.Resolver(wire_prefixes=)、:264 _prefix_for()、:282

后端落点  resolver.py:118 self.wire_prefixes = {k.lower(): v …}
          resolver.py:361 prefix = self.wire_prefixes.get(low) or self.wire_prefixes.get(wire_name.lower())
                          （命中映射的 wire 视为「用户确认存在」，不再算 wire 兜底风险 —— R41 的总根就在这条）
          generator.py:60 归一化；generator.py:839 断言/force 路径按 opts.probe_prefixes 加 ENV_RF.<前缀>
          excel_model.py:91 / 280 rtl_base_full —— 前缀 key 常是带尾缀的全名，两个名都试
CLI       cli.py:166 --probe-prefix（可多次）/ :170 --probe-prefix-file → cli.py:178 _parse_probe_prefixes
                     → cli.py:1384 GenOptions(probe_prefixes=…)
红区      redzone_tools/scan_rtl.py:228 render_prefix_file() 生成 probe_prefixes.txt（合并格式，GUI 导入即用）
          dreg_verify/rtl_scan.py（Windows 侧 collect_nets → nets.txt）
```

- **v2 只需要接的参数**：**0 个新参数**。入口挪进「诊断」抽屉（保留导入/导出）。
- ⚠ **R41 提醒**：`resolver.py:361` 的 prefixed-wire 提升把「配了前缀」无条件当「网存在且正确」，零校验。v2 的清单/输入表/信号流图要按契约 §7 区分「表里查到」vs「命名约定猜的」——这条不是删除安全问题，但 A3 把它记在这里，因为 `probe_prefixes` 正是那条路径的入口。

---

### 2. 删除操作清单 + 「删了会静默/当场出错」的 5 个坑

**实证（`delete_sim.py`）**：在活的 offscreen `MainWindow` 上把 8 个控件 `delattr` 掉，再跑 20 条会碰它们的代码路径：

```
已 delattr：['top_only','append_to_logic_chk','append_to_mux_chk','include_risky_chk',
             'cascade_logic_combo','cascade_mux_combo','suffix_chk','sig_cascade_combo']

  OK     _append_to_logic_on()                 → True        ← hasattr 兜底
  OK     _append_to_mux_on()                   → False
  OK     _include_risky_on()                   → False
  OK     _logic_cascade() / _mux_cascade()     → 'cone'
  OK     _cascade_for(sig) / _suffix_type_default(sig) / _set_suffix_chk / _set_sig_cascade_combo
  OK     _opts(...)（预览/生成 .sv 的 GenOptions）
  OK     _persist_coverage() / _persist_edits()
  OK     topout_view.refresh() / provider.view_models() / provider.render_sv()
  OK     page_views['logic'].refresh()
  ✗ 炸   apply_filter()                        → AttributeError: no attribute 'top_only'   (gui.py:3708)
  ✗ 炸   _reanalyze_all()                      → 同上（内部调 apply_filter）
  ✗ 炸   _collect_config()   ← 导出完整配置        → AttributeError: 'append_to_logic_chk'    (gui.py:5413)
  ✗ 炸   _apply_global_settings({}) ← 导入完整配置 → AttributeError: 'cascade_logic_combo'     (gui.py:5450)

安全（有 hasattr 兜底或根本不碰）: 16 条        会炸（删控件必须同时删/加守卫）: 4 条
```

| 坑 | 位置 | 现象 | 处理 |
|---|---|---|---|
| ① | `gui.py:3708` `top_only = self.top_only.isChecked()` + `3745` 的过滤分支 | 删控件后**载表就崩**（`_reanalyze_all` → `apply_filter`），不是静默——但会挡住整个 GUI | 两行一起删 |
| ② | `gui.py:5413-5414` `_collect_config` 直接 `self.append_to_logic_chk.isChecked()` / `append_to_mux_chk` | **导出完整配置**炸。注意隔壁 5415 的 `include_risky` 用的是有兜底的 `self._include_risky_on()`——**两种写法混在相邻三行**，很容易漏 | 改成 `self._append_to_logic_on()` / `self._append_to_mux_on()`，或整条 `global` 段随 legacy 一起删 |
| ③ | `gui.py:5450-5466 / 5477-5478` `_apply_global_settings` 直接触 `cascade_logic_combo` / `cascade_mux_combo` / `append_to_logic_chk` / `append_to_mux_chk` | **导入完整配置**炸（`include_risky` 那段有 `hasattr` 守卫，其余没有） | 同上 |
| ④ **静默** | `settings` 的 `append_to_logic/append_to_mux/cascade_logic/cascade_mux/suffix_override/include_risky` 六个键在用户真机上**已经有值**（同事机器上可能是非默认值） | v2 若不读这些键，是对的；但若 v2 复用 `_apply_global_settings` 这类代码又**只读到一半**，会出现「配置里写着 force、界面显示 cone、产物按 cone」的所见≠所得 | v2 载入时**显式忽略并记一条日志**，不要半读半不读；见 §3.1 的「v2 是否还需要」列 |
| ⑤ **静默（文案过时）** | 三处**用户可见文案**点名了被删/被挪的控件，删控件不会报错、但用户照着找不到：<br>· `gui.py:200`（`_skipped_detail_text`，在「生成 .sv 完成」弹窗的详情里，`gui.py:7368`）：「如需强制生成：勾选工具栏「缺前缀强制生成」」<br>· `gui.py:5140`（真值表头部 `_update_ti_header`）：「级联=展开上游/force级联网(logic全局 \| 本信号单点)」<br>· `gui.py:266-288` `CASCADE_DOC_FALLBACK` + `_open_cascade_doc`(3345)：级联 `?` 的内置帮助窗，随三个下拉一起下岗 | 用户按提示去工具栏找控件，找不到 → 以为程序坏了 | 删控件时同步改这三段文案：①改指「诊断抽屉 → 缺前缀是否强制生成」；②改成范围口径（「范围=全链 / 本页」）或直接删；③随 `?` 按钮一起删（`docs/级联模式说明.md` 保留作背景资料） |

另一个**已经存在、不是删出来的**静默源（删完反而消失）：`Resolver.__init__` 会把 `append_to_logic/append_to_mux/suffix_override` **回写到 `wb.logic` / `wb.mux` 每个信号对象的共享字段 `_append_to_logic`**（`resolver.py:143-147`）。所以『排查(旧)』把开关关掉后，在下一个 provider 重建 Resolver 之前，Topout 视图看到的 `rtl_name` 会被污染——`pageviews.py:54-56` 的注释里写了这个实测教训，`gui.py:5591` 的导入顺序也是为绕开它。**删掉这三个 GUI 入口后，全进程只剩默认值一种取法，这类跨视图污染自动消失。**

**删除 PR 的护栏（改前改后各跑一次，值必须全同）**：

```
byte-gate（tools/byte_gate.py，2026-09-12 基线，本审计当场复跑 PASS）
  btlp min=61a3cc83 max=06bc2194 exh=76646b70
  wl   min=54c06776 max=41e47830 exh=ac03a474
旧 CLI 默认路径 .sv（scratchpad/a3/sv_sensitivity.py [A] baseline）
  wl   sha=656ef194（7 块）    btlp sha=a0808517（1 块）
```

**受影响的现有测试（L4 退役规则要登记的）**：
`tests/test_mux_wl_gui.py`（`append_to_logic_chk` 9 处 / `append_to_mux_chk` 2 处 / `cascade_logic_combo` 2 处 / `cascade_mux_combo` 3 处 / `sig_cascade_combo` 3 处 / `suffix_chk` 7 处 / `include_risky_chk` 2 处）、`tests/test_topout_gui.py:610`（`assert hasattr(w,"cascade_logic_combo") and hasattr(w,"append_to_logic_chk")`）、`tests/test_testitems.py:1313/1330/1339/1361/1368`（`include_risky_chk` 随配置导入导出）。`top_only` 与 `HEADERS` 在测试里零引用。

---

## 第二部分 · 持久化盘点

### 3.1 `~/.dreg_verify_gui.json`（界面偏好）—— 全部 27 个键

`SETTINGS_PATH = ~/.dreg_verify_gui.json`（`gui.py:46`）。`_load_settings()`(160) 读失败返回 `{}`；`_save_settings()`(170) **pytest 下 no-op**、写失败静默吞（`except Exception: pass`）。无版本号、无 schema、平坦 dict。

实证（`dump_persist.py`，offscreen 跑一遍把每个键都逼出来）：共 **27 个键**。

| 键 | 类型 | 默认/缺省行为 | 谁写（gui.py） | 谁读（gui.py） | v2 是否还需要 |
|---|---|---|---|---|---|
| `last_excel` | str | 无 | `_save_last_excel`(185) | `_load_last_excel`(181) | ✅ 要（启动预填路径） |
| `coverage_logic` | str `精简/全面/穷举` | 缺键回退 `coverage` → 精简 | `_persist_coverage`(3199，写在 3201-3203) / `_apply_global_settings`(5474) | 2675-2679 | ✅ 要（塌进「覆盖度三层」控件） |
| `coverage_mux` | str 同上 | 同上 | 同上 | 同上 | ✅ 要 |
| `coverage` | str | **legacy 只读**，新代码不写 | —— | 2676（缺新键时回退） | ⬜ 载入时读一次作迁移，不再写 |
| `max_tests` | int 1..100000 | 256 | `_persist_coverage`(3203) | 2689-2691 | ✅ 要 |
| `cov_<view_id>` ×5<br>（`cov_topout/cov_logic/cov_mux/cov_dft/cov_iddq`） | str | 缺键 → `全面` | `SignalView._persist_cov`(1062) | 781 | ✅ 要（按视图/范围记档） |
| `maxt_<view_id>` ×5<br>（`maxt_topout/…/maxt_iddq`） | int | 缺键 → 256 | `SignalView._persist_maxt`(1068) | 800 | ✅ 要 |
| `cascade_logic` | str `cone/force` | 缺键回退 `cascade_mode` → cone | `on_cascade_mode_changed`(3231) / `_apply_global_settings`(5476) | 2705、5451 | ❌ **随入口删，载入时忽略不报错** |
| `cascade_mux` | str 同上 | 同上 | 同上 | 2714、5451 | ❌ 同上 |
| `cascade_mode` | str | **legacy 只读** | —— | 2705/2714/5449 | ❌ 同上 |
| `append_to_logic` | bool | 缺键 → True | `on_append_to_logic_changed`(3299)、`_apply_global_settings`(5477) | 2734、5456 | ❌ 随入口删，忽略不报错 |
| `append_to_mux` | bool | 缺键 → False | `on_append_to_mux_changed`(3314)、5478 | 2747、5462 | ❌ 同上 |
| `include_risky` | bool | 缺键 → **True**（生产）/ 测试基线不勾 | `on_include_risky_changed`(3331)、5480 | 2761、5467 | ⚠ **保留**（降级进诊断抽屉的可见开关，默认 True） |
| `suffix_override` | dict `{excel全路径: {信号名低: bool}}` | `{}` | `_save_suffix_override`(3160) | 3477 | ❌ 随入口删，忽略不报错 |
| `probe_prefixes` | dict `{excel全路径: {信号名低: 层级路径}}` | `{}` | `_save_probe_prefixes`(3943) | 3475 | ✅ **要**（诊断抽屉） |
| `force_signals` | dict `{excel全路径: [基名]}` | `{}` | `_save_force_signals`(4166) | 3476 | ✅ **要**（诊断抽屉） |
| `logic_overrides` | dict `{excel全路径: {基名: spec}}` | `{}` | `_save_logic_overrides`(3185) | 3470 | ✅ **要**（诊断抽屉 · RTL 补充逻辑） |
| `export_scope` | str `all/pos/neg` | `all` | `_ask_export_options` ×2（2271、7273） | 2253、7230 | ✅ 要（导出中心记忆上次） |
| `export_comments` | bool | False | 同上 | 2258、7230 | ✅ 要 |
| `export_sv_summary` | bool | ⚠ **两处默认不一致**：SignalView 读 `False`(2260)、MainWindow 读 `True`(7233/7093) | 同上 | 2260、7093、7233 | ✅ 要，**v2 必须统一成一个默认值**（契约 §4.2「两个『导出 .sv 选项』→ 1 个，统一默认值与 settings 键」） |
| `export_owner_in_msg` | bool | ⚠ 同上：SignalView `False`(2262)、MainWindow `True`(7091/7232) | 同上 | 2262、7091、7232 | ✅ 要，同样要统一 |
| `nets_pages` | list[str] | 缺/空 → 全勾当前可导类别 | `_ask_nets_pages`(4112) | 4087 | ✅ 要（nets.txt 按类别） |

**实测 dump（键名与真实取值形状）**：

```
append_to_logic   bool  false
append_to_mux     bool  true
cascade_logic     str   "force"
cascade_mux       str   "force"
cov_dft/cov_iddq/cov_logic/cov_mux/cov_topout   str  "全面"
coverage_logic    str   "全面"          coverage_mux  str  "穷举"
export_comments   bool  true            export_owner_in_msg bool false
export_scope      str   "pos"           export_sv_summary   bool true
force_signals     dict  {"C:\\code\\Dreg_verify\\mirror_btlp_dreg.xlsx": ["d_force_demo"]}
include_risky     bool  true            last_excel str "C:\\code\\Dreg_verify\\mirror_btlp_dreg.xlsx"
logic_overrides   dict  {"…mirror_btlp_dreg.xlsx": {"d_demo_base": {"enabled": true, …}}}
max_tests         int   77
maxt_dft/maxt_iddq/maxt_logic/maxt_mux/maxt_topout  int  512
nets_pages        list  ["logic","mux","topout"]
probe_prefixes    dict  {"…mirror_btlp_dreg.xlsx": {"d_probe_demo": "U_TOP.U_SUB"}}
suffix_override   dict  {"…mirror_btlp_dreg.xlsx": {"clk_force_on": false}}
```

**给 v2 的三条硬要求**
1. `_load_settings()` 已经是「读不到就 `{}`」的宽容实现——**v2 不要收紧**；未知键一律忽略、不报错（执行计划 §2 硬护栏：同事机上的旧文件必须仍可载入）。
2. 四个 `export_*` 键的默认值目前两套门面不一致（见上表 ⚠），合并成一个导出中心时必须显式挑一个，并在契约表里登记。
3. 所有按 Excel **全路径**分桶的键（`probe_prefixes` / `force_signals` / `suffix_override` / `logic_overrides`）在换机器后路径必然不同 → 静默失效。v2 沿用即可（不是本轮范围），但导入配置时已有「按文件名核对、配错表给提示」的先例（`gui.py:5546-5551`），值得推广到这四个键。

---

### 3.2 `~/.dreg_verify_edits.json`（劳动成果）—— 两种桶格式

`EDITS_PATH = ~/.dreg_verify_edits.json`（`gui.py:49`）。顶层 = `{Excel 全路径: 桶}`。`_load_edits_file()`(55) 读失败 → `{}`；`_save_edits_file()`(65) 在 pytest 下且路径未被 monkeypatch 时 no-op。**无版本号**。

**关键事实：两种「格式」不是两个文件、也不是两个版本号，而是同一个桶里的两组顶层键。**

| | legacy（『排查(旧)』门面） | SignalView（Topout + 4 个子视图） |
|---|---|---|
| 桶内顶层键 | `edits` `neg_only` `mux_expected` `mux_neg` `mux_data` `mux_dropped` `mux_cleared` `mux_user_vecs` `signals_checked` | `view_edits` `view_checks` |
| 写 | `MainWindow._persist_edits`（gui.py:5179-5224） | 同一个 `_persist_edits`，经 `_collect_view_edits`(5356) / `_collect_view_checks`(5365)，**仅非空时才挂上去**（5386-5389，保证旧桶逐字节不变） |
| 读 | `MainWindow._apply_edits_bucket`（gui.py:5265-5345） | `MainWindow._restore_views_from_bucket`（gui.py:5374）→ `SignalView._restore_view_edits`(2121) / `_apply_view_checks`(2176) |
| 模型 | **行模型**：每信号一个 `rows` 列表，一行 = 一条测试 | **列模型**：每信号一个 `cols` 列表，一列 = 一条测试 |
| 键空间 | `edits` 按 **logic 源 out_name 小写**；`mux_*` 按 **mux 组 out_name 小写（含位宽切片）** | 按 **view_id → Topout 顶层名小写** |

#### A. legacy 桶 schema

```
桶[excel路径] = {
  "edits":       { logic源名低: [ 行, … ] },          # _serialize_rows (gui.py:89)
  "neg_only":    { logic源名低: "first" | "all" },
  "mux_expected":{ mux组名低: { 输入取值键(str): int } },
  "mux_neg":     [ mux组名低, … ],
  "mux_data":    { mux组名低: { 物理基名低: int } },
  "mux_dropped": { mux组名低: [ 列签名, … ] },
  "mux_cleared": [ mux组名低, … ],
  "mux_user_vecs": { mux组名低: [ 序列化TestVector, … ] },   # _serialize_mux_vecs (gui.py:120)
  "signals_checked": [ 原样信号名(保留大小写), … ],
}
行 = { "base_values": {物理基名: int}, 再加 _ROW_PERSIST_KEYS 里非空的字段 }
_ROW_PERSIST_KEYS = ("kind","wrong_value","name","user_added","note","designer_expected")  # gui.py:52
计算字段 correct / correct_width / expected / is_negative / _vec 【不落盘】，加载时 _recompute_row 重算
```

**真实例子**（`dump_legacy_bucket.py`，mirror btlp，`d_logic_bt_lp_rx_en` 的首个正向行 + 一条负向行）：

```json
[
 { "base_values": { "d_bt_lp_linelocal_mode_ctrl": 0, "d_bt_lp_linectrl_rx_en": 1,
                    "d_bt_lp_rx_en_local": 1, "d_bt_lp_pll_dig_dft_iddq_mode": 0 },
   "kind": "pos", "note": "A3 审计示例：手填期望", "designer_expected": 1 },
 { "base_values": { "d_bt_lp_linelocal_mode_ctrl": 0, "d_bt_lp_linectrl_rx_en": 1,
                    "d_bt_lp_rx_en_local": 1, "d_bt_lp_pll_dig_dft_iddq_mode": 0 },
   "kind": "neg", "wrong_value": 0, "name": "T0_NEG", "user_added": true,
   "note": "A3 审计示例：手填期望" }
]
```

桶顶层键实测：`['edits','neg_only','mux_expected','mux_neg','mux_data','mux_dropped','mux_cleared','mux_user_vecs','signals_checked']`。

`mux_user_vecs` 的单条序列化（`_serialize_mux_vecs`，`file_formats.py` 实证）：

```json
{ "assignments": {"c:A": 1, "d:0": 10}, "exp_value": 10, "exp_width": 4,
  "is_negative": true, "neg_value": 5, "neg_mode": "invert",
  "name": "mux0_NEG", "note": "A3 审计示例", "designer_expected": null, "case_index": 1 }
```

⚠ `_serialize_rows`（gui.py:89-99）的过滤条件是 `v is not None and v != "" and v is not False`——**靠 `is not False` 的身份比较救下了 `0`**（`wrong_value: 0` / `designer_expected: 0` 都能存活），但 `user_added: False` 会被丢掉（丢了也没事，反序列化默认 False）。v2 重写这层时别把它改成真值判断，否则「错值 = 0」的负向列会**静默丢失**。

#### B. SignalView 桶 schema

```
桶[excel路径]["view_edits"] = {
  view_id: {                                 # view_id ∈ topout / logic / mux / dft / iddq
    Topout名低: {
      "kind": "logic"|"mux"|"register",
      "src_out_name": 源 out_name（logic/mux 源名；register 根 = 顶层名）,
      "name": 顶层名,
      "renamed": bool,                        # dft 改名根：编辑要走 reg 路按顶层名键
      "cols": [ 列, … ]
    }
  }
}
列 = { "name": str, "neg": bool,
       "vals": { 输入行键: int },              # logic/register：物理基名；mux：'c:<字母>' 控制 / 'd:<i>' 数据
       "exp": int | null,                      # 正向=designer 手填期望；负向=故意填错的值
       "auto": int, "auto_w": int,             # 引擎算出的正确值/位宽（logic 恢复时 _recompute_col_an 权威重算）
       "user": bool, "dft": bool,
       "case_index": int | null }              # mux 列的路由 case（重建 TestVector 用）

桶[excel路径]["view_checks"] = { view_id: [信号名, …] }   # 仅「取消过部分勾选」的视图才写；全勾=不写
```

**真实例子**（`dump_persist.py`，mirror btlp，Topout 视图）——`view_edits.topout` 的两个 key：`clk_force_on`（register 根）与 `d_bt_lp_lna_itrim`（mux 根）：

```json
"clk_force_on": {
  "kind": "register", "src_out_name": "clk_force_on", "name": "clk_force_on", "renamed": false,
  "cols": [
    { "name":"T0", "neg":false, "vals":{"clk_force_on":0}, "exp":5,    "auto":0, "auto_w":1,
      "user":false, "dft":false, "case_index":null },
    { "name":"T1", "neg":false, "vals":{"clk_force_on":1}, "exp":null, "auto":1, "auto_w":1,
      "user":false, "dft":false, "case_index":null } ] }

"d_bt_lp_lna_itrim": {
  "kind": "mux", "src_out_name": "d_bt_lp_lna_itrim[3:0]", "name": "d_bt_lp_lna_itrim",
  "renamed": false,
  "cols": [
    { "name":"T0", "neg":false,
      "vals":{"c:A":0,"c:B":0,"c:C":15,"d:0":10,"d:1":9,"d:2":8,"d:3":7,
              "d:4":6,"d:5":5,"d:6":4,"d:7":3},
      "exp":null, "auto":10, "auto_w":4, "user":false, "dft":false, "case_index":0 }, … ] }

"view_checks": { "topout": ["d_bt_lp_lna_itrim","d_en_refbuf_ls","d_logic_bt_lp_lna_agc", … ] }
```

#### C. `_apply_edits_bucket` 怎么「区分」两种格式

**它不区分——它只认 legacy 键，对 `view_edits` / `view_checks` 视而不见。** 区分是靠**顶层键名分流**，两条恢复链各读各的：

```
换表/载表  MainWindow.on_load
   ├─ gui.py:3524  n_restored = self._restore_edits()
   │        → gui.py:5230 bucket = _load_edits_file().get(path)
   │        → gui.py:5233 self._apply_edits_bucket(bucket)     ← 只读 legacy 键
   │              bucket.get("edits") / ("neg_only") / ("mux_expected") / ("mux_neg")
   │            / ("mux_data") / ("mux_dropped") / ("mux_cleared") / ("mux_user_vecs")
   │        → gui.py:5235 self._apply_signal_checks(bucket.get("signals_checked"))
   └─ gui.py:3531  self._restore_views_from_bucket(_load_edits_file().get(path))
            → gui.py:5378-5379  ve = bucket.get("view_edits") or {}；vc = bucket.get("view_checks") or {}
            → 各 SignalView._restore_view_edits(ve.get(vid)) / _apply_view_checks(vc.get(vid))
```

两条链都是「**缺键 = 保持默认、不报错**」：`_restore_views_from_bucket` 的 docstring 明写「bucket 缺 `view_edits`/`view_checks`（旧桶）→ 视图保持默认（全勾、无自定义），向后兼容」。导入配置走同样两条链（`gui.py:5588` `_apply_edits_bucket(payload)` + `5591` `_restore_views_from_bucket(payload)`）——所以**完整配置文件和 edits 桶是同一套键**，只是配置文件多了 `dreg_verify_config` / `excel` / `global` / `probe_prefixes` 等（见 §3.3）。

容错细节（v2 要保住的行为）：
- `_coerce_int_map`(gui.py:76)：`mux_expected` / `mux_data` 里手改坏的数值**跳过并计数**，不让整个载表流程崩，跳过项在弹窗里点名 + 原因。
- `_deserialize_mux_vecs`(gui.py:138)：损坏的向量条目静默跳过。
- `_restore_view_edits`(2121)：信号在当前表找不到 / 不可编辑 → 跳过不崩；恢复个数作返回值。
- `_apply_edits_bucket` 的 `missing` 列表带**名字 + 原因**（「信号名在 logic 页不存在(表改名/删行?)」「mux 页不存在」「mux 期望：N 个数值非法，已跳过」），`_restore_edits`(5236-5241) 把它追加到预览页。**这正是 gui-feedback 原则要的形状，v2 照抄。**
- **单点覆盖度永不进桶也永不恢复**（`_persist_edits` 5193 注释 + `_apply_edits_bucket` 5343）——会话内临时档，存盘会静默盖过全局下拉（用户实测 bug）。v2 别「顺手」把它持久化了。

#### D. 给 v2 的建议

**必读 SignalView 桶**（`view_edits` / `view_checks`）——Topout 是 designer 的默认门面，手填期望全在这里。

**legacy 桶：默认不读、不删、不覆盖。** v2 的 `_persist_edits` 等价物写桶时，必须**原样保留**它没认识的顶层键（现在的实现是整桶重建 `allbuckets[path] = bucket`，会把 legacy 段一起写回，因为 legacy 状态还在内存里；v2 删掉 legacy 门面后，同样的写法会**把同事的旧 legacy 编辑整段抹掉**——这是本次审计发现的**最危险的一个静默丢数据点**）。
→ **护栏**：v2 写桶前先 `old = _load_edits_file().get(path, {})`，再 `old.update(新键)`，只覆盖 `view_edits`/`view_checks`/`signals_checked` 这些自己管的键。

**关于「廉价迁移」**：logic / register 根**可以**廉价迁移，mux **不行**。实证（`compare_keys.py`，mirror btlp `d_logic_bt_lp_rx_en`）：

```
legacy rows[0].base_values 键 = ['d_bt_lp_linectrl_rx_en','d_bt_lp_linelocal_mode_ctrl',
                                'd_bt_lp_pll_dig_dft_iddq_mode','d_bt_lp_rx_en_local']
SignalView e_inputs 行键     = ['d_bt_lp_linelocal_mode_ctrl','d_bt_lp_linectrl_rx_en',
                                'd_bt_lp_rx_en_local','d_bt_lp_pll_dig_dft_iddq_mode']
SignalView cols[0].vals 键   = 同上
键完全相同?  True     legacy-only: []     view-only: []
```

迁移映射（logic / register 根）：

| legacy 行字段 | → SignalView 列字段 | 说明 |
|---|---|---|
| `base_values` | `vals` | **键空间完全相同**（物理基名），直接搬 |
| `kind == "neg"` | `neg = true` | |
| `wrong_value` | `exp` | 负向列的「故意填错的期望」就存在 `exp` 里（`_add_negatives` gui.py:1808-1840 的 `"exp": wrong`） |
| `designer_expected` | `exp` | 正向列的手填期望 |
| `name` | `name` | 缺省 `T<n>` |
| `user_added` | `user` | |
| `note` | —— | **列模型没有 note 字段，会丢**（要么加字段，要么迁移时点名告知） |
| —— | `auto` / `auto_w` | 填 `0/1` 占位即可：`_restore_cols`(2096-2120) 对 `editable=="logic"` 会调 `_recompute_col_an` **权威重算**（改表后不陈旧） |
| —— | `case_index` | logic/register 恒 `null` |
| `neg_only`（"first"/"all"） | —— | 是**规则**不是数据；legacy 的负向已经物化成 `kind=="neg"` 的行，不需要迁 |

两个必须处理的落差：
1. **键名不同域**：legacy `edits` 按 **logic 源名**键，`view_edits` 按 **Topout 顶层名**键。迁移要过一次 `topout.resolve_root`（dft 改名根两者不同名）。
2. **列数会被冻结**：实测同一信号 legacy 4 行（精简档）vs Topout 视图 12 列（全面档）。迁进去之后 `edits` 是权威、自动列全被替换 → 用户会看到用例数突然变少。

**所以给 v2 的落点建议**：不做自动迁移，在「诊断」抽屉放一个**一次性、显式**的『导入旧版(排查)真值表编辑』动作：
- 只迁 `kind ∈ {logic, register}` 的信号；
- mux（`mux_expected` / `mux_data` / `mux_user_vecs` / `mux_dropped` / `mux_cleared`）**点名列出「未迁移 + 原因：mux 用的是 case/数据列坐标（`c:A` / `d:0`），与旧版按物理基名存的取值对不上」**；
- 迁完把「本信号用例数 4 → 原自动 12」这件事在反馈里说清楚；
- 迁移**只读不删** legacy 段。

---

### 3.3 配置导出 / 导入文件 schema（`_collect_config` / `on_export_edits` / `on_import_edits`）

- 写：`on_export_edits`（gui.py:5493）→ `json.dump(payload, ensure_ascii=False, indent=1)`，默认文件名 `<excel basename>_config.json`。
- 读：`on_import_edits`（gui.py:5526）。
- **有版本号**：顶层 `"dreg_verify_config": 2`（gui.py:5405，在 `_collect_config` 5397 内）。

```
{
  "dreg_verify_config": 2,                     ← 版本号（v2 完整配置）；缺它 = legacy 编辑文件
  "excel":      "mirror_btlp_dreg.xlsx",       ← 源表文件名（跨机器比对用）
  "excel_path": "C:\\code\\Dreg_verify\\mirror_btlp_dreg.xlsx",
  "signals_checked": [ … ],
  "global": {                                  ← 全局工具栏设置（6 组开关里有 5 组在这儿）
      "coverage_logic": "全面", "coverage_mux": "穷举", "max_tests": 77,
      "cascade_logic": "force", "cascade_mux": "force",
      "append_to_logic": false, "append_to_mux": true,
      "include_risky": true
  },
  "probe_prefixes": { 信号名低: 层级路径 },
  "force_signals":  [ 基名, … ],
  "suffix_override":{ 信号名低: bool },
  "logic_overrides":{ 基名低: spec },
  "edits": …, "neg_only": …, "mux_expected": …, "mux_neg": …, "mux_data": …,
  "mux_dropped": …, "mux_cleared": …, "mux_user_vecs": …,     ← 与 edits 桶 legacy 段同 schema
  "view_edits": …, "view_checks": …                            ← 与 edits 桶 SignalView 段同 schema
}
```

实测顶层键（`dump_persist.py`）：
`['dreg_verify_config','edits','excel','excel_path','force_signals','global','logic_overrides','mux_cleared','mux_data','mux_dropped','mux_expected','mux_neg','mux_user_vecs','neg_only','probe_prefixes','signals_checked','suffix_override','view_checks','view_edits']`

导入的两种语义（`gui.py:5540-5546`）：
- `is_full` = 有 `dreg_verify_config` → **先 `_reset_all_config_state()` 清空再照单恢复**（= 加载这份工作状态）。
- `is_legacy` = 有 `dreg_verify_edits` 或任一 `edits`/`mux_expected`/`mux_data` → **合并语义**（只并 per-signal 编辑）。
- 都不是 → 拒绝并给出人话原因。
- 源表核对按**文件名**（不按全路径）比对，不一致时**照常导入 + 顶部 ⚠ 提示**（`gui.py:5546-5551`、5613-5616）。

**v2 注意**：`global` 段里的 `cascade_logic/cascade_mux/append_to_logic/append_to_mux` 四项随入口删；`include_risky` 保留（它会改产物，`gui.py:5415` 注释「改产物→须随配置带走」，且**缺键时保持当前**而非复位，5467-5473）。版本号建议升到 `3`，并在读到 `2` 时忽略这四项 + 记一条可见的日志（不要静默）。

---

### 3.4 三种用户可导入/导出的文本文件格式

实证：`file_formats.py`。

#### ① 探针前缀 `.txt`

- 导入：`on_set_probe_prefix` 内的 `do_import`（gui.py:3994-4007），解析器 `generator.parse_probe_prefix_lines`（generator.py:934）。
- 导出：`do_export`（gui.py:4009-4020），排版器 `generator.render_probe_prefix_grouped`（generator.py:972）。
- 与 CLI `--probe-prefix-file`、红区 `redzone_tools/scan_rtl.py:228 render_prefix_file` **同一格式**，可直接互换。
- **三种写法可混用**：①扁平 `名=路径` ②合并 `路径:` 组头 + 其下信号名（每行一个 / 一行多个，逗号或空格分隔）③`#` 注释与空行跳过。

导出的真实样例（合并格式，`render_probe_prefix_grouped`）：

```
U_BT_LP_PLL_DIG:
    d_bt_lp_lna_itrim, d_logic_bt_lp_rx_en

U_BT_LP_PLL_DIG.DIG_1:
    d_en_refbuf_ls
```

往返解析回来（无损）：
`{'d_bt_lp_lna_itrim': 'U_BT_LP_PLL_DIG', 'd_logic_bt_lp_rx_en': 'U_BT_LP_PLL_DIG', 'd_en_refbuf_ls': 'U_BT_LP_PLL_DIG.DIG_1'}`

扁平格式也认：

```
# 注释
d_bt_lp_lna_itrim=U_BT_LP_PLL_DIG
d_en_refbuf_ls = U_BT_LP_PLL_DIG.DIG_1
```
→ `{'d_bt_lp_lna_itrim': 'U_BT_LP_PLL_DIG', 'd_en_refbuf_ls': 'U_BT_LP_PLL_DIG.DIG_1'}`

scan_rtl 生成的文件还会带三段注释头 / 「顶层可探、无需前缀」/ 「⚠ RTL 里找不到」清单（`scan_rtl.py:228-252`），解析端按 `#` 忽略。

#### ② 强制 force 文本（**目前只在对话框里，没有导入/导出按钮**）

- 编辑器：`on_set_force_signals`（gui.py:4178）的 `QPlainTextEdit`，每行一个**基名**（去 `_to_logic`/`_to_mux` 尾缀、去位宽）。
- 解析：`gui.py:4207-4211`——`line.split("#",1)[0].strip().lower()`，空行跳过；留空 = 清除。
- 等价 CLI `--force-signals`（逗号分隔）与 for_test 的 `force \`ENV_RF.<基名>`。

```
d_bt_lp_lna_itrim
d_logic_bt_lp_rx_en   # 行尾 # 之后是注释

# 整行注释
```
→ 解析结果 `['d_bt_lp_lna_itrim', 'd_logic_bt_lp_rx_en']`

**契约缺口**：推进方案 §4.1 要求「强制 force 信号（入口 + Topout 后端接通）」，§4.3 把它降级进诊断抽屉——但它**没有导入/导出**（探针前缀有）。v2 应补上，格式就用上面这个（每行一个基名 + `#` 注释），与 CLI 的逗号分隔互转。

#### ③ RTL 补充逻辑 JSON

- 编辑器：`on_logic_overrides`（gui.py:4275；SignalView 侧的同名转发在 1081）；模板 `_supplement_template`（gui.py:4217）；「从文件导入…」`_from_file`（gui.py:4320）**只把文件内容塞进文本框**，真正落盘前必过 `_validate_supplements`（gui.py:4242）。
- 无导出按钮（内容随**完整配置**的 `logic_overrides` 段走）。
- 校验：顶层必须是对象；信号名非空且不以 `<` 开头（防模板占位符没替换）；`expr` 非空且能 `expr.parse`；`inputs` 非空列表 `[{var, raw}]`；**表达式里用到的变量必须都有 input 映射**，否则点名报错、整份不保存。
- 后端：`generator._norm_logic_overrides`（generator.py:226，丢弃 `enabled:false` / 无 `expr` 的项）→ `generator.make_supplement_signal`(243) → `_logic_with_overrides`(309)（命中基名**替换**、纯新增**追加**，`wb.logic` swap-and-restore 不污染共享 wb）。Topout 侧由 `_TopoutProvider._supplemented()`（gui.py:529）包一层。合成块顶强制 `// ⚠`（`_supplement_warning`，generator.py:331）。

格式 `{信号基名(小写): {enabled, expr, inputs:[{var, raw}], note}}`，真实样例：

```json
{
 "d_en_vco_fc": {
  "enabled": true,
  "note": "SE 说 RTL 顶层口后多了一级 ECO iddq",
  "expr": "ECO_IDDQ ? 1'b0 : (VCO_FC_SEL ? VCO_EN_FASTON : (EN & ~IDDQ))",
  "inputs": [
   { "var": "EN",            "raw": "d_bt_lp_vco_en" },
   { "var": "IDDQ",          "raw": "iddq" },
   { "var": "VCO_FC_SEL",    "raw": "d_vco_fc_sel_ls[0]" },
   { "var": "VCO_EN_FASTON", "raw": "d_vco_en_faston" },
   { "var": "ECO_IDDQ",      "raw": "d_eco_iddq" }
  ]
 }
}
```

`var` = 表达式里的变量名（大小写无关），`raw` = 真实网名（可带 `[msb:lsb]`），`enabled:false` = 临时停用，`note` = 理由（进合成块顶注释给 SE 复核）。

---

## 附录 · 复现

```bash
# 全部用 .venv 解释器；GUI 相关加 QT_QPA_PLATFORM=offscreen
.venv/Scripts/python.exe tools/byte_gate.py                      # 6 sha 护栏
.venv/Scripts/python.exe scratchpad/a3/probe_flags.py            # CLI 默认 / 形参表 / 运行时录音 / 反向依赖
.venv/Scripts/python.exe scratchpad/a3/sv_sensitivity.py         # 逐开关 .sv 字节敏感度
.venv/Scripts/python.exe scratchpad/a3/suffix_effect.py          # 尾缀开关对 rtl_base 的真实影响面
.venv/Scripts/python.exe scratchpad/a3/risky_effect.py           # include_risky 写死 True 改可关的代价
.venv/Scripts/python.exe scratchpad/a3/dump_persist.py           # settings 27 键 + 两种桶 dump
.venv/Scripts/python.exe scratchpad/a3/dump_legacy_bucket.py     # legacy 桶完整例子
.venv/Scripts/python.exe scratchpad/a3/compare_keys.py           # 两种桶键空间对比（迁移可行性）
.venv/Scripts/python.exe scratchpad/a3/delete_sim.py             # delattr 删控件 → 哪些路径会炸
.venv/Scripts/python.exe scratchpad/a3/header_rename.py          # 列头纯显示证明
.venv/Scripts/python.exe scratchpad/a3/file_formats.py           # 三种可导入导出文件样例
```

脚本在 `C:\Users\Yusheng\AppData\Local\Temp\claude\C--code-Dreg-verify\0790323c-8316-4bac-9cab-7126e4f33297\scratchpad\a3\`（会话级临时目录，**不入库**）。所有 GUI 脚本都把 `gui.SETTINGS_PATH` / `gui.EDITS_PATH` 重定向到该目录，用户真机的两个持久化文件未被读写。
