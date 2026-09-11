"""Bounded host invocation for trusted adapters only, never candidate code."""

import os
import selectors
import signal
import subprocess
import time


def bounded_run(argv, *, payload=b"", cwd=None, env=None, timeout=120, limit=1024 * 1024):
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for pipe in buffers:
        selector.register(pipe, selectors.EVENT_READ)
    offset = 0
    if payload:
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE)
    else:
        process.stdin.close()
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise TimeoutError("Trusted adapter exceeded its deadline")
            for key, _ in selector.select(0.05):
                if key.fileobj is process.stdin:
                    try:
                        offset += os.write(key.fd, payload[offset : offset + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(payload)
                    if offset == len(payload):
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue
                chunk = os.read(key.fd, 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    buffers[key.fileobj].extend(chunk)
                    if sum(map(len, buffers.values())) > limit:
                        raise ValueError("Trusted adapter exceeded its output limit")
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return process.returncode, bytes(buffers[process.stdout]), bytes(buffers[process.stderr])
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
        for pipe in (*buffers, process.stdin):
            pipe.close()
