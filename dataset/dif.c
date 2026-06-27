#include <stdio.h>

int main() {
    int a = 5;
    int b = 10;     // dead
    int c = 20;     // dead
    int d = 30;     // dead

    int x = a + 1;

    int y = 100;    // dead
    int z = 200;    // dead

    if (x > 0) {
        int p = 50;   // dead
        x = x + 2;
    }

    return x;
}