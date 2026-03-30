#!/usr/bin/env python3
"""calculator: A simple math tool."""
import sys

VERSION = "1.1.0"


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        print("Error: division by zero", file=sys.stderr)
        return None
    return a / b


OPERATIONS = {
    "add": add,
    "sub": subtract,
    "mul": multiply,
    "div": divide,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--help":
        print(f"calculator {VERSION}")
        print("Usage: calc <operation> <a> <b>")
        print(f"Operations: {', '.join(OPERATIONS)}")
        sys.exit(0)

    if sys.argv[1] == "--version":
        print(f"calculator {VERSION}")
        sys.exit(0)

    if len(sys.argv) != 4:
        print("Usage: calc <operation> <a> <b>", file=sys.stderr)
        sys.exit(1)

    op, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    if op not in OPERATIONS:
        print(f"Unknown operation: {op}", file=sys.stderr)
        sys.exit(1)

    result = OPERATIONS[op](a, b)
    if result is not None:
        # Display as integer if result is whole number
        print(int(result) if result == int(result) else result)


if __name__ == "__main__":
    main()
