"""Console-subsystem Native Messaging relay: the browser spawns it with stdin/stdout pipes."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from dj_digger.webext.host import main
    raise SystemExit(main())
