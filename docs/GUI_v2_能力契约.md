# GUI v2 能力契约（2026-09-12）

> **这张表是 GUI 重构「一项功能都不能丢」的单一真相。** Design 映射表、测试命名、`tools/contract_check.py` 全部对着它。承 `docs/GUI_v2_执行计划_20260912.md` §2 五道锁的 L1。
> 机器可读副本：`docs/GUI_v2_能力契约.csv`（UTF-8 带 BOM，表头 `id,area,capability,category,old_entry,backend_api,v2_location,test_ids,status,source,note`），两份一一对应。
>
> **维护约定**：后续只会改三列 —— `v2 落点`（Design 映射表到手后，B2 填）、`测试 ID`（C 阶段每写一条测试就回填）、`状态`（待落点 → 已落点 → 已验证）。**改任一份都要同步改另一份**；`tools/contract_check.py` 读 csv，所以 csv 是机器真相，md 是人读真相。能力行只增不改语义：要改语义就新开一个 ID、把旧 ID 标成删除。

## 0. 怎么用这张表

| 列 | 含义 |
|---|---|
| **ID** | `C-001` 起连续编号，**永不复用**。删掉的能力保留它的 ID（类别=删除），新增能力取新号，绝不填补空缺。 |
| **能力** | 一句话，用 IC 验证工程师的语言写「用户能做成什么事」，不是「界面上有个什么控件」。 |
| **旧入口** | `gui.py` 里的方法/属性名。`SV.` = `SignalView`（Topout + 4 个页本地视图共 5 个实例），`MW.` = `MainWindow`（含『排查(旧)』那 4900 行）。 |
| **后端 API** | `topout` / `pageviews` / `generator` / `rtl_scan` / `cli` / `sigflow` / `vectors` / `sv_writer` / `truth_edit` / `mux_gen` / `resolver` 里真正干活的函数。`—` = 纯界面行为，不过后端。 |
| **类别** | 保留 / 合并 / 降级(诊断抽屉) / 删除 / 新增。 |
| **v2 落点** | Design 映射表到手后填（L2）。 |
| **测试 ID** | v2 测试函数名含本 ID（如 `test_c042_…`），多个用 `;` 分隔（L3）。 |
| **状态** | `待落点`；执行计划 §5 的待拍板项写 `待拍板(默认=…)`。 |
| **来源** | 至少一个。`方案§x` = `推进方案_GUI_v2_20260912.md`；`计划§x` = `GUI_v2_执行计划_20260912.md`；`Design§x` = `Claude_Design_prompt_GUI_v2.md`；`审计x` = `审计_真值表能力对照_20260912.md`；`清单§x` = `GUI现状清单_20260912.md`；`6月审计x` = `通用功能丢失审计_20260624.md`；`gui:<方法名>` = 本轮 gui.py 代码普查；`TASK-xxx` = backlog。 |

**分节约定**：类别=新增的行统一收在「新增」节（信号流图除外，它自成一节），其余各节只列今天真实存在的能力。这样各节读起来就是「现状盘点」，新增需求集中一处。

**粒度约定**：一个可独立验证的用户可见行为 = 一行。「加反例」和「加反例去重」是两行；「导出 .sv」和「导出 .sv 完成反馈点名跳过项」是两行。

## 清单（49 条：保留 45，删除 2，合并 2）

> 载表 → 要验哪些信号 → 哪些建得出、哪些建不出以及为什么（Design §3 的 Q1）。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-001 | 浏览并选中 Excel 真表，选完即载入（不用再点一次加载） | MW.on_browse | excel_model.load_workbook | 保留 | 顶栏「浏览… Ctrl+O」按钮（excelPath 输入框右侧） | | 已落点 | 清单§1; 清单§5-1; gui:on_browse |
| C-002 | 重新载入当前路径的 Excel（表改了不用重启） | MW.on_load | excel_model.load_workbook | 保留 | 顶栏「载入 / 重新载入 Ctrl+L」主按钮（state 键 loadLabel） | | 已落点 | 清单§1; 清单§5-1; gui:on_load |
| C-003 | 开工具时自动把上次那张表载进来 | gui.main + _load_last_excel/_save_last_excel | excel_model.load_workbook | 保留 | 主控补位: 空态「最近打开」首条即上次表；启动时按 last_excel 自动载入并直接进工作台（Design 只画了最近打开列表，没画自动载入） | | 已落点(补位) | gui:main; gui:_load_last_excel |
| C-004 | 命令行带一个 .xlsx 启动，直接载入这张表 | gui.main（sys.argv 首个存在的文件） | excel_model.load_workbook | 保留 | 主控补位: 无界面元素；命令行带 .xlsx 启动时跳过空态直接进工作台，顶栏 excelPath 显示该路径 | | 已落点(补位) | gui:main |
| C-005 | 表读不进来时弹一句人话报错，工具不退出 | MW.on_load（QMessageBox.critical「加载失败」） | excel_model.load_workbook | 保留 | 主控补位: 空态/工作台顶部一条红色错误条（不弹窗、不退出），文案过 inputs_table.scrub_terms | | 已落点(补位) | 清单§3; gui:on_load |
| C-006 | 载完在状态栏报：信号总数、logic/mux 各几条、几条非 clean、tmm/regmap 字段数、Topout 要验几个 | MW.on_load（status.showMessage） | excel_model.load_workbook | 保留 | 状态栏 statusLeft「已载入 21 个信号（logic 6 + mux 15）· Topout 要验 10 个 · 有问题 3 个」 | | 已落点 | gui:on_load |
| C-007 | 单个信号解析炸了不连累整张表，那一行标『解析异常』并留住 error 全文 | MW.on_load / MW._reanalyze_all（逐信号 try） | generator.analyze_signal / generator.analyze_mux_group | 保留 | 清单行状态列 stS.bad + 点状态展开的行内原因块（reasonTitle / reasonBody） | | 已落点 | gui:on_load; gui:_reanalyze_all |
| C-008 | 要验信号清单 10 列：勾选/反例/信号名/断言号/owner/分类/逻辑类型/状态/探针前缀/用例数 | SV._populate_table（TOPO_HEADERS） | topout.topout_view_models | 保留 | 清单表头 6 列默认可见（勾选 / 反例 / 信号 ▴ / 状态 / 用例 / owner）+ 清单头部「列设置…」补断言号·分类·逻辑类型·探针前缀·RTL补充标记 | | 已落点 | 方案§4.1; Design§10; 清单§1; gui:_populate_table |
| C-009 | 信号名列带位宽切片显示（如 d_bt_lp_lna_itrim[3:0]），查找仍按裸名 | SV._populate_table（m['disp']） | topout.topout_view_models | 保留 | 清单信号名列（Consolas 11.5px），样例 d_wl_rf_lp5g_rxrf_lna_lctune[5:0] | | 已落点 | gui:_populate_table |
| C-010 | 断言号列 + 悬停说明「仿真 log 报 assert_<号>_T<n> 时按它回查本信号」 | SV._populate_table（TOPO_AID tooltip） | topout.topout_view_models | 保留 | 清单「列设置…」可选列「断言号」（V2Spec §5 M2） | | 已落点 | 方案§4.1; gui:_populate_table |
| C-011 | owner 列 | SV._populate_table（TOPO_OWNER） | topout.topout_view_models | 保留 | 清单 owner 列（默认可见，宽 76px） | | 已落点 | 方案§4.1; Design§10 |
| C-012 | 分类列（选路/logic · mux · 直连寄存器 · RO回读(跳过) · 未解析） | SV._populate_table（TOPO_KIND_LABEL） | topout.topout_view_models | 保留 | 清单「列设置…」可选列「分类」；详情标题栏 curKind「选路/logic」 | | 已落点 | 方案§4.1; 清单§1 |
| C-013 | 逻辑类型列（展开后表达式形态 F0–F4），覆盖度按它派发 | SV._populate_table（TOPO_FORM / m['form_label']） | topout.topout_view_models | 保留 | 清单「列设置…」可选列「逻辑类型」；覆盖度弹层 covRows 按它分档 | | 已落点 | 方案§4.1; 清单§1 |
| C-014 | 状态列 4 档（✅可建 / ↷跳过(RO) / ✗未解析 / ✗error）带颜色 | SV._populate_table（TOPO_STATUS_LABEL/COLOR） | topout.topout_view_models | 保留 | 清单状态列 stS 四色（ok #eaf6ee / warn #fdf3e0 / bad #fdecea / note #eef0f3） | | 已落点 | 方案§4.1; 清单§1 |
| C-015 | 有问题的信号状态列变橙、悬停列出全部问题原因 | SV._populate_table（m['issues'] tooltip） | topout.topout_view_models | 保留 | 清单状态列 warn 橙 + 虚线下划线；悬停改为点状态展开行内原因块（reasonBody 列全部原因） | | 已落点 | 审计B; gui:_populate_table |
| C-016 | 状态 8 档逐档解释（clean / wire兜底 / 未解析 / 解析错 / 规格冲突 / 输入缺前缀·跳过 / 输出裸名·已生成 / 字段太窄·假绿） | MW._populate_table（STATUS_HELP，legacy 状态列 tooltip） | generator.analyze_signal | 保留 | 清单状态列 + 行内原因块；Design 给了 5 档实例（可建 / ⚠字段太窄·假绿 / ✗规格冲突·待核对 / ⚠缺前缀·跳过 / 输出裸名·已生成），余 3 档按同一模板补 | | 已落点 | 方案§4.1; Design§10; 审计B; 清单§6; gui:STATUS_HELP |
| C-017 | 探针前缀列：显示该信号断言探针配的层级前缀，蓝色=已配 | SV._populate_table（TOPO_PREFIX） | topout.topout_view_models | 保留 | 清单「列设置…」可选列「探针前缀」（V2Spec §5 M4） | | 已落点 | 方案§4.1; 审计B |
| C-018 | 探针前缀列悬停给出探针网真名 + 断言完整路径（配了/没配两种文案） | SV._populate_table（TOPO_PREFIX tooltip） | topout.topout_view_models | 保留 | 主控补位: 探针前缀列悬停给探针网真名 + 断言完整路径（配了/没配两种文案）；Design 未画 tooltip | | 已落点(补位) | 审计B; gui:_populate_table |
| C-019 | 探针前缀列标出『探针口=xxx (level_shift)』——探针网名经 level_shift 页自动解析，无需手配 | MW._prefix_cell | generator.probe_prefix_for | 保留 | 主控补位: 探针前缀列单元格文案「探针口=xxx (level_shift)」，沿用旧门面写法 | | 已落点(补位) | gui:_prefix_cell |
| C-020 | 探针前缀列标出输入侧命中：『<输入名>→<前缀>』——某根 force 输入的路径带前缀 | MW._prefix_cell（found_in=='prefixed-wire'） | resolver.Resolver | 保留 | 主控补位: 探针前缀列单元格文案「<输入名>→<前缀>」，沿用旧门面写法 | | 已落点(补位) | gui:_prefix_cell |
| C-021 | 用例数列（本信号会产出几条测试） | SV._populate_table（TOPO_NTEST） | topout.topout_view_models | 保留 | 清单「用例」列（默认可见，右对齐 Consolas） | | 已落点 | 方案§4.1; Design§10 |
| C-022 | 逐信号勾选（决定哪些信号进导出/预览） | SV._checked_names / SV._on_sig_item_changed | topout.render_topout_sv(only=) | 保留 | 清单第 1 列勾选框（state 键 S.checks，box() 蓝 #2f6fd0）—— 用户标注截图 c22c965c 圈定此列 | | 已落点 | 方案§4.1; Design§10 |
| C-023 | 逐信号反例勾选（给该信号加 1 条故意填错的自检用例） | SV._toggle_signal_negative | vectors.make_negative / vectors.add_negatives | 保留 | 清单第 2 列反例勾选（state 键 S.negs，box() 琥珀 #b4690e，✕ 标记） | | 已落点 | 方案§4.1; Design§10 |
| C-024 | 按 owner 多选筛（勾多个=任一命中即显示；不勾=全部） | SV._rebuild_owner_menu / SV._on_owner_toggled（_CheckableMenu） | topout.topout_view_models | 保留 | 筛选行「全部 owner ▾」多选下拉 | | 已落点 | 方案§4.1; Design§10; 审计B |
| C-025 | owner 下拉里『（无 owner）』单列一项并带条数 | MW._rebuild_owner_menu（NO_OWNER） | — | 保留 | 主控补位: owner 下拉里「（无 owner）」单列一项并带条数（Design 只画了收起态按钮） | | 已落点(补位) | gui:_rebuild_owner_menu |
| C-026 | owner 按钮文字随已选个数变化，悬停列全部已选 owner | MW._update_owner_btn_text | — | 保留 | 主控补位: owner 按钮文字随已选个数变、悬停列全部已选（Design 只画了「全部 owner ▾」） | | 已落点(补位) | gui:_update_owner_btn_text |
| C-027 | 换表后自动丢掉新表里已不存在的 owner 选项（不留死项、计数不虚高） | SV._rebuild_owner_menu / MW._rebuild_owner_menu | — | 保留 | 主控补位: 换表后重建 owner 菜单、丢掉新表没有的 owner（无界面元素） | | 已落点(补位) | gui:_rebuild_owner_menu |
| C-028 | 按分类筛（下拉只列本表真有的分类） | SV._rebuild_kind_combo / SV._apply_filter | topout.topout_view_models | 保留 | 筛选行「全部分类 ▾」下拉 | | 已落点 | 方案§4.1; Design§10 |
| C-029 | 按状态筛（全部 / 仅可建 / 仅有问题） | SV._apply_filter（status_combo） | topout.topout_view_models | 保留 | 筛选行「全部状态 ▾」下拉 | | 已落点 | 方案§4.1; Design§10; 审计B |
| C-030 | 正则搜索：同时匹配信号名、断言号、表达式、输入信号名 | SV._apply_filter（search） | topout.topout_view_models | 保留 | 筛选行搜索框「搜索：信号名 / 表达式 / 输入信号名（支持正则）」 | | 已落点 | 方案§4.1; Design§10; 审计B |
| C-031 | 搜索/筛选后状态栏报『可见 N / 共 M』，并单独点出有几个是因为输入信号名命中才列出来的 | MW.apply_filter（n_by_input） | — | 保留 | 主控补位: 筛选后在 statusLeft 追加「可见 N / 共 M（其中 K 个按输入信号名命中）」 | | 已落点(补位) | gui:apply_filter |
| C-032 | 全选所有可见信号 | SV._check_all(True) | — | 保留 | 清单底部工具条「全选」 | | 已落点 | 方案§4.1; Design§10 |
| C-033 | 清空全部勾选 | SV._check_all(False) | — | 保留 | 清单底部工具条「清空勾选」 | | 已落点 | 方案§4.1; Design§10 |
| C-034 | 把表里框选/Ctrl 多选的行一次勾上 | SV._check_selected / MW.on_check_selected_rows | — | 保留 | 清单底部工具条「勾选选中行」 | | 已落点 | 方案§4.1; Design§10; 审计B |
| C-035 | 一键给一批信号各加 1 条反例（有勾选只作用勾选，否则作用于全部可见） | SV._bulk_neg(True) / MW.on_all_signals_neg(True) | vectors.add_negatives | 保留 | 清单底部工具条「全部加反例」 | | 已落点 | 方案§4.1; Design§10; 审计B |
| C-036 | 一键清除一批信号的反例；含自定义命名/手填错值的先弹确认 | SV._bulk_neg(False) / MW.on_all_signals_neg(False) + MW._confirm_lose_named | — | 保留 | 清单底部工具条「清除反例」；二次确认按 V2Spec §5 M22「三处确认」同款补 | | 已落点 | 方案§4.1; 审计A; gui:_confirm_lose_named |
| C-037 | 点表头按列排序（211 行按 owner / 用例数 / 状态排） | MW.table.setSortingEnabled(True)（legacy 独有） | — | 保留 | 清单表头点击排序（表头「信号 ▴」）+ 清单头部「排序」入口 | | 已落点 | 方案§4.1; Design§10; 审计B; 审计Top10-7 |
| C-038 | 范围切换：全链 / 只看 logic 页 / mux 页 / dft 页 / iddq 页（页本地、不跨页） | MainWindow.main_tabs 的 4 个 _PageProvider 子视图 | pageviews.page_view_models / pageviews.PAGES | 合并 | 筛选行第一个控件「范围」5 段（Topout 全 / 只看 logic / 只看 mux / dft / iddq，state 键 scopes；scopeHint「Topout = 全链展到源寄存器；其余 = 只看本页输入输出，不跨页」）—— 用户标注截图 2bca924f 圈定此处 | | 已落点 | 方案§4.1; 方案§4.3; Design§5-H1; Design§10; 主控裁决: 按 Design§5-H1 归清单范围筛选 |
| C-039 | RTL 补充逻辑信号在清单里琥珀高亮 + ⚠[RTL补充] 标记 + 悬停给理由 | MW._populate_table（_is_supplement 分支） | generator._logic_with_overrides | 保留 | 清单信号名后琥珀圆点 + 悬停给理由（V2Spec §5 M11）；原横幅改为状态栏一行 | | 已落点 | 方案§4.1; Design§10; 审计B; 审计次要 |
| C-040 | 嵌套 mux 被自动折叠过的信号，状态格加 ⚙ 标记、悬停给折叠全文 | MW._populate_table（normalized_note） | excel_model（normalized_note） | 保留 | 主控补位: 状态格加 ⚙ 标记 + 悬停给折叠全文（Design 未画嵌套 mux 折叠态） | | 已落点(补位) | gui:_populate_table |
| C-041 | 开了『缺前缀强制生成』后，状态列如实写成『⚠缺前缀·已强制生成』而不是『跳过』 | MW._populate_table（include_risky 分支） | generator.GenOptions(include_risky=) | 保留 | 主控补位: 状态列增加第 6 档「⚠ 缺前缀·已强制生成」（Design D 只给了「⚠ 缺前缀·跳过」），与诊断抽屉开关联动 | | 已落点(补位) | gui:_populate_table |
| C-042 | 本表没有该页时给空态提示而不是空白表 | SV.refresh + provider.empty_hint / SV._guard | pageviews.page_available | 保留 | 范围开关上该页置灰 + 标「本表无 dft 页」（V2Spec §3 状态一：不弹窗不报错，状态栏写「本表无 dft / iddq 页，门控层跳过」） | | 已落点 | gui:empty_hint |
| C-043 | 分析整表出错时在详情区写『分析失败（已捕获，未崩）』并留住原因，界面不崩 | SV.refresh（except 分支） | topout.topout_view_models | 保留 | 主控补位: 详情区写「分析失败（已捕获，未崩）」+ 原因全文（Design §3 只给了单信号未解析态） | | 已落点(补位) | gui:refresh |
| C-044 | 刷新清单后自动选中第一个可见行（直接就能看到内容） | SV.refresh 末尾 | — | 保留 | 清单刷新后默认选中第一行（state 键 S.sel=0，选中底色 #dbe7f8） | | 已落点 | gui:refresh |
| C-045 | 清单默认可见列必须包含『状态』和『用例』（现在 1600px 宽时它们在横滚条后面） | SV._build（split.setSizes([440,760])） | — | 保留 | V2Spec §4「默认可见列必须含 状态 与 用例，任何布局下都不准进横向滚动条」；清单默认宽 S.listW=560 | | 已落点 | Design§8-7; 清单§2; 清单§7; 方案§1 |
| C-046 | 清单里直接看到表达式（不必逐个点开） | MW._populate_table（COL_EXPR，legacy 独有） | — | 保留 | 主控补位: 「表达式」做成「列设置…」里的可选列、默认隐藏（执行计划 §5 待拍板 4 默认值；Design 映射表未列） | | 已落点(补位) | 计划§5-4; 审计B; 原 status: 待拍板(默认=可选列、默认隐藏) |
| C-047 | 按 Excel type 列筛（如只看喂 dft 的那一批） | MW.apply_filter（type_combo，legacy 独有） | — | 合并 | 主控补位: Excel type 筛并入筛选行正则搜索（支持 _to_dft 这类目的地后缀），不单独出控件（执行计划 §5 待拍板 4） | | 已落点(补位) | 计划§5-4; 审计B; 审计次要; 原 status: 待拍板(默认=并入正则搜索) |
| C-048 | 只看 top_output=1 的信号 | MW.apply_filter（self.top_only） | — | 删除 | | | 待落点 | 方案§4.4; 方案§5-2; 用户2026-06-23; 清单§1 |
| C-049 | 清单列头直接写 Excel 列字母 R / K / top / type | gui.HEADERS（legacy 列头） | — | 删除 | | | 待落点 | 方案§4.4; 方案§5-2; 用户2026-06-23; 清单§6 |
| C-303 | 清单「反例」「状态」「断言号」三个列头自带说明 tooltip（不查文档就知道这一列是什么） | MW 清单列头 tooltip（旧测试 test_gui_header_tooltips_present） | — | 新增 | signal_list 列头 ToolTipRole，文案进 terms.LIST_HEADER_TIPS；F2 落地 |  | 待落点 | C5-a 迁移 §2（legacy_only 待裁决 → 加契约行） | 落地后删 legacy_only 测试 test_gui_header_tooltips_present |

## 详情（23 条：保留 23）

> 选中一个信号后：逻辑长什么样、每条测试驱动了什么探了什么、名字可信吗（Q2 / Q3）。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-050 | 跨页展开链：每层两行（Excel 原式 / 字母代入真实信号名），①②③ 标层级 | SV._render_chain / MW._populate_chain | topout.analyze_signal（res.chain） | 保留 | 右侧常驻栏上半「逐层展开」（S.chainH=300）：①②③ 每层小字页标签 + Excel 原式 + 「= 代入真实信号名」两行 | | 已落点 | 方案§4.1; Design§3-Q2; Design§10; 审计A |
| C-051 | 没有上游的单级 logic 信号，链区显示『原式 / 字母代入真名』并说明输入均为直连叶子 | SV._render_chain（kind=='logic' 分支） | pageviews.analyze_page_signal | 保留 | 「逐层展开」②「logic 页」样式：原式 A & (B \| C) + 代入真名行 | | 已落点 | gui:_render_chain |
| C-052 | mux 信号链区显示 case 选路结构（控制值 → 选中的数据源） | SV._render_chain（kind=='mux' 分支） | mux_gen.expand_mux_group | 保留 | 「逐层展开」③「mux 页 · mux3」样式：case(ctrl){1'b0: …; 1'b1: …} | | 已落点 | gui:_render_chain |
| C-053 | 直连寄存器信号链区说明『顶层口 == 该寄存器字段写值，无展开』 | SV._render_chain（kind=='register' 分支） | topout.analyze_signal | 保留 | 主控补位: 直连寄存器信号链区文案「顶层口 == 该寄存器字段写值，无展开」（Design 只在电路图 small 变体给了「寄存器直连顶层，中间无逻辑」） | | 已落点(补位) | gui:_render_chain |
| C-054 | 不可建信号在链区直接写出状态与原因（不是空白） | SV._render_chain（else 分支） | topout.analyze_signal（status/note/issues） | 保留 | 清单行内原因块 reasonTitle / reasonBody；右侧链区同步写状态与原因（V2Spec §3「未解析的信号」：电路图画到能画的那层，断点用红色端子标「展不下去」） | | 已落点 | gui:_render_chain |
| C-055 | 详情头部一行摘要：信号名 / owner / 分类 / 状态 / 用例数 / 是否已自定义 / note / issues | SV._render_head | topout.analyze_signal | 保留 | 详情标题栏 curName + curStatus + 「owner {curOwner} · {curKind} · 用例 {curCases}」；note / issues / 已自定义 三项与行内原因块同源 | | 已落点 | gui:_render_head |
| C-056 | 『输入信号』表常驻：字母 → 信号(位宽) · 角色 · RO/RW · 驱动方式 | MW.ti_inputs + MW._populate_inputs（legacy 独有） | vectors.input_groups / resolver.Resolver.resolve | 保留 | 右侧常驻栏下半「输入信号 6 个」表（字母 / 信号(位宽) / 角色 / 类型 四列，state 键 inputs=IN） | | 已落点 | 方案§4.1; Design§3-Q3; Design§10; 审计A; 审计Top10-1; 清单§7 |
| C-057 | 输入表把 RO 写成 force ENV_RF.<网名>、RW 写成 RF_WRITE 0x<地址> bit<<<位> | MW._binding_meta | sv_writer.compute_drives / resolver.Resolver | 保留 | 输入信号表每行下方第二行驱动串（蓝 #1d4f9c）：force ENV_RF.<网名> / RF_WRITE 0x64 bit<<0 | | 已落点 | 方案§4.1; Design§3-Q3; 审计A |
| C-058 | 输入表标出『⚠需探针前缀（内部衔接网，跑 scan_rtl 配前缀否则跳过）』 | MW._binding_meta（found_in in needs-prefix/mux-output） | resolver.Resolver | 保留 | 主控补位: 输入表「⚠需探针前缀（跑 scan_rtl 配前缀否则跳过）」标记（Design 只区分了「猜名」橙虚下划线，没区分「需前缀」） | | 已落点(补位) | gui:_binding_meta |
| C-059 | 输入表的驱动列上色：未解析=红、需前缀=琥珀 | MW._populate_inputs / MW._populate_mux_inputs | — | 保留 | 主控补位: 驱动行上色分两档——未解析=红 #a9302a、需前缀=琥珀 #8a6412（Design 驱动行统一蓝色） | | 已落点(补位) | gui:_populate_inputs |
| C-060 | mux 信号的输入表按控制三来源细分角色（寄存器直出 / line路径 / local路径 / 模式位 / 经上游mux驱动 / 上游mux配方） | MW._populate_mux_inputs + MW._mux_ctrl_rows | mux_gen.expand_mux_group（ctrl_drivers） | 保留 | 主控补位: 输入表角色列按 mux 控制三来源细分（寄存器直出 / line路径 / local路径 / 模式位 / 经上游mux驱动 / 上游mux配方）；Design 角色列只有 控制/选择位·数据位·DFT门(iddq) | | 已落点(补位) | 清单§1; gui:_mux_ctrl_rows |
| C-061 | mux 输入表的数据寄存器按物理寄存器收拢（同一寄存器只显一行）并标『被哪个 case 选中』 | MW._populate_mux_inputs（seen_bases） | mux_gen.expand_mux_group（data_keys） | 保留 | 主控补位: mux 数据寄存器按物理寄存器收拢成一行并标「被哪个 case 选中」（Design 的 IN 按 mux3.d0/d1 逐口列出，未收拢） | | 已落点(补位) | gui:_populate_mux_inputs |
| C-062 | 输入表单列出 dft 页的 iddq 门（角色『DFT门(iddq)·每条测试驱透传值』） | MW._dft_gate_input_row | generator.pin_dft_gate | 保留 | 输入信号表末行「dft 页 · d_wl_rf_trx_reg_dft_iddq_mode · DFT门(iddq) · RO · force ENV_RF.…」 | | 已落点 | gui:_dft_gate_input_row |
| C-063 | 每条测试列的 force / RF_WRITE 明细（列头与单元格悬停都给） | MW._ti_render_col + MW._drive_strs | sv_writer.compute_drives | 保留 | 主控补位: 真值表列头与单元格悬停给该列 force / RF_WRITE 明细（inputs_table.column_drives）；Design 列头只有 T0…T24 | | 已落点(补位) | 方案§4.1; Design§10; 审计A; 审计Top10-1 |
| C-064 | 点信号看解析明细：逐字母 输入名 / 类型 / 来源 / 是否解析到 / 最终 net 名 / note | MW.on_row_focus | generator.analyze_signal（inputs） | 保留 | 详情标题栏「解析明细」按钮 → 面板（inputs_table.resolve_detail） | | 已落点 | 方案§4.1; Design§10; 审计B; 审计Top10-1 |
| C-065 | 解析明细里把输入来源翻成中文（tmm命中 / regmap命中 / 级联前级 / 内部信号 / wire兜底 / 前缀wire / 自引用前级 / 需探针前缀 / 上游计算网） | gui.FOUND_IN_LABEL（MW.on_row_focus 用） | resolver.Resolver（found_in） | 保留 | 主控补位: 解析明细里 FOUND_IN_LABEL 九种来源的中文翻译（Design 术语表只覆盖 prefixed-wire / wire 兜底两条） | | 已落点(补位) | 计划§2-⑥; gui:FOUND_IN_LABEL |
| C-066 | 解析明细末尾给『仿真器找不到这根网』的排查提示（对比 force/输出 net 名是否真存在） | MW.on_row_focus 末尾 | — | 保留 | 解析明细末尾「仿真器找不到这根网」排查提示，直链诊断抽屉三步（V2Spec §5 M17） | | 已落点 | 方案§4.1; Design§3-Q6; 审计B |
| C-067 | 预览勾选集的 .sv（含真值表编辑，所见即所得） | SV.on_preview / MW.on_preview | topout.render_topout_sv / generator.build + generator.render | 保留 | .sv 预览标签（S.tab='sv'）工具条「改看勾选集」按钮 | | 已落点 | 方案§4.1; Design§10; 清单§1 |
| C-068 | 预览本信号的 .sv 片段（不必取消其余 210 条勾选） | MW.on_ti_preview_signal（legacy 独有） | sv_writer.render_signal_block | 保留 | .sv 预览标签标题「本信号 .sv 片段（不落盘）」+ svText 正文 | | 已落点 | 方案§4.1; Design§10; 审计A; 审计Top10-5 |
| C-069 | 全量预览超过 600 行时截断并注明总行数（不卡界面） | MW.on_preview（lines[:600]） | — | 保留 | 主控补位: 全量预览超 600 行截断并注明总行数（Design 的 svText 只是 9 行样例） | | 已落点(补位) | gui:on_preview |
| C-070 | 全量预览末尾追加本次会跳过哪些信号、各自缺哪根输入 | MW.on_preview 末尾 appendPlainText | generator.build（res['skipped']） | 保留 | 主控补位: 全量预览末尾追加本次跳过哪些信号、各缺哪根输入（Design 把点名放在「导出完成」弹层） | | 已落点(补位) | gui:on_preview |
| C-071 | 本信号覆盖度下拉就在详情区（对着当前这一个信号调档） | SV.sig_cov_combo / MW.sig_cov_combo | topout.analyze_signal(mode=,exhaustive=) | 保留 | 覆盖度弹层 covRows 末行「本信号 · d_wl_rf_lo2g5g_bias_en · 跟随上级」（可改档） | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-072 | 配置变更（前缀/级联/尾缀）后预览页自动按新配置重算，不留旧内容误导 | MW._refresh_preview（_preview_source） | — | 保留 | 主控补位: 配置变更（前缀 / 强制force / 补充逻辑）后 .sv 预览按新配置自动重算（无界面元素） | | 已落点(补位) | gui:_refresh_preview |

## 真值表编辑（69 条：保留 68，合并 1）

> 期望值是谁给的、怎么手填、怎么造反例（Q4）。粒度到「一个可独立验证的用户可见行为」。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-073 | 真值表：行=输入信号 + auto_out + 期望，列=一条测试 | SV._populate_truth / MW._ti_populate | vectors.generate_vectors | 保留 | 真值表区（S.truthH=420）：冻结列 = 6 输入行 + auto_out 行 + 期望行；网格列 = T0…T24 | | 已落点 | 方案§4.1; Design§3-Q2; Design§10 |
| C-074 | 行表头直接写真实信号名（不写 A/B/C 字母），控制/选择位加粗 | MW._vheader_short / SV._populate_truth | vectors.input_groups | 保留 | 冻结列行标签「<真实信号名>　(控制位 · mux3.ctrl0)」；控制位加粗按 V2Spec §4「控制/选择位加粗」补 | | 已落点 | Design§8-5; 清单§7; gui:_vheader_short |
| C-075 | 行表头悬停给出『信号 = 表达式里的哪个字母 · 类型 · 位宽 · 控制位还是数据位』 | MW._ti_populate（verticalHeaderItem tooltip） | — | 保留 | 角色已内联进行标签括号（控制位 · mux3.ctrl0 / 数据位 · mux3.d1 / DFT 门(iddq) · 猜名），tooltip 作补充 | | 已落点 | Design§8-5; gui:_ti_populate |
| C-076 | 输入行按 for_test 页的行序排（与报告/回填/截图同一口径） | gui._order_groups_fortest / MW._load_test_items | generator.fortest_order_entries | 保留 | 主控补位: 输入行按 for_test 页行序排（analysis_norm.order_groups_fortest），与报告/回填同口径 | | 已落点(补位) | gui:_order_groups_fortest |
| C-077 | auto_out 行只读、斜体灰字，悬停说明『它来自表达式本身，拿它当期望有自证嫌疑』 | SV._populate_truth / MW._ti_populate | — | 保留 | auto_out 行「auto_out　（程序按表达式算的，只读参考）」灰底灰字 #5d6773；斜体与「有自证嫌疑」悬停按 V2Spec §4 只读态补 | | 已落点 | 方案§4.1; Design§3-Q4; gui:_ti_populate |
| C-078 | 期望行可逐格手填，.sv 断言用它；留空则生成时用 auto_out 兜底 | SV._on_truth_item / MW.on_ti_item_changed | vectors.make_vector_from_base_values(designer_expected=) | 保留 | 期望行「期望　（designer 手填，进 .sv 断言）」逐格可编辑；留空 = 待手填态（白底虚线框，生成时用程序算的值兜底） | | 已落点 | 方案§4.1; Design§3-Q4; 方案§5-5 |
| C-079 | 期望格状态色：绿=已填且与 auto 一致，红=已填但不一致，灰=未填，琥珀=反例，淡紫=iddq 自检拍，淡蓝=手编列 | SV._populate_truth（DSGN_BG/DIFF_BG/FB_FG/NEG_BG/DFT_BG/USER_BG） | — | 保留 | V2Spec §4「真值表单元格状态色」6 态：#eaf6ee/#a9d6ba/#14603b 一致 · #fdecea/#e5a9a4/#a9302a 不一致 · #fff/#c3c9cf 虚线 待手填 · #fdf3e0/#e6c98a/#8a6412 反例 · #eef4fd/#a9c4ea/#1d4f9c iddq 拍 · #fafbfc/#e3e7eb/#5d6773 只读 | | 已落点 | 计划§2-⑥; Design§11-4; gui:_populate_truth |
| C-080 | 改任一输入值 → auto_out 当场重算；已手填的期望不动 | SV._recompute_col / MW._recompute_row | vectors.make_vector_from_base_values | 保留 | 真值表 model：改输入格 → auto_out 当场重算、已手填期望不动（Design mock 的 auto 由 ctrl/d1/d0/A/C/iddq 现算，口径一致） | | 已落点 | 方案§4.1; Design§10 |
| C-081 | 期望格清空 = 恢复未填状态（回到 auto_out 兜底） | SV._on_truth_item（txt==''）/ MW.on_ti_item_changed | — | 保留 | 期望格清空 → 回到「待手填」白底虚线框态 | | 已落点 | gui:_on_truth_item |
| C-082 | 8 种数值写法都认：16'h3 / 'b101 / 'd9 / hA / 0x3 / 0b101 / 十进制 / 裸 hex | truth_edit.parse_int（SV._on_truth_item / MW._parse_int 共用） | truth_edit.parse_int | 保留 | 主控补位: 8 种数值写法（truth_edit.parse_int）在单元格编辑器内接受；Design 样例全是 0/1 未体现 | | 已落点(补位) | 方案§4.1; Design§10; 审计A; 审计Top10-2 |
| C-083 | 数值写法没认出来时状态栏说清楚并把这一格还原，绝不静默吞成 0 / 未填 | SV._parse_failed / MW.on_ti_item_changed（except ValueError） | truth_edit.parse_int | 保留 | 主控补位: 解析失败 → 状态栏说清楚 + 该格还原（A5 §5 model 的 parseFailed(str) 已预留） | | 已落点(补位) | 方案§4.1; 审计A; 审计Top10-2 |
| C-084 | 单元格取值显示格式：1 位→0/1；2–4 位→0bXXXX 零填充；更宽→0xNN | MW._cell_text | — | 保留 | 主控补位: 单元格显示格式 1位→0/1、2–4位→0bXXXX、更宽→0xNN（Design 样例是 1 bit） | | 已落点(补位) | gui:_cell_text |
| C-085 | 重新生成：丢弃本信号自定义，按当前覆盖度重出真值表 | SV._e_regen / MW.on_ti_regen | vectors.generate_vectors | 保留 | 真值表工具条「重新生成」 | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-086 | 加一条正向测试列（输入全 0、auto_out 自动算、期望留空） | SV._e_add / MW.on_ti_add | vectors.make_vector_from_base_values | 保留 | 真值表工具条「加列」 | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-087 | 复制选中的测试列 | SV._e_copy / MW.on_ti_copy | vectors.clone_vector | 保留 | 真值表工具条「复制列 Ctrl+D」—— 与顶栏「诊断 Ctrl+D」撞键，见 Design对齐 §4 冲突① | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-088 | 删除选中的测试列（支持多选） | SV._e_del / MW.on_ti_del | — | 保留 | 真值表工具条「删列」 | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-089 | 清零本信号=零用例（导出时本信号只记账不产断言） | SV._e_clear / MW.on_ti_clear | topout.build_for_topout(edit_overrides=) | 保留 | 真值表工具条「清零…」 | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-090 | 清零前弹确认框（说明后果、默认按钮=否） | SV._confirm / MW.on_ti_clear | — | 保留 | 「清零…」省略号即二次确认（V2Spec §5 M22「三处都加二次确认并在表上留痕」） | | 已落点 | 方案§4.1; 审计A; 审计Top10-6 |
| C-091 | 给用户新增的列改名（双击列头或按钮） | SV._e_rename / SV._e_rename_col / MW.on_ti_rename_col | truth_edit.check_col_name | 保留 | 真值表工具条「重命名列…」；也可双击列头 | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-092 | 改名三道校验：非法字符清成下划线且不得为空 / 不许占 T<编号> 保留名 / 不得与其它列最终标号重名 | truth_edit.check_col_name（SV._e_rename_col / MW._ti_set_test_name 共用） | truth_edit.check_col_name | 保留 | 「重命名列…」接 truth_edit.check_col_name 三道关（V2Spec §5 M23「名称校验 + 拒 T<n> + 查重」） | | 已落点 | 方案§4.1; 审计A; 审计Top10-4 |
| C-093 | 自动生成的 T 列不许改名，点了给明确提示 | SV._e_rename_col / MW.on_ti_rename_col（user_added 判定） | — | 保留 | 同 V2Spec §5 M23：自动生成的 T 列拒改名并给明确提示 | | 已落点 | gui:_e_rename_col |
| C-094 | auto→期望：把 auto_out 一次填进所有未填的期望格，已填的不动 | SV._e_fill / MW.on_ti_fill_expected | — | 保留 | 真值表工具条「auto→期望…」（琥珀描边 #c9922a，与普通按钮区分） | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-095 | auto→期望前弹确认框，明说这等于放弃 designer 独立核对 | SV._e_fill / MW._confirm_fill_expected | — | 保留 | 「auto→期望…」二次确认 + 表上留痕（V2Spec §5 M22） | | 已落点 | 方案§4.1; Design§3-Q4; 审计A; 审计Top10-6 |
| C-096 | 给选中的正向列各加一条反例（未选则取首条正向） | SV._e_addneg / MW.on_ti_add_neg_selected | truth_edit.plan_negatives / vectors.make_negative | 保留 | 真值表工具条「加反例（选中列）」（琥珀 #e6c98a）—— 用户标注截图 43a5d90d 圈定单列操作 | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-097 | 同一组输入取值已经有反例的跳过，不叠重复断言，并在状态栏报跳过几条 | truth_edit.plan_negatives（SV._add_negatives / MW.on_ti_add_neg_selected） | truth_edit.plan_negatives | 保留 | 主控补位: 同输入取值已有反例的跳过并在状态栏报跳过几条（truth_edit.plan_negatives） | | 已落点(补位) | 方案§4.1; 审计A; 审计bug① |
| C-098 | 只对正向列造反例，不给反例列再套反例 | truth_edit.plan_negatives（all_positive 分支） | truth_edit.plan_negatives | 保留 | 主控补位: 只对正向列造反例、不给反例列套反例 | | 已落点(补位) | 审计bug①; 方案§3-轨0-1 |
| C-099 | 新列名对全集唯一（含 T<n> 自动列与 _NEG 反例列，大小写无关），不产生重复 .sv 标号 | truth_edit.uniq_col_name（SV._new_col_name） | truth_edit.uniq_col_name | 保留 | 新列名对全集唯一（含 T<n> 与 _NEG），走 truth_edit.uniq_col_name；并入 V2Spec §5 M23 查重 | | 已落点 | 审计bug①; 方案§3-轨0-1 |
| C-100 | 反例错值同时避开 auto_out 和 designer 手填期望两个『正确值』，防止反例静默 PASS | SV._add_negatives（V.make_negative 防撞） | vectors.make_negative | 保留 | 主控补位: 反例错值同时避开 auto_out 与手填期望两个正确值 | | 已落点(补位) | gui:_add_negatives |
| C-101 | 每条正向用例各追加一条反例（全覆盖，用例数翻倍） | SV._e_addneg_all / MW.on_ti_add_neg_all | vectors.add_negatives | 保留 | 清单底部工具条「全部加反例」+ 真值表工具条「加反例（选中列）」（V2Spec §5 M24 两处合并） | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-102 | 删除本信号全部反例、保留正向 | SV._e_delneg / MW.on_ti_del_neg | — | 保留 | 真值表工具条「删反例…」 | | 已落点 | 方案§4.1; Design§10; 审计A |
| C-103 | 删反例前，若有自定义命名或手填错值的反例先弹确认（那是用户的活） | SV._protected_negatives + SV._e_delneg / MW.on_ti_del_neg | truth_edit.is_auto_neg_name | 保留 | 「删反例…」省略号 = 有自定义命名/手填错值时先确认（V2Spec §5 M22） | | 已落点 | 方案§4.1; 审计A; 审计Top10-6 |
| C-104 | 反例期望被填成与正确值相同时，仍保住它的反例身份（记成 NEG-BROKEN，不静默退化成普通断言） | SV._cols_to_vectors（is_negative 兜底） | vectors.make_vector_from_base_values | 保留 | 主控补位: 反例期望被填成正确值时记成 NEG-BROKEN、保住反例身份 | | 已落点(补位) | 审计A; gui:_cols_to_vectors |
| C-105 | mux 反例错值等于正确值时自动翻一位并在状态栏说明 | MW._on_mux_user_exp_changed | — | 保留 | 主控补位: mux 反例错值撞正确值时自动翻一位并在状态栏说明 | | 已落点(补位) | 方案§4.1; 审计A |
| C-106 | 期望手填进度 N/M 显示在真值表头部 | MW._update_ti_header（fill_tag）/ MW._update_mux_header | — | 保留 | 详情标题栏「手填期望 7/25」+ 28% 绿色进度条；主视图标签也带「25 列 · 手填 7/25」 | | 已落点 | 方案§4.1; Design§3-Q4; Design§10; 审计A; 审计次要 |
| C-107 | 当前测试列整列淡蓝高亮（30 列横滚时不丢『我在哪一列』）；反例列被选中时用更深的琥珀 | MW._ti_on_current_col + MW._ti_render_col（HL_BG / HL_NEG_BG） | — | 保留 | 当前列整列高亮：列头 #c9dcf5、列体 #eef5fd、左右 1px #2f6fd0、期望格 outline 2px #2f6fd0（样例列 ACT=T13） | | 已落点 | 方案§4.1; 审计A; 审计次要 |
| C-108 | 列宽拖过之后重建表格保留手动宽度，换信号才恢复自动适应 | MW._on_ti_section_resized + MW._ti_fit_columns | — | 保留 | 主控补位: 列宽拖过后重建保留手动宽度、换信号才恢复自动（A5 已断言列宽在重绘/插列后不动）；Design 只画了冻结列宽 S.frozenW=288 可拖 | | 已落点(补位) | 方案§4.1; 审计A; 审计次要 |
| C-109 | Ctrl+D 复制列；单元格正在编辑时不触发（不打断输入） | MW._build_testitems_tab（QShortcut WidgetShortcut on ti_table） | — | 保留 | 真值表工具条「复制列 Ctrl+D」角标；单元格编辑中不触发（WidgetShortcut 范围） | | 已落点 | 方案§4.1; Design§9; 审计A; 审计次要 |
| C-110 | mux 数据值整表手填：按物理寄存器同步整行，清空恢复自动分配 | SV._set_mux_data / MW._on_mux_data_changed | mux_gen.make_mux_vectors(data_overrides=) | 保留 | 真值表工具条整表入口（V2Spec §5 M26「工具条整表」） | | 已落点 | 方案§4.1; Design§10; 审计A; 6月审计N8 主控 2026-09-12：mux 数据值随 view_edits 落盘（R2-01 / F1） |
| C-111 | mux 单列数据值手填（只改本列，auto_out 按路由 case 的数据源重算） | SV._set_mux_user_data / MW._on_mux_user_data_changed | mux_gen.expand_mux_group（data_keys） | 保留 | 真值表右键列菜单「设置本列 mux 数据值」（V2Spec §5 M26；提示条「右键行/列：插入 · 删除 · 复制 · 设为反例」） | | 已落点 | 方案§4.1; Design§10; 审计A; 审计Top10-8 |
| C-112 | 已编辑过的 mux 信号改数据值后屏幕值与内部/导出值一致（所见即所得） | SV._mux_resync_cols | mux_gen.make_mux_vectors | 保留 | 主控补位: mux 改数据值后屏幕值与内部/导出值一致（edits.mux_resync_cols） | | 已落点(补位) | 审计bug②; 方案§3-轨0-2 |
| C-113 | mux 手填数据值撞值时提示『≥2 条数据路取到相同值=选错路也测不出（假绿）』 | MW._load_mux_test_items（meta['override_collision']） | mux_gen.make_mux_vectors | 保留 | 主控补位: mux 手填数据值撞值时提示「≥2 条数据路取到相同值 = 选错路也测不出（假绿）」 | | 已落点(补位) | gui:_load_mux_test_items |
| C-114 | mux 手填期望按输入取值键存，切覆盖度不串号 | MW._on_mux_exp_changed（generator.mux_assign_key） | generator.apply_mux_expected | 保留 | 主控补位: mux 手填期望按输入取值键存、切覆盖度不串号（generator.mux_assign_key）；无界面元素 | | 已落点(补位) | gui:_on_mux_exp_changed |
| C-115 | mux 加正向列：克隆选中列的 case 成一条可改名、可改本列数据值的手编列 | MW._mux_add_col | vectors.clone_vector | 保留 | 真值表工具条「加列」的 mux 语义（克隆选中列的 case 成可改名可改数据值的手编列） | | 已落点 | 方案§4.1; 审计A |
| C-116 | mux 复制列（数据值与期望一并带走，自动换新名避免标号冲突） | MW._mux_copy_col | vectors.clone_vector | 保留 | 真值表工具条「复制列」的 mux 语义（数据值与期望一并带走、自动换新名） | | 已落点 | 方案§4.1; 审计A |
| C-117 | mux 删列：自动生成列按签名记下（可重新生成恢复），手编列按对象删，全局反例列删=清整信号反例标记 | MW._mux_del_cols | generator.mux_assign_key / GenOptions.mux_dropped | 保留 | 真值表工具条「删列」的 mux 语义（自动列按签名记下可恢复 / 手编列按对象删 / 全局反例列删=清整信号反例标记） | | 已落点 | 方案§4.1; 审计A |
| C-118 | mux 清空=零用例（与覆盖度无关） | MW.on_ti_clear（mux 分支 → _mux_cleared） | generator.GenOptions(mux_cleared=) | 保留 | 真值表工具条「清零…」的 mux 语义（零用例，与覆盖度无关） | | 已落点 | 方案§4.1; 审计A |
| C-119 | mux 重新生成：丢弃清空/删列/手填期望/数据值/手编列/反例，回出厂态 | MW.on_ti_regen（mux 分支） | — | 保留 | 真值表工具条「重新生成」的 mux 语义（回出厂态） | | 已落点 | 方案§4.1; 审计A |
| C-120 | mux 手编列改名（自动生成列与勾选产生的自检列拒绝改名） | MW._mux_rename_col | sv_writer.test_label | 保留 | 真值表工具条「重命名列…」的 mux 语义（自动列与自检列拒改名） | | 已落点 | 方案§4.1; 审计A |
| C-121 | mux 逐 case 加反例（已有相同反例的跳过） | MW._mux_add_neg | vectors.make_negative | 保留 | 真值表工具条「加反例（选中列）」的 mux 逐 case 语义 | | 已落点 | 方案§4.1; 审计A |
| C-122 | mux 全部正向列加反例 | MW.on_ti_add_neg_all（mux 分支） | vectors.make_negative | 保留 | 清单底部「全部加反例」的 mux 语义 | | 已落点 | 方案§4.1; 审计A |
| C-123 | mux 删反例（手编反例列 + 整信号反例标记一起清） | MW.on_ti_del_neg（mux 分支） | — | 保留 | 真值表工具条「删反例…」的 mux 语义（手编反例列 + 整信号反例标记一起清） | | 已落点 | 方案§4.1; 审计A |
| C-124 | mux 真值表头部：case 结构 + 生效覆盖档 + 该档怎么展开 + 期望手填进度 | MW._update_mux_header | generator._mux_ctrl_desc / mux_gen.coverage_mode | 保留 | 主控补位: mux 专用真值表头部条（case 结构 + 生效覆盖档 + 该档怎么展开 + 手填进度）；Design 把进度放主视图标签、覆盖档放详情标题栏、case 结构只在右栏「逐层展开」③ | | 已落点(补位) | 清单§1; gui:_update_mux_header |
| C-125 | mux 头部标出被跳过的死分支（靠后重复 case） | MW._update_mux_header（shadowed_note） | mux_gen.make_mux_vectors（meta） | 保留 | 主控补位: mux 头部标出被跳过的死分支（靠后重复 case）；sigflow 已有「✗死分支」文字后缀可复用 | | 已落点(补位) | gui:_update_mux_header |
| C-126 | mux 头部标出本信号受 dft 页 iddq 门控，以及门能不能 force | MW._update_mux_header 末尾 | generator.pin_dft_gate | 保留 | 主控补位: mux 头部标出本信号受 dft 页 iddq 门控以及门能不能 force（Design 在电路图与展开链①体现了门控，头部未给） | | 已落点(补位) | gui:_update_mux_header |
| C-127 | 字段太窄·假绿的 mux 组，头部说明为什么整组跳过（要加宽字段或拆组，属设计层） | MW._load_mux_test_items（meta['value_collision']） | mux_gen.make_mux_vectors | 保留 | 清单行内原因块（D 第 3 行「⚠ 字段太窄·假绿」：「寄存器字段只有 2 bit（@0x91[5:4]），输出是 4 bit……断言只会验到低 2 位，仿真必过」） | | 已落点 | gui:_load_mux_test_items; 审计B |
| C-128 | iddq DFT 门作为一行只读输入出现在真值表里（紫字），每条测试显示 force 的透传值 | SV._populate_truth（is_dft_gate）/ MW._ti_render_col（_ti_dft_pin） | generator.pin_dft_gate | 保留 | 真值表输入行「d_wl_rf_trx_reg_dft_iddq_mode　(DFT 门(iddq) · 猜名)」只读行；Design 用琥珀 #8a6412（猜名色）而非旧门面紫字 | | 已落点 | 清单§1; gui:_dft_pin_display |
| C-129 | iddq 漏电态自检拍列的期望格用淡紫 + 说明『工具自动加的，不是反例』 | SV._populate_truth（DFT_BG）/ SV._is_dft_pitch_col | generator.pin_dft_gate | 保留 | iddq 自检拍列 T24：列头 #eef4fd/#1d4f9c，期望格 #eef4fd 边框 #a9c4ea；图例「iddq=1 漏电态自检拍」（Design 定为淡蓝，旧门面是淡紫 → 以 Design 为准） | | 已落点 | gui:_is_dft_pitch_col |
| C-130 | iddq 自检拍列不参与表达式重算（否则会假红当成反例） | SV._recompute_col_an（_is_dft_pitch_col 早退） | — | 保留 | 主控补位: iddq 自检拍列不参与表达式重算（否则假红当成反例）；无界面元素 | | 已落点(补位) | gui:_recompute_col_an |
| C-131 | 门已经是本信号显式输入时不再单列一行 DFT 门（与生成/报告去重同口径） | MW._dft_pin_display(input_bases=) | generator.pin_dft_gate | 保留 | 主控补位: 门已是本信号显式输入时不再单列 DFT 门行（与生成/报告去重同口径）；无界面元素 | | 已落点(补位) | gui:_dft_pin_display |
| C-132 | 编辑（含手填期望）立刻在 .sv 预览与导出里生效 | SV._compute_edited → provider.render_sv / MW._vector_overrides → generator.build | topout.build_for_topout(edit_overrides=) / generator.GenOptions(vector_overrides=) | 保留 | 编辑 → .sv 预览与导出即时生效（.sv 预览标签与导出中心同源） | | 已落点 | 方案§4.1; Design§10 |
| C-133 | 本信号用例数实时提示（『→ 当前信号 N 条，含 X 反例（已自定义）』） | SV._update_cov_hint / MW._update_cov_hint | — | 保留 | 覆盖度弹层底部「用例数上限 256 → 本信号当前 25 条」 | | 已落点 | 清单§1; gui:_update_cov_hint |
| C-134 | 真值表编辑按钮按信号可编辑性启用/禁用（不可建信号不给点） | SV._set_edit_buttons | topout.analyze_signal（status/editable） | 保留 | 主控补位: 不可建信号时真值表工具条按钮置灰（Design 只画了可建态） | | 已落点(补位) | gui:_set_edit_buttons |
| C-135 | 选中 mux 信号时『加列/复制/反例/改名』按钮的说明文案换成 mux 语义 | MW._set_ti_buttons_for_mux（_MUX_BTN_TIPS） | — | 保留 | 主控补位: 选中 mux 信号时工具条按钮说明文案换成 mux 语义（_MUX_BTN_TIPS）；Design 未画 tooltip | | 已落点(补位) | gui:_set_ti_buttons_for_mux |
| C-136 | 导出本信号真值表 CSV（转置：每列一条测试） | SV.on_export_csv / MW.on_ti_export_csv | — | 保留 | 真值表工具条右端「导出 CSV」 | | 已落点 | 方案§4.1; Design§10; 6月审计N7 |
| C-137 | 单信号 CSV 里含 期望(bin) / force / RF_WRITE 三行（能还原驱动） | MW.on_ti_export_csv（legacy 版） | sv_writer.compute_drives / sv_writer.fmt_bin | 保留 | 「导出 CSV」含 期望(bin) / force / RF_WRITE 三行（V2Spec §5 M28 明写） | | 已落点 | 方案§4.1; 审计A; 审计次要 |
| C-138 | 单信号 CSV 另写『期望来源』行（反例 / designer手填 / auto_out兜底）与『反例?』行 | SV.on_export_csv / MW.on_ti_export_csv | — | 保留 | 主控补位: CSV 另写「期望来源」行（反例 / designer手填 / auto_out兜底）与「反例?」行 | | 已落点(补位) | gui:on_export_csv |
| C-139 | mux 信号导出真值表 CSV，且忠于产物（勾了反例就带上反例列、与生成同口径去重重排号） | MW._export_mux_csv | generator._dedup_negatives / vectors.add_negatives | 保留 | 主控补位: mux 信号 CSV 忠于产物（带反例列、与生成同口径去重重排号） | | 已落点(补位) | gui:_export_mux_csv |
| C-140 | 两套真值表编辑器（SignalView 版 + 排查(旧)版）合成一套 | SV._e_* 一组 + MW.on_ti_* 一组 | — | 合并 | V2Spec §5 M30「两套真值表编辑器（24 个按钮）→ 合成一套工具条」（Design 真值表工具条 13 按钮 + 1 提示条） | | 已落点 | 方案§4.2; 清单§2 |
| C-141 | 真值表编辑器实现路线 | — | — | 保留 | 真值表工具条 + 冻结列 + 网格：按 A5 已拍板路线 QTableView + QAbstractTableModel + QUndoStack 实现（A5 §5 model 接口） | | 已落点 | 计划§5-2; A5; 原 status: 待拍板(默认=QTableView 原生实现，A5 已实证) |

## 覆盖度（15 条：保留 13，合并 2）

> 三层档位（全局默认 → 按逻辑类型 → 本信号）+ 上限，和它们的口径一致性。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-142 | 全局默认覆盖档（精简 / 全面 / 穷举），所有信号的基线 | SV.cov（_build_coverage_panel） | topout.topout_view_models(mode=,exhaustive=) | 保留 | 覆盖度弹层 covRows 第 1 行「全局默认 · 整张表的兜底档 · 全面」 | | 已落点 | 方案§4.1; Design§10 |
| C-143 | 用例数上限（安全阀，防穷举/全面产生过多用例） | SV.maxt_spin / MW.max_tests | topout.topout_view_models(max_tests=) | 保留 | 覆盖度弹层底部「用例数上限 256」 | | 已落点 | 方案§4.1; Design§10 |
| C-144 | 覆盖度算法说明弹窗（logic 与 mux 各自三档怎么展开） | SV._build_coverage_panel 的 ? 按钮（_COVERAGE_HELP） | — | 保留 | 覆盖度弹层右下「档位怎么算的？」链接 | | 已落点 | 方案§4.1; 方案§4.3; Design§10; 清单§3; 主控裁决: ? 留在覆盖度控件上，诊断抽屉放链接 |
| C-145 | 按逻辑类型整批设档：直连寄存器(F0) / 布尔位运算(F1) / 选路(F2) / 门控·iddq(F3/F4) | SV._build_coverage_panel（_FORM_COV_ROWS）+ SV._on_form_cov_changed | topout.topout_view_models(form_cov=) | 保留 | 覆盖度弹层 covRows 第 2–5 行（逻辑类型 · 直连寄存器 / 布尔·位运算 / 选路 / 门控 iddq） | | 已落点 | 方案§4.1; Design§10; 清单§1 |
| C-146 | 每种逻辑类型给一条例子表达式 + 各档怎么展开的说明 | gui._FORM_COV_ROWS | — | 保留 | covRows 说明列的例子表达式：out = d_reg[3:0] / out = (A & ~B) \| C / out = sel ? A : B / out = iddq ? 0 : (sel ? A : B) | | 已落点 | 清单§1; gui:_FORM_COV_ROWS |
| C-147 | 本信号单点覆盖档（最高优先级，压过逻辑类型档与全局档） | SV.sig_cov_combo / SV._on_sig_cov_changed / MW.on_sig_cov_changed | topout.topout_view_models(sig_cov=) / topout.analyze_signal(mode=) | 保留 | covRows 第 6 行「本信号 · 跟随上级」（可改档，最高优先级） | | 已落点 | 方案§4.1; Design§10; 6月审计N3 |
| C-148 | 三层优先级（本信号 > 逻辑类型 > 全局）在清单用例数与真值表之间同一口径 | SV._mode_for（与 topout._effective_cov_str 同源） | topout.topout_view_models | 保留 | 详情标题栏「覆盖度 全面（来自：逻辑类型·选路）」= 三层同一口径的生效档；清单用例数与真值表同源 | | 已落点 | 方案§4.1; Design§5-H3; gui:_mode_for |
| C-149 | 切信号时『本信号覆盖度』下拉回显该信号已设的档（不触发重算） | SV._sync_sig_cov_combo / MW._set_sig_cov_combo | — | 保留 | 主控补位: 切信号时覆盖度控件回显该信号已设档、不触发重算（Design 弹层是静态样例） | | 已落点(补位) | gui:_sync_sig_cov_combo |
| C-150 | 显示生效档的来源（『单点档，已压过全局』/『跟随全局 X 档=全面』） | MW._set_sig_cov_combo（sig_cov_tag） | — | 保留 | 覆盖度弹层「生效来源」蓝条「本信号「跟随上级」→ 逻辑类型「选路」= 全面 → 全局默认 = 全面」+ 生效两行高亮 #eef4fd（后端 CoverageState.effective_label 目前只返回二元组，见 Design对齐 §4 冲突⑧） | | 已落点 | Design§5-H3; gui:_set_sig_cov_combo |
| C-151 | 动全局下拉时清掉当前正在看的这个信号的单点档，让全局改动立刻可见 | MW._clear_displayed_sig_cov_on_global | — | 保留 | 主控补位: 动全局档时清掉当前信号的单点档、让全局改动立刻可见（Design 未画联动规则） | | 已落点(补位) | gui:_clear_displayed_sig_cov_on_global |
| C-152 | 改逻辑类型档后整张清单的用例数与当前真值表一起重算 | SV._on_form_cov_changed | topout.topout_view_models(form_cov=) | 保留 | 主控补位: 改逻辑类型档后整张清单用例数与当前真值表一起重算 | | 已落点(补位) | gui:_on_form_cov_changed |
| C-153 | 已自定义编辑过的信号，改档只影响它的清单用例数与导出，不冲掉编辑 | SV._on_sig_cov_changed（cur_name in edits 判定）/ MW.on_coverage_changed | — | 保留 | 主控补位: 已自定义编辑过的信号改档只影响清单用例数与导出、不冲掉编辑 | | 已落点(补位) | gui:_on_sig_cov_changed |
| C-154 | 只加过反例、没手改正向的信号，切全局档时正向按新档重算再补回反例 | MW._neg_only_rows_now / MW._set_signal_negatives（_neg_only 规则 first/all） | vectors.generate_vectors / vectors.add_negatives | 保留 | 主控补位: 只加过反例没手改正向的信号，切全局档时正向按新档重算再补回反例 | | 已落点(补位) | gui:_neg_only_rows_now |
| C-155 | 界面上 5 个覆盖度下拉、2 套模型收成一套 | SV.cov ×5 + SV._form_cov_combos + SV.sig_cov_combo + MW.coverage/coverage_mux/sig_cov_combo | — | 合并 | V2Spec §5 M32「界面同时 5 个下拉、2 套模型 → 1 个控件、1 套模型」 | | 已落点 | 方案§4.2; 清单§2; 方案§1 |
| C-156 | 三层塌成一个控件：平时只显示『生效档 + 来源』，点开才见三层与上限、穷举 | SV._build_coverage_panel + MW 的 logic覆盖/mux覆盖 两个下拉 | — | 合并 | 详情标题栏覆盖度按钮（收起态「全面（来自：逻辑类型·选路）」）→ 点开 covPop 三层表格 + 上限；生效两行高亮 | | 已落点 | 方案§4.1; Design§5-H3; Design§11-1 |

## 导出（43 条：保留 33，降级(诊断抽屉) 5，新增 4，合并 1）

> 六种交付物、范围、选项、完成反馈（Q5）。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-157 | 导出 .sv 到文件 | SV.on_export_sv / MW.on_generate | topout.render_topout_sv / generator.build + generator.render | 保留 | 导出中心（S.open='export'，Ctrl+G）第 1 行「.sv 断言文件」 | | 已落点 | 方案§4.1; Design§10 |
| C-158 | 导出范围三选：全部（正向+反例）/ 仅正向 / 仅反例 | SV._ask_export_options / MW._ask_export_options（scope） | topout.render_topout_sv(scope=) / MW._vector_overrides(positive_only=,negative_only=) | 保留 | 导出中心「范围」列循环切 SCOPES=[勾选的 7 个 / 全部 10 个 / 仅正向 / 仅反例] —— 用户标注截图 5a7b48b6 圈定此列 | | 已落点 | 方案§4.1; Design§10; 6月审计#3 |
| C-159 | 导出选项：给 .sv 加注释（默认关，便于 diff） | SV._ask_export_options / MW._ask_export_options（comments） | topout.render_topout_sv(comments=) / generator.render(comments=) | 保留 | 导出中心第 1 行选项「无注释」 | | 已落点 | 方案§4.1; Design§10; 6月审计#3 |
| C-160 | 导出选项：末尾测试汇总 + 计数器（统计真 FAIL / NEG-broken 数） | SV._ask_export_options / MW._ask_export_options（sv_summary） | topout.render_topout_sv(sv_summary=) | 保留 | 导出中心第 1 行选项「末尾汇总」 | | 已落点 | 方案§4.1; Design§10; 6月审计#3 |
| C-161 | 导出选项：断言消息尾部追加 owner | SV._ask_export_options / MW._ask_export_options（owner_in_msg） | topout.render_topout_sv(owner_in_msg=) | 保留 | 导出中心第 1 行选项「写 owner」 | | 已落点 | 方案§4.1; Design§10; 6月审计#3 |
| C-162 | 导出选项记住上次选择，下次预选 | SV._ask_export_options / MW._ask_export_options（_save_settings） | — | 保留 | 主控补位: 四个选项记住上次选择、下次预选（exports.load_export_options / store_export_options）；Design 的「选项」列是只读摘要，需做成可点开的选项弹层 | | 已落点(补位) | 清单§3; gui:_ask_export_options |
| C-163 | 两个『导出 .sv 选项』对话框（默认值不同却共用同一份设置）合成一个 | SV._ask_export_options + MW._ask_export_options | — | 合并 | 导出中心第 1 行合二为一，统一默认值 = exports.EXPORT_OPTION_DEFAULTS（scope=all / comments=False / sv_summary=False / owner_in_msg=False） | | 已落点 | 方案§4.2; 清单§3; 清单§7; 主控裁决: 统一默认值=SignalView 版（sv_summary/owner_in_msg 默认关，exports.EXPORT_OPTION_DEFAULTS 唯一定义）；v2 预览与导出同读 exports.load_export_options；legacy _opts 的 True 兜底随旧门面删 |
| C-164 | 重复 assert 标号（非法 SV）在写文件前弹确认，列出冲突的标号与两个信号 | MW._confirm_dup_labels（SV.on_export_sv / MW.on_generate 都调） | generator.build（dup_labels）/ topout.build_for_topout | 保留 | 主控补位: 写文件前弹重复 assert 标号确认并列出冲突标号与两个信号（Design 导出中心只有「导出勾选的 N 项」按钮） | | 已落点(补位) | 方案§4.1; Design§10; 审计C; 6月审计N9 |
| C-165 | 导出完成摘要：信号数 / 断言块数 / 测试用例数（其中反例几条、designer 手填期望几条）/ 只记账不产断言几个 | SV._export_summary_text | topout.render_topout_sv（summary） | 保留 | 导出完成弹层「已写出」段「7 信号 · 断言块 7 · 测试用例 168 · 手填期望 7 · 其余用程序算的值兜底 161」 | | 已落点 | 方案§4.1; 6月审计#3 |
| C-166 | 导出完成摘要点名有哪些信号只记账不产断言 | SV._export_summary_text（accounted） | topout.render_topout_sv（accounted） | 保留 | 主控补位: 导出完成摘要点名「只记账不产断言」的信号（Design 只点名了被跳过的 2 个） | | 已落点(补位) | Design§8-2; 清单§7; gui:_export_summary_text |
| C-167 | 导出 .sv 完成后点名跳过了哪些信号、每个缺哪几根输入（可展开明细） | MW.on_generate（box.setDetailedText + gui._skipped_detail_text） | generator.build（skipped） | 保留 | 导出完成弹层顶部琥珀块「2 个信号没有进 .sv —— 名字和原因：」+ 每信号一条 └ 原因 | | 已落点 | 方案§4.1; Design§3-Q1; Design§8-2; 清单§7 |
| C-168 | 导出完成后自动跳到 .sv 预览页显示这次的内容 | SV.on_export_sv（inner.setCurrentIndex(1)） | — | 保留 | 主控补位: 关掉「导出完成」弹层后自动切到 .sv 预览标签显示本次内容（Design 用模态代替，两者不冲突） | | 已落点(补位) | gui:on_export_sv |
| C-169 | 按导出范围给不同的默认文件名（wr_rf_tc.sv / _pos.sv / _neg.sv） | MW.on_generate（default_name） | — | 保留 | 主控补位: 按范围给默认文件名 wr_rf_tc.sv / _pos.sv / _neg.sv（Design「上次导出到哪」列显示 D:\work\wr_rf_tc.sv） | | 已落点(补位) | gui:on_generate |
| C-170 | 汇总命名块按范围加后缀（_pos/_neg），两份贴进同一作用域也不重名 | MW.on_generate（block_suffix） | generator.render(block_suffix=) | 保留 | 主控补位: 汇总命名块按范围加 _pos/_neg 后缀（无界面元素） | | 已落点(补位) | gui:on_generate |
| C-171 | 写文件失败时说清楚是不是被仿真器/编辑器占用 | MW.on_generate（except OSError） | — | 保留 | 主控补位: 写文件失败时说清是否被仿真器/编辑器占用（Design 未画失败态） | | 已落点(补位) | gui:on_generate |
| C-172 | 预览完在状态栏报：信号 / 断言块 / 用例（反例、手填期望）/ 记账 各几条 | SV.on_preview / MW.on_preview | topout.render_topout_sv（summary） | 保留 | 主控补位: 预览完在状态栏报 信号 / 断言块 / 用例(反例、手填期望) / 记账 各几条 | | 已落点(补位) | 清单§5-7; gui:on_preview |
| C-173 | 导出报告 HTML（汇总 + 每信号真值表 + 完整明细 + 可验证性） | SV.on_export_report / MW.on_report | cli.write_report / topout.topout_report | 保留 | 导出中心第 2 行「报告（给人看）· HTML（含电路图）」 | | 已落点 | 方案§4.1; Design§10 |
| C-174 | 导出报告 CSV（明细 + 汇总 + 可验证性三份扁平表） | SV.on_export_report / MW.on_report | cli.write_report | 保留 | 导出中心第 2 行选项「也可 CSV / Excel」→ CSV | | 已落点 | 方案§4.1; Design§10 |
| C-175 | 导出报告 Excel（真值表 sheet 给 designer 看，autofilter + 冻结表头） | MW.on_report（legacy 独有） | cli.write_report | 保留 | 导出中心第 2 行选项「也可 CSV / Excel」→ Excel | | 已落点 | 方案§4.1; Design§10; 审计C; 清单§2 |
| C-176 | 选了 Excel 格式就存成 .xlsx（按所选格式纠正扩展名，不出双扩展名） | MW.on_report（want_ext 纠正） | — | 保留 | 主控补位: 选 Excel 就存 .xlsx、按所选格式纠正扩展名（exports.correct_report_ext） | | 已落点(补位) | 方案§4.1; 审计C; 审计次要 |
| C-177 | 报告导出完成报『范围（勾选 N 个 / 全部）· 用例 N 条 · 反例 N 条』并列出写了哪几个文件 | MW.on_report 完成弹窗 | cli.write_report（written） | 保留 | 主控补位: 报告完成报「范围（勾选 N 个 / 全部）· 用例 N 条 · 反例 N 条」+ 写了哪几个文件（Design 完成弹层已列 D:\work\用例表.html，计数需补） | | 已落点(补位) | 方案§4.1; 审计C; 审计次要 |
| C-178 | 报告只导勾选的信号 | SV.on_export_report(only=) / MW.on_report(sel) | topout.topout_report(only=) | 保留 | 导出中心第 2 行「范围」列（勾选的 7 个） | | 已落点 | 方案§4.1; 6月审计N6 |
| C-179 | 回填 for_test 页到一份新 Excel（复制源表全部 sheet，只替换 for_test） | SV.on_fortest / MW.on_fortest | fortest_writer.write_fortest / topout.report_for_topout | 保留 | 导出中心第 3 行「回填 for_test 页」 | | 已落点 | 方案§4.1; Design§10 |
| C-180 | 回填时拒绝把源 Excel 当输出名（防覆盖源表） | MW.on_fortest（abspath 比对） | — | 保留 | 导出中心第 3 行选项「写副本，不覆盖源表」 | | 已落点 | 方案§4.1; 审计C; 审计次要 |
| C-181 | 回填时自动补 .xlsx 扩展名 | MW.on_fortest | — | 保留 | 主控补位: 回填时自动补 .xlsx 扩展名 | | 已落点(补位) | 方案§4.1; 审计C; 审计次要 |
| C-182 | 回填完成报回填了几组、mux 信号有几个没进 for_test 及原因 | MW.on_fortest 完成弹窗 | fortest_writer.write_fortest（返回组数） | 保留 | 主控补位: 回填完成报回填了几组、mux 有几个没进 for_test 及原因（Design 完成弹层未列 for_test 行） | | 已落点(补位) | 方案§4.1; 审计C; 审计次要 |
| C-183 | 回填只导勾选的信号 | SV.on_fortest(only=) / MW.on_fortest(sel) | topout.report_for_topout(only=) | 保留 | 导出中心第 3 行「范围」列（全部 9 个，可切勾选） | | 已落点 | 方案§4.1; 6月审计N6 |
| C-184 | 回填含 mux 表 | SV.on_fortest（include_mux=True） | fortest_writer.write_fortest(include_mux=) | 保留 | 主控补位: 回填含 mux 表（include_mux=True，无界面元素） | | 已落点(补位) | gui:_TopoutProvider.fortest |
| C-185 | 导出 nets.txt（要在 ENV_RF 层定位的网清单，给红区跑 scan_rtl） | MW.on_export_nets / SV.on_export_nets | rtl_scan.collect_nets / rtl_scan.render_nets_text | 降级(诊断抽屉) | 导出中心第 4 行「nets.txt（红区扫网名）」+ 诊断抽屉第 1 步「导出 nets.txt…」 | | 已落点 | 方案§4.1; 方案§4.3; Design§10; 6月审计N5 |
| C-186 | nets.txt 按类别勾选导出（Topout+cone 输入 / 仅 Topout 探针 / logic / mux / dft / iddq），每类带说明 | MW._ask_nets_pages（_NETS_CAT_INFO） | rtl_scan.collect_nets(pages=) / pageviews.PAGES | 降级(诊断抽屉) | 导出中心第 4 行选项「类别：顶层输出 · force 目标 · 猜名的网」—— 与后端 exports.nets_categories 按页 5 类口径不一致，见 Design对齐 §4 冲突⑤ | | 已落点 | 方案§4.1; Design§10; 清单§3 |
| C-187 | nets.txt 类别只列本表真有内容的页（空页不出现） | MW._nets_categories | pageviews.page_available | 降级(诊断抽屉) | 主控补位: 类别只列本表真有内容的页（exports.nets_categories(wb) 已如此）；Design 的选项列是静态文本，需做成可勾类别列表 | | 已落点(补位) | gui:_nets_categories |
| C-188 | nets.txt 类别记住上次勾选，下次预选（默认全勾） | MW._ask_nets_pages（settings['nets_pages']） | — | 降级(诊断抽屉) | 主控补位: 类别勾选记住上次（settings 键 nets_pages），默认全勾 | | 已落点(补位) | 清单§3; gui:_ask_nets_pages |
| C-189 | nets.txt 导出完成报总网数 + 各类别分项计数，并给出跨机器三步下一步指引 | MW.on_export_nets 完成弹窗 | rtl_scan.collect_nets | 降级(诊断抽屉) | 诊断抽屉第 1 步结果框「D:\work\nets.txt　9 个信号 · 41 根网 · 其中 3 根名字是猜的」+ 三步流程本身即跨机器指引 | | 已落点 | Design§3-Q6; gui:on_export_nets |
| C-190 | 导出完整配置 .json（勾选 / 全局档 / 探针前缀 / 强制 force / 全部测试编辑）给同事或换机器用 | MW.on_export_edits + MW._collect_config | — | 保留 | 导出中心第 6 行「整份配置 · 探针前缀 · 强制 force · 补充逻辑 · 覆盖度 · 手填期望」 | | 已落点 | 方案§4.1; Design§10; 审计C; 审计Top10-10; 6月审计#1a |
| C-191 | 导出配置完成后逐段报数（勾选几个 / 全局档 / 前缀几条 / 强制 force 几个 / 编辑几个信号、手填期望几条） | MW.on_export_edits 完成弹窗 | — | 保留 | 主控补位: 导出配置完成后逐段报数（Design 完成弹层未列配置行） | | 已落点(补位) | gui:on_export_edits |
| C-192 | 导入配置（完整配置=先清空再照单恢复；旧版测试项编辑文件=只并入测试编辑） | MW.on_import_edits + MW._reset_all_config_state + MW._apply_global_settings | — | 保留 | 空态「导入配置…」按钮（V2Spec §5 M39「空态页也放一个『导入配置…』」） | | 已落点 | 方案§4.1; Design§10; 审计C; 6月审计#1a |
| C-193 | 导入配置时按文件名核对源表，不一致就提示『这份配置是为《X》导出的，当前是《Y》』 | MW.on_import_edits（excel_mismatch） | — | 保留 | 主控补位: 导入时按文件名核对源表，不一致提示「这份配置是为《X》导出的，当前是《Y》」 | | 已落点(补位) | gui:on_import_edits |
| C-194 | 导入配置时列出文件里有、当前表里找不到的信号名与原因（最多 30 个 + 总数） | MW.on_import_edits + MW._apply_edits_bucket（missing） | — | 保留 | 主控补位: 导入时列出文件里有、当前表没有的信号名与原因（最多 30 个 + 总数） | | 已落点(补位) | Design§8-2; gui:_apply_edits_bucket |
| C-195 | 导入的不是 dreg_verify 配置文件时明确说缺哪个段，不静默失败 | MW.on_import_edits（is_full/is_legacy 判定） | — | 保留 | 主控补位: 导入的不是本工具配置时说明缺哪个段，不静默失败 | | 已落点(补位) | gui:on_import_edits |
| C-196 | 导出 claims.json（每个信号探哪根网、force/写哪些网、名字是查到的还是猜的）给红区诊断脚本 | GUI 无入口（仅 CLI --export-claims） | cli._export_claims / generator.NAMING_MODEL_TOPOUT | 新增 | 导出中心第 5 行「claims.json（红区比对）」+ 注「随 .sv 进红区，诊断脚本的唯一输入」+ 选项「每根探针：探哪根网 · 名字是查到的还是猜的」 | | 已落点 | 方案§4.1; 方案§5-4; Design§10; 清单§2 |
| C-197 | 一个导出中心列全 6 种交付物：交付物 × 范围 × 选项 × 上次导出到哪 | 旧为 5 个分散按钮 + 2 个对话框 | — | 新增 | 导出中心表格 5 列（勾选 / 交付物 / 范围 / 选项 / 上次导出到哪）× 6 行（ER 数组） | | 已落点 | 方案§4.1; Design§5-H4; Design§11-2-⑥ |
| C-198 | 每种交付物记住上次导出到哪个目录 | 无（只有 nets.txt 用源表目录做默认） | — | 新增 | 导出中心「上次导出到哪」列（D:\work\wr_rf_tc.sv 今天 14:22 / 从未导出）—— 后端目前无此持久化，见 Design对齐 §4 冲突⑦ | | 已落点 | Design§5-H4 |
| C-199 | 所有导出完成反馈统一成『跳过项名字 + 原因在前、计数在后』 | SV.on_export_sv 现只给纯计数 | — | 新增 | 导出完成弹层版式即规范：跳过点名琥珀块在上、「已写出」计数在下；exportSummary 也写「2 个信号会被跳过（导完点名）」 | | 已落点 | Design§5-H4; Design§8-2; 清单§7 |

## 诊断（27 条：降级(诊断抽屉) 17，删除 4，新增 3，保留 3）

> 仿真回来说找不到网、规格缺一级、撞名——怎么修（Q6）。v2 收进抽屉，平时收起。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-200 | 探针前缀映射编辑器（信号名 → ENV_RF 下的层级路径） | MW.on_set_probe_prefix / SV.on_set_prefix | generator.parse_probe_prefix_lines / resolver.Resolver(wire_prefixes=) | 降级(诊断抽屉) | 诊断抽屉「仿真报『找不到这根网』（CUVUNF）」第 3 步；前缀映射编辑器按 V2Spec §5 M40 挂在该步内 | | 已落点 | 方案§4.1; 方案§4.3; Design§10; 审计C |
| C-201 | 探针前缀支持合并写法（路径只写一次，其下信号名逗号/换行分隔）与扁平写法混用，# 开头是注释 | MW.on_set_probe_prefix（hint） | generator.parse_probe_prefix_lines / generator.render_probe_prefix_grouped | 降级(诊断抽屉) | 主控补位: 编辑器保留合并写法（路径只写一次、其下信号名逗号/换行分隔）与扁平写法混用、# 注释（Design 未画编辑器本体） | | 已落点(补位) | 清单§3; gui:on_set_probe_prefix |
| C-202 | 探针前缀从 .txt 导入（与现有合并，同名以导入为准）——接红区 scan_rtl 的 probe_prefixes.txt | MW.on_set_probe_prefix.do_import | generator.parse_probe_prefix_lines | 降级(诊断抽屉) | 诊断抽屉第 3 步主按钮「导入 probe_prefixes.txt…」（蓝 #2f6fd0） | | 已落点 | 方案§4.1; Design§3-Q6; Design§10; 清单§3 |
| C-203 | 探针前缀导出成 .txt（换表/给同事复用） | MW.on_set_probe_prefix.do_export | generator.render_probe_prefix_grouped | 降级(诊断抽屉) | 主控补位: 探针前缀导出成 .txt（换表/给同事复用）；Design 第 3 步只画了导入 | | 已落点(补位) | 方案§4.1; Design§10; 清单§3 |
| C-204 | 改完探针前缀报影响面：共 N 条映射、影响 M 个信号 | MW.on_set_probe_prefix 末尾 | — | 降级(诊断抽屉) | 诊断抽屉第 3 步结果框「当前已有前缀映射 6 条 · 还差 3 根网没有前缀」 | | 已落点 | gui:on_set_probe_prefix |
| C-205 | 改完探针前缀重建各视图清单时保住用户已有的勾选（不被重置成全勾） | MW.on_set_probe_prefix（_collect_view_checks/_apply_view_checks 往返） | — | 降级(诊断抽屉) | 主控补位: 改完前缀重建清单时保住已有勾选（不被重置成全勾）；无界面元素 | | 已落点(补位) | gui:on_set_probe_prefix |
| C-206 | 强制 force 信号名单编辑器：列进来的信号跳过展开、直接 force 顶层基名网 | MW.on_set_force_signals / SV.on_set_force | resolver.Resolver(force_overrides=) / generator.GenOptions(force_overrides=) | 降级(诊断抽屉) | 诊断抽屉 otherSymptoms 第 2 条「两个东西撞名，展开走错了路 → 强制直接 force 顶层网，跳过逐层展开」 | | 已落点 | 方案§4.1; 方案§4.3; Design§10; 审计C; 审计Top10-9 |
| C-207 | 强制 force 名单支持行尾 # 注释、留空=清除 | MW.on_set_force_signals（split('#')） | — | 降级(诊断抽屉) | 主控补位: 强制 force 名单支持行尾 # 注释、留空=清除（Design 未画编辑器本体） | | 已落点(补位) | gui:on_set_force_signals |
| C-208 | 强制 force 名单能导入/导出（跟探针前缀一样可复用） | 无（编辑器只有粘贴） | — | 新增 | 主控补位: 强制 force 名单导入 / 导出 .txt（与探针前缀同款） | | 已落点(补位) | 清单§7; Design§8-3 |
| C-209 | 强制 force 名单对 Topout 视图与页本地视图的分析与 .sv 都真正生效 | _TopoutProvider._fo / _PageProvider._fo | topout.topout_view_models(force_overrides=) / pageviews.page_view_models(force_overrides=) | 保留 | V2Spec §5 M41「同时接通 Topout 后端（v1 不生效）」——名单对 Topout 与页本地视图的分析与 .sv 都真正生效 | | 已落点 | 方案§4.1; 审计C; 审计Top10-9 |
| C-210 | RTL 补充逻辑编辑器：Excel 真表缺某信号的 ECO 级时手工补一条等价表达式来扫真值表 | MW.on_logic_overrides / SV.on_logic_overrides | generator._logic_with_overrides / generator.GenOptions(logic_overrides=) | 降级(诊断抽屉) | 诊断抽屉 otherSymptoms 第 1 条「规格缺了一级逻辑，工具展不下去 → 手工补一段等价表达式（RTL 补充逻辑）」 | | 已落点 | 方案§4.1; 方案§4.3; Design§10; 6月审计N4 |
| C-211 | RTL 补充逻辑『插入模板(当前信号)』：预填原表达式与原输入映射 | MW._supplement_template | generator.make_supplement_signal | 降级(诊断抽屉) | 主控补位: 「插入模板(当前信号)」预填原表达式与原输入映射（Design 未画编辑器本体） | | 已落点(补位) | 方案§4.1; Design§10; 清单§3 |
| C-212 | RTL 补充逻辑从 .json 文件导入 | MW.on_logic_overrides._from_file | — | 降级(诊断抽屉) | 主控补位: RTL 补充逻辑从 .json 导入 | | 已落点(补位) | 方案§4.1; Design§10; 清单§3 |
| C-213 | RTL 补充逻辑六道校验（信号名占位符未替换 / spec 非对象 / 缺 expr / inputs 非空列表 / 表达式解析 / 变量没有 input 映射），不通过不保存并逐条列错 | MW._validate_supplements | expr.parse / expr.collect_vars / generator.make_supplement_signal | 降级(诊断抽屉) | 主控补位: 六道校验不通过不保存并逐条列错（session.validate_supplements 已有） | | 已落点(补位) | gui:_validate_supplements |
| C-214 | 补充的基名不在当前 Excel logic 页时提示『将作为纯新增合成信号生成』 | MW.on_logic_overrides（unknown） | — | 降级(诊断抽屉) | 主控补位: 补充基名不在当前 logic 页时提示「将作为纯新增合成信号生成」 | | 已落点(补位) | gui:on_logic_overrides |
| C-215 | 用了 RTL 补充逻辑的信号，真值表头部挂琥珀横幅 + 理由 | MW._update_ti_header（supp_tag + setStyleSheet） | — | 保留 | V2Spec §5 M11「横幅改成状态栏一行」+ 清单信号名后琥珀圆点（真值表头部琥珀横幅降级） | | 已落点 | 方案§4.1; 审计A; 审计次要 |
| C-216 | Topout 视图的展开也看得到 RTL 补充信号（补充的新输入自动成为真值表维度） | _TopoutProvider._supplemented（swap-and-restore） | generator._logic_with_overrides | 保留 | 主控补位: Topout 视图的展开也看得到补充信号（补充输入自动成为真值表维度）；无界面元素 | | 已落点(补位) | 6月审计N4; gui:_supplemented |
| C-217 | 『缺前缀是否强制生成』做成可见开关（现在 Topout 路径写死开着） | MW.include_risky_chk / MW.on_include_risky_changed（legacy 独有） | generator.GenOptions(include_risky=) / topout.py 写死 True | 降级(诊断抽屉) | 诊断抽屉 otherSymptoms 第 4 条「还有网没有前缀，要不要照样生成 → 缺前缀是否强制生成　当前：是」（默认必须仍 True，与 A3 审计一致 ✓） | | 已落点 | 方案§4.3; Design§10; 审计C; 清单§7; 主控裁决: 默认仍 True（A3§1.5） |
| C-218 | 级联模式说明窗（展开上游 vs force级联网 的图解与选择建议） | MW._open_cascade_doc | — | 删除 | 主控补位: 级联模式说明窗随三处级联下拉（C-223/C-224）一并退役，docs/级联模式说明.md 留作背景资料；Design 映射表未给落点 | | 已落点(补位) | 方案§4.1; 清单§3 |
| C-219 | 级联说明窗真的读到 docs/级联模式说明.md（现在永远显示『仓库根目录没找到该文件』） | MW._open_cascade_doc（路径已修为 docs/） | — | 删除 | 主控补位: 同 C-218，随入口一并退役（不再有「读不到文件」这条路径） | | 已落点(补位) | 方案§3-轨0-7; 清单§7; 方案§7 |
| C-220 | 级联说明每次打开重读文件，文档更新不用重启工具 | MW._open_cascade_doc | — | 删除 | 主控补位: 同 C-218，随入口一并退役 | | 已落点(补位) | gui:_open_cascade_doc |
| C-221 | 『仿真器找不到这根网』按症状组织成三步：导 nets.txt → 红区跑 scan_rtl → 导入前缀 | 旧为一排孤立按钮 | rtl_scan.collect_nets | 新增 | 诊断抽屉蓝框「仿真报『找不到这根网』（CUVUNF）」+ steps 三步（导 nets.txt → 红区跑 scan_rtl → 导入前缀），每步带结果框与主按钮 | | 已落点 | Design§3-Q6; Design§5-H5; Design§11-2-⑦ |
| C-222 | 逻辑展开说明（工具怎么从顶层输出回溯到源寄存器） | 无 | — | 新增 | 右侧常驻栏「逐层展开」标题行右侧帮助文「从顶层输出往回到源寄存器 · 每层：Excel 原式 = 代入真实信号名」+ V2Spec §5 M44 | | 已落点 | Design§10 |
| C-223 | logic 级联 / mux 级联下拉（展开上游 vs force级联网） | MW.cascade_logic_combo / MW.cascade_mux_combo / MW.on_cascade_mode_changed | resolver.Resolver(cascade_mode=) / generator.GenOptions(logic_cascade=,mux_cascade=) | 删除 | | | 待落点 | 方案§4.4; 方案§5-2; 用户2026-06-23; 清单§1 |
| C-224 | 本信号级联下拉（单点压过全局） | MW.sig_cascade_combo / MW.on_sig_cascade_changed | generator.GenOptions(sig_cascade=) | 删除 | | | 待落点 | 方案§4.4; 方案§5-2; 用户2026-06-23; 审计A |
| C-225 | logic 加尾缀 / mux 加尾缀 全局开关 | MW.append_to_logic_chk / MW.append_to_mux_chk | resolver.Resolver(append_to_logic=,append_to_mux=) | 删除 | | | 待落点 | 方案§4.4; 方案§5-2; 用户2026-06-23; 清单§1 |
| C-226 | 本信号探尾缀网单点开关 | MW.suffix_chk / MW.on_suffix_changed / MW._save_suffix_override | resolver.Resolver(suffix_override=) / generator.GenOptions(suffix_override=) | 删除 | | | 待落点 | 方案§4.4; 方案§5-2; 用户2026-06-23; 审计A |

## 持久化与兼容（26 条：保留 25，删除 1）

> **升级不能让同事的活消失。** 旧配置文件、旧编辑文件、旧交换格式、旧入口全部要能继续用。

**`~/.dreg_verify_gui.json`（界面偏好）键全集** —— v2 必须全部能读进来（缺键各自的默认值见括号）：

| 键 | 内容 |
|---|---|
| `last_excel` | 上次载入的 Excel 全路径 |
| `cov_<view_id>` / `maxt_<view_id>` | 每个视图的全局覆盖档（缺=全面）与用例上限（缺=256）。`view_id` ∈ `topout` / `logic` / `mux` / `dft` / `iddq` |
| `coverage` | 旧单键覆盖档（v1 遗留，只在 `coverage_logic`/`coverage_mux` 缺失时作回退） |
| `coverage_logic` / `coverage_mux` / `max_tests` | 『排查(旧)』侧的两档覆盖与上限 |
| `cascade_mode` / `cascade_logic` / `cascade_mux` | 级联模式（`cascade_mode` 是旧单键回退）——对应能力已列为删除，键本身仍要能读不报错 |
| `append_to_logic`（缺=True）/ `append_to_mux`（缺=False） | 输出引用尾缀开关——已列为删除，同上 |
| `include_risky`（缺=True） | 缺前缀强制生成 |
| `export_scope`（缺=all）/ `export_comments`（缺=False）/ `export_sv_summary` / `export_owner_in_msg` | 导出 .sv 四个选项的记忆 |
| `nets_pages` | 上次勾选的 nets.txt 类别列表 |
| `probe_prefixes` | `{Excel 全路径: {信号名低: 层级前缀}}` |
| `force_signals` | `{Excel 全路径: [基名低, …]}` |
| `suffix_override` | `{Excel 全路径: {信号名低: bool}}`——已列为删除，键仍要能读 |
| `logic_overrides` | `{Excel 全路径: {基名低: spec}}`，spec = `{enabled, expr, inputs:[{var,raw}], note}` |

**`~/.dreg_verify_edits.json`（劳动成果）两种桶格式** —— 顶层是 `{Excel 全路径: bucket}`，一个 bucket 里同时可能有两套：

1. **SignalView 桶（v2 必读、必写）**：`bucket["view_edits"] = {view_id: {信号名低: {kind, src_out_name, name, renamed, cols:[…]}}}`，
   每个 col = `{name, neg, vals{绑定键:int}, exp|null, auto, auto_w, user, dft, case_index|null}`；
   `bucket["view_checks"] = {view_id: [勾选的信号原名, …]}`（全勾=默认时**不写这个键**，旧桶逐字节不变）。
   还原时按当前表与覆盖度重新分析、logic 的 `auto` 权威重算（改表后不留陈旧假绿），找不到的信号跳过并点名。

2. **legacy 桶（v2 默认不读、也不删）**：`edits` / `neg_only` / `mux_expected` / `mux_neg` / `mux_data` /
   `mux_dropped` / `mux_cleared` / `mux_user_vecs` / `signals_checked`。这些是『排查(旧)』门面的编辑模型，
   v2 里没有对应的编辑器。**处理方式：读桶时原样保留在内存/文件里、不解析、不迁移、不清除**——
   同事回退旧版本仍能读到自己的活；一旦 v2 主动清掉它，回退就等于删库。

**不随桶走的**：单点覆盖档（`SV._sig_cov`）与单点级联档（`MW._sig_cascade`）是刻意的会话内临时档，存盘会静默盖过全局下拉（R25 实测 bug）。

**交换格式不变**：探针前缀 `.txt`（`generator.parse_probe_prefix_lines` / `render_probe_prefix_grouped`，接红区 `scan_rtl.py` 产出的 `probe_prefixes.txt`）、
RTL 补充逻辑 `.json`、完整配置 `.json`（`dreg_verify_config: 2`）、`nets.txt`（`rtl_scan.render_nets_text`）。

**入口不变**：`python -m dreg_verify.gui`。


| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-227 | 旧 ~/.dreg_verify_gui.json 的全部键仍可载入（不因升级丢同事的设置） | gui._load_settings / gui._save_settings | — | 保留 | 主控补位: 无界面元素；session.load_settings 保持宽容（未知键忽略不报错），旧 27 键全可载入 | | 已落点(补位) | 计划§2-硬护栏; 计划§3-A3 |
| C-228 | 记住上次载入的 Excel 路径 | gui._load_last_excel / gui._save_last_excel（键 last_excel） | — | 保留 | 空态「最近打开」列表（Design 画了 2 条 + 时间 + 信号数）；后端目前只有单键 last_excel，见 Design对齐 §4 冲突⑥ | | 已落点 | 清单§5-1; gui:_save_last_excel |
| C-229 | 每个视图各自记住全局覆盖档与用例上限（键 cov_<view_id> / maxt_<view_id>） | SV._persist_cov / SV._persist_maxt | — | 保留 | 主控补位: 无界面元素；按范围（view_id）记 cov_<view_id> / maxt_<view_id>，与筛选行「范围」开关同 id | | 已落点(补位) | 6月审计N2; gui:_persist_cov |
| C-230 | legacy 侧的 logic/mux 覆盖档与用例上限持久化（键 coverage_logic / coverage_mux / max_tests，兼容只有旧 coverage 单键的文件） | MW._persist_coverage + MW._build_ui 启动恢复 | — | 保留 | 主控补位: 无界面元素；legacy 键 coverage_logic / coverage_mux / max_tests 载入时读一次作迁移、不再写 | | 已落点(补位) | 6月审计N2; gui:_persist_coverage |
| C-231 | 导出 .sv 四个选项持久化（export_scope / export_comments / export_sv_summary / export_owner_in_msg） | SV._ask_export_options / MW._ask_export_options | — | 保留 | 主控补位: 无界面元素；四个 export_* 键统一默认值后照常持久化（见 C-163） | | 已落点(补位) | 6月审计#3; gui:_ask_export_options |
| C-232 | nets.txt 类别勾选持久化（键 nets_pages） | MW._ask_nets_pages | — | 保留 | 主控补位: 无界面元素；nets_pages 键持久化（配合 C-188） | | 已落点(补位) | gui:_ask_nets_pages |
| C-233 | 探针前缀 / 强制 force 名单 / 单点尾缀 / RTL 补充逻辑 按 Excel 路径分桶持久化，换表自动恢复 | MW._save_probe_prefixes / _save_force_signals / _save_suffix_override / _save_logic_overrides | — | 保留 | 主控补位: 无界面元素；probe_prefixes / force_signals / logic_overrides 仍按 Excel 全路径分桶（suffix_override 随入口删） | | 已落点(补位) | gui:_save_probe_prefixes; gui:on_load |
| C-234 | SignalView 桶（~/.dreg_verify_edits.json 的 view_edits / view_checks，按 view_id 分子桶）v2 必须能读 | SV._serialize_view_edits / SV._restore_view_edits / MW._collect_view_edits / MW._restore_views_from_bucket | — | 保留 | 主控补位: 无界面元素；v2 必须能读 view_edits / view_checks 子桶（按 view_id） | | 已落点(补位) | 计划§2-硬护栏; 计划§3-A3; 6月审计#2 |
| C-235 | 旧 legacy 桶（edits / neg_only / mux_expected / mux_neg / mux_data / mux_dropped / mux_cleared / mux_user_vecs / signals_checked）默认不读不删 | MW._persist_edits / MW._apply_edits_bucket / gui._serialize_rows / gui._serialize_mux_vecs | — | 保留 | 主控补位: 无界面元素；legacy 桶默认不读不删（迁移只走 C-302 的显式动作） | | 已落点(补位) | 计划§2-硬护栏; 计划§3-A3; 方案§5-1 |
| C-236 | 编辑按『已载入』的 Excel 路径分桶（改了路径没点加载时，编辑仍算旧表的） | MW._persist_edits（_loaded_excel_path） | — | 保留 | 主控补位: 无界面元素；编辑按「已载入」的 Excel 路径分桶 | | 已落点(补位) | gui:_persist_edits |
| C-237 | 换表时先清空各视图编辑再按本表的桶还原（上一张表的编辑不会按同名串进新表） | MW._restore_views_from_bucket | — | 保留 | 主控补位: 无界面元素；换表先清空各范围编辑再按本表桶还原 | | 已落点(补位) | gui:_restore_views_from_bucket |
| C-238 | 恢复编辑时按当前表与覆盖度重新分析、重算 auto_out（改表后不留陈旧假绿） | SV._restore_cols（_recompute_col_an） | vectors.make_vector_from_base_values | 保留 | 主控补位: 无界面元素；恢复编辑时按当前表与覆盖度重算 auto_out（edits.restore_cols 已如此） | | 已落点(补位) | 6月审计#2; gui:_restore_cols |
| C-239 | 恢复/导入时，桶里有、当前表里找不到的信号跳过并点名 + 原因（改名？删行？mux 页不存在？） | MW._apply_edits_bucket（missing）/ MW._restore_edits | — | 保留 | 主控补位: 恢复/导入时点名桶里有、当前表没有的信号 + 原因，反馈落状态栏（与 C-194 同一通道） | | 已落点(补位) | Design§8-2; gui:_apply_edits_bucket |
| C-240 | 桶里数值损坏（手改坏的 mux_expected / mux_data）逐条跳过并报个数，整个载入流程不崩 | gui._coerce_int_map / gui._deserialize_mux_vecs | — | 保留 | 主控补位: 无界面元素；桶里损坏数值逐条跳过并报个数，载入不崩（edits.coerce_int_map） | | 已落点(补位) | gui:_coerce_int_map |
| C-241 | designer 手填期望关 GUI / 换表都不丢，下次自动恢复并在状态栏报恢复了几个信号 | MW._persist_edits / MW._restore_edits / SV._persist | — | 保留 | 主控补位: 手填期望关 GUI / 换表都不丢，恢复后在状态栏报恢复了几个信号 | | 已落点(补位) | 方案§4.1; 6月审计#2 |
| C-242 | 信号勾选（哪些信号进导出）持久化并在开工具时恢复 | MW._collect_checked / MW._apply_signal_checks / SV._collect_view_checks / SV._apply_view_checks | — | 保留 | 主控补位: 无界面元素；清单勾选持久化并在开工具时恢复（Design 的 S.checks 只是会话态） | | 已落点(补位) | 方案§4.1; 6月审计N1 |
| C-243 | 全勾=默认状态时不写勾选桶（保持旧桶逐字节不变、向后兼容） | SV._collect_view_checks（names>=models 返回 None） | — | 保留 | 主控补位: 无界面元素；全勾=默认时不写勾选桶（保旧桶逐字节不变） | | 已落点(补位) | gui:_collect_view_checks |
| C-244 | 批量操作（全选/清空/批量反例）挂起逐格存盘，结束统一写一次 | MW._persist_suspended（SV._check_all / SV._bulk_neg / MW.set_all_visible 等） | — | 保留 | 主控补位: 无界面元素；清单底部工具条的批量操作挂起逐格存盘、结束统一写一次 | | 已落点(补位) | gui:_persist_suspended |
| C-245 | 单点覆盖档与单点级联档刻意不存盘（否则上次留的单点档会静默盖过全局下拉） | SV._sig_cov / MW._sig_cov / MW._sig_cascade（_persist_edits 注释） | — | 保留 | 主控补位: 无界面元素；覆盖度弹层里的「本信号」单点档刻意不存盘 | | 已落点(补位) | 6月审计N3; gui:_persist_edits |
| C-246 | pytest 下不写用户的真实配置/编辑文件（防污染） | gui._save_settings / gui._save_edits_file（pytest 判定） | — | 保留 | 主控补位: 无界面元素；pytest 下不写真实配置/编辑文件 | | 已落点(补位) | gui:_save_edits_file |
| C-247 | 配置 .json 带版本号 dreg_verify_config: 2，导入时据此区分完整配置与旧版编辑文件 | MW._collect_config / MW.on_import_edits | — | 保留 | 主控补位: 无界面元素；配置 .json 带 dreg_verify_config: 2 | | 已落点(补位) | gui:_collect_config |
| C-248 | 探针前缀 .txt 的文件格式不变（红区 scan_rtl 产出的 probe_prefixes.txt 直接能导） | MW.on_set_probe_prefix.do_import/do_export | generator.parse_probe_prefix_lines / generator.render_probe_prefix_grouped | 保留 | 主控补位: 无界面元素；probe_prefixes.txt 格式不变（诊断抽屉第 3 步直接导红区产物） | | 已落点(补位) | 计划§2-硬护栏; Design§3-Q6 |
| C-249 | RTL 补充逻辑 JSON 的文件格式不变（{基名: {enabled, expr, inputs:[{var,raw}], note}}） | MW.on_logic_overrides._from_file / MW._validate_supplements | generator.make_supplement_signal | 保留 | 主控补位: 无界面元素；RTL 补充逻辑 JSON 格式不变 | | 已落点(补位) | 计划§2-硬护栏; 6月审计N4 |
| C-250 | 完整配置 .json 的字段集不变（老同事的配置文件仍能导入） | MW._collect_config / MW.on_import_edits | — | 保留 | 主控补位: 无界面元素；完整配置 .json 字段集不变 | | 已落点(补位) | 计划§2-硬护栏; 6月审计#1a |
| C-251 | python -m dreg_verify.gui 入口不变 | gui.main | — | 保留 | 主控补位: 无界面元素；python -m dreg_verify.gui 入口不变 | | 已落点(补位) | 计划§2-硬护栏; 计划§3-C5 |
| C-252 | 旧门面退役方式 | MainWindow 的 legacy 段（约 4900 行） | — | 删除 | | | 待拍板(默认=搬到 legacy_gui.py，验收后删) | 计划§5-1; 方案§5-1 |

## 快捷键与入口（16 条：保留 13，合并 3）

> 已形成肌肉记忆的键和版面参数。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-253 | Ctrl+O 打开 Excel | MW._build_ui（browse.setShortcut） | — | 保留 | 顶栏「浏览…」按钮上的 Ctrl+O 角标 | | 已落点 | Design§9; 清单§1 |
| C-254 | Ctrl+L 重新载入当前 Excel | MW._build_ui（load.setShortcut） | — | 保留 | 顶栏「载入 / 重新载入」按钮上的 Ctrl+L 角标 | | 已落点 | Design§9; 清单§1 |
| C-255 | Ctrl+P 预览勾选集 .sv | MW._build_ui（prev.setShortcut） | — | 保留 | 主控补位: Ctrl+P 绑主视图「.sv 预览」标签（Design 未标该键） | | 已落点(补位) | Design§9; 清单§1 |
| C-256 | Ctrl+R 导出报告 | MW._build_ui（rep.setShortcut） | — | 保留 | 主控补位: Ctrl+R 绑导出中心「报告（给人看）」行的直接导出（Design 未标该键） | | 已落点(补位) | Design§9; 清单§1 |
| C-257 | Ctrl+G 生成 .sv | MW._build_ui（gen.setShortcut） | — | 保留 | 顶栏「导出中心 Ctrl+G」—— 语义由「直接生成 .sv」改为「打开导出中心」，见 Design对齐 §4 冲突① | | 已落点 | Design§9; 清单§1 |
| C-258 | Ctrl+D 复制真值表列 | MW._build_testitems_tab（QShortcut on ti_table） | — | 保留 | 真值表工具条「复制列 Ctrl+D」—— 与顶栏「诊断 Ctrl+D」撞键，见 Design对齐 §4 冲突① | | 已落点 | Design§9; 审计A; 审计次要 |
| C-259 | 窗口标题带代码版本（git 短 HEAD），一眼看出跑的是哪份代码 | gui._code_version + MW.__init__（setWindowTitle） | — | 保留 | 主控补位: 窗口标题写「Dreg_verify · 版本 <短HEAD>」——§8.1 禁「git」字样，改叫「版本」；Design 顶栏只写了 Dreg_verify | | 已落点(补位) | 计划§2-⑥; gui:_code_version |
| C-260 | 窗口初始 1360×800，最小可用到 1366×768 | MW.__init__（resize） | — | 保留 | 窗口 winW×winH = 1920×1080 为常用版面；V2Spec §4「1366×768 时清单收到 420px、右侧常驻栏折叠成标签」 | | 已落点 | Design§9; 清单§1 |
| C-261 | 左右分栏默认宽度（清单 440 / 详情 760；legacy 640/700；信号表 540 / 明细 170） | SV._build（split.setSizes）/ MW._build_ui（splitter.setSizes / left.setSizes） | — | 保留 | Design state 默认宽高：listW 560 / sideW 470 / chainH 300 / frozenW 288 / truthH 420 | | 已落点 | 清单§7; gui:_build |
| C-262 | 分栏两侧都拖不没、手柄加粗好抓 | SV._build / MW._build_ui（setChildrenCollapsible(False) + setHandleWidth(6)） | — | 保留 | vHandle / hHandle 6px 手柄 + drag() 的 clamp（list 320–900 / side 280–900 / frozen 160–600 / truth 160–820 / chain 120–620），两侧都拖不没 | | 已落点 | gui:_build |
| C-263 | 工具条按钮按可用宽度自动换行，窄屏不把面板挤没、分栏拖得动 | gui.FlowLayout | — | 保留 | 真值表工具条 flex-wrap:wrap 自动换行；清单底部工具条同 | | 已落点 | gui:FlowLayout |
| C-264 | 表格与信号名用等宽字体 | MW._mono | — | 保留 | V2Spec §4「所有信号名、地址、数值、表达式一律 Consolas 11.5px」 | | 已落点 | Design§9; gui:_mono |
| C-265 | 切回某个视图时，只有覆盖度/上限真变过才重建清单（不每次切页都重跑全表） | MW._on_main_tab_changed（built_key 指纹） | — | 保留 | 主控补位: 切范围时按覆盖度/上限指纹判断是否重建清单（无界面元素） | | 已落点(补位) | gui:_on_main_tab_changed |
| C-266 | 5 份逐字重复的工具条（约 110 个按钮）收成 1 份 | SignalView ×5 实例的工具条 | — | 合并 | V2Spec §1 说明「工具条从 5 份降到 1 份，约 110 个重复按钮消失」+ §5 映射表多行 | | 已落点 | 方案§4.2; 清单§2; 方案§1 |
| C-267 | 6 个顶层 tab 收成 1 个工作台（左清单 + 右详情） | MW.main_tabs 的 6 页 | — | 合并 | Design 主画板整体：一个工作台 = 顶栏 + 筛选行 + 清单区 + 详情区（标题栏 / 主视图 / 右侧常驻栏）+ 状态栏，6 tab 全部收掉 | | 已落点 | 方案§4.2; Design§5-H1; 清单§1 |
| C-268 | 两处 .sv 预览（SignalView 的与 legacy 的）合成一处 | SV.sv / MW.preview | — | 合并 | 主视图「.sv 预览」标签一处，工具条「改看勾选集」在本信号与勾选集之间切（V2Spec §5 M18） | | 已落点 | 方案§4.2; 清单§2 |

## 反馈与文案（9 条：新增 5，保留 4）

> Design §8 的设计原则落到具体行为上。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-269 | 状态栏逐操作反馈（加了几条反例、跳过几条、改名成什么、同步了哪个寄存器…） | MW.status.showMessage（全局 statusBar） | — | 保留 | 状态栏三段（statusLeft / statusRight /「编辑自动存盘 · 上次 14:31」）；逐操作反馈走 statusLeft | | 已落点 | 清单§5; gui:_build_ui |
| C-270 | 跳过/过滤掉的内容必须点名 + 给原因，计数放后面 | gui._skipped_detail_text / SV._export_summary_text | generator.build（skipped） | 保留 | V2Spec §6 文案红线「跳过/过滤的东西一律先点名字和原因，计数放后面」+ 导出完成弹层版式 | | 已落点 | Design§8-2; 清单§7; 方案§7 |
| C-271 | 界面不出现仓库路径、git、CLI 命令、『请把下面发给维护者』 | MW.on_load（预览页 traceback）+ 6 处 tooltip 里的 CLI 命令 | — | 保留 | V2Spec §6 文案红线「不出现仓库路径、git、CLI 命令、『请把下面发给维护者』」—— 诊断抽屉第 2 步写了 python3 scan_rtl.py，见 Design对齐 §4 冲突⑪ | | 已落点 | Design§8-1; 清单§7; 方案§3-轨0-7 |
| C-272 | 载表出错时用工程师语言报错，不把 traceback 塞进 .sv 预览页 | MW.on_load（preview.setPlainText 分析异常清单） | — | 保留 | 主控补位: 载表出错用工程师语言报错、不把 traceback 塞进 .sv 预览页（与 C-005 同一通道） | | 已落点(补位) | Design§8-1; 清单§7; 计划§3-C1 |
| C-273 | 禁用词就地解释或换说法（cone / F0–F4 / 缝 / prefixed-wire / wire 兜底 / 记账 / claims / tmm / regmap / CUVUNF） | 现 76 处 cone、16 处 CUVUNF 全窗口无定义 | — | 新增 | V2Spec §6 术语替换表 11 行（cone / F0–F4 / 缝 / prefixed-wire / wire 兜底 / 记账 / claims / tmm / regmap / CUVUNF / 假绿）+「可以直接用的词」白名单 | | 已落点 | Design§8-6; 清单§6; 方案§7 |
| C-274 | 术语替换表（每个禁用词给替代说法）作为一份可查的表落进代码 | 无 | — | 新增 | V2Spec §6 的 T 数组即该表；落码时对应 inputs_table.scrub_terms 的替换表，三列（禁止裸用 / 界面上写成 / 首次出现时的完整说法） | | 已落点 | Design§11-6; 计划§3-C1 |
| C-275 | 所有显示线网名的地方区分『表里查到的』与『工具按命名约定猜的』 | 无（resolver 的 found_in 有信息，界面不分） | resolver.Resolver（found_in） | 新增 | 输入信号表橙色虚下划线 + 表头角标「名字来自命名约定，表里未查到」；电路图虚线橙框 + 角标；V2Spec §3 状态四 | | 已落点 | Design§3-Q3; 方案§7; 计划§3-C2 |
| C-276 | 载入 200+ 信号时给进行中态，分析放后台不卡界面 | 无（on_load 同步跑完全表） | topout.topout_view_models | 新增 | 载入态（S.loading）：「正在展开 Topout 信号 137 / 211」+ 65% 进度条 + 当前信号名 +「清单已可点，展开完的信号先出现；未完成的显示「分析中」」+「停止分析」按钮 —— 后端无 worker，见 Design对齐 §4 冲突⑨ | | 已落点 | Design§11-3; 计划§3-C1; 方案§7 |
| C-277 | 『Excel 没有某一页』『未解析信号』『规格冲突』『猜名线网』『手填与 auto 不一致』五种状态有统一的视觉表达 | 散在 STATUS_LABEL / TOPO_STATUS_LABEL / 各处 tooltip | — | 新增 | V2Spec §3 六种状态设计（Excel 少了某一页 / 未解析的信号 / 规格冲突 / 猜名的线网 / 载入 200+ 进行中 / 手填与 auto 不一致），每种给底色 + 处置文案 | | 已落点 | Design§11-3 |

## 信号流图（12 条：新增 12）

> 随 v2 一起出的新组件（数据层已在 `sigflow.py`，TASK-013 已完成）。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-278 | 每个信号一张门级图：源寄存器在左、顶层输出在右，每条线标真实网名 + 位宽 | 无（数据层已在 sigflow.py） | sigflow.build_graph / sigflow.render_svg | 新增 | 电路图区 SigFlowDiagram（mid 变体）：源寄存器在左、顶层输出在右，线上标 d_wl_rf_logen_mixer_en [0:0] 这类真名 + 位宽 | | 已落点 | 方案§0-3; 方案§5-3; Design§6; TASK-013 |
| C-279 | 图元齐全：寄存器盒 / 线控·RO 输入盒 / 猜名输入盒（虚线+角标）/ AND·OR·NOT·XOR·NAND / MUX2 / MUXn（每支标 case 值含 don't-care，冲突支红虚线+Excel 行号）/ DFT 门控盒 / 总线抽头合并 / 改名·电平移位透传块 / 顶层输出端子 | 无 | sigflow.render_svg | 新增 | SigFlowDiagram 已给：寄存器盒（实线 + 左侧色条 + RW @0x64[0:0] 副标）/ 猜名盒（虚线橙框 #a8710f）/ AND 半圆体（含气泡）/ OR 曲线体 / MUXn 梯形（S/1/0 支标）/ TOP 箭头形端子 / 冲突支红虚线 + Excel 行号 / dft 门控；缺 NOT·XOR·NAND 独立形、don't-care 支标 4'b000x、总线抽头三角与拼接梯形、改名/电平移位透传块 → 按 V2Spec §4 补齐 | | 已落点 | Design§6; TASK-013 |
| C-280 | 图在 GUI 详情页显示（三视合一之一） | 无（数据层不挂现 GUI） | sigflow.render_svg | 新增 | 详情区主视图：真值表下方常驻电路图区（S.truthH 分割，S.flowFull 可全屏） | | 已落点 | 方案§5-3; Design§5-H2; TASK-015 |
| C-281 | 图可缩放平移 | 无 | — | 新增 | 电路图工具条「适应窗口」「100%」+ zoomLabel；画布右键拖拽平移（onPan）、Ctrl+滚轮缩放（onZoom）、双击复位 | | 已落点 | Design§6; TASK-015 |
| C-282 | 点一根线 → 展开链 / 真值表 / 输入表里同名行联动高亮 | 无 | sigflow.render_svg（data-net） | 新增 | 电路图底栏「点一根线 → 真值表同名行与展开链同步高亮」（sigflow.render_svg 已挂 data-net / data-node 钩子） | | 已落点 | Design§3-Q2; Design§6; TASK-015 |
| C-283 | hover 寄存器盒出地址/位段/为什么判成 RO 或 RW | 无 | sigflow.build_graph（binding.note） | 新增 | 电路图底栏「hover 寄存器盒看地址 / 位段 / 为什么判成 RO 或 RW」 | | 已落点 | Design§6; TASK-015 |
| C-284 | 图导出 SVG / PNG | 无 | sigflow.render_svg / sigflow.render_png | 新增 | 电路图工具条「导出 SVG / PNG」 | | 已落点 | Design§6; TASK-015; 计划§5-3 |
| C-285 | 同一张图内联进 HTML 报告 | 报告路径已可内联 | sigflow.attach_report_svgs | 新增 | 导出中心第 2 行「HTML（含电路图）」；导出完成弹层「D:\work\用例表.html　含每个信号的电路图」 | | 已落点 | Design§6; TASK-013; TASK-015 |
| C-286 | 排版打磨：交叉最小化 / ANSI 门形 / 列缝按最长标签定宽 / 端口标签让位 / channel 超 6 条不重叠 | 无 | sigflow.render_svg | 新增 | 主控补位: 排版打磨（交叉最小化 / 列缝按最长标签定宽 / 端口标签让位 / channel 超 6 条不重叠）——Design 三个变体是手摆坐标不含自动排版；沿用 sigflow.py 分层算法并按 Design 视觉规范调参 | | 已落点(补位) | TASK-015; 计划§5-3 |
| C-287 | 数据层改动点 B / D / E + RO 回读根挂图 + review 遗留 M4/m2/m3/m5/m7(b) | 无 | sigflow.build_graph / sigflow.attach_report_svgs | 新增 | 主控补位: 数据层改动点 B/D/E + RO 回读根挂图 + review 遗留 M4/m2/m3/m5/m7(b)——后端项，无界面元素 | | 已落点(补位) | TASK-015; 计划§5-3; 原 status: 待拍板(默认=进本轮) |
| C-288 | 覆盖度叠加：把当前向量集实际走到的 mux 支点亮、没被驱动过的支涂灰（假绿可视化） | 无 | sigflow.build_graph | 延期 | 主控补位: 覆盖度叠加（点亮走到的 mux 支 / 没驱动过的涂灰）排到 v2 之后单开任务（执行计划 §5 待拍板 3 默认值）；V2Spec §4「深度上限 6 层 / 约 60 节点」给了预留空间 | | 延期(v2 之后单开任务) | 方案§3-轨1-二期; TASK-015; 计划§5-3; 原 status: 待拍板(默认=v2 之后单开任务) |
| C-289 | 多信号总览图 + PDF 导出 | 无 | — | 延期 | 主控补位: 多信号总览图 + PDF 导出排到 v2 之后单开任务（执行计划 §5 待拍板 3 默认值） | | 延期(v2 之后单开任务) | TASK-015; 计划§5-3; 原 status: 待拍板(默认=v2 之后单开任务) |

## 新增（10 条：新增 9，删除 1）

> 两边都没有、v2 新做的能力（信号流图除外，见上一节）。

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-290 | 粘贴一份信号名单，一次勾上对应的信号 | 无（两边都没有） | — | 新增 | 主控补位: 清单底部工具条加「粘贴名单勾选…」（执行计划 §5 待拍板 5 默认=进本轮 C1）；Design 底部工具条只有 5 个按钮 | | 已落点(补位) | 审计B; TASK-007; 计划§5-5; 原 status: 待拍板(默认=进本轮 C1) |
| C-291 | 把当前的勾选/筛选存成一个命名预设，下次一键取回 | 无（两边都没有） | — | 新增 | 主控补位: 筛选行右端加「预设 ▾」（存/取当前勾选 + 筛选，执行计划 §5 待拍板 5 默认=进本轮 C1） | | 已落点(补位) | TASK-007; 计划§5-5; 原 status: 待拍板(默认=进本轮 C1) |
| C-292 | 按模块前缀把信号分组显示 | 无（两边都没有） | — | 删除 | | | 待拍板(默认=不做) | TASK-007; 计划§5-5 |
| C-293 | 真值表多格选区（像 Excel 一样框选一片单元格） | 无 | — | 新增 | 真值表提示条「已选 T13 整列（6 格）」+「右键行/列：插入 · 删除 · 复制 · 设为反例」；矩形多格选区按同一口径补 —— 用户标注截图 43a5d90d 圈定单列 | | 已落点 | 方案§5-5; Design§5-H6; Design§10 |
| C-294 | TSV 粘贴落在活动格；粘贴超出行数自动追加；整次粘贴算一个撤销步 | 无 | — | 新增 | 真值表提示条「Ctrl+V 从 Excel 粘贴 TSV，落在活动格；超出列数自动追加列，整次粘贴算一步撤销」 | | 已落点 | 方案§5-5; Design§5-H6 |
| C-295 | 冻结左侧信号名列与列表头 | 无（行表头已是纵表头，列表头未冻结） | — | 新增 | 真值表冻结列区（frozenStyle，S.frozenW=288，可拖 160–600）+ 列头 28px sticky；清单表头亦 sticky | | 已落点 | 方案§5-5; Design§5-H6 |
| C-296 | 真值表右键行列菜单 | 无（审计实测两边都没有右键菜单） | — | 新增 | 真值表提示条「右键行/列：插入 · 删除 · 复制 · 设为反例」 | | 已落点 | 方案§5-5; Design§5-H6; 审计A |
| C-297 | 放大到接近全屏的编辑覆盖层 | 无 | — | 新增 | V2Spec §2 H6「放大到全屏的编辑覆盖层……建议放到二期」——Design 明确降级；主视图本身已 1150×700，电路图「全屏」按钮给了同类体验 | | 已落点 | 方案§5-5; Design§5-H6 |
| C-298 | 从 CSV / xlsx 批量导入期望值（一个信号 25 列、一张表上百次单元格编辑） | 无 | — | 新增 | 真值表工具条「导入期望…」与「批量填…」两个按钮 | | 已落点 | 方案§5-5; Design§3-Q4; Design§10; 清单§5 |
| C-299 | 撤销 / 重做（真值表编辑） | 无 | — | 新增 | 真值表提示条「整次粘贴算一步撤销」；撤销栈由 A5 §5 的 QUndoStack 承担，按 Qt 惯例走 Ctrl+Z/Y 快捷键 | | 已落点 | 方案§5-5; Design§5-H6 |

## 主控追加（A3 删除安全审计后，3 条：新增 2，保留 1）

| ID | 能力 | 旧入口 | 后端 API | 类别 | v2 落点 | 测试 ID | 状态 | 来源 |
|---|---|---|---|---|---|---|---|---|
| C-300 | v2 写 ~/.dreg_verify_edits.json 时按路径桶【合并】：先读旧桶再更新自己管的键；legacy 段与其它 view_id 段逐字节不动（整桶重建会静默抹掉同事的手填期望） | MW._persist_edits（整桶重建 allbuckets[path]=bucket，靠内存里还有 legacy 状态才没丢） | — | 新增 | 主控补位: 无界面元素；写 edits 文件按路径桶【合并】（先读旧桶再更新自己管的键），legacy 段与其它 view_id 段逐字节不动 | | 已落点(补位) | A3§2-坑④ |
| C-301 | 序列化保住错值/期望为 0 的负向列（wrong_value: 0 / exp: 0 不能被真值判断当成空丢掉） | gui._serialize_rows（v is not False 身份比较） | — | 保留 | 主控补位: 无界面元素；序列化保住 wrong_value: 0 / exp: 0 的负向列（is not False 身份比较） | | 已落点(补位) | A3§3.2 |
| C-302 | 『导入旧版真值表编辑』一次性显式动作：把 legacy 桶里 logic/直连寄存器根的手填期望/负向迁到 v2（键空间相同，auto 重算）；mux 根点名列出不迁的原因；legacy 段只读不删 | 无（A3 实测可行） | topout.resolve_root（源名→Topout 顶层名） | 新增 | 主控补位: 诊断抽屉 otherSymptoms 加第 5 条「从旧版真值表编辑迁进来」（一次性显式动作，legacy 桶只读不删，mux 根点名列出不迁原因）；Design 未画 | | 已落点(补位) | A3§3.2-D; 原 status: 待拍板(默认=做，进诊断抽屉) |

另：C-217『缺前缀是否强制生成』可见开关的**默认值必须仍为 True**（A3 实测改 False 会改 Topout .sv 字节、页本地 dft 视图整页空掉）；C-235 legacy 桶策略经 A3 确认。

## 待拍板项 → 契约 ID 对照

执行计划 §5 的 5 个待拍板项，逐个落到具体能力行上（不拍板就按默认走，不停工）：

| §5 项 | 内容 | 契约 ID | 默认值 |
|---|---|---|---|
| 待拍板 #1 | 退役方式 | C-252 | 搬到 legacy_gui.py，验收后删 |
| 待拍板 #2 | 真值表编辑器路线 | C-141 | QTableView 原生实现 |
| 待拍板 #3 | 信号流图二期范围 | C-287、C-288、C-289 | v2 之后单开任务；进本轮 |
| 待拍板 #4 | Design §10 没列、但旧界面有的两项能力 | C-046、C-047 | 可选列、默认隐藏；并入正则搜索 |
| 待拍板 #5 | TASK-007 余项 | C-290、C-291、C-292 | 不做；进本轮 C1 |

> 注：#4 与 #5 各覆盖多条独立可验证的能力，按粒度约定各占一行，所以带 `待拍板` 状态的行数（10）多于待拍板项数（5）。

## 文档没写、代码里有（65 条）

普查 `gui.py` 7475 行时发现的、前述六份文档（推进方案 / 执行计划 / Design prompt / 真值表能力对照审计 / GUI 现状清单 / 6 月通用功能丢失审计）都没提到的能力。**这是本表最值钱的部分**——它们正是「照文档重构就会静默丢掉」的那一批。

| ID | 能力 | 旧入口 | 为什么算一条能力 |
|---|---|---|---|
| C-003 | 开工具时自动把上次那张表载进来 | gui.main + _load_last_excel/_save_last_excel | 清单§5-1 写的是「记住上次路径但不自动加载」，实际 main() 会 on_load() |
| C-004 | 命令行带一个 .xlsx 启动，直接载入这张表 | gui.main（sys.argv 首个存在的文件） | 同事发一张表过来，命令行直接开就能看，不用再走浏览；换入口时最容易漏掉 |
| C-006 | 载完在状态栏报：信号总数、logic/mux 各几条、几条非 clean、tmm/regmap 字段数、Topout 要验几个 | MW.on_load（status.showMessage） | 工程师判断『这张表读全了没』的唯一依据——logic/mux 条数、非 clean 个数、字段数对不上就是表有问题 |
| C-007 | 单个信号解析炸了不连累整张表，那一行标『解析异常』并留住 error 全文 | MW.on_load / MW._reanalyze_all（逐信号 try） | 211 个信号里坏 1 个不能让其余 210 个看不成；error 全文是排查这一个的唯一线索 |
| C-009 | 信号名列带位宽切片显示（如 d_bt_lp_lna_itrim[3:0]），查找仍按裸名 | SV._populate_table（m['disp']） | 位宽是 IC 工程师认信号的一半信息：d_xxx 和 d_xxx[3:0] 是两种验法 |
| C-019 | 探针前缀列标出『探针口=xxx (level_shift)』——探针网名经 level_shift 页自动解析，无需手配 | MW._prefix_cell | 这是第三种前缀来源，与手配前缀、输入侧前缀并列 |
| C-020 | 探针前缀列标出输入侧命中：『<输入名>→<前缀>』——某根 force 输入的路径带前缀 | MW._prefix_cell（found_in=='prefixed-wire'） | 『前缀配了但配在输入侧还是输出侧』只能从这里看出来，配错了要到仿真才报错 |
| C-025 | owner 下拉里『（无 owner）』单列一项并带条数 | MW._rebuild_owner_menu（NO_OWNER） | Excel owner 列留空的信号最容易没人认领；不单列出来就永远筛不到它们 |
| C-026 | owner 按钮文字随已选个数变化，悬停列全部已选 owner | MW._update_owner_btn_text | 多选筛之后『我现在筛的是谁』要一眼能确认，否则会把筛剩的一部分当全量导出 |
| C-027 | 换表后自动丢掉新表里已不存在的 owner 选项（不留死项、计数不虚高） | SV._rebuild_owner_menu / MW._rebuild_owner_menu | 不清就留死项、按钮计数虚高，用户以为筛到了、实际筛空 |
| C-031 | 搜索/筛选后状态栏报『可见 N / 共 M』，并单独点出有几个是因为输入信号名命中才列出来的 | MW.apply_filter（n_by_input） | 搜输入网名时『这一行为什么会出现』是关键反馈，否则看着像搜错了 |
| C-040 | 嵌套 mux 被自动折叠过的信号，状态格加 ⚙ 标记、悬停给折叠全文 | MW._populate_table（normalized_note） | 工具替 designer 做了折叠 = 改了对规格的理解，必须让 designer 能复核 |
| C-041 | 开了『缺前缀强制生成』后，状态列如实写成『⚠缺前缀·已强制生成』而不是『跳过』 | MW._populate_table（include_risky 分支） | 开了这个逃生阀之后，这批信号到底进没进 .sv 是两种完全不同的后果 |
| C-042 | 本表没有该页时给空态提示而不是空白表 | SV.refresh + provider.empty_hint / SV._guard | 『这张表没有 mux 页』和『mux 页读失败』是两回事，空白表分不出来 |
| C-043 | 分析整表出错时在详情区写『分析失败（已捕获，未崩）』并留住原因，界面不崩 | SV.refresh（except 分支） | 『绝不崩』护栏的落点：崩一次用户就丢掉本次全部未存盘的操作 |
| C-044 | 刷新清单后自动选中第一个可见行（直接就能看到内容） | SV.refresh 末尾 | 载完就能看到内容；空详情页会让人以为表没载成 |
| C-058 | 输入表标出『⚠需探针前缀（内部衔接网，跑 scan_rtl 配前缀否则跳过）』 | MW._binding_meta（found_in in needs-prefix/mux-output） | 解析到了但仍要配前缀——这是仿真报『找不到这根网』最常见的隐藏原因 |
| C-059 | 输入表的驱动列上色：未解析=红、需前缀=琥珀 | MW._populate_inputs / MW._populate_mux_inputs | 一屏几十行输入里，哪根驱不动要能扫一眼看到，不能靠逐行读文字 |
| C-061 | mux 输入表的数据寄存器按物理寄存器收拢（同一寄存器只显一行）并标『被哪个 case 选中』 | MW._populate_mux_inputs（seen_bases） | 死分支 / LUT 型同源多 case 会刷出十几行重复；收拢后才和 for_test 的 t0~t7 对得上 |
| C-062 | 输入表单列出 dft 页的 iddq 门（角色『DFT门(iddq)·每条测试驱透传值』） | MW._dft_gate_input_row | 门在 dft 页、不在展开输入里；不单列出来，用户对照 for_test 会以为漏了一个源头控制 |
| C-069 | 全量预览超过 600 行时截断并注明总行数（不卡界面） | MW.on_preview（lines[:600]） | 211 信号全量预览是几万行，不截断界面直接卡死 |
| C-070 | 全量预览末尾追加本次会跳过哪些信号、各自缺哪根输入 | MW.on_preview 末尾 appendPlainText | 预览阶段就点名跳过项，不必等导出完才发现少了信号 |
| C-072 | 配置变更（前缀/级联/尾缀）后预览页自动按新配置重算，不留旧内容误导 | MW._refresh_preview（_preview_source） | 留着按旧前缀算的预览，会让人得出『改了没生效』的错误结论 |
| C-076 | 输入行按 for_test 页的行序排（与报告/回填/截图同一口径） | gui._order_groups_fortest / MW._load_test_items | m4 口径统一，避免编辑器与导出两套行序 |
| C-084 | 单元格取值显示格式：1 位→0/1；2–4 位→0bXXXX 零填充；更宽→0xNN | MW._cell_text | 这个格式保证能被 parse_int 原样读回 |
| C-100 | 反例错值同时避开 auto_out 和 designer 手填期望两个『正确值』，防止反例静默 PASS | SV._add_negatives（V.make_negative 防撞） | m6，~auto 恰等于手填期望时反例会变成 NEG-BROKEN |
| C-113 | mux 手填数据值撞值时提示『≥2 条数据路取到相同值=选错路也测不出（假绿）』 | MW._load_mux_test_items（meta['override_collision']） | 两条数据路取到同一个值 = 选错路也 PASS（假绿），手填数据值时最容易踩 |
| C-114 | mux 手填期望按输入取值键存，切覆盖度不串号 | MW._on_mux_exp_changed（generator.mux_assign_key） | mux 向量由 case 结构生成、列号随覆盖度变；按列号存会把期望串到别的 case 上 |
| C-125 | mux 头部标出被跳过的死分支（靠后重复 case） | MW._update_mux_header（shadowed_note） | 被跳过的重复 case 是表的问题；不标出来，designer 永远不知道有一支没验 |
| C-126 | mux 头部标出本信号受 dft 页 iddq 门控，以及门能不能 force | MW._update_mux_header 末尾 | 门能不能 force 决定这组还验不验得成，要在编辑器当场看到，而不是导出后才发现被跳过 |
| C-129 | iddq 漏电态自检拍列的期望格用淡紫 + 说明『工具自动加的，不是反例』 | SV._populate_truth（DFT_BG）/ SV._is_dft_pitch_col | 它是工具自动加的、不是用户的反例；混在琥珀反例里会被当成误加而删掉 |
| C-130 | iddq 自检拍列不参与表达式重算（否则会假红当成反例） | SV._recompute_col_an（_is_dft_pitch_col 早退） | 表达式不建模 iddq 门，重算会算出功能值 → 整列假红，用户会以为自己填错了 |
| C-131 | 门已经是本信号显式输入时不再单列一行 DFT 门（与生成/报告去重同口径） | MW._dft_pin_display(input_bases=) | 门已经是显式输入时再列一行，同一根网在真值表里出现两次、与 .sv 和报告对不上 |
| C-134 | 真值表编辑按钮按信号可编辑性启用/禁用（不可建信号不给点） | SV._set_edit_buttons | 不可建信号点编辑会崩或静默无效；置灰是唯一的『这条不能编辑』提示 |
| C-135 | 选中 mux 信号时『加列/复制/反例/改名』按钮的说明文案换成 mux 语义 | MW._set_ti_buttons_for_mux（_MUX_BTN_TIPS） | mux 的『加列』是按 case 克隆、不是凭空造输入；沿用 logic 文案会误导 |
| C-138 | 单信号 CSV 另写『期望来源』行（反例 / designer手填 / auto_out兜底）与『反例?』行 | SV.on_export_csv / MW.on_ti_export_csv | designer 拿到 CSV 要分得清哪条是自己填的、哪条是工具兜底的 |
| C-139 | mux 信号导出真值表 CSV，且忠于产物（勾了反例就带上反例列、与生成同口径去重重排号） | MW._export_mux_csv | CSV 要和实际导出的 .sv 一条不差，否则核对不了 |
| C-151 | 动全局下拉时清掉当前正在看的这个信号的单点档，让全局改动立刻可见 | MW._clear_displayed_sig_cov_on_global | 用户拍板的『你拧哪个旋钮都对眼前信号生效』 |
| C-153 | 已自定义编辑过的信号，改档只影响它的清单用例数与导出，不冲掉编辑 | SV._on_sig_cov_changed（cur_name in edits 判定）/ MW.on_coverage_changed | 改全局档不能把用户手填的期望冲掉——这是编辑与覆盖度两个系统的边界 |
| C-154 | 只加过反例、没手改正向的信号，切全局档时正向按新档重算再补回反例 | MW._neg_only_rows_now / MW._set_signal_negatives（_neg_only 规则 first/all） | 设计哲学——纯加反例不该把信号冻结成自定义 |
| C-168 | 导出完成后自动跳到 .sv 预览页显示这次的内容 | SV.on_export_sv（inner.setCurrentIndex(1)） | 导出完能立刻看到写出去的是什么，不必再开文件 |
| C-169 | 按导出范围给不同的默认文件名（wr_rf_tc.sv / _pos.sv / _neg.sv） | MW.on_generate（default_name） | 仅正向 / 仅反例两份不能互相覆盖，默认文件名就把这事挡住 |
| C-170 | 汇总命名块按范围加后缀（_pos/_neg），两份贴进同一作用域也不重名 | MW.on_generate（block_suffix） | 两份 .sv 贴进同一个 task 体里不重名，否则 elaboration 失败 |
| C-171 | 写文件失败时说清楚是不是被仿真器/编辑器占用 | MW.on_generate（except OSError） | 文件被仿真器/编辑器开着是最常见的写出失败原因，直接说出来省一轮排查 |
| C-184 | 回填含 mux 表 | SV.on_fortest（include_mux=True） | SignalView 走 include_mux=True，legacy 不带 |
| C-187 | nets.txt 类别只列本表真有内容的页（空页不出现） | MW._nets_categories | 空页导出也是空，列出来只会让人以为导漏了 |
| C-191 | 导出配置完成后逐段报数（勾选几个 / 全局档 / 前缀几条 / 强制 force 几个 / 编辑几个信号、手填期望几条） | MW.on_export_edits 完成弹窗 | 配置是给同事用的交付物，导出时要能当场确认带走了哪些东西 |
| C-193 | 导入配置时按文件名核对源表，不一致就提示『这份配置是为《X》导出的，当前是《Y》』 | MW.on_import_edits（excel_mismatch） | 拿错配置导进来会静默落空；按文件名（不按全路径）提示，跨机器路径不同不误报 |
| C-195 | 导入的不是 dreg_verify 配置文件时明确说缺哪个段，不静默失败 | MW.on_import_edits（is_full/is_legacy 判定） | 不说清缺哪个段，用户只会反复试别的文件 |
| C-204 | 改完探针前缀报影响面：共 N 条映射、影响 M 个信号 | MW.on_set_probe_prefix 末尾 | 配完前缀到底解决了几个信号，是决定要不要再跑一轮 scan_rtl 的依据 |
| C-205 | 改完探针前缀重建各视图清单时保住用户已有的勾选（不被重置成全勾） | MW.on_set_probe_prefix（_collect_view_checks/_apply_view_checks 往返） | 重建清单会把勾选重置成全勾；不保住就等于把用户挑了半天的导出集清掉了 |
| C-207 | 强制 force 名单支持行尾 # 注释、留空=清除 | MW.on_set_force_signals（split('#')） | 名单要能带着『为什么加这一条』一起交给同事 |
| C-213 | RTL 补充逻辑六道校验（信号名占位符未替换 / spec 非对象 / 缺 expr / inputs 非空列表 / 表达式解析 / 变量没有 input 映射），不通过不保存并逐条列错 | MW._validate_supplements | 补充式是偏离 Excel 的手写逻辑，写错会静默生成错断言；不通过不保存是唯一防线 |
| C-214 | 补充的基名不在当前 Excel logic 页时提示『将作为纯新增合成信号生成』 | MW.on_logic_overrides（unknown） | 补一个表里没有的基名是合法用法，但也可能是名字打错了，必须问一声 |
| C-220 | 级联说明每次打开重读文件，文档更新不用重启工具 | MW._open_cascade_doc | 文档更新后不用重启工具，说明窗永远是最新的 |
| C-236 | 编辑按『已载入』的 Excel 路径分桶（改了路径没点加载时，编辑仍算旧表的） | MW._persist_edits（_loaded_excel_path） | 用户改了路径还没点加载时，这批编辑仍属于旧表；分错桶 = 编辑落到别的表上 |
| C-237 | 换表时先清空各视图编辑再按本表的桶还原（上一张表的编辑不会按同名串进新表） | MW._restore_views_from_bucket | 不清就会静默写错断言 |
| C-240 | 桶里数值损坏（手改坏的 mux_expected / mux_data）逐条跳过并报个数，整个载入流程不崩 | gui._coerce_int_map / gui._deserialize_mux_vecs | 编辑文件可能被手改坏；每次载表都要读它，崩一次用户就再也打不开工具 |
| C-243 | 全勾=默认状态时不写勾选桶（保持旧桶逐字节不变、向后兼容） | SV._collect_view_checks（names>=models 返回 None） | 保持旧桶逐字节不变，是同事回退旧版本仍能读的前提 |
| C-244 | 批量操作（全选/清空/批量反例）挂起逐格存盘，结束统一写一次 | MW._persist_suspended（SV._check_all / SV._bulk_neg / MW.set_all_visible 等） | 全选时否则要写几百次文件 |
| C-246 | pytest 下不写用户的真实配置/编辑文件（防污染） | gui._save_settings / gui._save_edits_file（pytest 判定） | v2 的测试基建要保住这条，否则跑一次测试覆盖用户真实手填期望 |
| C-247 | 配置 .json 带版本号 dreg_verify_config: 2，导入时据此区分完整配置与旧版编辑文件 | MW._collect_config / MW.on_import_edits | 区分完整配置与旧版编辑文件——两者导入语义完全不同（先清后载 vs 只合并） |
| C-262 | 分栏两侧都拖不没、手柄加粗好抓 | SV._build / MW._build_ui（setChildrenCollapsible(False) + setHandleWidth(6)） | 拖没了就再也拖不回来，只能重启工具 |
| C-263 | 工具条按钮按可用宽度自动换行，窄屏不把面板挤没、分栏拖得动 | gui.FlowLayout | 按钮一多，普通布局会把面板最小宽度撑到分栏拖不动；这是分栏能用的前提 |
| C-265 | 切回某个视图时，只有覆盖度/上限真变过才重建清单（不每次切页都重跑全表） | MW._on_main_tab_changed（built_key 指纹） | 每次切页都重跑 211 信号全表分析会卡；但档变了不重建又会显示陈旧的用例数 |

## 统计

| 类别 | 条数 |
|---|---|
| 保留 | 228 |
| 合并 | 9 |
| 降级(诊断抽屉) | 19 |
| 删除 | 11 |
| 新增 | 34 |
| 延期 | 2 |
| **合计** | **303** |

| 分节 | 条数 |
|---|---|
| 清单 | 50 |
| 详情 | 23 |
| 真值表编辑 | 69 |
| 覆盖度 | 15 |
| 导出 | 43 |
| 诊断 | 27 |
| 持久化与兼容 | 26 |
| 快捷键与入口 | 16 |
| 反馈与文案 | 9 |
| 信号流图 | 12 |
| 新增 | 10 |
| 主控追加 | 3 |
| **合计** | **302** |

其它口径：

- 「文档没写、代码里有」：**65** 条
- 非「删除」类（必须有 v2 落点 + ≥1 条通过的测试，L2/L3）：**294** 条（302 − 删除 8；旧版此处写 291 有误，B1 更正）
- 原带 `待拍板(默认=…)` 状态的 **11** 条（旧版此处写 10 有误，B1 更正）：类别≠删除的 9 条（C-046 C-047 C-141 C-287 C-288 C-289 C-290 C-291 C-302）已在 B1 改成 `已落点`/`补位待过目`，原状态文字挪进 `note` 列的「原 status: …」；类别=删除的 C-252、C-292 保持不动

### B1（Design 对齐，2026-09-12）填完 `v2 落点` 后的状态分布

对照文档：`docs/GUI_v2_Design对齐_20260912.md`。

| 状态 | 条数 | 含义 |
|---|---|---|
| `已落点` | **172** | Design 主画板 / V2Spec 给了明确落点 |
| `补位待过目` | **122** | Design 没给落点，`v2 落点` 列以 `主控补位: ` 开头，等用户过目 |
| 类别=删除（不动） | **8** | C-048 / C-049 / C-223 / C-224 / C-225 / C-226 / C-252 / C-292 |
| **合计** | **302** | |

| 分节 | 合计 | 已落点 | 已落点(补位) | 删除类 |
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

> 上表按 csv 的 `area` 列聚合，与本文档的「分节」略有差异：md 里的「主控追加」3 行在 csv 里 area 分别是 持久化与兼容（C-300 / C-301）与 诊断（C-302），所以 诊断 28（md 27）、持久化与兼容 28（md 26）。
>
> 「持久化与兼容」26/28 是补位，不是 Design 的漏——这一节几乎全是**无界面元素**的行为契约（桶合并写、格式兼容、pytest 不落盘），Design 本来就画不出来；这些行的 `v2 落点` 写的是实现约束而不是控件位置。
