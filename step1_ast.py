from pycparser import CParser
#pycparser understands c code aisi ek library h vo 

code = r""" #this ignores special characters like \n
int main() {
    int x = 5;
    int y = 10;
    if (x > 0) {
        y = x + 1;
    }
    return y;
}
"""
#converts c code to ast
parser = CParser() # yeh basically decl type ke node banayega in the tree
ast = parser.parse(code) #tree object. makes the ast

def visit(node): #traverse the tree
    print(type(node).__name__) #node ka type print krega

    for _, child in node.children(): #node.children - returns all child nodes
        visit(child) #calls same function at every node

visit(ast)
