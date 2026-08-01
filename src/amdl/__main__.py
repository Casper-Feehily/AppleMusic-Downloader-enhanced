"""python -m amdl → launch server or desktop app."""
import sys

try:
    from amdl.cli import main
    main()
except RuntimeError as e:
    print(f"FATAL: {e}", file=sys.stderr)
    sys.exit(1)