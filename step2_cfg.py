from pycparser import CParser
import networkx as nx
from networkx.drawing.nx_pydot import write_dot

# Input C code
code = r"""
int main() {
    int x = 5;
    int y = 10;
    if (x > 0) {
        y = x + 1;
    }
    return y;
}
"""

parser = CParser()
ast = parser.parse(code)

# Graph
cfg = nx.DiGraph()
block_count = 0 #har node ko unique naam deta h

def new_block(label):
    global block_count
    block_count += 1
    name = f"B{block_count}_{label}"   # fixed
    cfg.add_node(name) #adds node in graph
    return name #node name returned

# Main builder
def build_cfg(node, prev):
    if node is None:
        return prev

    # Compound block
    if hasattr(node, 'block_items') and node.block_items:
        for stmt in node.block_items:
            prev = build_cfg(stmt, prev)
        return prev

    # Declaration
    if node.__class__.__name__ == "Decl":
        curr = new_block("decl")
        cfg.add_edge(prev, curr)
        return curr

    # Assignment
    elif node.__class__.__name__ == "Assignment":
        curr = new_block("assign")
        cfg.add_edge(prev, curr)
        return curr

    # Return
    elif node.__class__.__name__ == "Return":
        curr = new_block("return")
        cfg.add_edge(prev, curr)
        return curr

    # If condition
    elif node.__class__.__name__ == "If":
        cond = new_block("if_cond")
        cfg.add_edge(prev, cond)

        true_block = new_block("true")
        false_block = new_block("false")

        cfg.add_edge(cond, true_block)
        cfg.add_edge(cond, false_block)

        # True branch
        t_end = build_cfg(node.iftrue, true_block)

        # False branch (if exists)
        if node.iffalse:
            f_end = build_cfg(node.iffalse, false_block)
        else:
            f_end = false_block

        merge = new_block("merge")

        cfg.add_edge(t_end, merge)
        cfg.add_edge(f_end, merge)

        return merge

    return prev


# Start node
start = "Start"
cfg.add_node(start)

# Build CFG
for ext in ast.ext:
    if ext.__class__.__name__ == "FuncDef":
        build_cfg(ext.body, start)

# Save graph
write_dot(cfg, "cfg.dot")

print("CFG created → cfg.dot")
