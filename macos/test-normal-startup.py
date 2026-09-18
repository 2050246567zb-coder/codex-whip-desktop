"""Run the packaged app normally with empty user data; preserve crash evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
output = root / 'macos/test-results/startup'
output.mkdir(parents=True, exist_ok=True)
report = output / 'ready.json'
exe = root / 'macos/dist/CodexWhip.app/Contents/MacOS/CodexWhip'
result = {'normal_startup': True, 'mocked_features': [], 'passed': False}
with tempfile.TemporaryDirectory(prefix='whip-ci-data-') as data:
    env = dict(os.environ, CODEX_WHIP_DATA_DIR=data,
               CODEX_WHIP_STARTUP_REPORT=str(report), PYTHONFAULTHANDLER='1')
    with (output / 'stdout.log').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
        proc = subprocess.Popen([str(exe)], env=env, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + 20
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.25)
            result['exit_code_before_cleanup'] = proc.poll()
            if report.exists():
                result['ready_report'] = json.loads(report.read_text())
            ready = result.get('ready_report', {})
            result['passed'] = (proc.poll() is None and ready.get('root_visible') is True
                                and ready.get('native_effects') == 'CodexWhipEffects'
                                and ready.get('sending_enabled') is False)
            subprocess.run(['screencapture', '-x', str(output / 'desktop.png')], check=False)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
(output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
print((output / 'stderr.log').read_text())
sys.exit(0 if result['passed'] else 1)
