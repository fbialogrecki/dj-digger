"""Own only media child processes; emergency shutdown must not orphan them."""
import os
import signal
import subprocess
import threading

_LOCK = threading.Lock()
_PROCESSES = set()


def register(process):
    with _LOCK:
        _PROCESSES.add(process)


def unregister(process):
    with _LOCK:
        _PROCESSES.discard(process)


def kill_tree(process):
    """Terminate an owned process group, including its decoder children."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def terminate_owned():
    with _LOCK:
        processes = tuple(_PROCESSES)
    for process in processes:
        try:
            if process.poll() is not None:
                continue
            kill_tree(process)
            process.wait(timeout=3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
