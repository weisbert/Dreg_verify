# 交接：Topout-rooted 验证（GUI/CLI 端到端）

> 状态：**端到端可用 + 全功能化 + 改名桥接 + 撞名 bug 收口 + 探针前缀接入 + 仓库整理，已 push**。HEAD `f90d4d9`（= origin/main），`788 passed + 1 xfailed`。
> 旧 logic-rooted CLI/generator 输出**逐字节不变**（默认 None 路径不变；改 topout.py/pageviews.py 全 additive）。
> 日期：2026-06-24。验证夹具：`mirror_btlp_dreg.xlsx`（7 真族金标准）。真表：`Hi1108_Pilot_BT_LP_DREG_95P_20260623.xlsx`（公司机 `D:\Onebox\Code\Dreg_verify\excel\`）。
> **真表 diag 实况**：211 信号 = logic 123 / mux 7 / register 80 / ro 0 / **unresolved 0**；撞名审计 `diag_name_collisions` **0 残留真问题**。
> **⚠ 仓库已整理**：探查/诊断/审计脚本全在 `tools/`（跑法 `python tools/<名>.py`）；文档在 `docs/`；`make_mirror_*` 留根（测试夹具）。

---

## ⭐ 第四轮（2026-06-24 续2）：撞名 bug 收口 + 探针前缀接入 + 显示位宽 + 整理

起于用户对一个黄字告警的提问，挖出并修掉一类系统性 bug，并补齐 Topout/页视图的前缀功能。

**A. 撞名解析错位 bug（两变体，都收口）** —— 同一基名在表里既是【输入寄存器】又是【计算输出】（这张表 register→logic算→dft观测→ls缓冲、同名贯穿全程，是常态）：
  - **变体1 改名带门**（`d_vco_en_faston_ls`）：`_ls ←(level_shift) d_vco_en_faston(=dft 观测 iddq?0:fsm，又恰有同名 RW 输入寄存器) ←(dft) d_vco_en_faston_fsm(logic 选路)`。旧版桥接走到 `d_vco_en_faston` 时撞上同名寄存器即停 → 误判直连寄存器（1 输入、按字段宽假绿），漏掉 logic 选路 + iddq 门 + fll_active。**修**：`resolve_root` 桥接撞到的寄存器【若还能继续被改名】→ 优先顺 dft 链找真 logic/mux 源；记途经的带门 dft 观测名 `dft_obs_name`。
  - **变体2 同名带门**（`d_bt_lp_pmu_test_en`）：寄存器被 dft【同名】带门观测（`d=iddq?0:d`，无改名链节点）→ 直连命中寄存器即停、漏 iddq。**修**：`resolve_root` 直连/桥接 REGISTER 根若同名在 `wb.dft`（带门）→ 也设 `dft_obs_name`。
  - **统一**：`analyze_signal` 的 LOGIC/REGISTER 路据 `dft_obs_name` 调 `pin_dft_gate`+`_append_dft_vectors`（iddq force 透传 0 + DFT 拍，对齐 for_test 金标准）。**关键认知**：~90 个 logic 输出带门的（`d_en_pfd` 等）本就走 `generator.build` 的 `pin_dft_gate(out_base)`、dft 输出名==logic 名故门已叠、不受影响——漏门只发生在「判直连寄存器」的路径。
**B. 两个探查脚本（`tools/`）**：`diag_one_topout.py 真表 信号 [额外门控名…]`（单信号 10 段：Topout行/改名链/resolve判定/字段宽/真值表issues/各结构页原始行/单信号.sv/for_test金标准/门控溯源）；`diag_name_collisions.py 真表`（全表四类撞名审计 + 逐 Topout ⚠真残留检测，`level_shift=透明缓冲`不算计算输出已去误报）。
**C. 探针前缀接入 Topout/页视图**：之前 `build_for_topout`/`_TopoutProvider`/`pageviews` 全程没传 `probe_prefixes` → force 叶子(RO/iddq门)埋子模块时 `ENV_RF.<裸名>` CUVUNF。数据层全链加 `probe_prefixes` 形参 → `Resolver(wire_prefixes=)`+`GenOptions(probe_prefixes=)`+`_topout_probe_block(probe_prefix=)`；GUI 两 Provider `_pp()` 取共享 `self._probe_prefixes`，**每个 SignalView 工具栏加「设置探针前缀…」按钮**（per-net + 全局，复用全局映射编辑器/导入 scan_rtl）。
**D. 显示带位宽**：Topout 信号清单显示名带切片（`aac_ctf_bit_sel[2:0]`，`_topout_disp_name`/`m["disp"]`）；`name`(查找 key)不变，只改显示。
**E. 仓库整理**：`tools/`(14 脚本，各补 sys.path 上溯仓库根) + `docs/`(10 md)；删过期示例 html。

---

## ⭐ 第三轮（2026-06-24 续）：改名桥接 + 探查脚本 —— 真表唯一未解析信号已解决

真表里有信号在 logic 页叫一个名、过中间页观测后【改了名】，顶层 Topout 名对不上 logic 候选 → 旧版标『未解析』。

- **探查脚本 `tools/diag_topout_rename.py`**（不改文件）：`python tools/diag_topout_rename.py 真表.xlsx`。输出 ① Topout 分类汇总（含「自动认出 N 条改名」）② 未解析清单 ③ 逐个未解析名在各结构页的定位（★=输出列=改名定义行，同行输入列=源名）。
- **自动改名桥接（topout.py，additive）**：直接命中不到的 Topout 名 → **链式回溯** 改名表（**level_shift → dft → logic 可多跳**，带环/深度8护栏）到真正的 logic/mux/寄存器源，拿逻辑/驱动，但**断言探针贴顶层真名**（不是源名）。
  - dft 改名：`_dft_rename_map`（dft 行输出 ≠ 唯一功能输入基名 = 改名）。
  - level_shift 改名：`_ls_rename_map`（顶层 `_ls` 口 → 输入源名；补 `_apply_level_shift` 对不上的「源是再上一层改名」）。
- **真表实证**：唯一未解析 `d_vco_en_faston_ls` = `level_shift ← d_vco_en_faston ←(dft) logic d_vco_en_faston_fsm`，现自动解析为 logic 源、`.sv` 断言贴 `ENV_RF.d_vco_en_faston_ls`（非 `_fsm`、非中间名）。
- **安全**：正常 level_shift（源直接是 logic 输出，`_apply_level_shift` 设 `_ls_name`）走老路不经桥接；无改名表逐字节不变。改名 mux（无单一 node）暂记账（少见）。
- 测试 `tests/test_topout_rename.py`（6）：dft 改名 / 链式 ls→dft→logic / 探顶层名 / 对照零改名 / GUI 编辑回流。

---

## ⭐ 第二轮（2026-06-24）：从『可用』到『全功能』——6 缺口全补

上一轮交付『端到端可用』后判定是半成品，本轮全部补齐（架构=可复用 `SignalView` 控件，Topout 与子视图共用）：

1. **Topout 真值表可编辑**：清零 / 加列 / 复制列 / 删列 / 改输入值(auto_out 即时重算) / 改期望 / 加删负向 / 重命名列 / 重新生成；编辑经 `vector_overrides`+`reg_overrides`+`mux_*` 回流到预览/导出的 `.sv`。
2. **信号选择面板补齐**：owner 多选筛 / 分类筛 / 状态筛 / 搜索(名·式·输入名,正则) / 全选 / 清空勾选 / 勾选选中行 / 逐信号『负向』勾选列（在『选』旁边）。
3. **新增 4 个子视图**：外层 6 标签 = `Topout 视图`(默认) + `logic 视图` + `mux 视图` + `dft 视图` + `iddq 视图` + `排查(旧)`。子视图 = **页本地·不跨页 cone**（force 级联=只看本模块的输入输出，不递归代入上游），各自有可编辑真值表 + 导出。
4. **覆盖度迁出右上角** → 每个视图的筛选工具条里，旁边 `?` 讲清 logic vs mux 各自怎么展开。
5. **展开链总显示**：单级 logic 信号也显示『原式 / 字母代入真实信号名』（不再是『无上游可展开』占位）。
6. **CLI `--page`**：`--page logic|mux|dft|iddq` headless 跑子视图（`--list`/`--report`/默认出 `.sv`），与 GUI 子视图同口径。

---

## 1. 一句话

「要验信号 = Topout 页 B 列（顶层真名，~211 个）」这个新模型，已从**数据层**一路接到 **GUI（新默认门面）+ CLI（`--topout`）**：打开 GUI 默认就是 Topout 视图，载表→看要验信号清单+分类→选一个看跨页 cone 展开链+真值表+账目→预览/导出 `.sv`、导出报告、回填 for_test。断言贴 Topout 顶层真名（无前后缀），prefix/suffix 整类问题（R32/34/38/40 死磕四轮）在源对象展到底后消失。

---

## 2. 怎么用

### GUI（`python -m dreg_verify.gui`）
1. 打开即停在 **「Topout 视图」**（外层第一个标签，新默认门面）。
2. 点上方 **「加载」** 载入 Excel（真表 或 `mirror_btlp_dreg.xlsx`）。
3. 左侧列出 **Topout B 列要验信号 + 分类**：选路/logic、mux、直连寄存器、RO 回读(跳过)、未解析。
4. **点一行** → 右侧：① 跨页 cone 展开链（缩进 + 原式/字母代入真信号名）② 转置真值表（行=输入+auto_out+绿色期望行，列=测试 T0/T1…）③ 顶部账目状态（分类/状态/叶子/用例）。
5. 底部按钮：
   - **设置探针前缀…**（第四轮）—— 给 force 叶子(RO/iddq 门)或顶层探针配层级路径（埋子模块的网，否则 `ENV_RF.<裸名>` CUVUNF）；per-net 逐条配 / 可导入 scan_rtl 的 `probe_prefixes.txt`；与「排查(旧)」共用同一份映射，改完本视图自动重算。
   - **预览选中.sv** —— 勾选的（未勾任何=全部）信号生成 .sv，显示在「.sv 预览」内层页。
   - **导出 .sv…** —— 同上 + 写盘（勾选过滤生效）。
   - **导出报告(HTML/CSV)…** —— Topout 限定报告（也支持 `.xlsx`）。
   - **回填 for_test…** —— 把真值表按 for_test 排版回填到新 Excel（**含 mux 表**）。
6. **「排查(旧)」**（外层第二个标签）= 原 logic/mux 全套 UI（前缀/后缀/级联/top_output 筛那套住这儿），降级保留、没删。

> 覆盖度下拉在 Topout 视图右上（精简/全面/穷举，默认全面）。`上限` 用的是「排查(旧)」那个 spinbox（共享）——在旧标签改了上限、切回 Topout 视图会自动按新值重建清单。

### CLI（headless，可脚本化）
```bash
python -m dreg_verify.cli --excel 真表.xlsx --topout --list      # 列要验信号+分类
python -m dreg_verify.cli --excel 真表.xlsx --topout --account   # 账目(默认不空, 堵 top_out=0 假警告)
python -m dreg_verify.cli --excel 真表.xlsx --topout --out tc.sv # 出 .sv (断言贴顶层真名)
python -m dreg_verify.cli --excel 真表.xlsx --topout --report r.html  # 报告 .html/.csv/.xlsx
# 旧路径不带 --topout，行为逐字节不变
```

---

## 3. 架构 / 代码在哪

**数据层引擎** `dreg_verify/topout.py`（全 additive，不碰旧路径）：
- `read_topout`/`TopoutSignal`（在 `excel_model.py`）→ `wb.topout`。
- `resolve_root(wb, name)` → 5 分类（LOGIC/MUX/REGISTER/RO_READBACK/UNRESOLVED），靠 out_base/_ls_name/rtl_base/rtl_base_full 候选命中。
- `analyze_signal` → 复用现有 `expand_signal`/`expand_mux_group` cone 引擎 + `vectors.py` 出真值表；**永不抛**（异常记 error）。
- `build_for_topout(...)` → 以 Topout B 列为外层产 **.sv 块**（复用 `generator.build` 过滤+B列序+owner覆盖；register 平凡 passthrough；RO/未解析/dup-source 块顶注释**记账不静默丢**；标号全局唯一）。`only=` 参数支持勾选过滤。
- `render_topout_sv` = build + `sv_writer.render_file`。
- `topout_view_models(...)` → 每信号一个视图模型（GUI/测试消费）。
- `topout_report(...)` → `write_report` 兼容 dict（全 Topout 限定，summary/verifiability 来自 `compose_topout_account`，tables 来自 `report_for_topout` 含 mux + register 平凡表）。
- `compose_topout_account`/`report_for_topout`/`topout_fortest_rows` + `validate_against_golden`（判据一，对 for_test 金标准）。

- `resolve_root` 改名桥接 + `_dft_rename_map`/`_ls_rename_map`/`_rename_map`（链式回溯，第三轮）；`_topout_probe_block`（改名/register 根：驱动来自源、断言贴顶层名）。

**数据层 子视图引擎** `dreg_verify/pageviews.py`（第二轮新增，页本地·不跨页 cone）：
- `page_signals/analyze_page_signal/page_view_models`（force 级联=不递归代入上游）；`build_page_sv/page_report/page_fortest`（复用 `generator.build/report`，dft/iddq swap `wb.logic`，mux signals 过滤）。
- dft/iddq 页由 `excel_model.read_logiclike_page` 读成 `LogicSignal`（D=输出/E=式/A-C=输入）→ `wb.dft_rows`/`wb.iddq_rows`。

**CLI** `dreg_verify/cli.py`：`--topout`+`cmd_topout`；`--page logic|mux|dft|iddq`+`cmd_page`（页本地，第二轮）。

**GUI** `dreg_verify/gui.py`（第二轮重构）：
- 可复用控件 **`SignalView`**（Topout + 4 子视图共用）+ `_TopoutProvider`/`_PageProvider`（provider 决定 cone vs 页本地）。
- 外层标签：`Topout 视图`(默认) + `logic/mux/dft/iddq 视图` + `排查(旧)`。
- 真值表编辑（`_e_*`/`_on_truth_item`/`_cols_to_vectors`/`_mux_derive`/`_compute_edited`）→ `vector_overrides`/`reg_overrides`/`mux_*`/改名走 reg 路回流导出。
- 旧 `topo_*` 属性/方法保留为 `SignalView` 别名/委托（旧 Topout 测试零改动）。
- 清单表列常量：`TOPO_*`（0=选/1=负向/2=信号/3=owner/4=分类/5=状态/6=用例）/`SV_HEADERS`/`TOPO_KIND_LABEL`/`TOPO_STATUS_LABEL`。

**探查脚本（都在 `tools/`，只读真表、不改文件）**：`diag_topout_rename.py`（未解析名在哪页改名）；`diag_one_topout.py`（单信号 10 段深挖，第四轮）；`diag_name_collisions.py`（全表撞名系统性审计，第四轮）；另有历史 `diag_signal_expand/diag_to_logic_suffix` + `audit_*` + `inspect_*`。

**测试**：`test_topout.py`(42)、`test_topout_cli.py`、`test_topout_gui.py`、`test_topout_rename.py`(11)、`test_pageviews.py`(11)、`test_pageviews_gui.py`、`test_pageviews_cli.py`、`test_vba.py`（inspect_vba 已挪 tools/，path 已修）。共 `788 passed + 1 xfailed`。

---

## 4. 仍开 / 下一步（按优先级）

1. **真表上跑全量验证（用户验收）**—— diag 已确认 211 信号全部能解析（unresolved 0）+ 撞名审计 0 残留 + `d_vco_en_faston_ls`/`d_bt_lp_pmu_test_en` 两个 .sv 已逐项核对对上 for_test 金标准。下一步是真机打开 GUI Topout 视图载真表，逐信号肉眼核对真值表/`.sv`，挑几条仿真跑通；仿真若报 force 叶子 CUVUNF→用「设置探针前缀…」配层级路径。性能（211 信号 × 深 cone × openpyxl 慢载）留意。
2. **ISO/iddq 当 cone 一级**（`test_iddq_as_a_cone_level` xfail）—— 改公共行为，按护栏单开 additive。
3. **改名 mux**（`renamed-mux` 记账）：dft/level_shift 改名指向 mux 源时（无单一 node）暂记账未渲染；真表里没出现（改名源都是 logic）。真碰到再补 mux 探针名覆盖渲染。
4. **多视图共享 `_append_to_logic` 标志**（对抗 review Finding，低风险未根治）：各视图各建 Resolver 会回写 `wb.logic/mux` 的 `_append_to_logic`，但 build/report 导出前都从 GenOptions 重建 resolver→导出自愈（已验逐字节）。根治=把标志移出共享对象，留待单开。
5. **bit-split 验证侧 fix B**（minor，只影响判据一对照、不影响 .sv/报告）。

---

## 5. 坑 / 注意

- **旧路径神圣不可变**：generator.build/report、旧 CLI 默认、`fortest_writer.write_fortest`（默认 `include_mux=False`）逐字节不变是硬护栏。改 topout.py/pageviews.py 随便，碰 generator/旧 CLI 要重新 diff。
- **改名桥接只兜「源是再上一层改名」**：正常 level_shift（源直接 logic 输出，`_apply_level_shift` 设 `_ls_name`）走老路、不经桥接；无改名表逐字节不变。
- **Topout/子视图 Resolver 现吃 `probe_prefixes`**（第四轮）：仍不吃旧视图的后缀/级联（那些只属「排查(旧)」），但前缀（force 叶子/探针层级路径）已接入、三视图共用同一份 `self._probe_prefixes`（按表路径持久化）。`name`(查找 key)与 `disp`(显示名带切片) 分离——动显示别动 key。
- **撞名是这张表的常态、不是 bug**：register→logic算→dft观测→ls缓冲、同名贯穿全程。危险只在「dft【带门】观测撞同名寄存器、且判成直连寄存器」→ 漏 iddq（已修两变体）。改 `resolve_root`/`analyze_signal` 的门控逻辑前先跑 `diag_name_collisions` 看有没有引入新 ⚠。
- **error/skip/unresolved 信号一律非可编辑**（`editable=""`）：防点选 error mux（expansion=None）崩。
- **逐信号编辑随换表清空**（`on_load` 清各 `SignalView.edits`）：防上一张表编辑按同名串进新表。
- **offscreen 截图中文成方框**：缺 CJK 字体，版面对、文字靠 `widget.text()` 断言；真机正常。
- **对抗 review 已跑两轮**：第一轮 ultracode 18 agent（`92da327`）；第二轮 4 agent（`5e4c573`，1 BLOCKER+3 MAJOR+4 minor 全修）。

---

## 6. 关键 commit（origin/main）

**第四轮（撞名收口 + 前缀 + 整理）**
- `68954b5` 探查脚本 `tools/diag_one_topout.py`（单信号深挖）
- `ffc4dec` 修撞名变体1（改名带门 `d_vco_en_faston_ls`）：桥接越过同名寄存器 + 叠 iddq 门
- `f04566e` 探查脚本 `tools/diag_name_collisions.py`（全表撞名审计）
- `f393f7e` Topout 数据层接入 `probe_prefixes`（force 叶子 + 顶层探针）
- `f95e2d7` GUI 接入探针前缀（Topout + logic/mux/dft 页视图，「设置探针前缀…」按钮）
- `32a9699` 修撞名变体2（同名带门寄存器 `d_bt_lp_pmu_test_en`）+ 审计去误报
- `4d80ca4` 整理仓库：脚本归 `tools/`、文档归 `docs/`、删过期 html
- `f90d4d9` Topout 信号清单显示带位宽切片（`aac_ctf_bit_sel[2:0]`）  ← **HEAD**

**第二/三轮**：`1d8a9d5`(pageviews) / `f5c55f8`(SignalView+全功能) / `7735cec`(子视图) / `6c888a5`(CLI --page) / `5e4c573`(对抗review) / `1a11c89`(dft 改名桥接) / `17895b1`(改名扩 level_shift+链式)。
**第一轮**（数据层→GUI/CLI）：`7253d20`/`29a64f6`/`c2aaed7`/`92da327`，起点 `43e7ad7`。
