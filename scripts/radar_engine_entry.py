"""PyInstaller entry point for the bundled engine (same CLI as `python -m radar`)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if getattr(sys, "frozen", False):
        # PyInstaller bakes the build machine's TZPATH (Anaconda); use the system zoneinfo instead.
        import zoneinfo
        zoneinfo.reset_tzpath(["/usr/share/zoneinfo", "/var/db/timezone/zoneinfo"])
    from radar.cli import main
    sys.exit(main())
