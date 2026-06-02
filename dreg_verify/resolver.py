# -*- coding: utf-8 -*-
"""
resolver.py — 把 logic 输入信号解析为驱动方式（force / RF_WRITE）、地址、寄存器内位段。

规则（来自 generation-rules 记忆，已用 reserve/lna_agc 对照 .sv）：
  1. force vs RF_WRITE = 字段类型 RO vs RW：RO→force wire；RW→RF_WRITE(addr,data)。
  2. 输入 wire 名 = 输入名去 _to_logic（excel_model.strip_to_logic 已做）。
  3. 地址+bit：去 _to_logic → total_memory_map 按 Field Name 找 → 地址=F, bit=B。
  4. 同地址多 RW 字段合并一条 RF_WRITE（在 sv_writer 里完成）。

因拿不到真表，名称匹配用多策略，解析不到不静默猜——标记 UNRESOLVED，由 CLI 汇总报告；
并支持手动覆盖：force_overrides / rfwrite_overrides（基名集合）。
"""


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
                 default_kind=None, wire_fallback=True, wire_prefixes=None):
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
        """
        self.wb = wb
        self.force_overrides = {s.lower() for s in (force_overrides or [])}
        self.rfwrite_overrides = {s.lower() for s in (rfwrite_overrides or [])}
        self.default_kind = default_kind
        self.wire_fallback = wire_fallback
        self.wire_prefixes = {k.lower(): v for k, v in (wire_prefixes or {}).items()}
        # 预建小写索引，便于不区分大小写匹配
        self._tmm_lower = {k.lower(): v for k, v in wb.tmm.items()}
        self._regmap_lower = {k.lower(): v for k, v in wb.regmap.items()}
        # logic 输出名(去位宽,小写) → (位宽, 是否 top_output, RTL 网名)：识别"输入其实是另一个 logic 输出"(级联)
        # RTL 网名：ls 行的真实端口名带 _ls 后缀（见 excel_model.rtl_net_name），force 时必须用它
        self._logic_outputs = {}
        for s in wb.logic:
            is_top = str(s.top_output).strip() in ("1", "1.0", "True", "true")
            self._logic_outputs.setdefault(s.out_base.lower(),
                                           (s.out_width, is_top, s.rtl_base))

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
    def resolve(self, letter, info):
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
            if chained is not None:
                chained_w, chained_top, chained_rtl = chained
                width = max(width, chained_w)
                if chained_top:
                    # 输入是另一个 top_output logic 输出（可见 wire）→ 直接 force RTL 网名(ls 行带 _ls)
                    kind = "RO"
                    found_in = "logic"
                    wire_name = chained_rtl
                    note = "级联：输入是另一个 top_output 输出 %r（%dbit），按中间 wire force" % (base, width)
                else:
                    # 输入是内部信号(top_output=0)：RTL/ENV_RF 层探不到，force 会层级查找失败
                    kind = "UNKNOWN"
                    found_in = "logic-internal"
                    note = ("⚠ 输入 %r 是内部信号(top_output=0)，RTL/ENV_RF 层探不到，无法 force；"
                            "该输出需改为驱动其底层寄存器(cone 展开)或一并排除" % base)
            elif found_in is None and self.wire_fallback and kind != "RW":
                # tmm/regmap 都查不到 → 视作普通 wire（顶层管脚/中间信号）→ force 信号名
                kind = "RO"
                found_in = "wire"
                note = "表中查无字段，按 wire 处理(force 信号名)——若它其实是寄存器请用 --rfwrite-signals 指定"

        # ── 探针前缀：该 wire 在 ENV_RF 的子模块里(用户显式指定) → force 路径加前缀 ──
        # 例: mon_active 是 U_BT_LP_PLL_DIG 内部 wire → force `ENV_RF.U_BT_LP_PLL_DIG.mon_active
        # 映射 key 兼容输入基名(low)与 RTL 网名(级联 ls 输出会带 _ls 后缀)——scan_rtl 导出用后者
        prefix = self.wire_prefixes.get(low) or self.wire_prefixes.get(wire_name.lower())
        if prefix and kind != "RW":
            wire_name = "%s.%s" % (prefix.strip("."), wire_name)
            if found_in in ("wire", None):
                found_in = "prefixed-wire"   # 用户确认该网存在 → 不再算 wire 兜底风险
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
        """对一个 LogicSignal 解析它表达式实际引用到的输入。返回 dict 字母->InputBinding。"""
        bindings = {}
        for letter, info in sig.inputs.items():
            bindings[letter] = self.resolve(letter, info)
        return bindings
