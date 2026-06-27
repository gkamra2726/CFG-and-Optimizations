from pycparser import CParser, c_ast

code = r"""
int main() {
    int x = 5;
    int y = 10;
    x = 20;
    return x;
}
"""

parser = CParser()
ast = parser.parse(code)

assigned = []
used = []

# Traverse AST
class Analyzer(c_ast.NodeVisitor):

    def visit_Decl(self, node):
        if node.init:  # agar initialization hai
            assigned.append(node.name)
        self.generic_visit(node)

    def visit_Assignment(self, node):
        assigned.append(node.lvalue.name)
        self.generic_visit(node)

    def visit_ID(self, node):
        used.append(node.name)

analyzer = Analyzer()
analyzer.visit(ast)

print("Assigned:", assigned)
print("Used:", used)
