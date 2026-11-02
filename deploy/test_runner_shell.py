import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def main():
    workflow = (Path(__file__).parent.parent / '.github/workflows/threadle.yml').read_text('utf-8')
    template = re.search(r'^\s+shell: (powershell .+)$', workflow, re.M).group(1)
    command = shlex.split(template)
    env = dict(os.environ, PSExecutionPolicyPreference='Restricted')
    with tempfile.TemporaryDirectory(prefix='threadle shell check ') as directory:
        script = Path(directory) / 'runner wrapper.ps1'
        for result in (0, 7):
            script.write_text(
                "if ((Get-ExecutionPolicy) -ne 'Bypass') { exit 99 }\n"
                "Write-Output 'RUNNER_WRAPPER_STARTED'\n"
                f'exit {result}\n', encoding='utf-8')
            args = [str(script) if part == '{0}' else part for part in command]
            executed = subprocess.run(args, env=env, capture_output=True, text=True, timeout=20)
            if executed.returncode != result or 'RUNNER_WRAPPER_STARTED' not in executed.stdout:
                raise RuntimeError(f'Runner wrapper did not preserve policy or exit code: {executed.returncode}, {executed.stderr}')
    print('Runner wrapper passed under inherited Restricted policy; success/failure exit codes preserved.')


if __name__ == '__main__':
    main()
