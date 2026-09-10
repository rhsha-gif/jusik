import subprocess
import sys
import pytest
from quantpilot.paper.process import bounded_run


def test_host_captures_at_most_the_output_budget():
    with pytest.raises(ValueError, match="output_limit"):
        bounded_run(
            [sys.executable, "-c", "print('x'*100000)"],
            timeout=5,
            env={},
            max_output_bytes=1024,
        )


def test_host_terminates_a_timed_out_process():
    with pytest.raises(subprocess.TimeoutExpired):
        bounded_run(
            [sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1, env={}
        )
