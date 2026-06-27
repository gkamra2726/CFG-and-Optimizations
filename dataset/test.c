#include <stdio.h>

int main() {
    /* 1. CONSTANT FOLDING: these should be folded into single values */
    int a = 2 + 3;        // should fold to 5
    int b = 10 - 4;       // should fold to 6
    int c = 3 * 4;        // should fold to 12
    int d = 20 / 4;       // should fold to 5

    /* 2. DEAD VARIABLE: z is declared but never used */
    int z = 99  ;

    /* 3. NORMAL ASSIGNMENT */
    int x = a + b;

    /* 4. IF-ELSE: CFG should show true/false/merge branches */
    if (x > 5) {
        x = x + 1;
    } else {
        x = x++;
    }

    /* 5. WHILE LOOP: CFG should show loop back-edge */
    int i = 0;
    while (i < 3) {
        i = i + 1;
    }

    /* 6. FOR LOOP: CFG should show init -> cond -> body -> next cycle */
    int sum = 0;
    int j;
    for (j = 0; j < 4; j++) {
        sum = sum + j;
    }

    /* 7. UNREACHABLE CODE: everything after return should be removed */
    return sum;
    int unreachable = 42;   // should be removed
    x = x + 100;            // should be removed
}