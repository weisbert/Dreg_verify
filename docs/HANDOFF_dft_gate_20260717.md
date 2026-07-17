# 交接 · dft 门控去猜化轮（2026-07-17）

**当前 HEAD = `8969010`；工作树干净；873 过 + 1 xfail（`.venv/Scripts/python.exe -m pytest tests -q`）。**

## 这一轮做了什么（都已 push）

| commit | 内容 |
|---|---|
| `3bde25d` | 修真 bug：dft 页同一 logic 源**扇出两行**（带门→`d_en_cnt`→ls / 透传→`d_en_cnt_to_crg`），透传支走改名桥接后被 out_base 兜底**串上兄弟行的 iddq 门**（假 iddq 输入 + 假 DFT 拍）。修=`resolve_root` 记 `dft_bridged`，途经 dft 改名边时抑制 out_base 兜底。 |
| `c45b2a2` | Fable 对抗评审 8 条 🔴 的 Excel 侧修复（下详）。+10 `tests/test_dft_gate.py`。 |
| `d4f27cf` | `tools/probe_signal_rows.py`：dump 某族信号在真表每页的原始行 + 工具判定，供红区探查回贴复现（真表 .xlsx 保密传不出）。 |
| `2e116c4`→`8969010` | 误加/撤销 `diff_mode_regs.py`（别的项目 Reg_tester 误粘贴），已删除。 |

### `c45b2a2` 具体修的（评审 🔴，全在 `topout.py`/`excel_model.py`/`generator.py`/`gui.py`）
- **A2** `read_dft` 按 E 条件字母取门列（新 `_parse_dft_gate_expr`）——此前盲取物理 B 列，`E="A?0:B"` 门/功能对调 = 真源不验。
- **A3** 门形态容差（位切片/括号/`!~`取反/定宽零）+ 含 `?` 认不出 → `wb.dft_unrecognized` **loud 记账**（此前静默漏门）。`B?1:A` 压 1 门语义未确认，不猜。
- **A4** 同名多条带门行 **first-wins** + `wb.dft_dups` 告警（对齐 R35 regmap 口径；此前 last-wins 静默）。
- **A1** dft **G 列（去向）首次消费**：out_base 兜底套门前交叉核对带门行去向与本支（经 level_shift ⇔ `dest∈{to_ls,ls}`），不一致 → 不套门 + ⚠门归属存疑。堵 `3bde25d` 未覆盖的直接命中/`_ls` 命中支串门。
- **A13** 门键政策 6 处散置**收口成 `topout._gate_obs_for` 唯一出口**（analyze 三分支 + `_result_gate_info` 全走它）。
- **A6** iddq 已是 mux 显式输入时不再 pin/补拍（build/report/topout/GUI 四处同步）——此前同拍同网 force 两次 = 选路拍假红。

## 关键产物
- **`docs/主线逻辑设计review_20260717.md`** — Fable 全评审报告：A 猜点清单 / B 语义模糊清单 / C 暴雷预测 / D 治本插桩方案 / 附录（实验）。**下一步的主索引。**
- `tools/probe_signal_rows.py` — 红区探查（`python tools\probe_signal_rows.py 真表.xlsx <子串...>`）。

## 已验证（真表 `excel/Hi1108_Pilot_BT_LP_DREG_100P_20260716.xlsx`）
designer main 页行25 规则「从 regmap 到小数字的信号去 iddq 钳位」点名 3 信号，工具产出**全部无 iddq、意图一字不差兑现**：
- `d_en_cnt_to_crg`（dft 扇出透传支，源=3 输入组合 → 6 tc）✅
- `datapath_clk_en_ls`（dft 单行透传 E=A，源=3 输入组合 → 6 tc）✅
- `d_ndiv_cnt_div_sel_ls[1:0]`（dft 单行透传，源=2bit 寄存器透传 → 3 tc）✅
- 对照 `d_en_cnt_ls`（带门支，未点名）保留 iddq ✅
两轮修复经真表数据反证正确。

## ⚠ 关键定调（影响下一步优先级）
**红区 RTL 层 = debug 路由，不是主设计路径；Excel 侧自洽 = 主线**（用户 2026-07-17 定）。
→ R41「scan_rtl 真 nets 当裁判 / GATE」**暂不做进主线**；治本改走「读表（如 dft G 列）+ 读不出 loud 记账 + 语义找 SE 确认」。

## 下一步可选（未做，按你挑）
**🟡 评审剩 5 条**（详情在评审报告 A 节）：
- A7 `build_index` 候选名撞车 setdefault 静默（验错行=假绿，无告警）
- A8 直接命中静默压掉 dft 改名边（同名双源，logic/mux 侧无 R-vco-faston 的续走逻辑）
- A9 `pin_dft_gate` 门非 RO 时静默不钉（append 侧有 iddq_skipped ⚠、pin 侧没有）
- A11 Topout 页同名重复行 → 重复 assert id = **非法 SV**（elaboration 才炸），`dup_labels` 不报
- A12 `make_negative` 不拷 `extra_forces` → 带门根的负向拍丢门 pin（现被 force 粘滞 + 排序不变量掩住，用户重排向量即爆）

**B 节语义清单转 SE 确认**（把猜变成问）：门判据三条 / dft G 列确切语义 / 那批 `E=A` 恒等行是否有意不门控（R37 旧账）。

**2 个 heads-up（非 bug）**：
- `d_ndiv_cnt_div_sel_ls[1:0]` 2bit 只测 3 码（`00/11/01` 缺 `10`，CONE-2 覆盖度）——要验全 4 分频码对它单设穷举档。
- `datapath_clk_en_ls`/`d_ndiv_cnt_div_sel_ls` 的 `_ls` 名不在 level_shift 页、取自 dft 透传行 D 列——仿真确认 RTL 有该网。

## memory（下轮自动加载）
`r-dft-fanout-false-gate.md`（起因 bug + 真表验证）、`r-dft-gate-deguess.md`（评审 8🔴 修复）、`goal-redzone-binder.md`（红区降级定调）。
