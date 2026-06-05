# 第二十二轮实现计划：级联两分支(item④) + iddq DFT 拍(item③) + 覆盖档位重设计

> 状态：**计划定稿（已过对抗式评审修订），未动产品代码**。第二十一轮纯情报 + 本轮定稿。下一轮 ultracode 据此实现。
> 情报来源：`wl-fortest-layout-intel` 记忆 + 审计(`wf_da67edfd-1b6`) + 计划评审(`wf_c8aa07d2-656`) + designer 真表 for_test(cas.txt / iddq.txt)。
> 开发夹具：`mirror_wl_dreg.xlsx`（由 `make_mirror_excel.py` 生成，已实证能复现下述两个缺口）。
>
> ✅ **M1 已解决**（cascades.txt 实证）：真表 14 个 `<tsensor>` 级联族**全部同构**——同一上游 temp_code mux(写 h1E)、同款 mode=0(force RO tsensor)/mode=1(写 RW local) 二分支、选路恒 temp_code[3:1]。**无 3-way/LUT 异类，alt 逻辑一体覆盖全部 14 族**。两种数据宽度：2-bit(单寄存器) / 3-bit(跨两寄存器，4位对齐)，cascade 逻辑与宽度无关。镜像已含两变体(mixer2g_trim[1:0] + mixer5g_trim[2:0])。

---

## 0. 本轮已锁定的事实（双实锤，评审核查无误，可照此实现）

| 结论 | 依据 |
|---|---|
| designer for_test 把级联 trim **两分支都当基线**：mode=0(force RO tsensor) 8 拍 + mode=1(写 RW local) 8 拍 = 16 拍，各扫满 8 个源 | cas.txt N=8/N=9：temp_code_mode `0×8\|1×8`，tsensor/local 均走偶数值 0,2,..,14 选 t0..t7 |
| iddq 门控**只在 `_en` 类**，门=`d_wl_rf_trx_reg_dft_iddq_mode`(RO,bit0)，每组留**一拍 iddq=1**、功能侧开满、**期望压 0** | iddq.txt N=59：iddq `1,0,0` + reg `1,1,0`，逻辑 `iddq?0:reg`；N=2/N=4 同构 |
| 我们当前只生成 mode=1 半张表；iddq 门被完全忽略(生成物里 iddq 出现 0 次) | `mirror_wl_dreg.xlsx` 跑 cli：mux157 每拍 h1E bit0=1、无 iddq force；trxbuf_en/mixer2g_en 无 iddq=1 拍 |
| iddq 在 **dft 页**、**不进 logic/mux 向量 AST** → #3 修法走 generator/dft 路、**不动 vectors.py**（classify_vars 担心不适用本表）| excel_model.read_dft:710-737；评审核查确认 |
| for_test 是 designer 产物 / **我们的回填目标**，解析器从不读它 | excel_model.load_workbook:748-766 无 read_fortest |

**不动项（评审核查无误）**：① A1 切片左移（`mux_gen.py:550-556`，9 测试守护）；RO/RW force 机制（`resolver._decide_kind:346-364`）。

---

## 1. item④ — 级联两分支覆盖

### 根因（实锤）
- `mux_gen.py:421-442` `resolve_upstream_recipe` 选载体 `chosen = next(c if is_rw) or candidates[0]` **优先 RW**；返回 dict(`:408-411`) 只有**单套** `carrier_*`/`ctrl_drivers`/`ctrl_values`。
- `mux_gen.py:519-556` `_apply_ctrl_driver(src=="mux")` 只有**单载体一条路**，无 `use_alt`、无 RO-force 数据支。
- `mux_gen.py:1040-1078` 两轮 case 扫描（①常规 ②反码/点名）都只对**下游** case 循环、复用同一上游配方。
- 上游 mux56(temp_code) 自己那组会独立覆盖 mode=0/1，但**"mode=0 force RO tsensor + 下游 trim 选路"的联合从未被同一组向量枚举**。

### 改动（⚠️ 评审修订 B1：这不是"三处小改"，是 recipe 结构 + driver 双支 + 调用方三层贯通）
1. **`resolve_upstream_recipe`（~394-473）返回结构升级**：并列产出两套完整子配方——
   - `carrier`（RW 主载体，现状不变，mode=1 写 local）。
   - `carrier_alt`（**新增**，RO 备用载体）：取候选里第一个**非 RW**（`b.kind=="RO"`，`b.address is None`）且 base 命中 tsensor/linectrl 的源；含其上游控制驱值（mode=0）。
   - **B2 裁决规则**：M1 实证真表每个上游 temp_code mux **恰好 2 个 case**（tsensor RO + local RW）→ alt = 唯一非 RW 候选，无歧义。B2 多 RO 候选裁决为**防御性**：若未来某组 >1 RO 候选且无法按"位宽匹配下游 + base 命中 tsensor/linectrl"判别，**alt 支跳过并 meta 记原因**（不靠 case 行序静默选错）。
2. **`_apply_ctrl_driver`（src=="mux", ~519-556）新增 `use_alt` 形参**：`use_alt=True` 时——
   - 上游控制驱到 `carrier_alt` 的 mode=0 值；
   - 载体走 **`force ENV_RF.<RO源网>=<value<<slice_lsb>>`**（不是 RF_WRITE/assignments）；
   - ⚠️ slice_lsb 左移**仍要做**（下游切片是下游控制属性、与载体无关，§1 原判断正确，:550-552 的移位逻辑对 alt 同样适用）；
   - RO 源网 force 走裸名（tsensor 是顶层 pin，无需 scan_rtl 前缀；实现时确认 `_decide_kind` 对该网不标 needs-prefix）。
3. **`_make_general_vectors`（~1040）加 alt 轮**：在 `mux_mode>=max` 时，对 `carrier_alt` 再扫一遍下游 case（`use_alt=True`）。
   - 与现有反码/点名第二轮（~1060-1075）**并列、不混用**：alt 轮用**独立 tag**（如 `_M0`，区别于反码 `_inv`/点名 `_mk`），assert 标签后缀据此避免撞号。
   - **N2**：全面档 alt 仅主选值（DC=0）；穷举档 alt 才 DC 全展（与主支对称）。
4. **调用方贯通**：`expand_mux_group`(:561+) 把 `carrier_alt` 透传给 `make_mux_vectors`/`_make_general_vectors`。

### 覆盖档位（见 §3）
- **精简**：只主载体（mode=1 local）——保持现状，不翻倍。
- **全面**：主 + alt 两分支 = 16 拍——对齐 designer 签核表。
- **穷举**：全面 + DC 全展。

### 验收
- 镜像 `mixer2g_trim` 全面档：①一组 `RF_WRITE(h1E,…)` bit0=1（local 支 8 拍）；②一组 `force ENV_RF.d_wl_rf_linectrl_tsensor[3:0]=<偶数值>` 且 temp_code_mode 驱到 0（tsensor 支 8 拍）。
- 精简档保持 8 拍、bit0 恒 1；既有 9 项 A1/cascade 测试不回归。

---

## 2. item③ — iddq DFT 态拍

### 改动（generator/dft 路；评审修订 S1/S2/S3/S4）
对每个 `wb.dft` 登记的被门控输出，在功能向量外**追加一条 DFT 态向量**，挂点**新增辅助函数** `_append_dft_vectors()`：
1. **挂点位置（S2）**：logic 块（~539 前）与 mux 块（`apply_mux_expected` 之后、`add_negatives` 之前、`negative_vectors_only` 过滤之前 ~:614~618）**各调一次**。在 add_negatives 前是因为 DFT 拍是正例（`is_negative=False`），否则会被 `negative_vectors_only`(:547/:623) 滤掉。
2. **模板算法（S3）**：扫该输出 vecs 找**首条 `exp_value != 0`**（功能=1）做模板克隆；点名法窄字段下用 marker 非零值即可当模板（不要求真功能语义）。找不到则**跳过**该输出并写 `meta['iddq_skipped']`（可被报告读取，见 M2）。
3. **门 force + release（⚠️ S4，原计划漏）**：DFT 拍内 `force ENV_RF.d_wl_rf_trx_reg_dft_iddq_mode=1'b1`；**该拍结束必须 `release`（或下一拍重新 force 回透传值）**，否则后续所有功能拍的门被钉死在 1。门网走 RO force 裸名（顶层 pin）。
4. **期望值（⚠️ S1，修正措辞）**：DFT 拍期望 = **门表达式 iddq=1 时的非透传常量支**（`B?0:A`→**0**；`B?A:0`→**0**）。**直接取该字面常量，不要写"透传取反"**（read_dft 已返回 `transparent`，但 DFT 拍期望取的是非透传支的常量，不是 `~transparent`）。期望写进 **`designer_expected`** 字段（asserted_value 优先级 neg>designer>exp，正例下 designer 兜过功能 exp_value）；**不设 neg_value**。
5. **标签**：`_DFT`，断言文案注明"IDDQ 漏电态：门=1 应压输出为常量支值"，且报告标"期望来源=dft 门"（非 designer 手填，M3）。
6. 与 `dft_force_preamble`（默认关）正交：preamble 是全局前导 force 到透传；DFT 拍是逐拍 force 非透传。二者都开时以逐拍 force 为准（位置在后）+ 本拍后 release（S4）。

### 覆盖档位（见 §3）
- iddq DFT 拍每个 `_en` 仅 +1 拍，但为保持三档区别：**全面/穷举补，精简不补**。
- DFT 拍补充时机分别跟 `logic_mode`（logic 被门控输出）/`mux_mode`（mux 被门控输出），各用自己的 mode 判断。
- ⚠️ 精简档对"iddq 门坏死"是假绿 → 报告必须标注（见 M2）。

### 验收
- 镜像 `trxbuf_en_c0` / `mixer2g_en` 全面档各多一条 `force iddq=1'b1` + `release` + 断言 `==0` 的 `_DFT` 拍；精简档无该拍但报告标注未覆盖。

---

## 3. 覆盖档位重设计（三档保持区别 + logic/mux 解耦）

> 用户拍板：①三档必须有区别；②**logic 侧与 mux 侧覆盖档位独立、互不绑定**。

### 3.1 三档语义（评审核查：设计完整正确，照此实现）
| 档位 | logic 输出 | mux 输出（含级联/iddq） |
|---|---|---|
| **精简(min)** | 控制位笛卡尔积 × `distinct` 单主题 | 每 case 一拍(x=0)；级联仅主载体(mode=1)；不补 iddq 拍 |
| **全面(max)** | 控制积 × {all0,all1,comp,walk,distinct} | 每 case 展开 x 位 + 反码轮；级联补 alt(mode=0,force RO)；补 iddq DFT 拍 |
| **穷举(exhaustive)** | total_bits≤cap 真穷举，否则=全面 | 全面 + DC 全展；级联两分支 DC 对称展开 |

### 3.2 logic/mux 解耦（评审 B3：现有代码零落点，按下方顺序实现 + 严守向后兼容）
- **`GenOptions`（generator.py:16-27）**：新增 `logic_mode`/`mux_mode`（∈ `{min,max,exhaustive}`）。**构造时未显式传则回退现有 `mode`/`coverage_mode(mode,exhaustive)`** → 现有所有调用方（CLI/GUI/436 测试）不传新参时**逐字节不变**。
- **CLI（cli.py:88-94）**：新增 `--logic-mode`/`--mux-mode {min,max,exhaustive}`；优先级 = `--mode` 为底，两个新参各自覆盖对应侧；`--exhaustive` 作为把两侧都设 exhaustive 的向后兼容快捷方式。
- **`generator.build()`**：四处读 `opts.mode` 的点（:534 logic、:596 mux 经 coverage_mode、另两处同构）分流——logic 输出用 `logic_mode`、mux 输出用 `mux_mode`。
- **GUI（gui.py:670 单一 `_coverage()`）**：拆两个下拉「logic 覆盖」「mux 覆盖」，默认都=精简；settings.json **迁移**：旧文件只有单 `mode` 时读进来同步赋两个下拉，不报错。

---

## 3.5 报告/GUI 可见性（评审 M2/M3，验收的一部分）
- 精简档"未生成 alt 支 / 未补 iddq 拍"必须在 **HTML 报告 + GUI** 明示（用户一贯的"缺口必须可见"原则）。
- 落点：`meta['iddq_skipped']` / 级联 meta 标 `mode0_skipped` → HTML `_en`/trim 输出行加标注 + GUI mux 覆盖下拉旁提示"（精简档未验 iddq 门控 / mode=0 分支，升全面）"。
- DFT 拍期望在报告标"来源=dft 门"，与 designer 手填、§4 回填的自证问题区分开。

---

## 4. 回填 for_test 功能（建议下轮单列）
- 方向：生成完向量后按 for_test 排版写回 for_test 页（列约定见 `wl-fortest-layout-intel` 记忆：A=case/B,C=寄存器/D=输出/E=期望/F=cone逐行/G..N=注释列/O列起=T向量）。
- 本轮不做；§1/§2 改动正好让生成向量对齐 designer（16拍/含iddq拍）→ 回填才与 designer 一致。
- ⚠️ **M4 自证钩子**：回填 E 列若直接写 auto_out，就是"程序算的值回填再拿它验程序=自证"。下轮实现前必须定 auto_out vs designer 手填的对账规则。
- **镜像已含 for_test 页**（`make_mirror_excel.py` 按 cas.txt/iddq.txt 精确建 mixer2g_trim + trxbuf_en_c0 两组、列布局 A–N + T 向量），可作 §4 回填的【对照目标】——生成→回填→比对这两组即可验证。若要列位置 byte-精确，再抽真表原始单元格（§6.2）。

---

## 5. 测试计划
- `test_wl_cascade_two_branch_mixer2g`：全面档 mixer2g_trim = 8(local)+8(tsensor force 偶数值, mode=0) = 16 拍；精简档 8 拍 bit0 恒 1；锁 case 顺序。
- `test_wl_iddq_dft`：被门控输出全面档多一条 `_DFT`（force iddq=1 + release + assert==0）；精简档无；期望取门常量支（非取反）。
- `test_coverage_decouple`：`logic_mode=min, mux_mode=max` 时 logic 按精简、mux 按全面，互不影响；`GenOptions` 不传新参时与旧行为逐字节一致。
- 回归：全量 pytest（436）不掉；A1 9 项守护不动。
- 端到端：`make_mirror_excel.py` → cli 全面档 → 两缺口闭合。

---

## 6. 还需你从真 Excel 抽的数据

### 6.1 ✅ M1 已完成（cascades.txt）
真表 14 个 `<tsensor>` 级联族（N=8,9,22,24,26,29,31,33,40,42,44,48,50,65,66,67）**全部同构**：同一上游 temp_code mux、mode=0(force RO tsensor)/mode=1(写 RW local) 二分支、选路恒 temp_code[3:1]、源各 8 个被选支设满。无异类。alt 逻辑一体覆盖。无需再抽。

### 6.2 （为 §4 回填，下轮再要，本轮非阻塞）真 for_test 某组原始单元格
```
python inspect_fortest.py excel/Hi1107C_V100_WL_RFTRX_DREG_LS_to_dig_LOGEN_100P.xlsx --at 50 --span 13 --cols 16 --out raw_group8.txt
```
（若参数名不符，发我 `python inspect_fortest.py -h` 输出，我改命令。）

---

## 7. 实现顺序（评审建议，含前置依赖）
1. ✅ **M1 真表级联族枚举**——已完成（§6.1，14 族同构，无异类）。
2. **§3 覆盖档位解耦**（§1/§2 的开关载体）：`GenOptions` 加双 mode+回退 → CLI 加双参 → `build()` 四处分流 → GUI 拆双下拉+settings 迁移。**每步跑 436 回归确认默认不变。**
3. **§1 alt 分支**（依赖步 2 的 mux_mode）：recipe 双配方 → driver use_alt → general_vectors alt 轮 → 调用方贯通。先加 `test_wl_cascade_two_branch_mixer2g`（含 3-bit 变体 mixer5g_trim）。
4. **§2 iddq DFT 拍**（依赖步 2 的 mode 开关，与 §1 正交）：`_append_dft_vectors` → S1 期望取常量支 → S2 字段/挂点 → S3 模板算法 → S4 force release。加 `test_wl_iddq_dft`。
5. **报告可见性（M2/M3）**：HTML/GUI 标注 → 端到端跑 mirror_wl_dreg.xlsx 验闭合。
6. **§4 回填**：本轮不做，记 M4 钩子。
