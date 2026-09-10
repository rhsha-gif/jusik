"""Bounded output capture for hostile subprocess output."""

import subprocess
import threading
from types import SimpleNamespace


def bounded_run(
    command,
    *,
    timeout,
    env,
    max_output_bytes=65536,
    capture_output=True,
    text=False,
    check=False,
    shell=False,
):
    if not capture_output or text or shell:
        raise ValueError("unsupported_subprocess_options")
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, shell=False
    )
    buffers = [bytearray(), bytearray()]
    exceeded = threading.Event()
    lock = threading.Lock()

    def read(stream, index):
        while True:
            data = stream.read(4096)
            if not data:
                break
            with lock:
                remaining = max_output_bytes - sum(map(len, buffers))
                buffers[index].extend(data[: max(0, remaining)])
                if len(data) > remaining:
                    exceeded.set()
                    process.kill()
                    break

    readers = [
        threading.Thread(target=read, args=(stream, index), daemon=True)
        for index, stream in enumerate((process.stdout, process.stderr))
    ]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=1)
    if exceeded.is_set():
        raise ValueError("subprocess_output_limit")
    if check and process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode,
            command,
            output=bytes(buffers[0]),
            stderr=bytes(buffers[1]),
        )
    return SimpleNamespace(
        returncode=process.returncode,
        stdout=bytes(buffers[0]),
        stderr=bytes(buffers[1]),
    )
