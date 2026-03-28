# Acceptance Criteria

## basic_operations
All 4 basic arithmetic operations return correct results.

- add(a, b) returns the sum of a and b
- subtract(a, b) returns the difference of a and b
- multiply(a, b) returns the product of a and b
- divide(a, b) returns the quotient of a divided by b

## edge_cases
Edge cases are handled correctly.

Depends on: basic_operations

- divide(a, 0) raises ValueError with message containing "zero"
- all operations handle negative numbers correctly
- all operations handle float inputs correctly
- multiply(a, 0) returns 0 for any a
