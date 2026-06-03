# -*- coding: utf-8 -*-
"""
resolver.py — 把 logic 输入信号解析为驱动方式（force / RF_WRITE）、地址、寄存器内位段。

规则（来自 generation-rules 记忆，已用 reserve/lna_agc 对照 .sv）：
  1. force vs RF_WRITE = 字段类型 RO vs RW：RO→force wire；RW→RF_WRITE(addr,data)。
  2. 输入 wire 名 = 输入名去 _to_logic（excel_model.strip_to_logic 已做）。
  3. 地址+bit：去 _to_logic → total_memory_map 按 Field Name 找 → 地址=F, bit=B。
  4. 同地址多 RW 字段合并一条 RF_WRITE（在 sv_writer 里完成）。

⭐ _to_logic 网的物理含义（2026-06-02 d2a_cnt_sclk + d_ndiv_n 仿真实证）：
  输入列原文 X_to_logic 的驱动者取决于 **X 自己的 logic 行是否自引用**：
  ① X 的行读自己的 X_to_logic（自引用，如 d2a_cnt_sclk / d_ndiv_n）
       → X_to_logic 是 regfile 导出的前级信号：端口/寄存器 X → regfile → X_to_logic → sig_logic
       → 驱动方式 = force 基名 X（RO，透传）或 RF_WRITE（RW）。
  ② X 的行不读自己的 X_to_logic（如 d_wl_wur_bt_pll_mode_sel）
       → RTL 是 assign X_to_logic = <X 的表达式>；基名 X 和 X_ls 只是它的下游拷贝
       → force 基名/X_ls 都钉不住源头 → 必须 force 字面 X_to_logic 网（需探针前缀）。
  任何情况下都绝不能 force 被验证行自己的输出网（X_ls）——那等于把被断言的输出钉死。

因拿不到真表，名称匹配用多策略，解析不到不静默猜——标记 UNRESOLVED，由 CLI 汇总报告；
并支持手动覆盖：force_overrides / rfwrite_overrides（基名集合）。
"""

import re

# 输入单元格原文的位宽尾巴，如 d2a_cnt_sclk_to_logic[3:0] → [3:0]
_WIDTH_TAIL = re.compile(r"\[[^\]]*\]\s*$")


def _raw_has_to_logic(raw):
    """输入单元格原文(去位宽)是否带 _to_logic 后缀。

    带 → 该网名是 RTL 里真实存在的 *_to_logic 网（由 regfile 导出 或 上游 logic 表达式驱动，
         按上游行是否自引用区分，见模块头注释）；
    不带 → 原文就是要 force 的网名（引用另一 logic 输出时用其 RTL 网名，ls 行带 _ls）。
    """
    name = _WIDTH_TAIL.sub("", str(raw or "")).strip()
    return name.lower().endswith("_to_logic")


def _raw_has_to_mux(raw):
    """输入单元格原文(去位宽)是否带 _to_mux 后缀（mux 页 A/B~E 列、或 logic 页引用 mux 衔接网）。

    带 _to_mux 且基名是别的 logic/mux 输出 → 该网由上游表达式/上游 mux 驱动，
    与基名/输出网是平行拷贝——force 基名钉不住它，必须 force 字面 _to_mux 网（需探针前缀）。
    （与 _to_logic 的 d_ndiv_n 教训同理，2026-06-03 WL_RFTRX 实证。）
    """
    name = _WIDTH_TAIL.sub("", str(raw or "")).strip()
    return name.lower().endswith("_to_mux")


class InputBinding:
    def __init__(self, letter, raw, base, width, kind, address, reg_lsb, reg_msb,
                 wire, found_in, reg_name="", note="", slice_msb=None, slice_lsb=None):
        self.letter = letter
        self.raw = raw                 # logic 单元格原文
        self.base = base               # 去 _to_logic 的基名（= wire 名 / 查表 key）
        self.width = width             # 求值用位宽（来自 logic 位宽标注，权威）
        self.kind = kind               # 'RO' | 'RW' | 'UNKNOWN'
        self.address = address         # int | None（RW 必需）
        self.reg_lsb = reg_lsb         # 寄存器内 lsb（RW 移位用）
        self.reg_msb = reg_msb
        self.wire = wire               # force 目标（RO）
        self.found_in = found_in       # 'tmm' | 'regmap' | None
        self.reg_name = reg_name
        self.note = note               # 诊断信息
        self.slice_msb = slice_msb     # 输入自身位宽切片(来自 logic 单元格 [msb:lsb])，force LHS 用
        self.slice_lsb = slice_lsb

    @property
    def wire_lhs(self):
        """force 目标 LHS：多位带位宽切片(如 d_x[2:0])，标量不带。"""
        if self.width <= 1:
            return self.wire
        if self.slice_msb is not None and self.slice_lsb is not None:
            return "%s[%d:%d]" % (self.wire, self.slice_msb, self.slice_lsb)
        return "%s[%d:0]" % (self.wire, self.width - 1)

    @property
    def resolved(self):
        if self.kind == "UNKNOWN":
            return False
        if self.kind == "RW" and self.address is None:
            return False
        return True

    def __repr__(self):
        return "InputBinding(%s=%s, kind=%s, addr=%s, lsb=%s, src=%s)" % (
            self.letter, self.base, self.kind, self.address, self.reg_lsb, self.found_in)


class Resolver:
    def __init__(self, wb, force_overrides=None, rfwrite_overrides=None,
                 default_kind=None, wire_fallback=True, wire_prefixes=None,
                 cascade_mode="cone"):
        """
        wb: DregWorkbook
        force_overrides / rfwrite_overrides: 基名集合(小写比较)，强制 RO / RW。
        default_kind: 当类型完全判不出时的兜底 ('RO'/'RW'/None)。None=保持 UNKNOWN。
        wire_fallback: True(默认) → 凡不是干净 RW 寄存器的输入都按 wire 处理(force 信号名)，
                       与旧 for_test 行为一致（输入是 wire 就 force，是寄存器才 RF_WRITE）。
                       False → 查不到就标 UNKNOWN 交人工。
        wire_prefixes: {信号名(小写): 层级前缀} —— 该 wire 不在 ENV_RF 顶层而在子模块里时，
                       force 路径写 ENV_RF.<前缀>.<名>（如 mon_active 在 U_BT_LP_PLL_DIG 内部）。
                       命中映射的 wire 视为"用户确认存在"，不再算 wire 兜底风险。
        cascade_mode: 输入引用"上游计算网"(级联到不自引用的 top 输出，如 d_ndiv_n 的
                       d_wl_wur_bt_pll_mode_sel_to_logic)时怎么驱动——详见 级联模式说明.md：
            "cone"(默认) → 展开上游表达式，驱动它的源头寄存器/管脚（纯 Excel，不需要探针前缀）
            "force"      → 直接 force 字面 _to_logic 网（每行 logic 隔离验证；需要 scan_rtl 前缀）
        """
        self.wb = wb
        self.force_overrides = {s.lower() for s in (force_overrides or [])}
        self.rfwrite_overrides = {s.lower() for s in (rfwrite_overrides or [])}
        self.default_kind = default_kind
        self.wire_fallback = wire_fallback
        self.wire_prefixes = {k.lower(): v for k, v in (wire_prefixes or {}).items()}
        self.cascade_mode = cascade_mode if cascade_mode in ("cone", "force") else "cone"
        # 预建小写索引，便于不区分大小写匹配
        self._tmm_lower = {k.lower(): v for k, v in wb.tmm.items()}
        self._regmap_lower = {k.lower(): v for k, v in wb.regmap.items()}
        # logic 输出名(去位宽,小写) → (位宽, 是否 top_output, RTL 网名, 是否自引用)
        # 识别"输入其实是另一个 logic 输出"(级联/自引用)。
        #
        # ⭐ 第 4 元素 self_ref（2026-06-02 d_ndiv_n 仿真实证）—— 决定该输出的 X_to_logic 网由谁驱动：
        #   该行读自己的 X_to_logic（自引用，如 d2a_cnt_sclk）
        #     → X_to_logic 是 regfile 导出的前级信号（端口 X → regfile → X_to_logic → logic）
        #     → 下游引用它时 force 基名 X 即可（透传）。
        #   该行不读自己的 X_to_logic（如 d_wl_wur_bt_pll_mode_sel）
        #     → RTL 是 assign X_to_logic = <表达式>；X / X_ls 只是它的下游拷贝
        #     → 下游引用它时 force 基名/X_ls 都没用（钉死拷贝不影响源头），必须 force 字面 X_to_logic 网。
        self._logic_outputs = {}
        for s in wb.logic:
            is_top = str(s.top_output).strip() in ("1", "1.0", "True", "true")
            self_ref = any(str(info.get("base", "")).lower() == s.out_base.lower()
                           for info in s.inputs.values())
            self._logic_outputs.setdefault(s.out_base.lower(),
                                           (s.out_width, is_top, s.rtl_base, self_ref))
        # mux 输出名(去位宽,小写) → (位宽, is_top, rtl_base, 组号)
        # 识别"输入其实是另一个 mux 组的输出"——WL_RFTRX 实证（2026-06-03 第十四轮）：
        #   mux→logic 级联（mux 输出喂 logic 输入，H 列=to_logic）
        #   mux→mux 级联（mux 输出是另一组的控制/数据，H 列=to_mux）
        # LPBT 无此形态（mux 输出全是顶层终点），该索引对 LPBT 永不命中。
        self._mux_outputs = {}
        for g in (getattr(wb, "mux", None) or []):
            self._mux_outputs.setdefault(g.out_base.lower(),
                                         (g.out_width, g.is_top, g.rtl_base, g.group_no))

    # ───────────── 名称匹配（多策略，歧义不静默猜） ─────────────
    def _match(self, base, table_lower, tag):
        """返回 (entry, ambiguity_note)。精确命中→直接返回；后缀匹配命中多个→判歧义。"""
        low = base.lower()
        if low in table_lower:
            return table_lower[low], None
        # 后缀匹配：字段名是基名的后缀，或基名是字段名的后缀
        cands = [(k, v) for k, v in table_lower.items()
                 if low.endswith("_" + k) or k.endswith("_" + low)]
        if len(cands) == 1:
            return cands[0][1], None
        if len(cands) > 1:
            return None, ("字段 %r 在 %s 后缀模糊匹配到多个候选 %s，已拒绝静默选取，请人工指定"
                          % (base, tag, [k for k, _ in cands]))
        return None, None

    def _match_tmm(self, base):
        return self._match(base, self._tmm_lower, "total_memory_map")

    def _match_regmap(self, base):
        return self._match(base, self._regmap_lower, "regmap")

    # ───────────── 主解析 ─────────────
    def resolve(self, letter, info, self_base=None):
        """解析一个输入。self_base = 当前被验证信号自己的输出基名(小写)，
        用于识别自引用输入（K=X 且输入=X_to_logic，如 d2a_cnt_sclk）。"""
        base = info["base"]
        width = info["width"]
        raw = info["raw"]
        low = base.lower()

        tmm, tmm_amb = self._match_tmm(base)
        rm, rm_amb = self._match_regmap(base)
        amb_note = tmm_amb or rm_amb

        # 地址 / 位段：优先 tmm（hex 地址），regmap H 列(十进制)兜底
        address = tmm.address if tmm else (rm.address if rm else None)
        reg_lsb = tmm.bit_lsb if tmm else (rm.bit_lsb if rm else None)
        reg_msb = tmm.bit_msb if tmm else (rm.bit_msb if rm else None)
        reg_name = (tmm.reg_name if tmm and tmm.reg_name else
                    (rm.reg_name if rm else ""))
        found_in = "tmm" if tmm else ("regmap" if rm else None)

        # 类型判定：覆盖 > tmm.H > regmap.F > DIG TOP PIN > default
        kind = self._decide_kind(low, tmm, rm)
        overridden = low in self.force_overrides or low in self.rfwrite_overrides
        if amb_note and not overridden:
            kind = "UNKNOWN"        # 歧义且用户未显式指定 → 不信任任何猜测，强制交人工

        note = ""
        if amb_note:
            note = amb_note
        elif found_in is None:
            note = "未在 tmm/regmap 找到字段 %r（名称匹配失败，需人工核对）" % base
        elif kind == "UNKNOWN":
            note = "字段 %r 找到但 RO/RW 类型判不出（可用 --force-signals/--rfwrite-signals 指定）" % base
        elif kind == "RW" and address is None:
            note = "字段 %r 判为 RW 但缺地址（tmm 未命中，regmap 无地址）" % base

        # ── 不是干净的 RW 寄存器时，按 for_test 规则把它当 wire → force（按信号名）──
        # 但"歧义匹配"不走兜底：它很可能是某个寄存器，应交人工，而非当 wire 强行 force。
        clean_rw = (kind == "RW" and address is not None)
        wire_name = base
        if not clean_rw and not overridden and not amb_note:
            chained = self._logic_outputs.get(low)
            # 自引用：输入基名 = 本行输出基名（如 K=d2a_cnt_sclk, A=d2a_cnt_sclk_to_logic）
            is_self = self_base is not None and low == self_base
            # 原文带 _to_logic → 该网是 regfile 导出的前级信号，不是任何 logic 输出网
            from_regfile = _raw_has_to_logic(raw)
            if chained is not None:
                chained_w, chained_top, chained_rtl, chained_self_ref = chained
                # 位宽推断：仅当输入单元格没写显式切片时，才用上游/本行输出的全宽。
                # 显式切片(如 d_x_to_logic[1:0])的求值位宽就是切片自身宽度，不能被输出全宽覆盖——
                # 否则枚举值越界(force 2bit 切片却生成 4bit 值)，断言期望与实际驱动不一致。
                if info.get("msb") is None and info.get("lsb") is None:
                    width = max(width, chained_w)
                if is_self and chained_top:
                    # ⭐ 自引用(2026-06-02 真实 bug d2a_cnt_sclk)：输入是本行输出的前级原始信号
                    # （RTL: 顶层端口 X → regfile → X_to_logic → sig_logic → X_ls）。
                    # force 前级信号名 X；绝不能 force 本行输出网 X_ls——那是把被验证的输出钉死。
                    kind = "RO"
                    if found_in is None:
                        found_in = "self-input"
                    wire_name = base
                    note = ("自引用：输入是本行输出 %r 的前级原始信号(RTL 端口 %s → regfile → "
                            "%s_to_logic)，force 前级信号名 %r；不能 force 输出网 %r"
                            % (base, base, base, base, chained_rtl))
                elif not chained_top:
                    # 输入是内部信号(top_output=0)：RTL/ENV_RF 层探不到，force 会层级查找失败。
                    # 注意内部信号"自引用"(is_self 且 top_output=0)也落在这里：它没有顶层端口可
                    # force，按内部信号交 cone 展开 → cone 会报"循环引用"错误(组合环，表本身有问题)。
                    kind = "UNKNOWN"
                    found_in = "logic-internal"
                    note = ("⚠ 输入 %r 是内部信号(top_output=0)，RTL/ENV_RF 层探不到，无法 force；"
                            "该输出需改为驱动其底层寄存器(cone 展开)或一并排除" % base)
                    if is_self:
                        note = ("⚠ 内部信号(top_output=0) %r 自引用——组合环，表本身可能有问题；"
                                "且内部信号没有顶层端口可 force，无法验证" % base)
                elif from_regfile and chained_self_ref:
                    # 级联、原文带 _to_logic、且上游 X 自己也是自引用行（如 d2a_cnt_sclk）：
                    # X_to_logic 是 regfile 导出的前级信号（= 顶层信号 X 的透传），
                    # 不是上游 logic 输出网 X_ls → force 前级信号名
                    kind = "RO"
                    if found_in is None:
                        found_in = "logic"
                    wire_name = base
                    note = ("级联(前级)：输入原文 %s 是 regfile 导出的前级信号(顶层信号 %r 的透传，"
                            "%dbit)，force 前级信号名；它与上游 logic 输出网 %r 是两根不同的 wire"
                            % (raw, base, width, chained_rtl))
                elif from_regfile:
                    # ⭐ 级联、原文带 _to_logic、但上游 X 不自引用（2026-06-02 d_ndiv_n 仿真实证：
                    # 输入 A=d_wl_wur_bt_pll_mode_sel_to_logic）：
                    # RTL 是 assign X_to_logic = <上游表达式>；基名 X / X_ls 只是它的下游拷贝。
                    # force 基名/X_ls 都钉不住 X_to_logic（assign 方向相反）。两种驱动模式：
                    net = _WIDTH_TAIL.sub("", str(raw)).strip()
                    if self.cascade_mode == "cone":
                        # 模式 B"展开上游"(默认)：标记为可展开 → cone 把上游表达式代入，
                        # 改为驱动上游的源头寄存器/管脚。纯 Excel，不需要探针前缀。
                        kind = "UNKNOWN"
                        found_in = "logic-computed"
                        note = ("级联(上游计算网)：%s 由上游 logic 行 %r 的表达式驱动；"
                                "已按『展开上游』模式处理——把上游表达式代入，驱动其源头寄存器"
                                "（切换到『force级联网』模式可改为直接 force 该网，见 级联模式说明.md）"
                                % (net, base))
                    else:
                        # 模式 A"force级联网"：直接 force 字面 _to_logic 网（每行 logic 隔离验证）。
                        # 该网在 sig_logic 模块内部 → 需要探针前缀(scan_rtl)；没配前缀时标 needs-prefix
                        # （build 默认跳过并给原因，保证产物能 elaborate）。
                        kind = "RO"
                        wire_name = net
                        found_in = "needs-prefix"
                        note = ("级联(上游计算网)：输入 %s 由上游 logic 行 %r 的表达式驱动"
                                "(RTL: assign %s = <表达式>)，基名/%s 只是它的下游拷贝、force 不生效；"
                                "必须 force 字面网 %s（在 sig_logic 模块内，需探针前缀——跑 scan_rtl 获取）"
                                % (net, base, net, chained_rtl, net))
                elif _raw_has_to_mux(raw):
                    # ⭐ 级联且原文带 _to_mux（WL：mux 页输入引用 logic 行输出，如数据/控制 = logic to_mux 行）：
                    # RTL 是 assign X_to_mux = <上游 logic 表达式>；基名 X / X_ls 只是平行拷贝，
                    # force 它们钉不住 _to_mux 网（与 d_ndiv_n 的 _to_logic 教训同理）。
                    # → force 字面 _to_mux 网（在导出模块内部，需 scan_rtl 探针前缀）。
                    net = _WIDTH_TAIL.sub("", str(raw)).strip()
                    kind = "RO"
                    wire_name = net
                    found_in = "needs-prefix"
                    note = ("级联(logic→mux 衔接网)：%s 由上游 logic 行 %r 的表达式驱动；"
                            "force 基名钉不住它，必须 force 字面 %s 网（需探针前缀——跑 scan_rtl 获取）"
                            % (net, base, net))
                else:
                    # 级联且原文不带 _to_logic（Excel 直接写上游输出名）→ force 其 RTL 网名(ls 行带 _ls)
                    kind = "RO"
                    found_in = "logic"
                    wire_name = chained_rtl
                    note = "级联：输入是另一个 top_output 输出 %r（%dbit），按中间 wire force" % (base, width)
            elif low in self._mux_outputs and found_in is None and kind != "RW":
                # ⭐ 输入是另一个 mux 组的输出（WL mux 级联 / mux→logic 级联，2026-06-03 实证）：
                # 该网由上游 mux 选路驱动。force 衔接网（原文 _to_mux/_to_logic 网，没有后缀就用基名）
                # 可以钉住它（force 优先级高于 assign）→ 隔离验证下游；需 scan_rtl 探针前缀。
                # 注意 expand_mux_group 的『展开上游 mux』(cone 思路) 不走这里——它直接查 wb.mux。
                mux_w, _mux_top, _mux_rtl, mux_no = self._mux_outputs[low]
                if info.get("msb") is None and info.get("lsb") is None:
                    width = max(width, mux_w)
                net = _WIDTH_TAIL.sub("", str(raw)).strip()
                kind = "RO"
                wire_name = net if net else base
                found_in = "mux-output"
                note = ("级联(mux 输出)：输入 %s 是 mux 组 %s 的输出 %r——由上游 mux 选路驱动；"
                        "force 该衔接网可钉住它（mux 输出不在顶层，需 scan_rtl 探针前缀）"
                        % (raw, mux_no, base))
            elif found_in is None and self.wire_fallback and kind != "RW":
                # tmm/regmap 都查不到 → 视作普通 wire（顶层管脚/中间信号）→ force 信号名
                kind = "RO"
                found_in = "wire"
                note = "表中查无字段，按 wire 处理(force 信号名)——若它其实是寄存器请用 --rfwrite-signals 指定"

        # ── 探针前缀：该 wire 在 ENV_RF 的子模块里(用户显式指定) → force 路径加前缀 ──
        # 例: mon_active 是 U_BT_LP_PLL_DIG 内部 wire → force `ENV_RF.U_BT_LP_PLL_DIG.mon_active
        # 映射 key 兼容输入基名(low)与实际 force 网名(级联 _ls / 上游计算网 _to_logic)——scan_rtl 导出用后者
        prefix = self.wire_prefixes.get(low) or self.wire_prefixes.get(wire_name.lower())
        if prefix and kind != "RW":
            wire_name = "%s.%s" % (prefix.strip("."), wire_name)
            if found_in in ("wire", "needs-prefix", "mux-output", None):
                found_in = "prefixed-wire"   # 用户/scan_rtl 确认该网存在 → 不再算风险，可生成
            note = (note + "；" if note else "") + "探针前缀: 在 ENV_RF.%s 下" % prefix

        return InputBinding(
            letter=letter, raw=raw, base=base, width=width, kind=kind,
            address=address, reg_lsb=reg_lsb, reg_msb=reg_msb,
            wire=wire_name, found_in=found_in, reg_name=reg_name, note=note,
            slice_msb=info.get("msb"), slice_lsb=info.get("lsb"),
        )

    def _decide_kind(self, low, tmm, rm):
        if low in self.force_overrides:
            return "RO"
        if low in self.rfwrite_overrides:
            return "RW"
        if tmm and tmm.reg_type in ("RO", "RW"):
            return tmm.reg_type
        if rm and rm.reg_type:
            rt = rm.reg_type.upper()
            if "RW" in rt:
                return "RW"
            if rt in ("RO", "R"):
                return "RO"
        # DIG TOP PIN = Y → 顶层管脚 → 外部驱动 → 软件视角只读 → force
        if tmm and tmm.dig_top_pin == "Y":
            return "RO"
        if tmm and tmm.dig_top_pin == "N":
            return "RW"
        return self.default_kind or "UNKNOWN"

    def resolve_signal_inputs(self, sig):
        """对一个 LogicSignal 解析它表达式实际引用到的输入。返回 dict 字母->InputBinding。

        传 self_base 让 resolve() 能识别自引用输入（输入=本行输出的前级原始信号，
        如 d2a_cnt_sclk：K=X 且 A=X_to_logic）。"""
        bindings = {}
        self_base = sig.out_base.lower()
        for letter, info in sig.inputs.items():
            bindings[letter] = self.resolve(letter, info, self_base=self_base)
        return bindings
