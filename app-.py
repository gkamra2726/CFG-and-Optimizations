import streamlit as st
from pycparser import c_ast, parse_file  
import networkx as nx
import os
import re
import copy

block_count = 0

# ---------------- GRAPH COLOR ----------------
def write_colored_dot(cfg, filename, highlight_nodes=None, deleted_nodes=None):
    with open(filename, "w") as f:
        f.write("digraph G {\n")
        for node in cfg.nodes:
            if highlight_nodes and node in highlight_nodes:
                color = "yellow"
            elif deleted_nodes and node in deleted_nodes:
                color = "red"
            else:
                color = "lightblue"
            f.write(f'"{node}" [style=filled, fillcolor={color}];\n')
        for u, v in cfg.edges:
            f.write(f'"{u}" -> "{v}";\n')
        f.write("}")

def get_expr(node):
    if node is None:
        return ""

    if isinstance(node, c_ast.Constant):
        return node.value

    if isinstance(node, c_ast.ID):
        return node.name

    if isinstance(node, c_ast.BinaryOp):
        return f"{get_expr(node.left)} {node.op} {get_expr(node.right)}"

    if isinstance(node, c_ast.UnaryOp):
        return f"{node.op}{get_expr(node.expr)}"

    if isinstance(node, c_ast.FuncCall):
        return "func_call"

    return ""

# ---------------- HIGHLIGHT HELPERS ----------------
def get_target_label(selected_line):
    line = selected_line.split("//")[0].strip().rstrip(";").rstrip("{").strip()

    # while (cond) -> "while cond"
    m = re.match(r"while\s*\((.+)\)", line)
    if m:
        return f"while {m.group(1).strip()}"

    # for (...; cond; ...) -> "for cond"
    m = re.match(r"for\s*\(([^;]*);([^;]*);([^)]*)\)", line)
    if m:
        return f"for {m.group(2).strip()}"

    # if (cond) -> "if cond"
    m = re.match(r"if\s*\((.+)\)", line)
    if m:
        return f"if {m.group(1).strip()}"

    # function definition line — skip
    m = re.match(r"\w[\w\s\*]*\s+\w+\s*\(.*\)", line)
    if m and "{" in selected_line:
        return None

    if line.startswith("int "):
        m = re.match(r"int\s+([A-Za-z_]\w*)\s*=\s*(.+)", line)
        if m:
            return f"decl {m.group(1)} = {m.group(2).strip()}"
        m = re.match(r"int\s+([A-Za-z_]\w*)", line)
        if m:
            return f"decl {m.group(1)}"

    if line.startswith("return"):
        m = re.match(r"return\s+(.*)", line)
        if m:
            return f"return {m.group(1).strip()}"
        return "return"

    m = re.match(r"([A-Za-z_]\w*)\s*=\s*(.+)", line)
    if m:
        return f"assign {m.group(1)} = {m.group(2).strip()}"

    return None


def get_highlight_nodes(cfg, target_label):
    if not target_label:
        return []

    nodes = []
    for node in cfg.nodes:
        # Strip the counter suffix (N) to get the label
        label = re.sub(r'\s*\(\d+\)$', '', node).strip()
        if label == target_label:
            nodes.append(node)
    return nodes

#ANALYSIS
def analyze(ast):
    declared, used = set(), set()

    class A(c_ast.NodeVisitor):
        def visit_Decl(self, node):
            if not isinstance(node.type, c_ast.FuncDecl):
                declared.add(node.name)
            self.generic_visit(node)

        def visit_ID(self, node):
            used.add(node.name)

    A().visit(ast)
    return list(declared - used)

# UNREACHABLE 
def remove_unreachable_ast(node):
    if node is None:
        return None

    # HANDLE COMPOUND BLOCK 
    if isinstance(node, c_ast.Compound):
        new_items = []
        found_return = False

        for stmt in node.block_items or []:
            if found_return:
                continue  # drop everything after return

            s = remove_unreachable_ast(stmt)

            if s is not None:          # was `if s:` - falsy nodes were dropped
                new_items.append(s)

            # check ORIGINAL stmt, not the transformed `s`
            if isinstance(stmt, c_ast.Return):
                found_return = True

        node.block_items = new_items
        return node

    # -------- HANDLE IF --------
    elif isinstance(node, c_ast.If):
        node.iftrue  = remove_unreachable_ast(node.iftrue)
        node.iffalse = remove_unreachable_ast(node.iffalse)
        return node

    # -------- HANDLE WHILE --------
    elif isinstance(node, c_ast.While):
        node.stmt = remove_unreachable_ast(node.stmt)
        return node

    # -------- HANDLE FOR --------
    elif isinstance(node, c_ast.For):
        node.stmt = remove_unreachable_ast(node.stmt)
        return node

    # -------- HANDLE FUNCDEFS --------
    elif isinstance(node, c_ast.FuncDef):
        node.body = remove_unreachable_ast(node.body)
        return node

    # -------- HANDLE FILE ROOT --------
    elif isinstance(node, c_ast.FileAST):
        node.ext = [remove_unreachable_ast(e) for e in node.ext]
        return node
    
    return node


# ---------------- CONSTANT FOLDING ----------------
def constant_folding(node):
    if node is None:
        return node

    if isinstance(node, c_ast.FileAST):
        node.ext = [constant_folding(ext) for ext in node.ext]
        return node

    if isinstance(node, c_ast.FuncDef):
        node.body = constant_folding(node.body)
        return node

    if isinstance(node, c_ast.Compound):
        if node.block_items:
            node.block_items = [constant_folding(s) for s in node.block_items]
        return node

    if isinstance(node, c_ast.Decl):
        if node.init:
            node.init = constant_folding(node.init)
        return node

    if isinstance(node, c_ast.Assignment):
        node.rvalue = constant_folding(node.rvalue)
        return node

    if isinstance(node, c_ast.Return):          
        if node.expr:
            node.expr = constant_folding(node.expr)
        return node

   #recurse into If branches so folding works inside conditionals
    if isinstance(node, c_ast.If):
        node.cond    = constant_folding(node.cond)
        node.iftrue  = constant_folding(node.iftrue)
        if node.iffalse:
            node.iffalse = constant_folding(node.iffalse)
        return node

    # recurse into While
    if isinstance(node, c_ast.While):
        node.cond = constant_folding(node.cond)
        node.stmt = constant_folding(node.stmt)
        return node

    # recurse into For
    if isinstance(node, c_ast.For):
        node.init = constant_folding(node.init)
        node.cond = constant_folding(node.cond)
        node.next = constant_folding(node.next)
        node.stmt = constant_folding(node.stmt)
        return node

    if isinstance(node, c_ast.BinaryOp):
        left  = constant_folding(node.left)
        right = constant_folding(node.right)

        if isinstance(left, c_ast.Constant) and isinstance(right, c_ast.Constant):
            try:                                # guard against non-int constants
                l, r = int(left.value), int(right.value)
                if   node.op == '+': val = l + r
                elif node.op == '-': val = l - r
                elif node.op == '*': val = l * r
                elif node.op == '/':
                    if r == 0: return node      # don't fold division by zero
                    val = l // r
                else:
                    node.left, node.right = left, right
                    return node
                return c_ast.Constant(type='int', value=str(val))
            except ValueError:
                pass                            # e.g. float constants — skip folding

        node.left, node.right = left, right
        return node

    # recurse into UnaryOp (e.g. -(2+3) should fold)
    if isinstance(node, c_ast.UnaryOp):
        node.expr = constant_folding(node.expr)
        return node

    return node

# ---------------- DEAD CODE ----------------
def remove_dead_decls(node, dead_vars):
    if node is None:
        return None

    if hasattr(node, 'block_items') and node.block_items:
        new_items = []
        for stmt in node.block_items:
            s = remove_dead_decls(stmt, dead_vars)
            if s is not None:
                new_items.append(s)
        node.block_items = new_items
        return node

    if isinstance(node, c_ast.Decl) and node.name in dead_vars:
        return None

    for _, child in node.children():
        remove_dead_decls(child, dead_vars)

    return node

# ---------------- CFG ----------------
def new_block(cfg, label):
    global block_count
    block_count += 1
    name = f"{label} ({block_count})"
    cfg.add_node(name)
    return name

def build_cfg(ast):
    global block_count
    block_count = 0

    cfg = nx.DiGraph()
    cfg.add_node("Start")

    def helper(node, prev):
        if node is None:
            return None

        if hasattr(node, 'block_items') and node.block_items:
            for stmt in (node.block_items or []):
                new_prev = helper(stmt, prev)
                if new_prev is None:
                    return None
                prev = new_prev
            return prev

        if isinstance(node, c_ast.Decl):
            val = ""
            if node.init:
                val = f" = {get_expr(node.init)}"
            curr = new_block(cfg, f"decl {node.name}{val}")
            cfg.add_edge(prev, curr)
            return curr

        elif isinstance(node, c_ast.Assignment):
            curr = new_block(cfg, f"assign {node.lvalue.name} = {get_expr(node.rvalue)}")
            cfg.add_edge(prev, curr)
            return curr

        # FIXED UNARY
        elif isinstance(node, c_ast.UnaryOp):
            var = get_expr(node.expr)
            if node.op in ['p++', 'p--']:
                curr = new_block(cfg, f"assign {var}{node.op[1:]}")
            elif node.op in ['++', '--']:
                curr = new_block(cfg, f"assign {node.op}{var}")
            else:
                return prev
            cfg.add_edge(prev, curr)
            return curr

        elif isinstance(node, c_ast.Return):
            curr = new_block(cfg, f"return {get_expr(node.expr)}")
            cfg.add_edge(prev, curr)
            return curr

        elif isinstance(node, c_ast.If):
            cond = new_block(cfg, f"if {get_expr(node.cond)}")
            cfg.add_edge(prev, cond)

            t = new_block(cfg, "true")
            f = new_block(cfg, "false")

            cfg.add_edge(cond, t)
            cfg.add_edge(cond, f)

            t_end = helper(node.iftrue, t)
            f_end = helper(node.iffalse, f) if node.iffalse else f

            merge = new_block(cfg, "merge")
            cfg.add_edge(t_end, merge)
            cfg.add_edge(f_end, merge)

            return merge

        # WHILE LOOP
        elif isinstance(node, c_ast.While):
            cond = new_block(cfg, f"while {get_expr(node.cond)}")
            cfg.add_edge(prev, cond)

            body = new_block(cfg, "loop")
            cfg.add_edge(cond, body)

            end = new_block(cfg, "end")
            cfg.add_edge(cond, end)

            body_end = helper(node.stmt, body)
            cfg.add_edge(body_end, cond)

            return end

        # FOR LOOP
        elif isinstance(node, c_ast.For):
            prev = helper(node.init, prev)

            cond = new_block(cfg, f"for {get_expr(node.cond)}")
            cfg.add_edge(prev, cond)

            body = new_block(cfg, "loop")
            cfg.add_edge(cond, body)

            end = new_block(cfg, "end")
            cfg.add_edge(cond, end)

            body_end = helper(node.stmt, body)
            next_node = helper(node.next, body_end)

            cfg.add_edge(next_node, cond)

            return end

        return prev

    for ext in ast.ext:
        if isinstance(ext, c_ast.FuncDef):
            helper(ext.body, "Start")

    return cfg

# ---------------- UI ----------------
st.set_page_config(layout="wide")
st.title("C Code Optimiser- CS202 Project")

col1, col2 = st.columns(2)

with col1:
    code = st.text_area("Paste C Code", height=400)
    lines = [l.strip() for l in code.split("\n") if l.strip()]
    selected_line = st.selectbox("Select line", lines)

with col2:
    if st.button("🚀 Run Analysis"):
        try:
            code_clean = "\n".join(
                line for line in code.splitlines()
                if not line.strip().startswith("#")
            )

            code_clean = re.sub(r"//.*", "", code_clean)
            code_clean = re.sub(r"/\*.*?\*/", "", code_clean, flags=re.DOTALL)

            #  PARSING FIX
            with open("temp.c", "w") as f:
                f.write(code_clean)

            ast = parse_file(
                "temp.c",
                use_cpp=True,
                cpp_args=r'-Ipycparser/utils/fake_libc_include'
            )

            dead = analyze(ast)

            target_label = get_target_label(selected_line)

            cfg_before = build_cfg(ast)

            ast = constant_folding(ast)

            if target_label:
                highlight_nodes_before = get_highlight_nodes(cfg_before, target_label)
            else:
                highlight_nodes_before = []

            write_colored_dot(cfg_before, "before.dot", highlight_nodes_before)
            os.system("dot -Tpng before.dot -o before.png")

            # cfg_folded: post-folding but before dead code removal — used for label mapping
            cfg_folded = build_cfg(ast)

            opt_ast = copy.deepcopy(ast)
            opt_ast = remove_dead_decls(opt_ast, dead)
            opt_ast = remove_unreachable_ast(opt_ast)

            cfg_after = build_cfg(opt_ast)

            def strip_counter(name):
                return re.sub(r'\s*\(\d+\)$', '', name)

            # Build before-label -> folded-label mapping (positional correspondence)
            before_to_folded = {}
            for nb, nf in zip(list(cfg_before.nodes), list(cfg_folded.nodes)):
                before_to_folded[strip_counter(nb)] = strip_counter(nf)

            # For cfg_after, use the folded version of target_label (e.g. "decl a = 2+3" -> "decl a = 5")
            folded_target_label = before_to_folded.get(target_label, target_label) if target_label else None

            if folded_target_label:
                highlight_nodes_after = get_highlight_nodes(cfg_after, folded_target_label)
            else:
                highlight_nodes_after = []

            # Build folded-label -> before-node mapping for deleted node detection
            folded_to_before = {}
            for nb, nf in zip(list(cfg_before.nodes), list(cfg_folded.nodes)):
                folded_to_before[strip_counter(nf)] = nb

            labels_after = set(strip_counter(n) for n in cfg_after.nodes)
            deleted_nodes = set(
                before_node
                for folded_label, before_node in folded_to_before.items()
                if folded_label not in labels_after
            )

            write_colored_dot(cfg_before, "before.dot", highlight_nodes_before, deleted_nodes)
            os.system("dot -Tpng before.dot -o before.png")

            write_colored_dot(cfg_after, "after.dot", highlight_nodes_after)
            os.system("dot -Tpng after.dot -o after.png")

            c1, c2 = st.columns(2)
            c1.image("before.png", caption="Before")
            c2.image("after.png", caption="After")

            st.subheader("Dead Variables")
            st.write(dead)

            # ADD THIS ↓
            st.subheader("CFG Summary")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Nodes", f"{len(cfg_before.nodes)} → {len(cfg_after.nodes)}", delta=-(len(cfg_before.nodes) - len(cfg_after.nodes)), delta_color="inverse")
            col_b.metric("Edges", f"{len(cfg_before.edges)} → {len(cfg_after.edges)}", delta=-(len(cfg_before.edges) - len(cfg_after.edges)), delta_color="inverse")
            col_c.metric("Dead Vars Removed", len(dead))

        except Exception as e:
            st.error(str(e))