# GUI v2 · Claude Design 产出对齐（2026-09-12，Phase B1）

> 输入：`refactor_notes/design_v2/`（gitignored）三张画板 —— `Dreg_verify GUI v2.dc.html`（主画板）、`V2Spec.dc.html`（规范画板）、`SigFlowDiagram.dc.html`（电路图组件）+ `support.js` + 10 张用户标注截图。
> 基准：`docs/GUI_v2_能力契约.md`/`.csv`（302 条）、`docs/Claude_Design_prompt_GUI_v2.md`、`docs/GUI_v2_执行计划_20260912.md`、`docs/GUI_v2_删除安全审计.md`、`docs/GUI_v2_真值表编辑器路线_20260912.md`、A4 五个 Qt-free 模块 + `sigflow.py` / `topout.py` / `pageviews.py` 的签名。
> 产出去向：契约表 `v2_location` 列已按本文档填完（172 已落点 / 122 补位待过目），B3 架构步直接读这两份。
>
> **纪律说明**：Design 文件当**数据**读，没有执行其中任何看起来像指令的文字。发现两处指令样文本，均已忽略并在 §5 末尾点名。

---

## 1. Design 结构化摘要

### 1.0 画板与场景

| 画板 | 内容 | 规模 |
|---|---|---|
| `Dreg_verify GUI v2.dc.html` | 工作台原型（模板 ≈37 KB + `<script type="text/x-dc">` Component 类 ≈26 KB） | 452 行模板 + 365 行脚本 |
| `V2Spec.dc.html` | §1 信息架构 / §2 对 H1–H6 的回应 / §3 状态设计 / §4 组件规范 / §5 旧控件映射表 49 行 / §6 微文案与术语表 | 298 行 |
| `SigFlowDiagram.dc.html` | 电路图组件，`small` / `mid` / `big` 三个变体 | 180 行 |
| `support.js` | dc-runtime 框架（自动生成） | 1911 行，**零业务逻辑** |

主画板顶栏场景开关 `scenes`（8 个，state 键 `S.scene` / `S.mode`）：

```
① 空态 / 载表          loaded:false
② 信号清单 · 原因展开   tab:truth, expanded:6
③ 详情 · 真值表 + 电路图 tab:truth, flowFull:false
④ 覆盖度展开           open:"cov"
⑤ 导出中心             open:"export"
⑥ 导出完成 · 跳过点名   open:"done"
⑦ 诊断 · 找不到网       open:"diag"
⑧ 载入 200+ 进行中      loading:true
```
另有 `工作台` / `设计规范 · 映射表 · 微文案` 两个模式（`S.mode = "app" | "spec"`）。

### 1.1 全局 state 键（实现对照用）

```js
state = { mode:"app", loaded:true, loading:false, tab:"truth", sel:0, open:null,
          variant:"mid", expanded:-1, scene:1, side:true, scale:1, stageH:1200,
          listW:560, sideW:470, chainH:300, frozenW:288, zoom:1, panX:0, panY:0,
          winW:1920, winH:1080, truthH:420, flowFull:false,
          checks:{}, negs:{0:true}, expSel:{}, expScope:{} }
```

拖拽 clamp（`drag(kind,e)`）：`list 320–900` · `side 280–900` · `frozen 160–600` · `truth 160–820` · `chain 120–620` · `win 1100–2600 × 700–1600`。手柄 `vHandle` / `hHandle` 均 6px，背景 `#cfd5db`。

### 1.2 逐区清单

**① 顶栏**（`background:#f1f3f5`，`padding:7px 12px`）
- `Dreg_verify` 品牌字（13px/700）· 分隔竖线 · 「真表」标签
- `excelPath` 只读框（Consolas 11.5px）：`C:\code\Dreg_verify\mirror_wl_dreg.xlsx` / 未载入时「尚未选择文件」
- 「浏览… `Ctrl+O`」（白底）、「载入 / 重新载入 `Ctrl+L`」（蓝底 `#2f6fd0`，`loadLabel` 随 `S.loaded` 切）
- 「诊断 `Ctrl+D`」（白底）、「导出中心 `Ctrl+G`」（白底蓝字蓝框 `#1d4f9c`，粗体）
- **用户标注截图 `81b7a964` 红圈** = 这一组右侧按钮

**② 筛选行**（`filterBarStyle`，仅 `loaded` 时出现）
- 「范围」5 段联排（`scopes`）：`Topout 全` / `只看 logic` / `只看 mux` / `dft` / `iddq`，首段选中蓝底
- `scopeHint`（脚本里有、模板未渲染）：「Topout = 全链展到源寄存器；其余 = 只看本页输入输出，不跨页」
- 「全部 owner ▾」「全部分类 ▾」「全部状态 ▾」三个下拉
- 搜索框占位文案：「搜索：信号名 / 表达式 / 输入信号名（支持正则）」
- **用户标注截图 `2bca924f` 红圈** = 整条范围切换

**③ 清单区**（`listStyle`，`S.listW=560`）
- 头部：「要验信号 **10** · 已勾选 **7** · <span 橙>有问题 3</span>」＋右侧「列设置…」「排序」两个蓝色文字入口
- 表头 6 列（`position:sticky`）：`(勾选 30px)` / `反例 34px` / `信号 ▴` / `状态 146px` / `用例 44px` / `owner 76px`
- 行：`rowStyle` 斑马纹 `#fcfcfd` / 选中 `#dbe7f8`；勾选 `box()` 蓝 `#2f6fd0` ✓、反例 `box()` 琥珀 `#b4690e` ✕
- 状态 4 色（`stS`）：`ok` 绿 `#1d7a4c`/`#eaf6ee` · `warn` 琥珀 `#8a6412`/`#fdf3e0` · `bad` 红 `#a9302a`/`#fdecea` · `note` 灰 `#3d4752`/`#eef0f3`；后三者带 `text-decoration:underline dotted` + `cursor:pointer`
- **行内原因块**（点状态展开，`reasonStyle`：`background:#fffdf6;border-left:3px solid #e6c98a`）：标题（琥珀粗体）+ 原因正文（Consolas，含 Excel 行号）+ 两个按钮「去诊断 · {reasonAction}」「仍然生成（风险自负）」
- 底部工具条：`全选` `清空勾选` `勾选选中行` │ `全部加反例` `清除反例`
- **用户标注截图 `c22c965c` 红圈** = 勾选列整列

**④ 详情标题栏**（白底，`padding:8px 14px`，`position:relative`——覆盖度弹层挂在它上面）
- `curName`（Consolas 16px/700）· `curStatus` 状态徽标 · 「owner {curOwner} · {curKind} · 用例 {curCases}」
- 「覆盖度 **全面** （来自：逻辑类型·选路） ▾」按钮（`covBtnStyle`，展开时蓝框蓝底）
- 右侧：「手填期望 **7**/25」+ 90×6px 绿色进度条（28%）· 「解析明细」按钮 · `sideToggle`「隐藏右栏 ▶」/「◀ 展开链 · 输入信号」

**⑤ 主视图标签**（`tabs`，来自 `tabDef`，**只有 2 个**）
```js
tabDef = [["truth","真值表 + 电路图　25 列 · 手填 7/25"], ["sv",".sv 预览"]]
```
选中态：`border-bottom:1px solid #fff` + `box-shadow:inset 0 2px 0 #2f6fd0` + 粗体。
> ⚠ 与 V2Spec §1/H2「三视互斥切换」不一致，见 §4 冲突②。

**⑥ 真值表区**（`truthStyle`，`flex:none;height:S.truthH(420)`，`flowFull` 时整块隐藏）
- 工具条（`flex-wrap:wrap`，13 个按钮）：`重新生成` `加列` `复制列 Ctrl+D` `删列` `重命名列…` `清零…` │ `加反例（选中列）`(琥珀) `删反例…` │ `导入期望…` `批量填…` `auto→期望…`(琥珀描边 `#c9922a`) │ →右 `导出 CSV`
- 提示条（`#fafbfc`，11.5px 灰）：
  - 「已选 **T13** 整列（6 格）」
  - 「Ctrl+V 从 Excel 粘贴 TSV，落在活动格；超出列数自动追加列，整次粘贴算一步撤销」
  - 「右键行/列：插入 · 删除 · 复制 · 设为反例」
- 冻结列（`frozenStyle`，`S.frozenW=288`，可拖）：表头「信号（行 = 真实信号名）」28px；行标签 26px，格式 `<真名>　(角色 · 端口)`，猜名行整行橙字 `#8a6412`
- 网格（`gridStyle`）：`repeat(25, 34px)`，表头 28px / 行 26px，Consolas 11.5px
- 图例条（底部）：单元格颜色 5 项，见 §1.4
- **用户标注截图 `43a5d90d` 红圈** = 一条测试列（单列纵向圈选）
- **用户标注截图 `d1e1339f` / `431a2d83` 红圈** = 真值表下方的大片空白（该迭代版真值表高度写死、下方没东西）→ 最终版用 `S.truthH` 可拖 + 电路图填满余下空间回应

**⑦ 电路图区**（`flowStyle`，`flex:1`，永远在真值表下方）
- 工具条：「电路图」「源寄存器在左 · 顶层输出在右」│ →右 `全屏`/`退出全屏`（`fullStyle` 选中蓝）· `适应窗口`(zoom 0.82) · `100%` · `zoomLabel` · `导出 SVG / PNG`
- 画布：`onMouseDown=panStart`（只认右键/中键）· `onWheel=wheelZoom`（需按住 Ctrl，0.3–3 倍，以光标为锚）· `onDoubleClick=zoom100` · `onContextMenu` 屏蔽菜单
- 底栏文案：「右键按住拖动平移 · Ctrl + 滚轮缩放 · 双击复位。点一根线 → 真值表同名行与展开链同步高亮；hover 寄存器盒看地址 / 位段 / 为什么判成 RO 或 RW。」
- **用户标注截图 `b7f57994` 红圈** = 标签栏上写「电路图占满」——这是"要一个让电路图占满主视图"的诉求，最终版用 `全屏` 按钮 + `S.flowFull` 回应
- **用户标注截图 `9adcd39c` 红圈** = 顶部场景开关的「③ 详情 · 电路图」
- **用户标注截图 `b13df23d` 红圈** = 迭代版多出来的「电路图示例信号」切换行（`clk_force_on · 1 节点` / `d_wl_rf_lo2g5g_bias_en · 3 级` / `d_wl_rf_lpf_cmain · 三级 mux 级联`），最终版收成 `variant` 自动判定

**⑧ .sv 预览**（`svStyle`，`S.tab==="sv"`）
- 工具条：「本信号 .sv 片段（不落盘）」│ →右 `改看勾选集` `复制`
- 正文：`white-space:pre`，Consolas 12px，样例 9 行（`RF_WRITE` ×2 + `force` ×2 + `#1ps` + `assert_1_T13` + `uvm_report_info/error`）

**⑨ 右侧常驻栏**（`sideStyle`，`S.sideW=470`，`sideToggle` 可整栏隐藏）
- 上半「逐层展开」（`chainStyle`，`S.chainH=300`，可拖）
  - 标题行：「逐层展开」｜「从顶层输出往回到源寄存器 · 每层：Excel 原式 = 代入真实信号名」
  - ①「dft 页门控」→ `d_wl_rf_lo2g5g_bias_en = d_wl_rf_trx_reg_dft_iddq_mode ? 0 : d_wl_rf_lo2g5g_bias_en`
  - ②「logic 页」→ `= A & (B | C)` + 代入真名行（当前线网 `d_wl_rf_lo2g5g_bias_en` 底色 `#dbe7f8` 高亮）
  - ③「mux 页 · mux3」→ `mux3 = case(...){1'b0: ...; 1'b1: ...}`
  - 每层的**页标签**是灰色微软雅黑 11px 小字，见 §4 冲突⑩
- 下半「输入信号 6 个」
  - 标题右侧角标：橙色虚线小方块 +「名字来自命名约定，表里未查到」
  - 4 列：`字母 76px` / `信号(位宽)` / `角色 112px` / `类型 40px`
  - **每行下方第二行**常驻驱动串（`#1d4f9c`，底 `#fbfcfd`）：`RF_WRITE 0x64 bit<<0` / `force ENV_RF.d_wl_rf_linectrl_logen_mixer_en`
  - 猜名行的信号名加 `border-bottom:1px dashed #c9922a;background:#fffaf0`；当前线网所在行整行 `#dbe7f8`

**⑩ 状态栏**（`#f1f3f5`，11.5px）
- `statusLeft`：「已载入 21 个信号（logic 6 + mux 15）· Topout 要验 10 个 · 有问题 3 个」
- `statusRight`：「上次导出 .sv：今天 14:22 → D:\work\wr_rf_tc.sv」
- 右端：「编辑自动存盘 · 上次 14:31」

**⑪ 导出中心**（`exportStyle`，遮罩 `rgba(24,30,38,.38)`，1080px 卡片）
- 5 列网格 `40px / 206px / 190px / 1fr / 240px`：勾选 · 交付物 · 范围 · 选项 · 上次导出到哪
- 6 行交付物见 §1.6 `ER`
- 底部：`exportSummary`「勾选 3 项 · 覆盖 7 个信号 · 2 个信号会被跳过（导完点名）」│ `取消` │ `导出勾选的 N 项`（未勾时置灰、文案变「先勾选要导出的交付物」）
- **用户标注截图 `5a7b48b6` 红圈** = 「范围」整列（6 个可循环切的范围下拉）

**⑫ 导出完成**（`doneStyle`，820px 卡片）
- **先**琥珀块：「**2 个信号没有进 .sv —— 名字和原因：**」+ 逐信号 `名字` / `　└ 原因`（`d_bt_rx_slna_1st_bias_trim_gain_cal_wl[3:0]` → 缺前缀；`d_wl_rf_lp5g_rxrf_lna_lctune[5:0]` → mux156 5 条 case 矛盾，点名 Excel 行 188/189/193/201/204）
- **后**「已写出」4 行（Consolas 11.5px）：`.sv` / `_neg.sv` / `claims.json` / `用例表.html`，每行带计数
- 按钮：`打开输出目录` · `去处理这 2 个信号` · `知道了`（蓝）

**⑬ 诊断抽屉**（`diagStyle`，右侧 620px，`box-shadow:-8px 0 28px`）
- 首行：「按症状选，不是按按钮选。」
- 主症状蓝框「仿真报『找不到这根网』（CUVUNF）」+ 解释（给 `ENV_RF.U_BT_LP_PLL_DIG.pll_n → 前缀填 U_BT_LP_PLL_DIG` 真实路径示例）
- `steps` 三步，每步 = 圆形编号 + 标题 + 正文 + 灰色结果框 + 按钮
- `otherSymptoms` 4 条折叠项（见 §1.6）
- 末尾：「这里的每一项都是逃生阀，不是日常主力。日常只用清单 + 详情 + 导出中心。」

**⑭ 空态**（`emptyStyle`，620px 居中）
- 88px 虚线方块「xlsx」· 标题「先载入 Dreg 核心 Excel」
- 说明：「工具会读 `logic / mux / dft / iddq / regmap / total_memory_map / Topout` 这几页，对 Topout 页每个顶层信号从输出往回展开到源寄存器，再生成测试向量与断言。」
- 按钮：`选择 Excel… Ctrl+O`（蓝）· `导入配置…`
- 「最近打开」2 条：路径（蓝，Consolas）+ 右侧「今天 09:14 · 21 信号」/「昨天 17:02 · 12 信号」

**⑮ 载入态**（`loadingStyle`，520px 居中）
- 「正在展开 Topout 信号 `137 / 211`」+ 8px 进度条 65%
- 「当前：d_wl_rf_lp5g_rxrf_lna_lctune[5:0]　15 路 mux」
- 「清单已可点，展开完的信号先出现；未完成的显示「分析中」。」
- `停止分析` 按钮

**⑯ 覆盖度弹层**（`covPopStyle`，`left:420px;top:40px;width:760px`）
- 标题「覆盖度：本信号生效档 = 全面」+「收起」
- 蓝条「生效来源：本信号「跟随上级」→ 逻辑类型「选路」= 全面 → 全局默认 = 全面」
- 三列表格 `150px / 1fr / 128px`（层级 / 说明·表达式例子 / 档位），6 行 `covRows`，生效行底色 `#eef4fd` + 蓝粗体
- 底行：「用例数上限 `256`」→「本信号当前 **25** 条」│ →右「档位怎么算的？」

### 1.3 V2Spec §3 六种状态设计

| 状态 | 主色 | 处置 |
|---|---|---|
| Excel 少了某一页 | `#eef0f3`/`#3d4752` | 范围开关该页置灰 + 标「本表无 dft 页」；不弹窗不报错；状态栏写「本表无 dft / iddq 页，门控层跳过」 |
| 未解析的信号 | `#fdecea`/`#8c2d27` | 行状态「未解析」，点开写清卡在哪一层、Excel 哪一行、原式是什么；电路图画到能画的那层为止，断点处红色端子标「展不下去」 |
| 规格冲突 | `#fdecea`/`#8c2d27` | 状态「规格冲突·待 designer 核对」；行内点名冲突 Excel 行号；电路图冲突 case 支红虚线 + 行号；**工具不猜，默认不生成** |
| 猜名的线网 | `#fffaf0`/`#8a6412` | 清单探针前缀列留空 + 橙点；输入表名字橙色虚下划线；电路图虚线橙框 + 角标；claims.json 单列一类 |
| 载入 200+ 进行中 | `#eef4fd`/`#1d4f9c` | 清单立刻可点；未完成行「分析中」骨架；进度条写「正在展开 137/211」+ 当前信号名；**可随时停止**；详情区打开未完成信号显示局部结果 +「还在展开第 3 层」 |
| 手填与 auto 不一致 | `#fdecea`/`#8c2d27` | 单元格红底。**这是特性不是错误** —— 文案写「仿真该断言 FAIL 恰恰说明表达式与 designer 意图不符，这正是要抓的 bug」，**不要写成「请修正」**；标题栏进度旁加「其中 1 条与程序算的不一致」 |

### 1.4 真值表单元格 6 态色（V2Spec §4，十六进制）

| # | 名称 | 底 | 边框 | 字 | 说明 |
|---|---|---|---|---|---|
| 1 | 手填 · 与 auto 一致 | `#eaf6ee` | `1px solid #a9d6ba` | `#14603b` | 最常见的正常态 |
| 2 | 手填 · 与 auto 不一致 | `#fdecea` | `1px solid #e5a9a4` | `#a9302a` | 断言会 FAIL —— 这正是要抓的 |
| 3 | 待手填 | `#fff` | `1px **dashed** #c3c9cf` | `#c9ced4` | 生成 .sv 时用程序算的值兜底 |
| 4 | 反例（故意填错） | `#fdf3e0` | `1px solid #e6c98a` | `#8a6412` | 自检 checker 抓不抓得到 |
| 5 | iddq=1 漏电态自检拍 | `#eef4fd` | `1px solid #a9c4ea` | `#1d4f9c` | 每个信号末尾固定一拍 |
| 6 | 只读（auto_out 行 / 输入行） | `#fafbfc` | `1px solid #e3e7eb` | `#5d6773` | 灰字，不可编辑 |

列级底色（`colBg`）：当前列 `#eef5fd` · 反例列 `#fdfaf3` · iddq 列 `#f6faff` · 普通 `#fff`。
列头：普通 `#f1f3f5`/`#3d4752`；当前列 `#c9dcf5`/`#12161a`；反例列 `#fdf3e0`/`#8a6412` 标签 `T23✗`；iddq 列 `#eef4fd`/`#1d4f9c`。
> ⚠ 契约 C-079 的第 6 态是「淡蓝=手编列」，Design 的第 6 态是「只读」——少一态，见 §4 冲突⑮。

### 1.5 信号表列集 / 密度规范（V2Spec §4）

- **默认可见（560px 内放得下）**：勾选 · 反例 · 信号名（带切片）· **状态** · **用例** · owner
- **列设置里可开**：断言号 · 分类 · 逻辑类型 · 探针前缀 · RTL 补充标记
- **删除**：R / K / top / type —— 不再暴露 Excel 列字母，改叫「断言号 / 信号名 / 顶层输出 / 分类」
- 行高 **26px** · 表头 **24px**（真值表实际用 28px）· 正文 **12px** · 信号名 Consolas **11.5px** · 斑马纹 `#fcfcfd`
- 界面字体：微软雅黑 12px（次要 11.5px，标题 13–16px）；**所有信号名、地址、数值、表达式一律 Consolas 11.5px**
- 按钮 24px 高；1366×768 时清单收到 **420px**、右侧常驻栏折叠成标签；1920×1080 为常用版面
- **任何布局下都不准进横向滚动条**

### 1.6 快捷键（Design 标注了 4 个）

| 键 | Design 位置 | 契约 |
|---|---|---|
| `Ctrl+O` | 顶栏「浏览…」+ 空态「选择 Excel…」 | C-253 ✓ |
| `Ctrl+L` | 顶栏「载入 / 重新载入」 | C-254 ✓ |
| `Ctrl+D` | **顶栏「诊断」** 与 **真值表「复制列」两处** | C-258 撞键，见冲突① |
| `Ctrl+G` | 顶栏「导出中心」 | C-257 语义改变，见冲突① |
| `Ctrl+V` | 真值表提示条（TSV 粘贴） | C-294 ✓ |
| `Ctrl+P` / `Ctrl+R` | **Design 完全没标** | C-255 / C-256 → 补位 |

### 1.7 用户标注截图各圈了什么（10 张，一句一张）

| 文件（前 8 位） | 红圈范围 | 读出的诉求 |
|---|---|---|
| `2bca924f` | 筛选行整条「范围」5 段开关 | 范围切换是 Q1 的第一入口，位置与分段必须照此 |
| `431a2d83` | 整个详情主视图区（真值表 + 其下大片空白） | 主视图空间没用满，别让真值表悬在上面 |
| `43a5d90d` | 真值表中的一条测试列（纵向长圈） | 「列」是真值表的操作单位（选中列 / 复制列 / 加反例选中列） |
| `5a7b48b6` | 导出中心的「范围」整列 | 每种交付物各有各的范围，这一列是导出中心的关键 |
| `81b7a964` | 顶栏右侧四个按钮（浏览 / 重新载入 / 诊断 / 导出中心） | 全局动作只留这一组，快捷键角标要在按钮上 |
| `9adcd39c` | 顶部场景开关的「③ 详情 · 电路图」 | 电路图是一个必须单独能看到的场景 |
| `b13df23d` | 「电路图示例信号」切换行（3 个样例信号） | 电路图要用三种规模的真实信号验（1 节点 / 3 级 / 三级 mux 级联） |
| `b7f57994` | 标签栏上一个空位，手写「电路图占满」 | 要一个让电路图占满主视图的开关 → 最终版的 `全屏` 按钮 |
| `c22c965c` | 清单最左的勾选列整列 | 勾选列是常驻第一列，不进「列设置」 |
| `d1e1339f` | 真值表下方的大片空白区 | 同 `431a2d83`：这块空白要被电路图吃掉 |

> 两张 `pasted-*.png` 已被截断（各恰好 196608 字节 = 192 KB 整），无法读取，按任务要求跳过。

---

## 2. V2Spec §5 映射表 49 行 → 契约 ID 逐行核对

`M` 数组 49 行，落到 **202** 个契约 ID（一行 Design 映射常对应多条契约 ID）。

| # | 区块 | 旧控件 | v2 位置 / 新名 | 处置 | 契约 ID |
|---|---|---|---|---|---|
| 1 | 清单 | 勾选 / 反例勾选 | 清单前两列，原位保留 | 保留 | C-022 C-023 |
| 2 | 清单 | 信号名带切片 / 断言号 / owner / 分类 / 逻辑类型 | 信号名与 owner 默认可见；断言号·分类·逻辑类型进「列设置」 | 保留 | C-008 C-009 C-010 C-011 C-012 C-013 |
| 3 | 清单 | 状态（8 档解释） | 状态列默认可见；点状态 → 行内展开原因 + Excel 行号 + 去诊断 | 保留 | C-014 C-015 C-016 |
| 4 | 清单 | 探针前缀列 | 进「列设置」；缺前缀时行内原因块直接给三步流程 | 保留 | C-017 C-018 C-019 C-020 |
| 5 | 清单 | 用例列 | 默认可见（v1 在横滚条后面，这是必须修的） | 保留 | C-021 C-045 |
| 6 | 清单 | 按列排序 | 表头点击排序，原位保留 | 保留 | C-037 |
| 7 | 清单 | owner 多选 / 分类 / 状态 / 正则搜索 | 筛选行，5 份合成 1 份 | 合并 | C-024 C-025 C-026 C-027 C-028 C-029 C-030 C-031 C-047 |
| 8 | 清单 | 全选 / 清空 / 勾选选中行 | 清单底部工具条 | 保留 | C-032 C-033 C-034 |
| 9 | 清单 | 全部加反例 / 清除反例 | 清单底部工具条 | 保留 | C-035 C-036 |
| 10 | 清单 | 范围切换（全链 / 某页本地） | 筛选行第一个控件「范围」；原 4 个 tab 降为 5 个选项 | 合并 | C-038 C-042 |
| 11 | 清单 | RTL 补充信号琥珀标记 | 信号名后琥珀圆点 + tooltip；横幅改成状态栏一行 | 保留 | C-039 C-215 |
| 12 | 详情 | 展开链 | 右侧常驻栏上半「逐层展开」 | 保留 | C-050 C-051 C-052 C-053 C-054 C-222 |
| 13 | 详情 | 电路图（新） | 主视图第 2 个标签 | 新增 | C-278 C-279 C-280 C-281 C-282 C-283 C-284 C-285 C-286 |
| 14 | 详情 | 真值表 | 主视图第 1 个标签（默认） | 保留 | C-073 C-074 C-075 C-076 C-077 C-078 C-079 C-080 C-081 |
| 15 | 详情 | 输入信号表 | 右侧常驻栏下半 —— 新门面原本没有，必须补 | 保留 | C-056 C-057 C-058 C-059 C-060 C-061 C-062 C-275 |
| 16 | 详情 | 每列驱动明细 | 输入信号表每行下方第二行常驻显示驱动式，不再只藏 tooltip | 保留 | C-063 |
| 17 | 详情 | 解析明细 / 找不到网排查提示 | 详情标题栏「解析明细」按钮 → 面板；提示直链诊断抽屉 | 保留 | C-064 C-065 C-066 |
| 18 | 详情 | .sv 预览（本信号 / 勾选集） | 主视图第 3 个标签，两处合成一处 | 合并 | C-067 C-068 C-069 C-070 C-268 |
| 19 | 详情 | 手填进度 | 详情标题栏常驻 N/M + 进度条 | 保留 | C-106 |
| 20 | 详情 | 本信号覆盖度 | 并进标题栏覆盖度控件第三层 | 合并 | C-071 C-147 C-149 |
| 21 | 真值表 | 重新生成 / 加列 / 复制列 / 删列 | 真值表工具条；复制列绑 Ctrl+D | 保留 | C-085 C-086 C-087 C-088 C-109 C-115 C-116 C-117 C-119 C-258 |
| 22 | 真值表 | 清零 / 删反例 / auto→期望 | 真值表工具条，三处都加二次确认并在表上留痕 | 保留 | C-089 C-090 C-094 C-095 C-102 C-103 C-118 C-123 |
| 23 | 真值表 | 重命名列 | 工具条「重命名列…」，接名称校验 + 拒 T\<n\> + 查重 | 保留 | C-091 C-092 C-093 C-099 C-120 |
| 24 | 真值表 | 加反例（选中）/ 全部用例加反例 | 工具条「加反例（选中列）」+ 清单「全部加反例」 | 合并 | C-096 C-097 C-098 C-101 C-121 C-122 |
| 25 | 真值表 | 逐格编辑 / 8 种数值写法 | 单元格直接编辑，解析失败要可见（状态栏 + 单元格还原） | 保留 | C-078 C-082 C-083 C-084 |
| 26 | 真值表 | mux 数据值 整表与单列 | 右键列菜单「设置本列 mux 数据值」+ 工具条整表 | 保留 | C-110 C-111 C-112 C-113 |
| 27 | 真值表 | 反例错值 == 正确值 保护 | 保存时校验，冲突弹提示不静默 | 保留 | C-100 C-104 C-105 |
| 28 | 真值表 | 单信号 CSV | 工具条「导出 CSV」（含 期望(bin)/force/RF_WRITE 三行） | 保留 | C-136 C-137 C-138 C-139 |
| 29 | 真值表 | 批量填 · 导入期望 | 工具条「导入期望（CSV/xlsx）…」「批量填期望…」+ TSV 粘贴 | 新增 | C-293 C-294 C-295 C-296 C-298 C-299 |
| 30 | 真值表 | 两套真值表编辑器（24 个按钮） | 合成一套工具条 | 合并 | C-140 |
| 31 | 覆盖度 | 全局默认 / 按逻辑类型 / 本信号 / 上限 / 穷举 | 详情标题栏一个控件，点开三层 + 上限；生效那层高亮 | 合并 | C-142 C-143 C-145 C-146 C-148 |
| 32 | 覆盖度 | 界面同时 5 个下拉、2 套模型 | 1 个控件、1 套模型 | 合并 | C-155 C-156 |
| 33 | 导出 | .sv（范围 / 注释 / 末尾汇总 / owner） | 导出中心第 1 行；两个旧对话框合成一个，统一默认值 | 合并 | C-157 C-158 C-159 C-160 C-161 C-162 C-163 |
| 34 | 导出 | 重复标号确认 / 跳过点名 | 导出前确认 + 完成反馈「跳过点名在前」 | 保留 | C-164 C-165 C-166 C-167 C-199 |
| 35 | 导出 | 报告 HTML / CSV / Excel | 导出中心第 2 行，HTML 内联电路图 | 保留 | C-173 C-174 C-175 C-176 C-177 C-178 |
| 36 | 导出 | 回填 for_test | 导出中心第 3 行，写副本不覆盖源表 | 保留 | C-179 C-180 C-181 C-182 C-183 C-184 |
| 37 | 导出 | nets.txt（按类别） | 导出中心第 4 行 + 诊断抽屉第 1 步 | 保留 | C-185 C-186 C-187 C-188 C-189 |
| 38 | 导出 | claims.json | 导出中心第 5 行 —— GUI 原本没有出口 | 新增 | C-196 |
| 39 | 导出 | 导出 / 导入配置 | 导出中心第 6 行；空态页也放一个「导入配置…」 | 保留 | C-190 C-191 C-192 C-193 C-194 C-195 |
| 40 | 诊断 | 探针前缀映射（导入 / 导出） | 诊断抽屉「找不到网」第 3 步 | 降级 | C-200 C-201 C-202 C-203 C-204 C-205 |
| 41 | 诊断 | 强制 force 信号 | 诊断抽屉「两个东西撞名」；同时接通 Topout 后端（v1 不生效） | 降级 | C-206 C-207 C-208 C-209 |
| 42 | 诊断 | RTL 补充逻辑（JSON + 模板 + 导入） | 诊断抽屉「规格缺了一级逻辑」 | 降级 | C-210 C-211 C-212 C-213 C-214 C-216 |
| 43 | 诊断 | 缺前缀是否强制生成 | 诊断抽屉，改成可见开关（v1 写死 True 且不提示） | 降级 | C-217 C-041 |
| 44 | 诊断 | 覆盖度算法说明 / 逻辑展开说明 | 覆盖度控件里「档位怎么算的？」+ 展开链标题行帮助 | 合并 | C-144 C-222 |
| 45 | 删除 | 仅 top_output=1 筛 | 真表该列全 0，无意义 | 删除 | C-048 |
| 46 | 删除 | logic 加尾缀 / mux 加尾缀 / 本信号探尾缀网 | 尾缀三件套，Topout 模型下无意义 | 删除 | C-225 C-226 |
| 47 | 删除 | 级联 force 模式（三处下拉） | 全链展开永不 force 中间网 | 删除 | C-223 C-224 |
| 48 | 删除 | R / K / top / type 列头 | 改叫 断言号 / 信号名 / 顶层输出 / 分类 | 删除 | C-049 |
| 49 | 删除 | 「排查(旧)」tab | 能力拆进详情区与诊断抽屉后整体删除，不并存 | 删除 | C-252 C-266 C-267 |

**核对结论**
- 49 行全部对上契约 ID，没有「Design 有、契约没有」的孤儿行。
- **M13「电路图 = 主视图第 2 个标签」与主画板 `tabDef` 只有 2 个标签自相矛盾**（见冲突②）。
- **M40/M41/M42 只给了症状入口，三个编辑器本体（前缀映射 / 强制 force / RTL 补充逻辑）Design 一个都没画**，全部落到补位（C-201/203/205/207/208/211/212/213/214/216 共 10 条）。
- M44 把「级联模式说明」漏了：契约 C-218/C-219/C-220 无落点（见 gap）。
- 剩下 **100** 个契约 ID 不在 M 的 49 行射程内（M 是按 Design prompt §10 组织的，§10 本身就没列这 100 条），其中大半在主画板上仍有落点（如 C-001 顶栏浏览、C-107 当前列高亮、C-197 导出中心、C-221 三步流程），其余进 gap。

---

## 3. gap 清单（类别≠删除、Design 没给落点：**122** 条）

> 契约表 `v2_location` 列已逐条填成 `主控补位: …`、`status` = `补位待过目`。下表给短版，长版以契约表为准。

### 3.1 清单（15 条）

| ID | 主控补位建议 |
|---|---|
| C-003 | 空态「最近打开」首条即上次表；启动按 `last_excel` 自动载入并直接进工作台 |
| C-004 | 无界面元素；命令行带 `.xlsx` 启动跳过空态直接进工作台 |
| C-005 | 空态/工作台顶部一条红色错误条（不弹窗、不退出），文案过 `inputs_table.scrub_terms` |
| C-018 | 探针前缀列悬停给探针网真名 + 断言完整路径（配了/没配两种文案） |
| C-019 | 探针前缀列单元格文案「探针口=xxx (level_shift)」 |
| C-020 | 探针前缀列单元格文案「\<输入名\>→\<前缀\>」 |
| C-025 | owner 下拉里「（无 owner）」单列一项并带条数 |
| C-026 | owner 按钮文字随已选个数变、悬停列全部已选 |
| C-027 | 换表后重建 owner 菜单、丢掉新表没有的 owner |
| C-031 | 筛选后在 `statusLeft` 追加「可见 N / 共 M（其中 K 个按输入信号名命中）」 |
| C-040 | 状态格加 ⚙ 标记 + 悬停给嵌套 mux 折叠全文 |
| C-041 | 状态列增加第 6 档「⚠ 缺前缀·已强制生成」，与诊断抽屉开关联动 |
| C-043 | 详情区写「分析失败（已捕获，未崩）」+ 原因全文 |
| C-046 | 「表达式」做成「列设置…」可选列、默认隐藏（执行计划 §5 待拍板 4） |
| C-047 | Excel `type` 筛并入正则搜索（支持 `_to_dft` 后缀），不单独出控件（待拍板 4） |

### 3.2 详情（10 条）

| ID | 主控补位建议 |
|---|---|
| C-053 | 直连寄存器链区文案「顶层口 == 该寄存器字段写值，无展开」 |
| C-058 | 输入表「⚠需探针前缀」标记（Design 只区分「猜名」，没区分「需前缀」） |
| C-059 | 驱动行两档上色：未解析=红 `#a9302a`、需前缀=琥珀 `#8a6412` |
| C-060 | 角色列按 mux 控制三来源细分（寄存器直出 / line / local / 模式位 / 经上游 mux / 上游 mux 配方） |
| C-061 | mux 数据寄存器按物理寄存器收拢并标「被哪个 case 选中」 |
| C-063 | 真值表列头与单元格悬停给该列 force/RF_WRITE 明细（`inputs_table.column_drives`） |
| C-065 | 解析明细里 `FOUND_IN_LABEL` 九种来源的中文翻译（术语表只覆盖 2 条） |
| C-069 | 全量预览超 600 行截断并注明总行数 |
| C-070 | 全量预览末尾追加本次跳过哪些信号、各缺哪根输入 |
| C-072 | 配置变更后 .sv 预览按新配置自动重算 |

### 3.3 真值表编辑（22 条）

| ID | 主控补位建议 |
|---|---|
| C-076 | 输入行按 for_test 页行序排（`analysis_norm.order_groups_fortest`） |
| C-082 | 8 种数值写法在单元格编辑器内接受（`truth_edit.parse_int`） |
| C-083 | 解析失败 → 状态栏说清 + 该格还原（A5 model 的 `parseFailed(str)` 已预留） |
| C-084 | 单元格显示格式 1位→0/1、2–4位→0bXXXX、更宽→0xNN |
| C-097 | 同输入取值已有反例的跳过并在状态栏报跳过几条 |
| C-098 | 只对正向列造反例、不给反例列套反例 |
| C-100 | 反例错值同时避开 auto_out 与手填期望两个正确值 |
| C-104 | 反例期望被填成正确值时记成 NEG-BROKEN、保住反例身份 |
| C-105 | mux 反例错值撞正确值时自动翻一位并在状态栏说明 |
| C-108 | 列宽拖过后重建保留手动宽度、换信号才恢复自动 |
| C-112 | mux 改数据值后屏幕值与内部/导出值一致（`edits.mux_resync_cols`） |
| C-113 | mux 撞值提示「≥2 条数据路取到相同值 = 选错路也测不出（假绿）」 |
| C-114 | mux 手填期望按输入取值键存、切覆盖度不串号 |
| C-124 | mux 专用真值表头部条（case 结构 + 生效档 + 该档怎么展开 + 手填进度） |
| C-125 | mux 头部标出被跳过的死分支（`sigflow` 已有「✗死分支」后缀可复用） |
| C-126 | mux 头部标出受 dft 页 iddq 门控、以及门能不能 force |
| C-130 | iddq 自检拍列不参与表达式重算（否则假红当成反例） |
| C-131 | 门已是显式输入时不再单列 DFT 门行 |
| C-134 | 不可建信号时真值表工具条按钮置灰 |
| C-135 | 选中 mux 信号时按钮说明换成 mux 语义（`_MUX_BTN_TIPS`） |
| C-138 | CSV 另写「期望来源」行与「反例?」行 |
| C-139 | mux CSV 忠于产物（带反例列、与生成同口径去重重排号） |

### 3.4 覆盖度（5 条）

| ID | 主控补位建议 |
|---|---|
| C-149 | 切信号时覆盖度控件回显该信号已设档、不触发重算 |
| C-151 | 动全局档时清掉当前信号的单点档，让全局改动立刻可见 |
| C-152 | 改逻辑类型档后整张清单用例数与当前真值表一起重算 |
| C-153 | 已自定义编辑过的信号改档只影响清单用例数与导出、不冲掉编辑 |
| C-154 | 只加过反例的信号，切全局档时正向按新档重算再补回反例 |

### 3.5 导出（19 条）

| ID | 主控补位建议 |
|---|---|
| C-162 | 四个选项记住上次选择（`exports.load_export_options`）；Design 的「选项」列要做成可点开的弹层 |
| C-164 | 写文件前弹重复 assert 标号确认、列出冲突标号与两个信号 |
| C-166 | 导出完成摘要点名「只记账不产断言」的信号 |
| C-168 | 关掉「导出完成」弹层后自动切到 .sv 预览标签 |
| C-169 | 按范围给默认文件名 `wr_rf_tc.sv` / `_pos.sv` / `_neg.sv` |
| C-170 | 汇总命名块按范围加 `_pos`/`_neg` 后缀 |
| C-171 | 写文件失败时说清是否被仿真器/编辑器占用 |
| C-172 | 预览完在状态栏报 信号/断言块/用例(反例、手填期望)/记账 各几条 |
| C-176 | 选 Excel 就存 `.xlsx`、按所选格式纠正扩展名 |
| C-177 | 报告完成报「范围 · 用例 N 条 · 反例 N 条」+ 写了哪几个文件 |
| C-181 | 回填时自动补 `.xlsx` 扩展名 |
| C-182 | 回填完成报回填了几组、mux 有几个没进 for_test 及原因 |
| C-184 | 回填含 mux 表（`include_mux=True`） |
| C-187 | nets 类别只列本表真有内容的页；Design 的选项列要做成可勾类别列表 |
| C-188 | nets 类别勾选记住上次（`nets_pages`），默认全勾 |
| C-191 | 导出配置完成后逐段报数 |
| C-193 | 导入时按文件名核对源表，不一致提示「这份配置是为《X》导出的，当前是《Y》」 |
| C-194 | 导入时列出文件里有、当前表没有的信号名与原因（最多 30 个 + 总数） |
| C-195 | 导入的不是本工具配置时说明缺哪个段，不静默失败 |

### 3.6 诊断（14 条）

| ID | 主控补位建议 |
|---|---|
| C-201 | 前缀编辑器保留合并写法 + 扁平写法混用、`#` 注释 |
| C-203 | 探针前缀导出成 `.txt`（换表/给同事复用）——Design 第 3 步只画了导入 |
| C-205 | 改完前缀重建清单时保住已有勾选 |
| C-207 | 强制 force 名单支持行尾 `#` 注释、留空=清除 |
| C-208 | 强制 force 名单导入/导出 `.txt`（与探针前缀同款） |
| C-211 | 「插入模板(当前信号)」预填原表达式与原输入映射 |
| C-212 | RTL 补充逻辑从 `.json` 导入 |
| C-213 | 六道校验不通过不保存并逐条列错（`session.validate_supplements` 已有） |
| C-214 | 补充基名不在当前 logic 页时提示「将作为纯新增合成信号生成」 |
| C-216 | Topout 视图的展开也看得到补充信号 |
| C-218 | 级联模式说明窗随三处级联下拉（C-223/C-224）一并退役，`docs/级联模式说明.md` 留作背景资料 |
| C-219 | 同 C-218（不再有「读不到文件」这条路径） |
| C-220 | 同 C-218 |
| C-302 | 诊断抽屉 `otherSymptoms` 加第 5 条「从旧版真值表编辑迁进来」（一次性显式动作，legacy 桶只读不删） |

### 3.7 持久化与兼容（26 条）

Design 本来就画不出这一节——全是无界面元素的行为契约。补位统一写成实现约束：

`C-227` settings 保持宽容读 · `C-229` 按范围记 `cov_<view_id>`/`maxt_<view_id>` · `C-230` legacy `coverage_*` 只读一次作迁移 · `C-231` 四个 `export_*` 键统一默认值后照常持久化 · `C-232` `nets_pages` · `C-233` 三套按 Excel 全路径分桶（`suffix_override` 随入口删）· `C-234` 能读 `view_edits`/`view_checks` 子桶 · `C-235` legacy 桶不读不删 · `C-236` 按「已载入」路径分桶 · `C-237` 换表先清空再还原 · `C-238` 恢复时重算 auto_out · `C-239` 点名找不到的信号 + 原因 · `C-240` 损坏数值逐条跳过报个数 · `C-241` 手填期望不丢 + 状态栏报恢复了几个 · `C-242` 勾选持久化并恢复 · `C-243` 全勾=默认时不写勾选桶 · `C-244` 批量操作挂起逐格存盘 · `C-245` 单点档刻意不存盘 · `C-246` pytest 下不落盘 · `C-247` 配置带版本号 2 · `C-248`/`C-249`/`C-250` 三种文本/JSON 格式不变 · `C-251` `python -m dreg_verify.gui` 入口不变 · `C-300` **edits 按路径桶合并写** · `C-301` 保住 `wrong_value: 0` / `exp: 0`。

> `C-300` 是 A3 审计的坑④，不是新加的——它是这一节 26 条里唯一会**静默抹掉同事手填期望**的一条，B3 必须写进架构约束。

### 3.8 快捷键与入口（4 条）

| ID | 主控补位建议 |
|---|---|
| C-255 | `Ctrl+P` 绑主视图「.sv 预览」标签 |
| C-256 | `Ctrl+R` 绑导出中心「报告（给人看）」行的直接导出 |
| C-259 | 窗口标题写「Dreg_verify · 版本 \<短HEAD\>」——§8.1 禁「git」字样，改叫「版本」 |
| C-265 | 切范围时按覆盖度/上限指纹判断是否重建清单 |

### 3.9 反馈与文案（1 条）

| ID | 主控补位建议 |
|---|---|
| C-272 | 载表出错用工程师语言报错、不把 traceback 塞进 .sv 预览页（与 C-005 同一通道） |

### 3.10 信号流图（4 条）

| ID | 主控补位建议 |
|---|---|
| C-286 | 排版打磨（交叉最小化 / 列缝按最长标签定宽 / 端口标签让位 / channel 超 6 条不重叠）——Design 三个变体是手摆坐标，沿用 `sigflow.py` 分层算法并按 Design 视觉规范调参 |
| C-287 | 数据层改动点 B/D/E + RO 回读根挂图 + review 遗留 M4/m2/m3/m5/m7(b) |
| C-288 | 覆盖度叠加排到 v2 之后单开任务（执行计划 §5 待拍板 3 默认值） |
| C-289 | 多信号总览图 + PDF 排到 v2 之后单开任务（同上） |

### 3.11 新增（2 条）

| ID | 主控补位建议 |
|---|---|
| C-290 | 清单底部工具条加「粘贴名单勾选…」（待拍板 5 默认=进本轮 C1） |
| C-291 | 筛选行右端加「预设 ▾」（存/取当前勾选 + 筛选，待拍板 5 默认=进本轮 C1） |

---

## 4. 冲突清单（16 条，每条给裁决建议）

> 编号 ①–⑩ 对应主控点名的 10 条（已逐条核实并补全）；⑪–⑯ 是本轮新发现的。

### ① `Ctrl+D` 撞键（顶栏「诊断」vs 真值表「复制列」）

**现象**：主画板顶栏 `诊断 Ctrl+D`，真值表工具条 `复制列 Ctrl+D`。Design prompt §9 明写「Ctrl+O/L/P/R/G/D 已有习惯」，契约 C-258 = `Ctrl+D 复制真值表列`（`MW._build_testitems_tab` 的 `QShortcut`，`WidgetShortcut` 范围绑在 `ti_table` 上）。

**裁决建议**：**`Ctrl+D` 保留给「复制列」（C-258 不动）；诊断抽屉改绑 `Ctrl+J`**（或 `F9`），顶栏按钮角标同步改。理由：`Ctrl+D` 是"已有习惯"、每天用几十次；诊断抽屉按 Design 自己的定位是"逃生阀，不是日常主力"，用一个不常用的键更合适。原绑定是 `WidgetShortcut` 范围，只在真值表有焦点时生效——若坚持两处共用一个键，会出现"焦点在真值表时 Ctrl+D 复制列、焦点在清单时 Ctrl+D 开诊断"的薛定谔行为，必须避免。

**附带两条**（同属快捷键，一并裁）：
- `Ctrl+G` 语义从「直接生成 .sv」（C-257）改成「打开导出中心」。**建议接受**：v2 的生成入口本来就收进导出中心了，语义变化是 H4 合并的必然结果；契约 C-257 的 `v2_location` 已如此登记。
- `Ctrl+P`（预览）/ `Ctrl+R`（导出报告）Design 完全没标。**建议补**：`Ctrl+P` → 主视图 `.sv 预览` 标签；`Ctrl+R` → 导出中心「报告」行直接导出（C-255/C-256 补位）。

---

### ② 真值表与电路图叠放（主画板）vs 三视互斥切换（V2Spec §1/H2）

**现象**：
- 主画板 `tabDef = [["truth","真值表 + 电路图　25 列 · 手填 7/25"], ["sv",".sv 预览"]]` —— **只有 2 个标签**，电路图固定在真值表下方（`truthStyle` 定高 `S.truthH=420` + 6px 拖柄 + `flowStyle: flex:1`），另给一个 `全屏` 按钮（`S.flowFull` 把真值表整块 `display:none`）。
- V2Spec §1 写「主视图（互斥切换）：真值表（默认）/ 电路图 / .sv 预览」，§2 H2 写「电路图与真值表互斥切换（都是 Q2 的答案，工程师一次只读一个）」，§5 M13 写「电路图 = 主视图第 2 个标签」。
- **用户标注截图佐证**：早期迭代（`431a2d83` / `b7f57994` / `d1e1339f`）确实是三标签互斥，用户在那几张上圈的全是「真值表下方大片空白」和手写「电路图占满」；最终主画板改成叠放 + `全屏`，正是对这些标注的回应。

**裁决建议**：**以主画板为准（叠放 + 可拖分割 + 全屏），V2Spec §1/§2/§5 的"三视互斥"是被后续迭代推翻的旧稿。** 三条理由：
1. 用户的标注（3 张）全部指向"别让真值表悬在上面、空白浪费"，叠放直接解决；
2. H2 反对同屏的理由是"每一视都压到不可用"，而叠放给了 `truthH 160–820` 的连续可调 + `全屏` 两个极端，等于同时拿到互斥与同屏两种版面；
3. 联动高亮（C-282）在同屏下才真正好用——点一根线、真值表同名行当场亮，不用切标签。

**实现约束**：主视图标签仍要能"只看电路图"（对应场景 ③、截图 `9adcd39c`），由 `全屏` 按钮承担；`.sv 预览` 保持第 2 个标签。V2Spec §5 M13/M14/M18 的"第 1/2/3 个标签"措辞在架构文档里按此更正。

---

### ③ 行内「仍然生成（风险自负）」= 逐信号 `include_risky`，后端只有全局开关

**现象**：清单行内原因块给了一个逐信号按钮「仍然生成（风险自负）」；诊断抽屉 `otherSymptoms` 第 4 条给的是全局开关「缺前缀是否强制生成　当前：是」。
**后端实测**：`session.normalize_global_settings` 里 `include_risky` 是 `global` 段的一个 **bool**（settings 顶层键 `include_risky`），`persist_global_settings` 也按全局写。没有任何逐信号维度。

**裁决建议**：**接受 Design 的逐信号按钮，后端做 additive 扩展**——`include_risky` 保持全局 bool（默认 True，A3 护栏不动），另加一个**逐信号白名单** `include_risky_signals: {excel路径: [基名]}`（与 `force_signals` 同款按路径分桶）。生效规则：`全局 True` → 全部强制生成（现有行为逐字节不变）；`全局 False` → 只有白名单里的信号强制生成。这样：
- 默认路径（全局 True）**零字节变化**，byte-gate 不会红；
- 行内按钮有了真实语义（把这一个信号加进白名单）；
- 诊断抽屉的全局开关继续存在，两者不打架。
> 这是**需后端新增 N1**，见 §4 末尾清单。

---

### ④ .sv 选项「正向+反例分文件」vs `export_sv` 一次只出一份

**现象**：导出中心第 1 行选项写「正向+反例分文件 · 无注释 · 末尾汇总 · 写 owner」；导出完成弹层也确实列了两个文件（`wr_rf_tc.sv` + `wr_rf_tc_neg.sv`）。
**后端实测**：
```python
def export_sv(provider, path, only=None, mode="min", max_tests=256, exhaustive=False,
              edited=None, options=None, sig_cov=None, form_cov=None, text=None, build=None)
```
`scope` 是 `options` 里的键（`"all"|"pos"|"neg"`），函数体内只读一次 `scope`、只 `write_text(path, text)` 一次。**一次调用一个 scope 一份文件。**

**裁决建议**：**接受 Design 的产品语义，在编排层加一层薄封装，不改 `export_sv` 本身**。导出中心把"正向+反例分文件"翻成两次 `export_sv(options={"scope":"pos"}, path=X_pos.sv)` + `export_sv(options={"scope":"neg"}, path=X_neg.sv)`，两个 `ExportOutcome` 合并成一条完成反馈（这正好把契约 C-169「按范围给不同默认文件名」和 C-170「汇总命名块按范围加后缀」两条落实了）。`build` 可复用一次渲染结果，避免跑两遍。
> **需后端新增 N2**（小，`exports` 加一个 `export_sv_split(...)` 或让编排层直接调两次并合并 outcome）。

---

### ⑤ nets.txt 类别口径：Design 三类 vs 后端按页 5 类

**现象**：Design 导出中心第 4 行选项写「类别：**顶层输出 · force 目标 · 猜名的网**」。
**后端实测**：`exports.nets_categories(wb)` / `collect_nets(wb, pages)` 的类别是**按 Excel 页**：`topout`（Topout+cone 输入）/ 仅 Topout 探针 / `logic` / `mux` / `dft` / `iddq`，契约 C-186 与 settings 键 `nets_pages` 都是这套；`pageviews.PAGES = ("logic","mux","dft","iddq")`。

**裁决建议**：**两套并存，默认显示 Design 的"按用途"三类，「更多」里放旧的"按页"类别。** 理由：Design 的三类才是红区那位工程师真正在问的问题（"我要扫的是哪些网"），按页分类是工具内部视角；但 `nets_pages` 已经在同事机器上有值（A3 §3.1 实测），不能删。做法：
- 新增按用途分类 `topout_out` / `force_target` / `guessed`（前两类可由现有 `collect_nets` 的产物重新切分，第三类用 `resolver` 的 `found_in ∉ TRUSTED_FOUND_IN` 判定，`sigflow.TRUSTED_FOUND_IN = ("tmm","regmap")` 已有同款判据）；
- 旧 `nets_pages` 键继续读继续写，作为"按页"模式的记忆。
> **需后端新增 N3**（中，`exports.collect_nets` 加一套用途分类）。

---

### ⑥ 空态「最近打开」多条 vs `last_excel` 单键

**现象**：Design 空态画了 2 条最近打开，每条带路径 + 时间 + 信号数（「今天 09:14 · 21 信号」）。
**后端实测**：`session.load_last_excel()` 只读 `settings["last_excel"]` —— 一个顶层字符串键，**没有 MRU 列表、没有时间戳、没有信号数**（A3 §3.1 的 27 键里也只有这一个）。

**裁决建议**：**做，新增 settings 键 `recent_excels`，`last_excel` 保留不动。** 结构 `[{"path":..., "ts":..., "n_signals":...}]`，上限 5 条，载表成功后写一条。`last_excel` 继续写（同事的旧文件、旧版本回退都靠它），`recent_excels` 缺键时用 `last_excel` 生成单条。信号数取 `topout_view_models` 的长度——但那是分析后才有的，所以要在**分析完成**时补写，不是在载表时。
> **需后端新增 N4**（小）。

---

### ⑦ 导出完成「打开输出目录」+ 导出中心「上次导出到哪」

**现象**：导出完成弹层有 `打开输出目录` 按钮；导出中心有一整列「上次导出到哪」（`D:\work\wr_rf_tc.sv　今天 14:22` / `从未导出`）。
**后端实测**：
- 「打开输出目录」后端没有，但这件事**根本不该进 Qt-free 层**——一行 `QDesktopServices.openUrl(QUrl.fromLocalFile(dirname))`，纯 GUI。
- 「上次导出到哪」**完全没有持久化**：`exports.py` / `session.py` 里搜不到 `last_export` / `export_dir` 之类的键；默认文件名都是现算的（`session.default_config_filename(excel)`）；`ExportOutcome.path` 只是本次结果。

**裁决建议**：
- 「打开输出目录」：**做，GUI 侧一行**，不进 Qt-free 模块（`exports.py` 不引 Qt 是 A4 的地基约定）。
- 「上次导出到哪」：**做，新增 settings 键 `last_export`**，形如 `{"sv": {"path":..., "ts":...}, "report": {...}, "fortest": {...}, "nets": {...}, "claims": {...}, "config": {...}}`，6 种交付物各一格。这一列是 V2Spec §2 H4 主动补的那条（「红区流程里最常见的问题是『我到底导没导过 nets.txt』」），价值高、成本低。同时它把契约 C-198 从"新增无落点"变成"有实现"。
> **需后端新增 N5（小）+ N6（极小）**。

---

### ⑧ 覆盖度弹层三层继承显示 vs `CoverageState.effective_label` 只给二元组

**现象**：Design 蓝条要显示**整条继承链**：「本信号「跟随上级」→ 逻辑类型「选路」= 全面 → 全局默认 = 全面」，并在三层表格里把生效的**两行**都高亮（`covRows` 里 `i===3 || i===5`）。
**后端实测**：
```python
def effective_label(self, name, models=None, form_key=None):
    """返回 (档中文名, 来源说明)：("穷举","本信号") / ("精简","逻辑类型·选路 (F2)") / ("全面","全局默认")"""
```
只返回**赢的那一层**，不返回被跨过的层、也不返回每层各自的档位。**给不了 Design 要的三层链。**

**裁决建议**：**保留 `effective_label` 不动（现有 GUI 在用），additive 加一个 `effective_chain(name, models=None, form_key=None)`**，返回
```python
[{"level": "本信号",            "label": "跟随上级", "active": False},
 {"level": "逻辑类型·选路 (F2)", "label": "全面",     "active": True},
 {"level": "全局默认",          "label": "全面",     "active": True}]
```
`active` 用于 Design 的两行高亮（本信号"跟随上级"时，逻辑类型层与全局层都算"参与了生效"）。蓝条文案由 GUI 侧按这个列表拼。
> **需后端新增 N7**（小，约 30–50 行，纯读现有 `sig_cov`/`form_cov`/`global_label`）。

---

### ⑨ 载入态「可随时停止」+ 未完成行「分析中」骨架 = 后台 worker + 增量填充

**现象**：Design 载入态给了进度（`137 / 211`）、当前信号名、「清单已可点，展开完的信号先出现；未完成的显示「分析中」」、`停止分析` 按钮；V2Spec §3 还加了「详情区打开未完成信号时显示局部结果 +『还在展开第 3 层』」。契约 C-276 是这条的新增项。
**后端实测**：`topout.topout_view_models(...)` **同步跑完全表才返回**，没有进度回调、没有取消点、没有逐信号 yield；`gui.on_load` 也是同步调用。这是本轮**最大的后端新增**。

**裁决建议**：**做，但分两级降级**：
- **一期（本轮必做）**：`topout_view_models` additive 加两个可选参数 `progress=None`（`callable(i, n, name)`）与 `should_cancel=None`（`callable() -> bool`），内部循环每信号调一次；不传时行为逐字节不变（byte-gate 安全）。GUI 侧 `QThread` + 信号槽，清单先出 `resolve_root` 的便宜结果（分类 + owner + 状态骨架），用例数与真值表随 worker 增量填。这与执行计划 §6 的风险对策口径一致。
- **二期**：「详情区打开未完成信号显示局部结果 + 还在展开第 3 层」——要 cone 展开本身能中途报告层数，改动更深，排到 v2 之后。
> **需后端新增 N8**（大，约 150–250 行 + 测试）。**这条是 C 阶段的关键路径，B3 架构必须把 worker 与「当前线网高亮总线」一起设计。**

---

### ⑩ 展开链每层的页标签（dft 页门控 / logic 页 / mux 页 · mux3）vs `an["chain"]` 没有页/kind 字段

**现象**：Design 右栏「逐层展开」每层顶上有一行灰色小字页标签：`dft 页门控` / `logic 页` / `mux 页 · mux3`。
**后端实测（已逐处核到产出点）**：`an["chain"]` 每层**只有三个键** `{"out", "expr", "subst"}`：
- `cone.py::expand` → `chain_out.append({"out": sig.out_base, "expr": str(sig.expr).strip(), "subst": E.to_text(node, rename)})`
- `mux_gen.py::synthesize_mux_expr` → `{"out": "mux%s" % group.group_no, "expr": ..., "subst": ...}`
- `topout.py::_gate_chain_entry` → `{"out": top_name, "expr": "<gate> ? 0 : <inner>", "subst": 同}`

**没有 `page`、没有 `kind`。** 唯一能"推断"的办法是看 `out` 的字符串规律（mux 层带 `mux<组号>` 前缀、门控层 `expr` 形如 `x ? 0 : y`）——这正是 R41 记忆里"靠命名约定猜"那一类脆弱推断，不能让 GUI 去做。
> 执行计划 §7 列了 `an` 的 6 处缺口，**这是第 7 处**，之前没被记下来。

**裁决建议**：**做，三处 additive 各加两个键 `page`（`"logic"|"mux"|"dft"|"iddq"`）与 `kind`（`"logic"|"mux"|"gate"`）**，GUI 直接读，不做字符串推断。三处改动都只是往已有 dict 里多塞两个键，不影响 `.sv` 路径（`chain` 只被 GUI 与报告消费），但**必须跑 byte-gate 6 sha**确认。`pageviews.PageResult.chain` 恒为空 list，不受影响。
> **需后端新增 N9**（小，约 20–30 行）。同时把这条**补进执行计划 §7 的缺口表**（作第 7 行）。

---

### ⑪ §8.1 违反：诊断第 2 步写了 CLI 命令 `python3 scan_rtl.py`

**现象**：`steps[1]` 正文「把 scan_rtl.py 和 nets.txt 一起传到仿真服务器，**source 过 dreg 环境后零参数跑**」，结果框直接写 `python3 scan_rtl.py`，按钮「复制这行命令」。Design prompt §8.1 明写「不出现仓库路径、git、**CLI 命令**」，V2Spec §6 自己的"文案红线"也照抄了这一条。

**裁决建议**：**保留，判定为非违反，但改一句措辞。** 理由：§8.1 禁的是**本工具的 CLI**（"别让 IC 工程师去敲 `python -m dreg_verify...`"），而 `scan_rtl.py` 是**红区仿真服务器上那位工程师必须亲手敲的命令**——不给命令行反而没法干活，契约 C-189 也要求"给出跨机器三步下一步指引"。改法：把「source 过 dreg 环境后零参数跑」里的「零参数跑」改掉（这是开发者视角措辞），写成「在仿真服务器上跑（不带参数）」；「复制这行命令」保留。同时在 V2Spec §6 的红线里把这一条加个例外注脚，免得 C 阶段 review 反复。

---

### ⑫ §8.1 边缘：空态「最近打开」用了本仓路径当样例

**现象**：`C:\code\Dreg_verify\mirror_wl_dreg.xlsx` / `C:\code\Dreg_verify\mirror_btlp_dreg.xlsx`，顶栏 `excelPath` 同。
**裁决建议**：**非违反（这是 mock 数据，生产里显示的是用户自己选的表），但实现时不得照抄成占位/示例文案。** 空态没有最近记录时写「还没有最近打开的表」，不写任何示例路径。

---

### ⑬ C-259 窗口标题带 git 短 HEAD vs §8.1 禁「git」

**现象**：契约 C-259「窗口标题带代码版本（git 短 HEAD），一眼看出跑的是哪份代码」（`gui._code_version`）；§8.1 与 V2Spec §6 红线都禁「git」。Design 顶栏只写了 `Dreg_verify`。
**裁决建议**：**能力保留、措辞改**：标题写「Dreg_verify · 版本 `<短HEAD>`」，界面上不出现 "git" 字样。`session.code_version()` 本身不动。已按此填进 C-259 的补位。

---

### ⑭ iddq 自检拍列色相：Design 淡蓝 vs 契约 C-079 淡紫

**现象**：契约 C-079 写「淡紫=iddq 自检拍」（旧门面 `DFT_BG`）；Design 统一用淡蓝 `#eef4fd`/`#a9c4ea`/`#1d4f9c`，V2Spec §4 也写「蓝底」。真值表里的 DFT 门输入行 Design 用琥珀 `#8a6412`（猜名色），旧门面是紫字。
**裁决建议**：**以 Design 为准（淡蓝）。** 执行计划 §6 已定「Design 的视觉/布局照单全收」；且淡紫与"猜名琥珀"在密集表格里区分度不如蓝。契约 C-079/C-128/C-129 的 `v2_location` 已按 Design 色值登记。

---

### ⑮ 单元格 6 态少一态：Design 第 6 态 = 只读，契约第 6 态 = 淡蓝手编列

**现象**：契约 C-079 的 6 态是「绿一致 / 红不一致 / 灰未填 / 琥珀反例 / 淡紫 iddq / **淡蓝手编列**」；V2Spec §4 的 6 态是「绿 / 红 / 待手填 / 反例 / iddq / **只读**」。**"手编列（user_added）"这一态在 Design 里消失了**，而"只读"是 Design 新加的。
**裁决建议**：**做成 7 态**：Design 的 6 态全收，另把"手编列"降级成**列头标记**而不是单元格底色——手编列已经有自定义列名（不是 `T<n>`），列头写成 `U1` / 用户命名即可区分，不必再占一个底色（8 种底色在 34px 宽的格子里已经过载）。契约 C-079 的 `v2_location` 已按 Design 6 态登记，手编列区分写进列头规范。

---

### ⑯ 导出中心 `exportSummary` 把点名推迟到完成弹层

**现象**：`exportSummary = "勾选 3 项 · 覆盖 7 个信号 · 2 个信号会被跳过（导完点名）"` —— **导出前只给了数量**，名字要等导完才看到。§8.2 / V2Spec §6 红线：「跳过/过滤的东西一律先点名字和原因，计数放后面」。
**裁决建议**：**轻度违反，改法很小**：把「2 个信号会被跳过（导完点名）」做成可点开的行（或直接在下面列出 2 个名字 + 一句原因）。导出**前**就知道会跳过谁，正是用户取消掉重新处理的时机；等导完再点名就晚了。完成弹层的点名（C-167）保持不变。

---

### 冲突汇总 → 「需后端新增」清单（10 条）

| # | 内容 | 来自 | 规模估计 |
|---|---|---|---|
| N1 | 逐信号 `include_risky` 白名单（全局 bool 不动，加按路径分桶的信号名单） | 冲突③ | **中** 80–150 行 + 测试；触及 `topout`/`pageviews`/`exports` 的 skip 判定，**byte-gate 必跑** |
| N2 | 「正向+反例分文件」一次出两份的编排封装（两次 `export_sv` + 合并 `ExportOutcome`） | 冲突④ | **小** 30–60 行 |
| N3 | nets.txt「按用途」三类（顶层输出 / force 目标 / 猜名的网），与按页类别并存 | 冲突⑤ | **中** 80–120 行 |
| N4 | settings 新键 `recent_excels`（MRU 5 条，带时间与信号数），`last_excel` 保留 | 冲突⑥ | **小** 40 行 |
| N5 | settings 新键 `last_export`（6 种交付物各记一次路径 + 时间） | 冲突⑦ | **小** 30 行 |
| N6 | 「打开输出目录」（GUI 侧 `QDesktopServices.openUrl`，不进 Qt-free 层） | 冲突⑦ | **极小** 5 行 |
| N7 | `CoverageState.effective_chain(name)` 返回三层继承链（`effective_label` 不动） | 冲突⑧ | **小** 30–50 行 |
| N8 | `topout_view_models` 加 `progress` / `should_cancel` 可选参数 + GUI 后台 worker + 清单增量填充 | 冲突⑨ | **大** 150–250 行 + 测试；**C 阶段关键路径** |
| N9 | `an["chain"]` 每层补 `page` / `kind` 两个键（`cone.py` / `mux_gen.py` / `topout._gate_chain_entry` 三处） | 冲突⑩ | **小** 20–30 行，**byte-gate 必跑**；补进执行计划 §7 作第 7 处缺口 |
| N10 | `inputs_table.input_rows` 把「需探针前缀」与「猜名」分成两个标记（`found_in` 已有信息） | gap C-058/C-059 | **小** 20–30 行 |

> 另：执行计划 §7 已列的 6 处 `an` 缺口（`out_net` / 状态 8 档 / mux 控制口 Binding / `used_vars` 对不齐 / `dft_gate_skipped` / 驱动串两套口径）在 Design 对齐后**一条都没少**——Design 的状态只给了 5 档（§7 缺口 #2 仍在），mux 输入表的三来源细分（gap C-060/C-061）正是 §7 缺口 #3/#4 的界面后果。

---

## 5. 主画板 `<script>` 段逻辑清单（23 条）

**总结论：只借布局与交互，逻辑一律走后端。** 下表逐条给 JS 名 → 界面职责 → 我们后端的模块.函数 → 差异 → 结论。

| # | JS 名 | 界面职责 | 我们的后端 | 差异 | 结论 |
|---|---|---|---|---|---|
| 1 | `state` 初始值 | 全部布局尺寸与开合态 | 无（纯 UI 态） | — | **只借数值**：`listW 560 / sideW 470 / chainH 300 / frozenW 288 / truthH 420 / winW×winH 1920×1080` 直接当 v2 默认值（契约 C-261） |
| 2 | `scenes`（8 个） | 顶栏场景开关，原型演示用 | 无 | v2 没有"场景"概念 | **不落码**；但它是**测试用例清单**——8 个场景 = 8 组 offscreen 截图断言 |
| 3 | `D`（10 行信号清单 mock） | 信号名 / 状态文案 / 状态色档 / 用例数 / owner / 分类 / 行内原因标题·正文·动作 | `topout.topout_view_models(...)` → 每模型 `{name,disp,owner,width,kind,status,note,issues,form,form_label,n_vectors,probe_net,prefix,assert_id,...}` | ① 状态只给 5 档实例，后端 `status` 只有 `ok/skip/unresolved/error` 四档（执行计划 §7 缺口 #2）；② 行内原因的三段（标题/正文/动作）后端没有对应结构，现在只有 `note` + `issues[]` 两个自由文本 | **逻辑走后端**；`an` 补细分状态（§7 #2），行内原因块的三段由 GUI 侧按 `status` + `issues` 模板化拼，**模板文案照抄 Design 的四条实例** |
| 4 | `box(on,col)` / `stS` / `btn(on)` | 勾选框 / 状态徽标 / 分段按钮的三套样式函数 | 无 | — | **只借色值**（已抄进 §1.2 / §1.4） |
| 5 | `rows = D.map(...)` | 清单行渲染 + `toggleCheck`/`toggleNeg`/`pick`/`toggle`（点状态展开） | GUI 侧 model；勾选走 `edits` 的 `view_checks` 桶 | `S.checks`/`S.negs` 是会话态，我们要持久化（C-242/C-243） | **只借交互**：点状态 → 展开原因块，是 Design 的关键发明（替代了旧门面的 tooltip），照做 |
| 6 | `cur = D[S.sel]` | 详情标题栏取当前行 | 同上 | — | 只借 |
| 7 | **真值表 mock 生成循环**：`NC=25 / ACT=13 / NEG=23 / IDDQ=24` + `names[6]` + `bits[6][]` + `auto[]` + `exp[]` | 造 25 列测试数据并算 `auto`/`exp` | `topout.analyze_signal(...).vectors` → `edits.cols_from_vectors(an, e_inputs)`；`auto` 由 `expr` 求值、`exp` 由 `edits.expected_cell_state` 分态 | `auto.push(iddq ? 0 : (A & (B | C)))` 是把**本样例的表达式硬编码**进循环；`exp` 的构造 `(i<7 ? (i===6 ? 1-v : v) : i===NEG ? 1-v : i===IDDQ ? v : null)` 是为了凑出"绿 6 个 + 红 1 个 + 反例 + iddq 拍 + 其余未填"的展示效果 | **纯展示脚手架，一行都不要**。染色规则本身照抄：`v===null`→待手填虚线框；`ci===NEG`→琥珀；`ci===IDDQ`→蓝；`v===auto[ci]`→绿；否则→红；`ci===ACT`→再叠 `outline 2px #2f6fd0`。这五条正好对上 `edits.expected_cell_state` 的 `EXP_DFT/EXP_NEG/EXP_UNFILLED/EXP_MATCH/EXP_DIFF` 五个返回值 —— **一一对应，零缺口** |
| 8 | `cols`（列头 + `T23✗`/`T24`/`ACT` 特判） | 列头标签与底色 | `edits.col_names(cols)` / 列 dict 的 `name`/`neg`/`user` | 反例列标签 Design 写 `T23✗`，旧门面写 `_NEG` 后缀 | **列名走后端**（`truth_edit.uniq_col_name` 保证全集唯一，C-099），`✗` 只作为**视觉后缀**加在显示层，不进 `.sv` 标号 |
| 9 | `trows` + `colBg` + 期望格上色 | 冻结列行标签 + 每格底色 | `inputs_table.vheader_short(g)` / `edits.expected_cell_state` / `edits.input_cell_state` | 行标签 Design 格式 `<真名>　(角色 · 端口)`，后端 `vheader_short` 现在给的是另一套 | **行标签格式改成 Design 版**（`inputs_table.vheader_short` 加一个 display 变体，不动旧调用点） |
| 10 | `gridCells` / `gridStyle` | 把表头 + 8 行铺平成一个 CSS grid | 无（Qt 侧是 `QTableView` + `QAbstractTableModel`） | Web grid 铺平 vs Qt model/view | **不落码**；但 `34px 列宽 / 28px 表头 / 26px 行高` 直接当 v2 的 `sectionSize` 默认值 |
| 11 | `IN`（6 行输入信号 mock） | 字母 / 真名 / 角色 / RO-RW / 驱动串 / 猜名与高亮标记 | `inputs_table.input_rows(an)` → `{letter,name,width,role,rw,drive,found_in,found_in_label,trusted,note,resolved,base,key,bold,is_control,is_dft_gate}` | **后端字段是超集**，Design 的 6 个字段全有对应（`trusted` ↔ Design 的 `d[5]===1` 猜名、`is_control` ↔ 角色加粗）。唯一差异：Design 只用一个"猜名"标记，后端 `found_in` 能区分 `needs-prefix` / `wire兜底` / `prefixed-wire`（gap C-058/C-059） | **逻辑走后端**，且**后端更细**——把 `found_in` 的细分显示出来（N10） |
| 12 | `covRows`（6 行三层继承） | 覆盖度三层表格 + 生效行高亮 | `session.CoverageState`（`global_label` / `form_cov` / `sig_cov` / `effective_label`） | `effective_label` 只给赢的那层，给不了整条链 | **需后端新增 N7**（`effective_chain`） |
| 13 | `SCOPES` + `ER`（6 行交付物）+ `cycleScope` / `toggle` | 导出中心表格与范围循环 | `exports.*`（`export_sv` / `export_report` / `export_fortest` / `export_nets` / `export_claims` / `write_json`）+ `EXPORT_OPTION_DEFAULTS` | ① 范围只有 4 档循环，真实范围是「勾选/全部」×「all/pos/neg」；② 「上次导出到哪」无持久化（N5）；③ 「正向+反例分文件」一次两份（N2）；④ nets 类别口径（N3） | **逻辑走后端**；范围控件做成两段（对象范围 + scope），别用循环点击 |
| 14 | `steps`（诊断三步） | 找不到网三步流程 | `exports.export_nets` / `session.parse_probe_prefix_text` / `merge_probe_prefix_text` / `read_text_file` | 后端齐全 | **文案照抄、逻辑走后端**；第 2 步措辞按冲突⑪改 |
| 15 | `otherSymptoms`（4 条） | 其余诊断入口的症状化标题 | `session.validate_supplements` / `parse_force_signal_text` / `normalize_global_settings.include_risky` | 第 4 条「缺前缀是否强制生成　当前：是」与 A3 护栏一致 ✓；行内「仍然生成」需 N1 | **文案照抄**；补第 5 条（C-302 旧编辑迁移） |
| 16 | `tabDef` / `tabs` | 主视图两标签 | 无（纯 UI） | 与 V2Spec 三标签矛盾 | 见冲突② |
| 17 | `scopes`（5 段范围） | 范围切换 | `pageviews.PAGES = ("logic","mux","dft","iddq")` + Topout | **完全对应**（Topout + 4 页 = 5 段）；`pageviews.page_available(wb, page)` 正好喂"本表无该页则置灰"（C-042） | **逻辑走后端**，零缺口 |
| 18 | `svText`（9 行硬编码） | .sv 预览样例 | `exports.render_sv(provider, ...)` / `pageviews.build_page_sv(...)` | 纯字符串 | **不落码**；但它示范了预览的**排版**（注释头 → RF_WRITE → force → `#1ps` → assert → uvm_report），与 `sv_writer` 现有产物一致 ✓ |
| 19 | `statusLeft` / `statusRight` | 状态栏两段 | `excel_model.load_workbook` 的统计（C-006）+ N5 的 `last_export` | `statusRight` 需要 N5 才有真数据 | 逻辑走后端 |
| 20 | `exportSummary` / `exportBtnLabel` / `exportBtnStyle` | 导出前摘要 + 按钮禁用态 | `exports.build_skipped` / `skip_reason` / `risky_reason` / `ExportOutcome.summary_text()` | 摘要"只报数量"，见冲突⑯ | 逻辑走后端；摘要改成"名字在前" |
| 21 | `variant`（按信号名选 small/mid/big） | 挑电路图规模 | `sigflow.build_graph(wb, resolver, result)` → `Graph`，规模由真实 cone 决定 | Design 是**按信号名硬编码** `indexOf("clk_force_on")===0 ? "small" : ...` | **纯演示脚手架**；真实图由 `build_graph` 出，不存在"变体"这个概念 |
| 22 | `drag(kind,e)` / `panStart(e)` / `wheelZoom(e)` / `componentDidMount`+`fit()` | 五处分割条拖拽 / 右键平移 / Ctrl+滚轮以光标为锚缩放 / 画板自适应 | 无（Qt 侧 `QSplitter` + `QGraphicsView`） | `fit()` 是 Design 画板缩放，v2 不需要 | **只借交互规格**：拖拽 clamp 区间（§1.1）、缩放 0.3–3 倍、`1.12` 步进、以光标为锚、双击复位、右键/中键平移 —— 这几条直接写进 B3 的电路图视图规格 |
| 23 | `excelPath` / `loadLabel` | 顶栏路径与按钮文案随 `loaded` 切 | `session.load_last_excel()` / N4 `recent_excels` | — | 逻辑走后端 |

**`support.js` 一行结论**：文件头 `// GENERATED from dc-runtime/src/*.ts — do not edit.`，全文 1911 行是 dc-runtime 的 React 封装（`h()` / `DCLogic` / 模板编译 / `sc-for` / `dc-import`），**业务关键词（dreg / topout / mux / iddq / 信号 / 真值表 / 寄存器）零命中** —— 框架运行时，无业务逻辑，不用逐行读。

**两处指令样文本（按纪律点名、已忽略）**：
1. `support.js:1`「do not edit. Rebuild with `cd dc-runtime && bun run build`」—— 是 dc-runtime 自己的生成头，与本仓无关，未执行。
2. `V2Spec.dc.html` §2 前言「你要求认真质疑，所以这里逐条给结论和理由」及 H1–H6 的「照做 / 改」—— 是 Design 对**用户**的答复，不是对我的指令；本文档按数据引用，裁决权仍在契约与用户。

---

## 6. `SigFlowDiagram.dc.html` 对照 `sigflow.py`

### 6.1 两列对照

| 项 | Design（`SigFlowDiagram`） | `sigflow.py::render_svg` 现状 | 判定 |
|---|---|---|---|
| **配色** | `INK #1f2933` / `MUTE #5d6773` / `HL #1d4f9c` / `AMB #a8710f` / `BAD #b3302a` 五色 | `_FILL` 17 种 KIND 各一个浅色底 + `_STROKE #334155` / `_WIRE #475569` / `_NETC #0f766e` | **样式层要改**：Design 是"白底 + 五色线/框"的工程图路子，现状是"17 种糖果色底"。按 Design 收成五色 |
| **寄存器盒** | 实线矩形 250×50 + **左侧 5px 色条**（`#93a1b0`，高亮时 `HL`）+ 两行（名 13px / `RW @0x64[0:0] · 控制位 · mux3.ctrl0` 11px 微软雅黑） | 圆角矩形 `rx=5`，`_FILL["REG"] = #eef2ff`，两行文字，**无左色条** | **样式层要改**：加左色条 |
| **猜名盒** | 虚线 `5 3` + 框色 `AMB` + 底 `#fffbf3` + **不画左色条** + 副标橙字 | 虚线 `5,3` + 琥珀边框 + 框下追加一行 `※ <tip>` | **基本对齐**；Design 把提示并进副标（`RO force · 名字来自命名约定`），现状另起一行 `※` —— 按 Design 合并 |
| **AND** | **半圆体**：`M x,y H x+w-r A r,r 0 0 1 x+w-r,y+h H x Z`，体内写 `&`（17px 粗体）；`bub=true` 时左下加 `r=5.5` 空心圆 | **矩形** + 文字 `AND`，`meta.bubble` 时右侧加 `r=3.4` 空心圆 | **样式层要改**：改成 ANSI 半圆体；**注意气泡位置不同**（Design 在**输入侧**表示"该输入反相"，现状在**输出侧**表示"整体反相"）——`iddq ? 0 : x ≡ x & ~iddq` 语义上是输入侧反相，Design 是对的 |
| **OR** | **曲线体**：三段二次贝塞尔，体内写 `≥1`（14px 粗体） | 矩形 + 文字 `OR` | **样式层要改** |
| **MUX** | **梯形**：`M x,y L x+w,y+cut L x+w,y+hh-cut L x,y+hh Z`，体内 `MUX` + 组名 `mux3`；端口标签在左外侧（`S` / `1` / `0` / `01` / `10` / `11`） | 矩形 + `sub` 写 `case(...)`，端口按 `ports` 排 | **样式层要改**：改梯形 |
| **TOP 端子** | **箭头形**：`M x,y H x+wd-14 L x+wd,y+h/2 L x+wd-14,y+h H x Z`，底 `#e8f0fb`、框 `HL` 2px，两行（名 / 「顶层输出」） | 矩形，`_FILL["TOPOUT"] = #111827` 深色底 + 白字 | **样式层要改**：改箭头形 + 浅蓝底 |
| **线型** | 4 种：普通 1.2px `INK` / `bus` 2.4px / `hl` 2.2px `HL` / `bad` `BAD` + `dasharray 6 3` / `ghost` `dasharray 5 3` | 2 种：`width>1` → 2.6px，否则 1.2px；不可信 → `dasharray 5,3` + 琥珀 | **样式层要改**：补 `hl`（选中加粗高亮）与 `bad`（冲突支红虚线） |
| **线上标注** | 手写 `T(x,y,"d_wl_rf_logen_mixer_en [0:0]")` 放在线旁 | 每条线自动标 `net` 名 + `[msb:lsb]`（1bit 不标），文字挂 `data-net` | **数据层不动**：现状**更完整**（自动、带钩子） |
| **冲突支** | 红虚线 + 旁边一行 `case 2'b11 与 logic 页第 58 行冲突 · Excel mux 页 行 214` | **文字后缀** `" ⚠conflict"` / `" ✗死分支"` / `" ·本轮不驱动"`，非虚线 | **样式层要改**：冲突支改红虚线 + 把 Excel 行号写出来（行号 `meta.row/rows` 已有） |
| **图例行** | 有：底部一行四项（`── 寄存器（表里查到地址）` / `┈ 名字来自命名约定，表里未查到` / `── 当前选中线网` / `┈ 规格冲突的 case 支`） | **没有**（全文无 legend） | **样式层要加** |
| **选中高亮** | `hl` 模式：框 `HL` 2px + 底 `#eef4fd` + 线 2.2px `HL` | **没有交互态**；只预埋了 `data-net` / `data-node` / `data-kind` 三个钩子 | **样式层要加**（数据钩子已就绪，C-282 零后端改动） |
| **交互钩子** | 无（静态 SVG） | 每条 `<polyline>` 与 net 名 `<text>` 挂 `data-net`；每个节点 `<g>` 挂 `data-node` / `data-kind` / `data-net` | **数据层不动**，现状更强 |
| **图元覆盖面** | `reg` / `AND`(+气泡) / `OR` / `MUX` / `TOP` 共 5 种 | `KINDS` **17 种**：REG PIN AND OR NOT XOR NAND NOR MUX2 MUXN GATE REDUCE CMP OP BUSTAP BUSMERGE RENAME CONST TOPOUT | **数据层不动**：Design 只画了样例用到的 5 种；剩下 12 种（尤其 `BUSTAP`/`BUSMERGE`/`RENAME`/`REDUCE`/`CMP`）**Design 没给样式**，按 V2Spec §4 的文字规范补 |
| **布局** | 手摆坐标（每个变体写死 x/y） | 最长路径分层 + 列内 DFS + 长边插 dummy + 三段正交折线 | **数据层不动**：Design 不含自动排版 |
| **深度上限** | 三变体分别 1 / 3 / 4 级 | `MAX_DEPTH = cone.MAX_DEPTH` | V2Spec §4 写「深度上限 6 层 / 约 60 节点；超出折叠 MUXn 分支」—— 与现状对齐 |

### 6.2 「Design 有 / `sigflow` 没有」单列（样式层 9 项）

1. 寄存器盒**左侧 5px 色条**（高亮时变蓝）
2. **MUX 梯形**（现为矩形）
3. **AND 半圆体 + 输入侧反相气泡**（现为矩形 + 输出侧气泡）
4. **OR 曲线体**（现为矩形）
5. **TOP 端子箭头形 + 浅蓝底**（现为深色矩形）
6. **冲突支红虚线 + Excel 行号**（现为文字后缀 `⚠conflict`）
7. **图例行**（现完全没有）
8. **选中线加粗高亮 `hl`**（现只有 `data-net` 钩子，无样式态）
9. 猜名提示**并进副标**（现另起一行 `※`）

### 6.3 「`sigflow` 有 / Design 没画」单列（按 V2Spec §4 文字规范补样式）

`PIN` / `NOT` / `XOR` / `NAND` / `NOR` / `MUX2` / `REDUCE` / `CMP` / `OP` / `BUSTAP`（切片 `[1:1]` 三角抽头）/ `BUSMERGE`（拼接梯形合并）/ `RENAME`（改名·电平移位透传块，左源名右顶层口名）/ `CONST` 共 13 种；以及 MUXn 支标的 **don't-care 写法 `4'b000x`**。→ 归入 gap C-279 的「按 V2Spec §4 补齐」。

**结论：数据层（`Node`/`Edge`/`Graph`/`KINDS`/`build_graph`/布局算法/`data-*` 钩子）一行不动；`render_svg` 的样式段（`_FILL`/`_STROKE`/`_WIRE` + 每个 KIND 的画法）按 Design 重写。** 这是纯渲染改动，不碰 `.sv` 路径，但 `attach_report_svgs` 会让报告 HTML 的字节变，需要在 HTML 报告的回归基线里登记一次。

---

## 7. 统计

| 口径 | 数 |
|---|---|
| Design 画板 | 3（主画板 / V2Spec / SigFlowDiagram）+ 1 框架文件 + 10 张标注截图 |
| 主画板**场景** | **8**（空态 / 清单·原因展开 / 详情·真值表+电路图 / 覆盖度展开 / 导出中心 / 导出完成 / 诊断抽屉 / 载入中） |
| 主画板**区域** | **16**（顶栏 · 筛选行 · 清单区 · 详情标题栏 · 主视图标签 · 真值表区 · 电路图区 · .sv 预览 · 右侧常驻栏 · 状态栏 · 导出中心 · 导出完成 · 诊断抽屉 · 空态 · 载入态 · 覆盖度弹层） |
| SigFlowDiagram 变体 | 3（small 1 节点 / mid 3 级 / big 三级 mux 级联 + 冲突支） |
| V2Spec 小节 | 6（信息架构 / H1–H6 回应 / 状态设计 / 组件规范 / 映射表 / 微文案） |
| **映射表 `M` 行数** | **49** |
| **49 行对上的契约 ID 数** | **202**（去重后；一行常对多条） |
| 未被 `M` 49 行覆盖的契约 ID | 100（`M` 按 Design prompt §10 组织，§10 本身没列这 100 条；其中大半在主画板上仍有落点） |
| **302 条中已落点** | **172** |
| **gap（类别≠删除、Design 无落点）** | **122** |
| 类别=删除（不填落点） | 8（C-048 C-049 C-223 C-224 C-225 C-226 C-252 C-292） |
| **冲突条数** | **16**（①–⑩ 主控点名 + ⑪–⑯ 本轮新发现） |
| **主画板 JS 逻辑点** | **23**（+ `support.js` 一条结论：框架运行时，无业务逻辑） |
| **需后端新增** | **10**（N1–N10；其中 1 大 / 2 中 / 6 小 / 1 极小） |
| 需跑 byte-gate 的后端改动 | 2（N1 逐信号 include_risky、N9 `an["chain"]` 加键） |

### 各 area 已落点 / 补位分布

| area | 合计 | 已落点 | 补位待过目 | 删除类 |
|---|---|---|---|---|
| 清单 | 49 | 32 | 15 | 2 |
| 详情 | 23 | 13 | 10 | 0 |
| 真值表编辑 | 69 | 47 | 22 | 0 |
| 覆盖度 | 15 | 10 | 5 | 0 |
| 导出 | 43 | 24 | 19 | 0 |
| 诊断 | 28 | 10 | 14 | 4 |
| 持久化与兼容 | 28 | 1 | 26 | 1 |
| 快捷键与入口 | 16 | 12 | 4 | 0 |
| 反馈与文案 | 9 | 8 | 1 | 0 |
| 信号流图 | 12 | 8 | 4 | 0 |
| 新增 | 10 | 7 | 2 | 1 |
| **合计** | **302** | **172** | **122** | **8** |

---

## 8. 判断：Design 可否直接作为 v2 蓝图？

**可以，作为「版面与交互」的蓝图直接用；不能当「功能清单」用。**

**可以直接用的（照单全收，不再讨论）**
- 16 个区域的分区、尺寸与默认值（`listW 560` / `sideW 470` / `truthH 420` / `frozenW 288` / `chainH 300`），拖拽 clamp 区间，6px 手柄；
- 三套色板：状态四色、单元格 6 态、电路图五色；字号与行高规范（微软雅黑 12 / Consolas 11.5 / 行高 26 / 表头 24）；
- 三个**发明**——它们解决的是 v1 真实的痛点，v1 没有对应物：
  1. **点状态 → 行内展开原因块**（替代 tooltip，把"名字 + 原因 + Excel 行号 + 去诊断"四件事放进一行），
  2. **输入信号表每行下方常驻驱动串**（旧门面只有 tooltip，契约 C-063 提了三年），
  3. **导出中心的「上次导出到哪」列**（V2Spec §2 H4 主动补的，解决"我到底导没导过 nets.txt"）；
- 术语替换表 11 行（§8.6 的 10 个禁用词全覆盖 + 自己补了「假绿」），文案红线 4 条；
- 六种状态设计（尤其"手填与 auto 不一致是特性不是错误、不许写『请修正』"这一条，正是 v1 最容易写错的地方）。

**不能直接用的（三个理由）**
1. **覆盖率只有 57%**（172/302）。Design 是照 prompt §10 的 6 大类做的，§10 本身是能力**提要**不是**清单**；契约表 302 条里有 65 条是"文档没写、代码里有"的，Design 自然一条都不知道。**持久化与兼容 28 条里 26 条无落点**——这些恰恰是"照 Design 重构就会静默丢掉同事手填期望"的那一批（尤其 C-300 的桶合并写）。
2. **有 3 处内部自相矛盾**，不裁决就没法动工：`tabDef` 两标签 vs V2Spec §5 M13"第 2 个标签"（冲突②）、`Ctrl+D` 一键两用（冲突①）、单元格 6 态与契约 6 态差一态（冲突⑮）。
3. **10 处后端够不着**（N1–N10），其中 N8（后台 worker）是 C 阶段关键路径、N1/N9 会动 byte-gate 保护的字节。Design 把它们画得像"已经有了"，若照着做 UI 再发现后端没有，会在 C 阶段末尾集中爆雷。

**给 B3 架构步的三条**
1. **版面按 Design、能力按契约表**——两份都是输入，冲突时按执行计划 §6「契约优先，Design 的视觉/布局照单全收」。
2. **先排 N8（后台 worker）**：它决定清单 model 是不是增量的、详情区能不能打开未分析完的信号，改的是骨架不是皮肤，越晚做越贵。
3. **122 条补位里有 26 条是"无界面元素"的行为契约**（持久化那一节）——它们不进任何一个视图模块，要在架构文档里单列一节"跨模块不变量"，否则按模块分波实现时会没人认领。

---

## 9. 主控裁决（Phase B2，2026-09-12）

**总方针**：版面与交互按主画板（Design），能力按契约表（冲突时契约优先）；Design 里一切「算东西」的 JS 一行不落码，逻辑走 Qt-free 层与 topout/pageviews/sigflow。

**16 条冲突**：全部按 §4 的建议裁决，除两处改动——
- ① 快捷键：`Ctrl+D` 保留给复制列；诊断改 **`Ctrl+Shift+D`**（不用 Ctrl+J，助记仍是 D）；`Ctrl+G` = 导出中心；`Ctrl+P` = 主视图 .sv 预览标签；`Ctrl+R` = 导出中心并预选「报告」行；`Ctrl+O/L` 不变。
- ③ 逐信号 include_risky（N1）**不做**（全局默认 True 时几乎用不到，且要动 byte-gate 保护的路径）；行内「仍然生成（风险自负）」改为**「去诊断 · 缺前缀是否强制生成」**跳转到抽屉里的全局开关。N1 移到 v2 之后的候选。
- ②④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯ 照建议执行。

**122 条 gap 补位**：全部采纳，契约表 status 由「补位待过目」改为「已落点(补位)」；其中持久化与兼容 26 条 + C-300/C-301 在架构文档单列「跨模块不变量」节，按模块分波时由集成 agent 认领。

**需后端新增**（9 条，N1 除外）：N2 分文件编排、N3 nets 按用途三类（与按页并存）、N4 recent_excels、N5 last_export、N6 打开输出目录（GUI 侧）、N7 effective_chain、**N8 后台分析 worker + progress/cancel + 增量填充（C 阶段第一波，关键路径）**、N9 an["chain"] 补 page/kind（byte-gate 必跑）、N10 inputs_table 区分「需前缀」与「猜名」；加上执行计划 §7 的 6 处 an 缺口 → 统一放进 **C0「后端补齐」波**，先于任何 UI 波，全部 additive + byte-gate 6/6。

**SigFlow**：数据层不动；`render_svg` 样式段按 §6.2 九项 + §6.3 十三种图元的 V2Spec 文字规范重写；HTML 报告因内联 svg 变化需重录报告回归基线（.sv 字节不受影响）。

**Design 的三个发明照单全收**：点状态 → 行内原因块；输入信号表每行常驻驱动串；导出中心「上次导出到哪」列。
