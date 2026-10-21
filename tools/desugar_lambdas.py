"""Rewrite Java lambdas / method refs into anonymous classes (build-time copy only).

Android's runtime has no LambdaMetafactory, and without D8 there is no
desugaring step, so every lambda must become a plain anonymous class.
The functional interface is chosen from the call each lambda is passed to.
"""
import re
import sys

IFACES = {
    "setOnClickListener": ("android.view.View.OnClickListener", "void", "onClick", ["android.view.View"]),
    "setOnCheckedChangeListener": ("android.widget.CompoundButton.OnCheckedChangeListener", "void",
                                   "onCheckedChanged", ["android.widget.CompoundButton", "boolean"]),
    "Thread": ("java.lang.Runnable", "void", "run", []),
    "runOnUiThread": ("java.lang.Runnable", "void", "run", []),
    "post": ("java.lang.Runnable", "void", "run", []),
    "postDelayed": ("java.lang.Runnable", "void", "run", []),
    "=Runnable": ("java.lang.Runnable", "void", "run", []),
    "SimpleItemSelectedListener": ("SpinnerSelectionHandler", "void", "onSelected", ["int"]),
    "simpleListener": ("ProgressAction", "void", "apply", ["int"]),
    "sort": ("java.util.Comparator<LibraryGame>", "int", "compare", ["LibraryGame", "LibraryGame"]),
}

METHOD_REFS = {
    "onAimAxis": ("AimController.Output", "void", "onAimAxis", [("int", "axis"), ("float", "value")]),
}


def skip_literal(s, i):
    """If s[i] starts a string/char literal or comment, return index after it, else None."""
    c = s[i]
    if c in "\"'":
        j = i + 1
        while s[j] != c:
            j += 2 if s[j] == "\\" else 1
        return j + 1
    if s.startswith("//", i):
        return s.index("\n", i)
    if s.startswith("/*", i):
        return s.index("*/", i) + 2
    return None


def code_positions(s, token):
    i, out = 0, []
    while i < len(s):
        e = skip_literal(s, i)
        if e is not None:
            i = e
            continue
        if s.startswith(token, i):
            out.append(i)
        i += 1
    return out


def match_forward(s, i):
    """s[i] is an opener; return index of its matching closer."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = [pairs[s[i]]]
    j = i + 1
    while stack:
        e = skip_literal(s, j)
        if e is not None:
            j = e
            continue
        c = s[j]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")}]":
            assert c == stack[-1], (s[i:j + 1][:200])
            stack.pop()
        j += 1
    return j - 1


def match_backward(s, i):
    """s[i] is ')'; return index of its '(' (lambda param lists hold no literals)."""
    depth = 0
    for j in range(i, -1, -1):
        if s[j] == ")":
            depth += 1
        elif s[j] == "(":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError


def expr_end(s, i):
    depth = 0
    j = i
    while True:
        e = skip_literal(s, j)
        if e is not None:
            j = e
            continue
        c = s[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif c in ",;" and depth == 0:
            return j
        j += 1


def qualify_this(body, outer):
    return re.sub(r"(?<![.\w])this\b(?!\s*::)", outer + ".this", body)


def context_name(s, start):
    """Name of the call (or '=Type') that receives the lambda starting at `start`."""
    j = start - 1
    while s[j].isspace():
        j -= 1
    if s[j] == "=":
        decl = s[s.rfind("\n", 0, j):j]
        return "=" + decl.split()[-2]
    # walk back over earlier arguments to the opening paren of this call
    depth = 0
    while True:
        e = None
        c = s[j]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                break
            depth -= 1
        j -= 1
    k = j - 1
    while s[k].isspace():
        k -= 1
    m = re.search(r"(\w+)$", s[:k + 1])
    return m.group(1)


def rewrite_lambda(s, arrow, outer):
    # parameters
    p_end = arrow - 1
    while s[p_end].isspace():
        p_end -= 1
    if s[p_end] == ")":
        p_start = match_backward(s, p_end)
        params = [p.strip() for p in s[p_start + 1:p_end].split(",") if p.strip()]
    else:
        m = re.search(r"(\w+)$", s[:p_end + 1])
        p_start = m.start()
        params = [m.group(1)]
    # body
    b = arrow + 2
    while s[b].isspace():
        b += 1
    if s[b] == "{":
        b_end = match_forward(s, b)
        body = s[b + 1:b_end]
        end = b_end + 1
        block = True
    else:
        end = expr_end(s, b)
        body = s[b:end].rstrip()
        block = False
    ctx = context_name(s, p_start)
    if ctx not in IFACES:
        raise SystemExit("unknown lambda context %r near: %s" % (ctx, s[p_start:p_start + 80]))
    iface, rtype, meth, ptypes = IFACES[ctx]
    assert len(ptypes) == len(params), (ctx, params)
    body = qualify_this(body, outer)
    sig = ", ".join("%s %s" % (t, n) for t, n in zip(ptypes, params))
    if block:
        inner = body
    else:
        inner = (" return %s; " if rtype != "void" else " %s; ") % body
    repl = "new %s() { @Override public %s %s(%s) {%s} }" % (iface, rtype, meth, sig, inner)
    return s[:p_start] + repl + s[end:]


def rewrite_method_refs(s, outer):
    def sub(m):
        name = m.group(1)
        if name in METHOD_REFS:
            iface, rtype, meth, params = METHOD_REFS[name]
            sig = ", ".join("%s %s" % p for p in params)
            args = ", ".join(n for _, n in params)
            return "new %s() { @Override public %s %s(%s) { %s.this.%s(%s); } }" % (
                iface, rtype, meth, sig, outer, name, args)
        return "new java.lang.Runnable() { @Override public void run() { %s.this.%s(); } }" % (outer, name)
    return re.sub(r"\bthis::(\w+)", sub, s)


def main(path):
    s = open(path, encoding="utf-8").read()
    if not code_positions(s, "->") and not code_positions(s, "::"):
        return
    outer = re.search(r"^(?:public )?(?:final )?class (\w+)", s, re.M).group(1)
    s = rewrite_method_refs(s, outer)
    while True:
        arrows = code_positions(s, "->")
        if not arrows:
            break
        s = rewrite_lambda(s, arrows[-1], outer)
    open(path, "w", encoding="utf-8").write(s)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
