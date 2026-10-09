import os
import sys

import pytest

from codex_whip.app_instance import AppInstance


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS GUI lock")
def test_only_one_copy_runs_and_stale_pid_does_not_block_restart(tmp_path):
    first = AppInstance(tmp_path)
    second = AppInstance(tmp_path)
    assert first.acquire()
    try:
        assert not second.acquire()
        assert second.existing_pid == os.getpid()
        isolated = AppInstance(tmp_path / "test-profile")
        assert isolated.acquire()
        isolated.close()
    finally:
        first.close()
    assert second.acquire()  # lock file's stale PID is harmless
    second.close()
