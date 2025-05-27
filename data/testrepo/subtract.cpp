#include <iostream>


int calculate_sum(int a, int b) {
    return a + b;
}


class Calculator {
private:
    int value;

 void add(int x) {
        value += x;
    }
    void subtract(int x) {
        value -= x;
    }
  int get_value() const {
        return value;
    }
};