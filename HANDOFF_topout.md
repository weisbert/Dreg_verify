# 交接：Topout-rooted 验证（GUI/CLI 端到端）

> 状态：**端到端可用 + 全功能化，已 push**。HEAD `5e4c573`（= origin/main），`775 passed + 1 xfailed`。
> 旧 logic-rooted CLI/generator 输出**逐字节不变**（已显式 worktree diff `92da327` pre vs post 验过 mirror btlp+wl 的 .sv+report）。
> 日期：2026-06-24。验证夹具：`mirror_btlp_dreg.xlsx`（7 真族金标准）。真表：`Hi1108_Pilot_BT_LP_DREG_95P_20260623.xlsx`（公司机 `D:\Onebox\Code\Dreg_verify\excel\`）。

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

**CLI** `dreg_verify/cli.py`：`--topout` flag + `cmd_topout`（在 `_dispatch` 里 wb 载入后第一个分支）。

**GUI** `dreg_verify/gui.py`：
- `_build_ui` 重构：共享路径栏之上 → 外层 `self.main_tabs`（tab0 Topout 视图 default / tab1 排查(旧)=原 UI 整体搬进 `legacy_lay`）。
- `_build_topout_tab` / `_refresh_topout`（on_load 末尾调）/ `_topo_*` 渲染 / `on_topo_preview/export_sv/export_report/fortest`。
- Topout 视图列常量：`TOPO_HEADERS`/`TOPO_*`/`TOPO_KIND_LABEL`/`TOPO_STATUS_LABEL`。

**测试**：`tests/test_topout.py`(41)、`tests/test_topout_cli.py`(8)、`tests/test_topout_gui.py`(16, 无头 Qt + 截图)。

---

## 4. 仍开 / 下一步（按优先级）

1. **在真表上跑全量**（唯一真·前置）—— mirror 已够验所有 GUI/CLI 功能，但 ~211 真信号的完整 Topout dump 只有公司机能跑。打开 GUI 点 Topout 视图载 `Hi1108_Pilot_BT_LP_DREG_95P_20260623.xlsx`，或 `--topout --account`，看分类分布/有没有大批 unresolved/性能（211 信号 × 深 cone × openpyxl 慢载）。回报。
2. **ISO/iddq 当 cone 一级**（`test_iddq_as_a_cone_level` xfail）—— 要扩 `read_logic` 后缀剥离 + `cone._find_logic` 搜索 + `resolver._logic_outputs` 索引，属【改公共行为】，按护栏单开 additive 扩展。
3. **bit-split 验证侧**：`evaluate_at` 已按 slice_lsb 取位（fix A）；`read_fortest_golden` 不暴露切片导致同寄存器多切片金标准行 overwrite（fix B）未做 = minor，**只影响判据一对照、不影响 .sv/报告**，真表跑到再说。
4. **scope** 已按 goal 拍成 additive 两标签（Topout 上位、旧降级保留正确），不再悬。

---

## 5. 坑 / 注意

- **旧路径神圣不可变**：generator.build/report、旧 CLI 默认、`fortest_writer.write_fortest`（`include_mux=False` 默认）输出逐字节不变是硬护栏。改 topout.py 随便，碰这些要重新 diff。
- **Topout 路径用干净 cone 默认**：`build_for_topout`/`topout_view_models`/`topout_report` 内部自建 `Resolver(wb)`，**故意不吃**旧视图的前缀/后缀/级联设置（那些只属「排查(旧)」）。
- **register 裸名位宽**：Topout B 列若写裸名（无 `[msb:lsb]`）但字段是多 bit，引擎按 regmap 字段全宽验（`root.obj.bit_msb/bit_lsb`），并在 issues 里标「按字段全宽验」——别当假绿警告，是对的。
- **offscreen 截图中文成方框**：缺 CJK 字体，版面对、文字靠 `widget.text()` 断言；真机正常。
- **对抗 review 已跑**：ultracode 18 agent，8 确认全修（2 major + 5 minor + 1 nit），见 `92da327` commit。

---

## 6. 关键 commit
- `7253d20` 数据层 .sv + 视图模型（build_for_topout / topout_view_models）
- `29a64f6` CLI `--topout` + topout_report
- `c2aaed7` GUI Topout 主视图 + 旧视图降级『排查(旧)』
- `92da327` 对抗 review 修复（2 major + 5 minor + 1 nit）+ 回归  ← **HEAD**
- 重构起点 `43e7ad7`，数据层 DoD 完成 `97ab6a2`。
