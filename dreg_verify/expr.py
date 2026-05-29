# -*- coding: utf-8 -*-
"""
expr.py — Dreg logic 真值表达式的解析与求值。

支持 Verilog 表达式子集（logic 页 L 列实际出现的语法 + 防御性扩展）：
  - 变量：单字母 A..J（对应 logic 行的 A..J 列输入信号），可带位选 A[3] / 部分选 A[3:1]
  - 基数字面量：N'bxxx / N'hxx / N'dxx / N'oxx，以及无尺寸 'bxxx；纯十进制整数
  - 三元 ?:（右结合，可嵌套）
  - 拼接 {a,b,...} 与重复 {n{a}}
  - 按位 ~ & | ^ ^~ ~^，归约 &a |a ^a ~&a ~|a ~^a
  - 逻辑 ! && ||，比较 == != < <= > >=，移位 << >>，算术 + - *
  - 括号 ()

⭐ 关键：实现 Verilog 的「两遍位宽」语义——
  Pass1 自决宽度(self-determined)，Pass2 把上下文宽度(含赋值左侧/输出位宽)向下传播到
  「上下文决定」的算子操作数(~ & | ^ ?: + - * 移位左操作数)；而拼接/重复/比较/归约/逻辑
  是「自决」的，不吃上下文宽度。这样 `out[2:0] = ~A`（A 1bit）才会得到 3'b11x 而非 3'b00x。

求值结果为无符号 2-state 整数（测试向量给的是具体 0/1，不含 x/z）。
表达式整体在 上下文宽度 = max(自决宽度, 输出位宽) 下求值，最后再截/扩到 输出位宽 用于格式化期望值。
"""

import re


# ───────────────────────────── 异常 ─────────────────────────────
class ExprError(Exception):
    """表达式解析或求值出错（语法不支持 / 变量未绑定 / 宽度未知等）。"""


# ───────────────────────────── 词法 ─────────────────────────────
# 注意：多字符算子要排在单字符前面，避免被拆开。
_TOKEN_SPEC = [
    ("WS", r"[ \t\r\n]+"),
    # 基数字面量：可选尺寸 + ' + base + value（含 x z ? 与下划线）
    ("BASED", r"(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ\?_]+"),
    ("DEC", r"\d+"),
    ("ID", r"[A-Za-z_][A-Za-z0-9_$]*"),
    ("SHL", r"<<"),
    ("SHR", r">>"),
    ("LE", r"<="),
    ("GE", r">="),
    ("EQ", r"=="),
    ("NE", r"!="),
    ("LAND", r"&&"),
    ("LOR", r"\|\|"),
    ("NAND", r"~&"),
    ("NOR", r"~\|"),
    ("XNOR", r"~\^|\^~"),
    ("LT", r"<"),
    ("GT", r">"),
    ("LBRACE", r"\{"),
    ("RBRACE", r"\}"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("LBRACK", r"\["),
    ("RBRACK", r"\]"),
    ("QUESTION", r"\?"),
    ("COLON", r":"),
    ("COMMA", r","),
    ("TILDE", r"~"),
    ("AND", r"&"),
    ("OR", r"\|"),
    ("XOR", r"\^"),
    ("NOT", r"!"),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("STAR", r"\*"),
    ("SLASH", r"/"),
]
_TOKEN_RE = re.compile("|".join("(?P<%s>%s)" % (n, p) for n, p in _TOKEN_SPEC))


class Token:
    __slots__ = ("kind", "text", "pos")

    def __init__(self, kind, text, pos):
        self.kind = kind
        self.text = text
        self.pos = pos

    def __repr__(self):
        return "Token(%s,%r)" % (self.kind, self.text)


def tokenize(s):
    toks = []
    i = 0
    n = len(s)
    while i < n:
        m = _TOKEN_RE.match(s, i)
        if not m:
            raise ExprError("无法识别的字符 %r @%d (表达式: %r)" % (s[i], i, s))
        i = m.end()
        kind = m.lastgroup
        if kind == "WS":
            continue
        toks.append(Token(kind, m.group(), m.start()))
    toks.append(Token("EOF", "", n))
    return toks


# ───────────────────────────── 字面量解析 ─────────────────────────────
def parse_based_literal(text):
    """返回 (value:int, width:int)。无尺寸默认宽度 32。x/z 视为 0（测试向量不含 x/z，仅常量里偶见）。"""
    m = re.match(r"(?P<size>\d+)?'(?P<sign>[sS]?)(?P<base>[bBoOdDhH])(?P<val>.+)", text)
    if not m:
        raise ExprError("非法基数字面量: %r" % text)
    size = m.group("size")
    if m.group("sign"):
        # 求值器为无符号语义；遇到带符号字面量显式拒绝，避免"无声"按无符号求出错误期望值
        raise ExprError("暂不支持有符号字面量 %r（求值器为无符号语义，如确需请扩展 expr.py）" % text)
    base = m.group("base").lower()
    raw = m.group("val").replace("_", "")
    radix = {"b": 2, "o": 8, "d": 10, "h": 16}[base]
    # x/z/? 当作 0（仅可能出现在常量；本工具向量是具体值）
    cleaned = re.sub(r"[xXzZ\?]", "0", raw)
    if cleaned == "":
        cleaned = "0"
    try:
        value = int(cleaned, radix)
    except ValueError:
        raise ExprError("无法解析字面量值: %r" % text)
    if size is not None:
        width = int(size)
    else:
        # 无尺寸：Verilog 至少 32 位
        width = max(32, value.bit_length(), 1)
    return value & mask(width), width


def mask(width):
    return (1 << width) - 1 if width > 0 else 0


# ───────────────────────────── AST ─────────────────────────────
class Node:
    pass


class Const(Node):
    def __init__(self, value, width):
        self.value = value
        self.width = width


class Var(Node):
    def __init__(self, name, msb=None, lsb=None):
        self.name = name          # 变量字母（大写规范化）
        self.msb = msb            # 位选/部分选（可选）
        self.lsb = lsb


class Unary(Node):
    def __init__(self, op, operand):
        self.op = op              # '~','!','-','+','&','|','^','~&','~|','~^'
        self.operand = operand


class Binary(Node):
    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right


class Ternary(Node):
    def __init__(self, cond, then, els):
        self.cond = cond
        self.then = then
        self.els = els


class Concat(Node):
    def __init__(self, parts):
        self.parts = parts        # list[Node]


class Repeat(Node):
    def __init__(self, count_node, body):
        self.count_node = count_node
        self.body = body


# ───────────────────────────── 解析器（带优先级的递归下降） ─────────────────────────────
# 二元算子优先级（数字大 = 结合更紧）。参考 Verilog 优先级。
_BINOP_PREC = {
    "STAR": 10, "SLASH": 10,
    "PLUS": 9, "MINUS": 9,
    "SHL": 8, "SHR": 8,
    "LT": 7, "LE": 7, "GT": 7, "GE": 7,
    "EQ": 6, "NE": 6,
    "AND": 5,
    "XOR": 4, "XNOR": 4,
    "OR": 3,
    "LAND": 2,
    "LOR": 1,
}
_BINOP_TEXT = {
    "STAR": "*", "SLASH": "/", "PLUS": "+", "MINUS": "-",
    "SHL": "<<", "SHR": ">>", "LT": "<", "LE": "<=", "GT": ">", "GE": ">=",
    "EQ": "==", "NE": "!=", "AND": "&", "XOR": "^", "XNOR": "~^",
    "OR": "|", "LAND": "&&", "LOR": "||",
}
# 既可一元（归约/否定）又可二元的算子
_UNARY_PREFIX = {
    "TILDE": "~", "NOT": "!", "MINUS": "-", "PLUS": "+",
    "AND": "&", "OR": "|", "XOR": "^", "NAND": "~&", "NOR": "~|", "XNOR": "~^",
}


class Parser:
    def __init__(self, toks, src):
        self.toks = toks
        self.i = 0
        self.src = src

    def peek(self):
        return self.toks[self.i]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind):
        t = self.peek()
        if t.kind != kind:
            raise ExprError("期望 %s 但得到 %r @%d (表达式: %r)"
                            % (kind, t.text or t.kind, t.pos, self.src))
        return self.next()

    def parse(self):
        node = self.parse_ternary()
        if self.peek().kind != "EOF":
            t = self.peek()
            raise ExprError("表达式尾部多余 token %r @%d (%r)" % (t.text, t.pos, self.src))
        return node

    def parse_ternary(self):
        cond = self.parse_binary(0)
        if self.peek().kind == "QUESTION":
            self.next()
            then = self.parse_ternary()
            self.expect("COLON")
            els = self.parse_ternary()   # 右结合
            return Ternary(cond, then, els)
        return cond

    def parse_binary(self, min_prec):
        left = self.parse_unary()
        while True:
            t = self.peek()
            prec = _BINOP_PREC.get(t.kind)
            if prec is None or prec < min_prec:
                break
            self.next()
            # 左结合：右侧用 prec+1
            right = self.parse_binary(prec + 1)
            left = Binary(_BINOP_TEXT[t.kind], left, right)
        return left

    def parse_unary(self):
        t = self.peek()
        if t.kind in _UNARY_PREFIX:
            self.next()
            operand = self.parse_unary()
            return Unary(_UNARY_PREFIX[t.kind], operand)
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t.kind == "LPAREN":
            self.next()
            node = self.parse_ternary()
            self.expect("RPAREN")
            return node
        if t.kind == "LBRACE":
            return self.parse_concat()
        if t.kind == "BASED":
            self.next()
            v, w = parse_based_literal(t.text)
            return Const(v, w)
        if t.kind == "DEC":
            self.next()
            return Const(int(t.text), 32)
        if t.kind == "ID":
            return self.parse_var()
        raise ExprError("意外的 token %r @%d (%r)" % (t.text or t.kind, t.pos, self.src))

    def parse_var(self):
        t = self.expect("ID")
        name = t.text.upper()
        msb = lsb = None
        if self.peek().kind == "LBRACK":
            self.next()
            msb_tok = self.expect("DEC")
            msb = int(msb_tok.text)
            if self.peek().kind == "COLON":
                self.next()
                lsb_tok = self.expect("DEC")
                lsb = int(lsb_tok.text)
            else:
                lsb = msb
            self.expect("RBRACK")
        return Var(name, msb, lsb)

    def parse_concat(self):
        self.expect("LBRACE")
        first = self.parse_ternary()
        if self.peek().kind == "LBRACE":
            # 重复 {n{body}}
            body = self.parse_concat()
            self.expect("RBRACE")
            return Repeat(first, body)
        parts = [first]
        while self.peek().kind == "COMMA":
            self.next()
            parts.append(self.parse_ternary())
        self.expect("RBRACE")
        return Concat(parts)


def parse(expr_text):
    """把表达式字符串解析为 AST。"""
    if expr_text is None:
        raise ExprError("空表达式")
    s = str(expr_text).strip()
    if s == "":
        raise ExprError("空表达式")
    return Parser(tokenize(s), s).parse()


# ───────────────────────────── 环境（变量 → 宽度/取值） ─────────────────────────────
class Env:
    """变量字母 → (width, value)。width 必填；value 仅求值时需要。"""

    def __init__(self, widths, values=None):
        self.widths = {k.upper(): v for k, v in widths.items()}
        self.values = {k.upper(): v for k, v in (values or {}).items()}

    def width_of(self, name):
        if name not in self.widths:
            raise ExprError("变量 %s 宽度未知（未在 logic 行的 A..J 列绑定）" % name)
        return self.widths[name]

    def value_of(self, name):
        if name not in self.values:
            raise ExprError("变量 %s 取值未提供" % name)
        return self.values[name]


# ───────────────────────────── Pass1：自决宽度 ─────────────────────────────
def self_width(node, env):
    if isinstance(node, Const):
        return node.width
    if isinstance(node, Var):
        if node.msb is not None:
            return abs(node.msb - node.lsb) + 1
        return env.width_of(node.name)
    if isinstance(node, Unary):
        if node.op in ("!",) or node.op in ("&", "|", "^", "~&", "~|", "~^"):
            return 1                      # 逻辑非 / 归约 → 1 位
        return self_width(node.operand, env)   # ~ - + 保持操作数宽度
    if isinstance(node, Binary):
        op = node.op
        if op in ("==", "!=", "<", "<=", ">", ">=", "&&", "||"):
            return 1
        if op in ("<<", ">>"):
            return self_width(node.left, env)
        # & | ^ ~^ + - * /
        return max(self_width(node.left, env), self_width(node.right, env))
    if isinstance(node, Ternary):
        return max(self_width(node.then, env), self_width(node.els, env))
    if isinstance(node, Concat):
        return sum(self_width(p, env) for p in node.parts)
    if isinstance(node, Repeat):
        cnt = eval_const(node.count_node, env)
        return cnt * self_width(node.body, env)
    raise ExprError("未知 AST 节点 %r" % type(node))


def eval_const(node, env):
    """对常量子表达式求值（用于重复次数）。

    用足够宽的上下文(32位)求值，避免按某变量的"声明位宽"截断次数——
    例如 {A{B}} 里 A 声明 1 位但取值 2 时，不应被 mask(1) 截成 0 次重复。
    （Verilog 规定重复次数必须是常量，这里宽容支持变量取值。）
    """
    return evaluate_node(node, 32, env)


# ───────────────────────────── Pass2：在上下文宽度下求值 ─────────────────────────────
def evaluate_node(node, ctx_width, env):
    """在上下文宽度 ctx_width 下求值，返回截/扩到 ctx_width 的无符号整数。"""
    if isinstance(node, Const):
        return node.value & mask(ctx_width)

    if isinstance(node, Var):
        raw = env.value_of(node.name)
        if node.msb is not None:
            lo, hi = min(node.msb, node.lsb), max(node.msb, node.lsb)
            raw = (raw >> lo) & mask(hi - lo + 1)
        return raw & mask(ctx_width)

    if isinstance(node, Unary):
        op = node.op
        if op == "~":
            v = evaluate_node(node.operand, ctx_width, env)     # 上下文决定 → 关键陷阱
            return (~v) & mask(ctx_width)
        if op == "-":
            v = evaluate_node(node.operand, ctx_width, env)
            return (-v) & mask(ctx_width)
        if op == "+":
            return evaluate_node(node.operand, ctx_width, env)
        if op == "!":
            sw = self_width(node.operand, env)
            v = evaluate_node(node.operand, sw, env)
            return (1 if v == 0 else 0) & mask(ctx_width)
        # 归约：操作数按自身自决宽度求值
        sw = self_width(node.operand, env)
        v = evaluate_node(node.operand, sw, env)
        bits = [(v >> i) & 1 for i in range(sw)]
        if op == "&":
            r = 1 if all(bits) else 0
        elif op == "|":
            r = 1 if any(bits) else 0
        elif op == "^":
            r = 0
            for b in bits:
                r ^= b
        elif op == "~&":
            r = 0 if all(bits) else 1
        elif op == "~|":
            r = 0 if any(bits) else 1
        elif op == "~^":
            x = 0
            for b in bits:
                x ^= b
            r = 1 - x
        else:
            raise ExprError("未知一元算子 %r" % op)
        return r & mask(ctx_width)

    if isinstance(node, Binary):
        op = node.op
        if op in ("&", "|", "^", "~^"):
            # 上下文决定：两操作数都按 ctx_width 求值
            a = evaluate_node(node.left, ctx_width, env)
            b = evaluate_node(node.right, ctx_width, env)
            if op == "&":
                return (a & b) & mask(ctx_width)
            if op == "|":
                return (a | b) & mask(ctx_width)
            if op == "^":
                return (a ^ b) & mask(ctx_width)
            return (~(a ^ b)) & mask(ctx_width)   # ~^ xnor
        if op in ("+", "-", "*", "/"):
            a = evaluate_node(node.left, ctx_width, env)
            b = evaluate_node(node.right, ctx_width, env)
            if op == "+":
                r = a + b
            elif op == "-":
                r = a - b
            elif op == "*":
                r = a * b
            else:  # "/" Verilog 整数无符号除法；除以 0 在 2-state 下取 0
                r = (a // b) if b != 0 else 0
            return r & mask(ctx_width)
        if op in ("<<", ">>"):
            a = evaluate_node(node.left, ctx_width, env)
            sw_r = self_width(node.right, env)
            sh = evaluate_node(node.right, sw_r, env)
            r = (a << sh) if op == "<<" else (a >> sh)
            return r & mask(ctx_width)
        # 比较 / 逻辑：操作数自决（取两边自决宽度的较大者）
        if op in ("==", "!=", "<", "<=", ">", ">=", "&&", "||"):
            sw = max(self_width(node.left, env), self_width(node.right, env))
            a = evaluate_node(node.left, sw, env)
            b = evaluate_node(node.right, sw, env)
            if op == "==":
                r = 1 if a == b else 0
            elif op == "!=":
                r = 1 if a != b else 0
            elif op == "<":
                r = 1 if a < b else 0
            elif op == "<=":
                r = 1 if a <= b else 0
            elif op == ">":
                r = 1 if a > b else 0
            elif op == ">=":
                r = 1 if a >= b else 0
            elif op == "&&":
                r = 1 if (a != 0 and b != 0) else 0
            else:
                r = 1 if (a != 0 or b != 0) else 0
            return r & mask(ctx_width)
        raise ExprError("未知二元算子 %r" % op)

    if isinstance(node, Ternary):
        sw_c = self_width(node.cond, env)
        c = evaluate_node(node.cond, sw_c, env)
        branch = node.then if c != 0 else node.els
        return evaluate_node(branch, ctx_width, env)   # 分支吃上下文宽度

    if isinstance(node, Concat):
        # 拼接：各段按自决宽度，从左(高位)到右(低位)
        result = 0
        total = 0
        for p in node.parts:
            w = self_width(p, env)
            v = evaluate_node(p, w, env)
            result = (result << w) | (v & mask(w))
            total += w
        return result & mask(ctx_width)

    if isinstance(node, Repeat):
        cnt = eval_const(node.count_node, env)
        w = self_width(node.body, env)
        v = evaluate_node(node.body, w, env) & mask(w)
        result = 0
        for _ in range(cnt):
            result = (result << w) | v
        return result & mask(ctx_width)

    raise ExprError("未知 AST 节点 %r" % type(node))


# ───────────────────────────── 顶层求值 API ─────────────────────────────
def evaluate(node, env, out_width):
    """
    在上下文宽度 = max(自决宽度, out_width) 下求值整表达式，再截/扩到 out_width。
    返回 (value, out_width)。
    """
    sw = self_width(node, env)
    ctx = max(sw, out_width)
    v = evaluate_node(node, ctx, env)
    return v & mask(out_width), out_width


def eval_expr(expr_text, widths, values, out_width):
    """便捷封装：解析 + 求值。widths/values 为 {字母:int}。"""
    node = parse(expr_text)
    env = Env(widths, values)
    return evaluate(node, env, out_width)


# ───────────────────────────── 变量收集与角色分类 ─────────────────────────────
def collect_vars(node):
    """返回出现的变量字母集合（去重，保持发现顺序）。"""
    seen = []

    def walk(n):
        if isinstance(n, Var):
            if n.name not in seen:
                seen.append(n.name)
        elif isinstance(n, Const):
            pass
        elif isinstance(n, Unary):
            walk(n.operand)
        elif isinstance(n, Binary):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, Ternary):
            walk(n.cond)
            walk(n.then)
            walk(n.els)
        elif isinstance(n, Concat):
            for p in n.parts:
                walk(p)
        elif isinstance(n, Repeat):
            walk(n.body)

    walk(node)
    return seen


def _contains_mux_or_wide(n):
    """子树里是否含三元（用于判断 & 的另一侧是否是被门控的数据路径）。"""
    if isinstance(n, Ternary):
        return True
    if isinstance(n, Unary):
        return _contains_mux_or_wide(n.operand)
    if isinstance(n, Binary):
        return _contains_mux_or_wide(n.left) or _contains_mux_or_wide(n.right)
    if isinstance(n, Concat):
        return any(_contains_mux_or_wide(p) for p in n.parts)
    if isinstance(n, Repeat):
        return _contains_mux_or_wide(n.body)
    return False


def _bare_var(n):
    """若 n 是变量或 ~变量/!变量，返回该变量名，否则 None（用于识别门控位）。"""
    if isinstance(n, Var) and n.msb is None:
        return n.name
    if isinstance(n, Unary) and n.op in ("~", "!"):
        return _bare_var(n.operand)
    return None


def classify_vars(node, env):
    """
    把变量分为：
      control —— 出现在三元条件处，或作为门控位 &/| 的一侧（另一侧含 mux），按 1 位枚举
      data    —— 其余（分支取值 / 拼接 / 纯数据），可宽，采样
    返回 (control:list, data:list)，均按 collect_vars 的发现顺序。
    """
    all_vars = collect_vars(node)
    control = set()

    def walk(n):
        if isinstance(n, Ternary):
            for v in collect_vars(n.cond):
                control.add(v)        # 条件里的都是控制位
            walk(n.then)
            walk(n.els)
            walk(n.cond)
            return
        if isinstance(n, Binary):
            if n.op in ("&", "|"):
                # 门控形：一侧是 mux/数据路径，另一侧是裸（取反）变量 → 该变量是门控控制位
                lv, rv = _bare_var(n.left), _bare_var(n.right)
                if lv and _contains_mux_or_wide(n.right):
                    control.add(lv)
                if rv and _contains_mux_or_wide(n.left):
                    control.add(rv)
            walk(n.left)
            walk(n.right)
            return
        if isinstance(n, Unary):
            walk(n.operand)
            return
        if isinstance(n, Concat):
            for p in n.parts:
                walk(p)
            return
        if isinstance(n, Repeat):
            walk(n.body)
            return

    walk(node)
    control_list = [v for v in all_vars if v in control]
    data_list = [v for v in all_vars if v not in control]
    return control_list, data_list
