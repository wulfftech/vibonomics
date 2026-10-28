"""Runs a task's unittest suite inside the sandbox and reports the outcome in a form the grader can trust.

The grader used to read the exit status and unittest's summary, both of which the code under test can write
itself: ``os._exit(0)`` at import passed every task. This script reports through a line that carries a
one-off nonce the grader chose and the code under test was never shown, so the line cannot be forged without
reading this process's arguments. A run that ends without it is graded as a failure.

It is shipped into the workspace as a file and run as ``python -P <this file> <nonce> <test dir>``. ``-P``
keeps the working directory off ``sys.path`` while this module imports what it needs; the workspace goes on
only afterwards, so a workspace file named like a standard-library module cannot stand in for one that is
already imported (``unittest.py`` containing ``sys.exit(0)`` passed every task under ``-m unittest``).
"""

import json
import os
import sys
import unittest

MARKER = "@@VIBONOMICS-RESULT"


def main() -> int:
    nonce, test_dir = sys.argv[1], sys.argv[2]
    sys.path.insert(0, os.getcwd())
    suite = unittest.defaultTestLoader.discover(test_dir)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    report = {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "unexpected_successes": len(result.unexpectedSuccesses),
    }
    sys.stderr.write(f"\n{MARKER} {nonce} {json.dumps(report)}\n")
    sys.stderr.flush()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
