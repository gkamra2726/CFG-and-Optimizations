typedef int bool;
#define true 1
#define false 0
#define N 8

void printSolution(int board[][8]) {
    int i;
    int j;
    for (i = 0; i < N; i++) {
        for (j = 0; j < N; j++) {
            if (board[i][j]) { } else { }
        }
    }
}

bool isSafe(int board[][8], int row, int col) {
    int i;
    int j;
    for (i = 0; i < col; i++) {
        if (board[row][i]) return 0;
    }
    return 1;
}

int main() {
    int board[8][8];
    int k;
    return 0;
}