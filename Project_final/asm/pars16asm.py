#!/usr/bin/env python3
"""
pars16asm -- assembler for the PARS-16 teaching CPU used in the Computer
Architecture final project.

No external dependencies. Python 3.8+.

Usage
-----
    python pars16asm.py prog.s -o prog.hex     # Digital "v2.0 raw" memory file
    python pars16asm.py prog.s --test          # program(0x...,0x...) for a testcase
    python pars16asm.py prog.s --lst           # address / machine code / source listing
    python pars16asm.py prog.s --words         # one decimal word per line

Instruction encoding (16 bit, fields are identical in every format)
-------------------------------------------------------------------
    R : CC[15:14] OP[13:10] Rd[9:7]     Rs1[6:4] Rs2[3:1] S[0]
    I : CC[15:14] OP[13:10] Rd/Rs2[9:7] Rs1[6:4] imm4[3:0]
    U : CC[15:14] OP[13:10] Rd[9:7]     imm7[6:0]
    B : CC[15:14] OP[13:10] offset10[9:0]

Condition codes (CC) -- every instruction is predicated:
    .AL = 00 (always, default)   .EQ = 01 (Z=1)
    .LT = 10 (N=1)               .VS = 11 (V=1)

The .S suffix (flag update) exists only on the seven R-format ALU
instructions; it is bit 0 of the word.
"""

import argparse
import re
import sys

# --------------------------------------------------------------------------
# instruction table:  mnemonic -> (opcode, format, allows .S)
# --------------------------------------------------------------------------
INSTRUCTIONS = {
    "ADD":  (0b0000, "R", True),
    "SUB":  (0b0001, "R", True),
    "AND":  (0b0010, "R", True),
    "OR":   (0b0011, "R", True),
    "XOR":  (0b0100, "R", True),
    "SHL":  (0b0101, "R", True),
    "SHR":  (0b0110, "R", True),
    "ADDI": (0b0111, "I", False),
    "LW":   (0b1000, "I", False),
    "SW":   (0b1001, "I", False),
    "MOVI": (0b1010, "U", False),
    "LUI":  (0b1011, "U", False),
    "B":    (0b1100, "B", False),
    "JAL":  (0b1101, "B", False),
    "JR":   (0b1110, "R", False),
    "NOP":  (0b1111, "N", False),   # opcode 1111 does nothing at all
}

CONDITIONS = {"AL": 0b00, "EQ": 0b01, "LT": 0b10, "VS": 0b11}

# pseudo instructions -> how many real words they expand to
PSEUDO = {"CMP": 1, "MOV": 1, "NOP": 1, "HALT": 1, "LI": 1}


class AsmError(Exception):
    def __init__(self, lineno, text, msg):
        super().__init__(msg)
        self.lineno = lineno
        self.text = text
        self.msg = msg

    def report(self, filename):
        return ("%s:%d: error: %s\n    %s" %
                (filename, self.lineno, self.msg, self.text.strip()))


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def parse_number(tok, symbols, lineno, text):
    """Accept 10, -3, 0x1f, 0b1010, 'A, or a symbol defined with .equ."""
    tok = tok.strip()
    if not tok:
        raise AsmError(lineno, text, "empty numeric operand")
    if tok in symbols:
        return symbols[tok]
    neg = False
    if tok[0] in "+-":
        neg = tok[0] == "-"
        tok = tok[1:]
    try:
        if tok.lower().startswith("0x"):
            v = int(tok, 16)
        elif tok.lower().startswith("0b"):
            v = int(tok, 2)
        else:
            v = int(tok, 10)
    except ValueError:
        raise AsmError(lineno, text, "'%s' is not a number or a known symbol" % tok)
    return -v if neg else v


def parse_reg(tok, lineno, text):
    tok = tok.strip().upper()
    m = re.fullmatch(r"R([0-7])", tok)
    if not m:
        raise AsmError(lineno, text, "'%s' is not a register (expected R0..R7)" % tok)
    return int(m.group(1))


def fits_signed(value, bits):
    return -(1 << (bits - 1)) <= value < (1 << (bits - 1))


def check_signed(value, bits, what, lineno, text):
    if not fits_signed(value, bits):
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        raise AsmError(lineno, text,
                       "%s = %d does not fit in %d signed bits (allowed %d..%d)"
                       % (what, value, bits, lo, hi))
    return value & ((1 << bits) - 1)


def split_operands(rest):
    """Split on commas that are not inside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in rest:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [o.strip() for o in out]


def need(ops, n, mnem, lineno, text):
    if len(ops) != n:
        raise AsmError(lineno, text,
                       "%s expects %d operand(s), got %d" % (mnem, n, len(ops)))


# --------------------------------------------------------------------------
# source line model
# --------------------------------------------------------------------------
class Line:
    __slots__ = ("lineno", "text", "label", "mnem", "cond", "setflags",
                 "operands", "directive", "addr", "words")

    def __init__(self, lineno, text):
        self.lineno = lineno
        self.text = text
        self.label = None
        self.mnem = None
        self.cond = 0
        self.setflags = False
        self.operands = []
        self.directive = None
        self.addr = None
        self.words = []


def parse_source(src):
    """Pass 0: strip comments, pull out labels, split mnemonic/suffixes."""
    lines = []
    for lineno, raw in enumerate(src.splitlines(), start=1):
        text = raw
        code = raw.split(";", 1)[0].strip()
        if not code:
            continue
        ln = Line(lineno, text)

        m = re.match(r"^([A-Za-z_.][A-Za-z0-9_]*)\s*:\s*(.*)$", code)
        if m:
            ln.label = m.group(1)
            code = m.group(2).strip()

        if code:
            parts = code.split(None, 1)
            head = parts[0]
            rest = parts[1] if len(parts) > 1 else ""

            if head.startswith("."):
                ln.directive = head.lower()
                ln.operands = split_operands(rest)
            else:
                pieces = head.upper().split(".")
                ln.mnem = pieces[0]
                for suffix in pieces[1:]:
                    if suffix == "S":
                        ln.setflags = True
                    elif suffix in CONDITIONS:
                        ln.cond = CONDITIONS[suffix]
                    else:
                        raise AsmError(lineno, text,
                                       "unknown suffix '.%s' (use .AL/.EQ/.LT/.VS or .S)"
                                       % suffix)
                ln.operands = split_operands(rest)

        if ln.label or ln.mnem or ln.directive:
            lines.append(ln)
    return lines


def size_of(ln, symbols=None):
    """Pass 1 helper: how many words does this line occupy?"""
    if ln.mnem == "LI":
        need(ln.operands, 2, "LI", ln.lineno, ln.text)
        value = parse_number(ln.operands[1], symbols or {}, ln.lineno, ln.text)
        return li_size(value, ln.lineno, ln.text)
    if ln.directive == ".word":
        return len(ln.operands)
    if ln.directive is not None:          # .org / .equ occupy no space
        return 0
    if ln.mnem:
        return 1
    return 0


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------
def encode_real(mnem, ops, cond, setflags, addr, labels, symbols, lineno, text):
    """Encode exactly one real (non-pseudo) instruction."""
    if mnem not in INSTRUCTIONS:
        raise AsmError(lineno, text, "unknown instruction '%s'" % mnem)

    opcode, fmt, allows_s = INSTRUCTIONS[mnem]

    if setflags and not allows_s:
        raise AsmError(lineno, text,
                       "%s has no S bit -- only the seven R-format ALU "
                       "instructions can update the flags" % mnem)

    s_bit = 1 if setflags else 0
    word = (cond << 14) | (opcode << 10)

    if fmt == "N":                       # NOP: opcode only, no operands
        need(ops, 0, mnem, lineno, text)
        return [word]

    if mnem == "JR":
        # The JR format has its own layout:  CC OPCODE Rs1[9:7] reserved[6:0]
        # -- the register number sits where Rd sits in the other formats, NOT
        # where Rs1 sits in the R format.
        need(ops, 1, "JR", lineno, text)
        return [word | (parse_reg(ops[0], lineno, text) << 7)]

    if fmt == "R":
        need(ops, 3, mnem, lineno, text)
        rd = parse_reg(ops[0], lineno, text)
        rs1 = parse_reg(ops[1], lineno, text)
        rs2 = parse_reg(ops[2], lineno, text)
        return [word | (rd << 7) | (rs1 << 4) | (rs2 << 1) | s_bit]

    if fmt == "I":
        if mnem in ("LW", "SW"):
            need(ops, 2, mnem, lineno, text)
            reg = parse_reg(ops[0], lineno, text)
            m = re.fullmatch(r"([^()]*)\(\s*([Rr][0-7])\s*\)", ops[1].strip())
            if not m:
                raise AsmError(lineno, text,
                               "%s expects the form  %s Rx, imm(Ry)" % (mnem, mnem))
            imm = parse_number(m.group(1).strip() or "0", symbols, lineno, text)
            base = parse_reg(m.group(2), lineno, text)
            return [word | (reg << 7) | (base << 4)
                    | check_signed(imm, 4, "offset", lineno, text)]
        need(ops, 3, mnem, lineno, text)
        rd = parse_reg(ops[0], lineno, text)
        rs1 = parse_reg(ops[1], lineno, text)
        imm = parse_number(ops[2], symbols, lineno, text)
        return [word | (rd << 7) | (rs1 << 4)
                | check_signed(imm, 4, "immediate", lineno, text)]

    if fmt == "U":
        need(ops, 2, mnem, lineno, text)
        rd = parse_reg(ops[0], lineno, text)
        imm = parse_number(ops[1], symbols, lineno, text)
        if mnem == "LUI":
            # LUI loads a raw 7-bit field into bits 15..9, so both the signed
            # spelling (-64) and the raw one (0x40) mean the same thing.
            if not (-64 <= imm <= 127):
                raise AsmError(lineno, text,
                               "LUI immediate %d is out of range; the field is "
                               "7 bits, so write -64..63 or 0..127 (LUI Rd,0x40 "
                               "gives 0x8000)" % imm)
            imm7 = imm & 0x7F
        else:
            imm7 = check_signed(imm, 7, "immediate", lineno, text)
        return [word | (rd << 7) | imm7]

    if fmt == "B":
        need(ops, 1, mnem, lineno, text)
        tok = ops[0].strip()
        if tok in labels:
            target = labels[tok]
        else:
            target = parse_number(tok, symbols, lineno, text) + addr + 1
        # the PC is 8 bit, so a target outside 0..255 can never be reached
        if not 0 <= target <= 0xFF:
            raise AsmError(lineno, text,
                           "branch target %d is outside the 8-bit program "
                           "address range 0..255" % target)
        offset = target - (addr + 1)
        return [word | check_signed(offset, 10, "branch offset", lineno, text)]

    raise AsmError(lineno, text, "internal error: unhandled format")


def li_parts(value, lineno, text):
    """How LI builds a 16-bit constant: [(mnem, extra_operands), ...]."""
    if fits_signed(value, 7):
        return [("MOVI", [str(value)])]
    # LUI reaches every multiple of 512; one ADDI then shifts it by -8..+7,
    # which is what makes 0x7FFF reachable as 0x8000 - 1.
    v16 = value & 0xFFFF
    low = v16 & 0x1FF
    if low <= 7:
        delta = low
    elif low >= 512 - 8:
        delta = low - 512
    else:
        raise AsmError(lineno, text,
                       "LI cannot build 0x%04X: MOVI reaches -64..63 and "
                       "LUI(+ADDI) reaches constants whose low 9 bits are "
                       "0..7 or 504..511" % v16)
    upper = ((v16 - delta) >> 9) & 0x7F
    parts = [("LUI", [str(upper)])]
    if delta:
        parts.append(("ADDI", [str(delta)]))
    return parts


def li_size(value, lineno, text):
    return len(li_parts(value, lineno, text))


def encode(ln, labels, symbols):
    """Pass 2: produce the machine word(s) for one source line."""
    mnem, ops = ln.mnem, ln.operands
    lineno, text = ln.lineno, ln.text
    cond, setflags = ln.cond, ln.setflags

    if mnem == "SYS":                    # kept so older sources still assemble
        need(ops, 0, "SYS", lineno, text)
        mnem, ops = "NOP", []
    elif mnem == "HALT":
        # There is no halt instruction any more.  Stopping means branching to
        # yourself for ever; the mnemonic is kept so programs stay readable.
        need(ops, 0, "HALT", lineno, text)
        mnem, ops = "B", ["-1"]
    elif mnem == "CMP":
        need(ops, 2, "CMP", lineno, text)
        mnem, ops, setflags = "SUB", ["R0", ops[0], ops[1]], True
    elif mnem == "MOV":
        need(ops, 2, "MOV", lineno, text)
        mnem, ops = "ADD", [ops[0], ops[1], "R0"]
    elif mnem == "LI":
        need(ops, 2, "LI", lineno, text)
        rd = ops[0]
        value = parse_number(ops[1], symbols, lineno, text)
        words, addr = [], ln.addr
        for step, (m, extra) in enumerate(li_parts(value, lineno, text)):
            args = [rd] + extra if m != "ADDI" else [rd, rd] + extra
            words += encode_real(m, args, cond, False, addr + step,
                                 labels, symbols, lineno, text)
        return words

    return encode_real(mnem, ops, cond, setflags, ln.addr,
                       labels, symbols, lineno, text)


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def assemble(src, filename="<stdin>"):
    """Return (words, listing_lines, labels)."""
    lines = parse_source(src)

    # pass 1 -- addresses, labels and .equ symbols
    symbols, labels, addr = {}, {}, 0
    for ln in lines:
        if ln.directive == ".equ":
            need(ln.operands, 2, ".equ", ln.lineno, ln.text)
            symbols[ln.operands[0].strip()] = parse_number(
                ln.operands[1], symbols, ln.lineno, ln.text)
            continue
        if ln.directive == ".org":
            need(ln.operands, 1, ".org", ln.lineno, ln.text)
            new_addr = parse_number(ln.operands[0], symbols, ln.lineno, ln.text)
            if new_addr < addr:
                raise AsmError(ln.lineno, ln.text,
                               ".org %d moves backwards (current address is %d)"
                               % (new_addr, addr))
            addr = new_addr
            continue
        if ln.label:
            if ln.label in labels:
                raise AsmError(ln.lineno, ln.text,
                               "label '%s' defined twice" % ln.label)
            labels[ln.label] = addr
        ln.addr = addr
        addr += size_of(ln, symbols)

    # pass 2 -- encode
    words, listing = [], []
    for ln in lines:
        if ln.directive in (".equ", ".org"):
            continue
        if ln.directive == ".word":
            ws = []
            for tok in ln.operands:
                v = parse_number(tok, symbols, ln.lineno, ln.text)
                ws.append(v & 0xFFFF)
            ln.words = ws
        elif ln.mnem:
            ln.words = encode(ln, labels, symbols)
        else:
            ln.words = []

        while len(words) < (ln.addr or 0):
            words.append(0)
        words.extend(ln.words)

        if len(words) > 256:
            raise AsmError(ln.lineno, ln.text,
                           "the program grew past 256 words; the instruction "
                           "memory has an 8-bit address and cannot hold it")

        if ln.words:
            listing.append("%04X  %s  | %s"
                           % (ln.addr,
                              " ".join("%04X" % w for w in ln.words),
                              ln.text.rstrip()))
        else:
            listing.append("      %s  | %s" % ("    ", ln.text.rstrip()))

    return words, listing, labels


def to_hex_file(words):
    """Digital / Logisim 'v2.0 raw' memory image."""
    out = ["v2.0 raw"]
    for i in range(0, len(words), 8):
        out.append(" ".join("%x" % w for w in words[i:i + 8]))
    return "\n".join(out) + "\n"


def to_program_statement(words, per_line=8):
    """The program(...) line for a Digital testcase."""
    items = ["0x%04x" % w for w in words]
    if len(items) <= per_line:
        return "program(%s)" % ",".join(items)
    chunks = [",".join(items[i:i + per_line]) for i in range(0, len(items), per_line)]
    return "program(" + (",\n        ".join(chunks)) + ")"


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pars16asm",
        description="Assembler for the PARS-16 teaching CPU.")
    ap.add_argument("source", help="assembly source file (.s)")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="write a Digital 'v2.0 raw' hex memory file")
    ap.add_argument("--test", action="store_true",
                    help="print the program(...) statement for a Digital testcase")
    ap.add_argument("--lst", action="store_true",
                    help="print an address / machine code / source listing")
    ap.add_argument("--words", action="store_true",
                    help="print one decimal word per line")
    args = ap.parse_args(argv)

    try:
        with open(args.source, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        print("cannot read %s: %s" % (args.source, exc), file=sys.stderr)
        return 2

    try:
        words, listing, labels = assemble(src, args.source)
    except AsmError as exc:
        print(exc.report(args.source), file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(to_hex_file(words))
        print("%s: %d words written to %s" % (args.source, len(words), args.output))
    if args.lst:
        print("\n".join(listing))
        print("\nlabels: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(labels.items())))
    if args.test:
        print(to_program_statement(words))
    if args.words:
        for w in words:
            print(w)
    if not (args.output or args.lst or args.test or args.words):
        print(to_hex_file(words), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
