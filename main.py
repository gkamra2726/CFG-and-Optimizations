"""
analyse.py  —  batch C code analyser (no UI)
Usage:
    python analyse.py file1.c file2.c ...
    python analyse.py path/to/folder/          # analyses every .c file in folder
"""

import sys
import os
import re
import copy
import glob

from pycparser import c_ast, parse_file
import networkx as nx

# ───────────────────────────── helpers ──────────────────────────────────────

def get_expr(node):
    if node is None:                          return ""
    if isinstance(node, c_ast.Constant):      return node.value
    if isinstance(node, c_ast.ID):            return node.name
    if isinstance(node, c_ast.BinaryOp):
        return f"{get_expr(node.left)} {node.op} {get_expr(node.right)}"
    if isinstance(node, c_ast.UnaryOp):       return f"{node.op}{get_expr(node.expr)}"
    if isinstance(node, c_ast.FuncCall):      return "func_call"
    return ""

# ───────────────────────── dead variable analysis ───────────────────────────

def analyze_dead_vars(ast):
    declared, used = set(), set()

    class A(c_ast.NodeVisitor):
        def visit_Decl(self, node):
            if not isinstance(node.type, c_ast.FuncDecl):
                declared.add(node.name)
            self.generic_visit(node)
        def visit_ID(self, node):
            used.add(node.name)

    A().visit(ast)
    return sorted(declared - used)

# ───────────────────────── unreachable code detection ───────────────────────

def find_unreachable(node, results=None, func_name="?"):
    """Walk the AST and collect statements that come after a return."""
    if results is None:
        results = []

    if isinstance(node, c_ast.FuncDef):
        func_name = node.decl.name
        find_unreachable(node.body, results, func_name)

    elif isinstance(node, c_ast.FileAST):
        for ext in node.ext:
            find_unreachable(ext, results, func_name)

    elif isinstance(node, c_ast.Compound):
        found_return = False
        for stmt in (node.block_items or []):
            if found_return:
                line = stmt.coord.line if stmt.coord else "?"
                results.append((func_name, line, _stmt_desc(stmt)))
            if isinstance(stmt, c_ast.Return):
                found_return = True
            # recurse into nested blocks even if not unreachable at this level
            find_unreachable(stmt, results, func_name)

    elif isinstance(node, c_ast.If):
        find_unreachable(node.iftrue,  results, func_name)
        find_unreachable(node.iffalse, results, func_name)

    elif isinstance(node, c_ast.While):
        find_unreachable(node.stmt, results, func_name)

    elif isinstance(node, c_ast.For):
        find_unreachable(node.stmt, results, func_name)

    return results


def _stmt_desc(stmt):
    if isinstance(stmt, c_ast.Decl):
        init = f" = {get_expr(stmt.init)}" if stmt.init else ""
        return f"int {stmt.name}{init}"
    if isinstance(stmt, c_ast.Assignment):
        return f"{get_expr(stmt.lvalue)} = {get_expr(stmt.rvalue)}"
    if isinstance(stmt, c_ast.Return):
        return f"return {get_expr(stmt.expr)}"
    if isinstance(stmt, c_ast.If):
        return f"if ({get_expr(stmt.cond)}) {{ ... }}"
    if isinstance(stmt, c_ast.While):
        return f"while ({get_expr(stmt.cond)}) {{ ... }}"
    if isinstance(stmt, c_ast.For):
        return f"for (...) {{ ... }}"
    return type(stmt).__name__

# ───────────────────────── constant folding detection ───────────────────────

def find_foldable(node, results=None):
    """Find BinaryOp nodes with two constant operands (foldable expressions)."""
    if results is None:
        results = []
    if node is None:
        return results

    if isinstance(node, c_ast.BinaryOp):
        if isinstance(node.left, c_ast.Constant) and isinstance(node.right, c_ast.Constant):
            try:
                l, r = int(node.left.value), int(node.right.value)
                if node.op == '+':   val = l + r
                elif node.op == '-': val = l - r
                elif node.op == '*': val = l * r
                elif node.op == '/':
                    if r == 0:
                        results.append((node.coord, f"{l} / {r}", "div-by-zero, skipped"))
                        return results
                    val = l // r
                else:
                    val = None
                if val is not None:
                    results.append((node.coord,
                                    f"{node.left.value} {node.op} {node.right.value}",
                                    str(val)))
            except ValueError:
                pass

    for _, child in (node.children() if hasattr(node, 'children') else []):
        find_foldable(child, results)

    return results

# ───────────────────────── semantic analysis ────────────────────────────────

def semantic_analysis(ast):
    warnings = []

    # Pass 1: collect function signatures
    func_params = {}
    for ext in ast.ext:
        if isinstance(ext, c_ast.FuncDef):
            params = ext.decl.type.args
            count = 0
            if params and params.params:
                is_void = (len(params.params) == 1 and
                           isinstance(params.params[0].type, c_ast.TypeDecl) and
                           isinstance(params.params[0].type.type, c_ast.IdentifierType) and
                           params.params[0].type.type.names == ["void"])
                if not is_void:
                    count = len(params.params)
            func_params[ext.decl.name] = count

    class SemanticChecker(c_ast.NodeVisitor):
        def __init__(self):
            self.scope_stack = [{}]

        def current_scope(self):
            return self.scope_stack[-1]

        def lookup(self, name):
            for scope in reversed(self.scope_stack):
                if name in scope:
                    return scope[name]
            return None

        def declare(self, name, coord, initialized):
            line = coord.line if coord else "?"
            if name in self.current_scope():
                prev_line = self.current_scope()[name][0].line if self.current_scope()[name][0] else "?"
                warnings.append(("Redeclaration",
                    "'" + name + "' redeclared in same scope "
                    "(first at line " + str(prev_line) + ", again at line " + str(line) + ")"))
            self.current_scope()[name] = (coord, initialized)

        def mark_initialized(self, name):
            for scope in reversed(self.scope_stack):
                if name in scope:
                    scope[name] = (scope[name][0], True)
                    return

        def visit_FuncDef(self, node):
            self.scope_stack.append({})
            params = node.decl.type.args
            if params and params.params:
                for p in params.params:
                    if isinstance(p, c_ast.Decl) and p.name:
                        self.declare(p.name, p.coord, True)
            self.visit(node.body)
            self.scope_stack.pop()

        def visit_Compound(self, node):
            self.scope_stack.append({})
            for stmt in (node.block_items or []):
                self.visit(stmt)
            self.scope_stack.pop()

        def visit_Decl(self, node):
            if isinstance(node.type, c_ast.FuncDecl):
                return
            initialized = node.init is not None
            if node.init:
                self.visit(node.init)
            self.declare(node.name, node.coord, initialized)

        def visit_Assignment(self, node):
            self.visit(node.rvalue)
            if isinstance(node.lvalue, c_ast.ID):
                entry = self.lookup(node.lvalue.name)
                line = node.coord.line if node.coord else "?"
                if entry is None:
                    warnings.append(("Undeclared Variable",
                        "'" + node.lvalue.name + "' assigned but never declared (line " + str(line) + ")"))
                else:
                    self.mark_initialized(node.lvalue.name)

        def visit_ID(self, node):
            entry = self.lookup(node.name)
            line = node.coord.line if node.coord else "?"
            if entry is None:
                warnings.append(("Undeclared Variable",
                    "'" + node.name + "' used but never declared (line " + str(line) + ")"))
            elif not entry[1]:
                warnings.append(("Uninitialized Variable",
                    "'" + node.name + "' may be used before initialization (line " + str(line) + ")"))

        def visit_FuncCall(self, node):
            if isinstance(node.name, c_ast.ID):
                fname = node.name.name
                if fname in func_params:
                    expected = func_params[fname]
                    actual = len(node.args.exprs) if node.args and node.args.exprs else 0
                    line = node.coord.line if node.coord else "?"
                    if actual != expected:
                        warnings.append(("Wrong Argument Count",
                            "'" + fname + "' expects " + str(expected) + " arg(s) but got "
                            + str(actual) + " (line " + str(line) + ")"))
            if node.args:
                for arg in (node.args.exprs or []):
                    self.visit(arg)

        def visit_BinaryOp(self, node):
            self.visit(node.left)
            self.visit(node.right)
            if node.op == "/" and isinstance(node.right, c_ast.Constant):
                try:
                    if int(node.right.value) == 0:
                        line = node.coord.line if node.coord else "?"
                        warnings.append(("Division by Zero",
                            "Division by zero detected (line " + str(line) + ")"))
                except ValueError:
                    pass

        def visit_Return(self, node):
            if node.expr:
                self.visit(node.expr)

        def visit_If(self, node):
            self.visit(node.cond)
            if node.iftrue:  self.visit(node.iftrue)
            if node.iffalse: self.visit(node.iffalse)

        def visit_While(self, node):
            self.visit(node.cond)
            self.visit(node.stmt)

        def visit_For(self, node):
            self.scope_stack.append({})
            if node.init: self.visit(node.init)
            if node.cond: self.visit(node.cond)
            if node.next: self.visit(node.next)
            if node.stmt: self.visit(node.stmt)
            self.scope_stack.pop()

    SemanticChecker().visit(ast)

    seen = set()
    unique = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique

# ───────────────────────── CFG stats ────────────────────────────────────────

def build_cfg(ast):
    block_count = [0]
    cfg = nx.DiGraph()
    cfg.add_node("Start")

    def new_block(label):
        block_count[0] += 1
        name = f"{label} ({block_count[0]})"
        cfg.add_node(name)
        return name

    def helper(node, prev):
        if node is None:
            return prev

        if hasattr(node, 'block_items') and node.block_items:
            for stmt in node.block_items:
                new_prev = helper(stmt, prev)
                if new_prev is None:
                    return None
                prev = new_prev
            return prev

        if isinstance(node, c_ast.Decl):
            val = f" = {get_expr(node.init)}" if node.init else ""
            curr = new_block(f"decl {node.name}{val}")
            cfg.add_edge(prev, curr)
            return curr

        elif isinstance(node, c_ast.Assignment):
            curr = new_block(f"assign {node.lvalue.name} = {get_expr(node.rvalue)}")
            cfg.add_edge(prev, curr)
            return curr

        elif isinstance(node, c_ast.UnaryOp):
            var = get_expr(node.expr)
            if node.op in ['p++', 'p--']:
                curr = new_block(f"assign {var}{node.op[1:]}")
            elif node.op in ['++', '--']:
                curr = new_block(f"assign {node.op}{var}")
            else:
                return prev
            cfg.add_edge(prev, curr)
            return curr

        elif isinstance(node, c_ast.Return):
            curr = new_block(f"return {get_expr(node.expr)}")
            cfg.add_edge(prev, curr)
            return curr

        elif isinstance(node, c_ast.If):
            cond = new_block(f"if {get_expr(node.cond)}")
            cfg.add_edge(prev, cond)
            t = new_block("true")
            f_node = new_block("false")
            cfg.add_edge(cond, t)
            cfg.add_edge(cond, f_node)
            t_end = helper(node.iftrue, t)
            f_end = helper(node.iffalse, f_node) if node.iffalse else f_node
            merge = new_block("merge")
            if t_end: cfg.add_edge(t_end, merge)
            if f_end: cfg.add_edge(f_end, merge)
            return merge

        elif isinstance(node, c_ast.While):
            cond = new_block(f"while {get_expr(node.cond)}")
            cfg.add_edge(prev, cond)
            body = new_block("loop")
            cfg.add_edge(cond, body)
            end = new_block("end")
            cfg.add_edge(cond, end)
            body_end = helper(node.stmt, body)
            if body_end: cfg.add_edge(body_end, cond)
            return end

        elif isinstance(node, c_ast.For):
            prev = helper(node.init, prev)
            cond = new_block(f"for {get_expr(node.cond)}")
            cfg.add_edge(prev, cond)
            body = new_block("loop")
            cfg.add_edge(cond, body)
            end = new_block("end")
            cfg.add_edge(cond, end)
            body_end = helper(node.stmt, body)
            next_node = helper(node.next, body_end)
            if next_node: cfg.add_edge(next_node, cond)
            return end

        return prev

    for ext in ast.ext:
        if isinstance(ext, c_ast.FuncDef):
            helper(ext.body, "Start")

    return cfg

# ───────────────────────── remove dead / unreachable ────────────────────────

def remove_dead_decls(node, dead_vars):
    if node is None:
        return None
    if hasattr(node, 'block_items') and node.block_items:
        node.block_items = [s for s in
                            (remove_dead_decls(stmt, dead_vars) for stmt in node.block_items)
                            if s is not None]
        return node
    if isinstance(node, c_ast.Decl) and node.name in dead_vars:
        return None
    for _, child in node.children():
        remove_dead_decls(child, dead_vars)
    return node


def remove_unreachable_ast(node):
    if node is None:
        return None
    if isinstance(node, c_ast.Compound):
        new_items = []
        found_return = False
        for stmt in node.block_items or []:
            if found_return:
                continue
            s = remove_unreachable_ast(stmt)
            if s is not None:
                new_items.append(s)
            if isinstance(stmt, c_ast.Return):
                found_return = True
        node.block_items = new_items
        return node
    elif isinstance(node, c_ast.If):
        node.iftrue  = remove_unreachable_ast(node.iftrue)
        node.iffalse = remove_unreachable_ast(node.iffalse)
        return node
    elif isinstance(node, c_ast.While):
        node.stmt = remove_unreachable_ast(node.stmt)
        return node
    elif isinstance(node, c_ast.For):
        node.stmt = remove_unreachable_ast(node.stmt)
        return node
    elif isinstance(node, c_ast.FuncDef):
        node.body = remove_unreachable_ast(node.body)
        return node
    elif isinstance(node, c_ast.FileAST):
        node.ext = [remove_unreachable_ast(e) for e in node.ext]
        return node
    return node

# ───────────────────────── pretty printing ──────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
GREY   = "\033[90m"
WHITE  = "\033[97m"

def header(title):
    bar = "─" * 60
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}")

def section(title):
    print(f"\n{BOLD}{WHITE}  ▸ {title}{RESET}")

def ok(msg):
    print(f"    {GREEN}✔  {msg}{RESET}")

def warn(msg):
    print(f"    {YELLOW}⚠  {msg}{RESET}")

def err(msg):
    print(f"    {RED}✖  {msg}{RESET}")

def info(msg):
    print(f"    {GREY}{msg}{RESET}")

# ───────────────────────── main analyser ────────────────────────────────────

def analyse_file(path):
    header(f"FILE: {path}")

    # strip preprocessor directives and comments
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        err(f"File not found: {path}")
        return

    code_clean = "\n".join(
        line for line in raw.splitlines()
        if not line.strip().startswith("#")
    )
    code_clean = re.sub(r"//.*",       "",  code_clean)
    code_clean = re.sub(r"/\*.*?\*/",  "",  code_clean, flags=re.DOTALL)

    tmp = path + ".tmp.c"
    with open(tmp, "w") as f:
        f.write(code_clean)

    try:
        ast = parse_file(
            tmp,
            use_cpp=True,
            cpp_args=r"-Ipycparser/utils/fake_libc_include"
        )
    except Exception as e:
        err(f"Parse error: {e}")
        os.remove(tmp)
        return
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    # ── 1. Dead variables ────────────────────────────────────────────────────
    section("Dead Variables (declared but never used)")
    dead = analyze_dead_vars(ast)
    if dead:
        for v in dead:
            warn(f"'{v}' is declared but never used")
    else:
        ok("No dead variables found")

    # ── 2. Unreachable code ──────────────────────────────────────────────────
    section("Unreachable Code (after return statements)")
    unreachable = find_unreachable(ast)
    if unreachable:
        for func, line, desc in unreachable:
            warn(f"In '{func}' at line {line}: unreachable statement  →  {desc}")
    else:
        ok("No unreachable code found")

    # ── 3. Constant folding opportunities ────────────────────────────────────
    section("Constant Folding Opportunities")
    foldable = find_foldable(ast)
    if foldable:
        for coord, expr, result in foldable:
            line = coord.line if coord else "?"
            if result == "div-by-zero, skipped":
                err(f"Line {line}: '{expr}'  →  division by zero (cannot fold)")
            else:
                info(f"Line {line}: '{expr}'  →  folds to  {result}")
    else:
        ok("No constant folding opportunities")

    # ── 4. Semantic issues ───────────────────────────────────────────────────
    section("Semantic Analysis")
    issues = semantic_analysis(ast)
    sem_errors   = [i for i in issues if i[0] in ("Undeclared Variable", "Division by Zero", "Wrong Argument Count")]
    sem_warnings = [i for i in issues if i[0] in ("Uninitialized Variable", "Redeclaration")]

    if not issues:
        ok("No semantic issues found")
    else:
        for cat, msg in sem_errors:
            err(f"[{cat}] {msg}")
        for cat, msg in sem_warnings:
            warn(f"[{cat}] {msg}")

    # ── 5. CFG stats before / after optimisation ─────────────────────────────
    section("CFG Stats (before vs after optimisation)")

    cfg_before = build_cfg(ast)

    opt_ast = copy.deepcopy(ast)
    opt_ast = remove_dead_decls(opt_ast, dead)
    opt_ast = remove_unreachable_ast(opt_ast)
    cfg_after = build_cfg(opt_ast)

    nodes_removed = len(cfg_before.nodes) - len(cfg_after.nodes)
    edges_removed = len(cfg_before.edges) - len(cfg_after.edges)

    info(f"Nodes : {len(cfg_before.nodes):>4}  →  {len(cfg_after.nodes):>4}   (removed {nodes_removed})")
    info(f"Edges : {len(cfg_before.edges):>4}  →  {len(cfg_after.edges):>4}   (removed {edges_removed})")

    # ── 6. Summary line ──────────────────────────────────────────────────────
    total_issues = len(dead) + len(unreachable) + len(foldable) + len(issues)
    section("Summary")
    if total_issues == 0:
        ok("Clean file — no issues detected")
    else:
        print(f"    {BOLD}Dead vars: {len(dead)}   "
              f"Unreachable: {len(unreachable)}   "
              f"Foldable exprs: {len(foldable)}   "
              f"Semantic issues: {len(issues)}{RESET}")

# ───────────────────────── entry point ──────────────────────────────────────

def collect_files(args):
    files = []
    for arg in args:
        if os.path.isdir(arg):
            found = glob.glob(os.path.join(arg, "**", "*.c"), recursive=True)
            if not found:
                print(f"{YELLOW}No .c files found in directory: {arg}{RESET}")
            files.extend(sorted(found))
        else:
            files.append(arg)
    return files


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"{BOLD}Usage:{RESET}  python analyse.py  file.c  [file2.c ...]  [folder/]")
        sys.exit(1)

    files = collect_files(sys.argv[1:])
    if not files:
        print(f"{RED}No files to analyse.{RESET}")
        sys.exit(1)

    for path in files:
        analyse_file(path)

    print(f"\n{BOLD}{GREEN}Done — analysed {len(files)} file(s).{RESET}\n")