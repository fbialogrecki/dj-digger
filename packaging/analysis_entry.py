"""Console-subsystem helper, launched hidden with pipes by the GUI."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from dj_digger.bundled import hold
    guard = hold()
    from dj_digger.analysis import _child
    if len(sys.argv) != 3:
        raise SystemExit(2)
    _child(sys.argv[1], sys.argv[2])
