from pycparser import CParser, c_ast
import networkx as nx

# C code
code = r"""
int main() {
    int x = 5;
    int y = 10;
    x = 20;
    return x;
}
"""

# -------- PARSE --------
parser = CParser()
ast = parser.parse(code)

# -------- ANALYSIS --------
assigned = []
used = []

class Analyzer(c_ast.NodeVisitor):

    def visit_Decl(self, node):
        if node.init:
            assigned.append(node.name)
        self.generic_visit(node)

    def visit_Assignment(self, node):
        assigned.append(node.lvalue.name)
        self.generic_visit(node)

    def visit_ID(self, node):
        used.append(node.name)

analyzer = Analyzer()
analyzer.visit(ast)

dead_vars = [var for var in assigned if var not in used]

print("Dead variables:", dead_vars)

# -------- CFG (simple version) --------
cfg = nx.DiGraph()

nodes = [
    ("B1", "x"),
    ("B2", "y"),
    ("B3", "x"),
    ("B4", "return")
]

# Add nodes
for n, var in nodes:
    cfg.add_node(n, var=var)

# Add edges
cfg.add_edges_from([
    ("B1", "B2"),
    ("B2", "B3"),
    ("B3", "B4")
])

# -------- OPTIMIZATION --------
for node in list(cfg.nodes):
    var = cfg.nodes[node].get("var")

    if var in dead_vars:
        print("Removing node:", node)
        cfg.remove_node(node)

print("Remaining nodes:", cfg.nodes)
