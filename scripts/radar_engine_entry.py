"""PyInstaller entry point for the bundled engine (same CLI as `python -m radar`)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from radar.cli import main
    sys.exit(main())
