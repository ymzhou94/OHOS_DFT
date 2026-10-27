"""Run hdc with logged output and an explicit device-side exit status."""

import re
import shlex
import subprocess


EXIT_STATUS = re.compile(r"\r?\nOHOS_DFT_EXIT=(\d+)\r?\n?\Z")


def run_hdc(arguments, log):
    """Log a command and reject host failures or missing/nonzero device status."""
    arguments = list(arguments)
    shell = arguments[0] == "shell"
    if shell:
        if len(arguments) != 2:
            raise ValueError("Pass one complete device command after 'shell'.")
        arguments[1] += (
            '; ohos_dft_status=$?; printf "\\nOHOS_DFT_EXIT=%s\\n" "$ohos_dft_status"; '
            'exit "$ohos_dft_status"'
        )
    command = ["hdc", *arguments]
    log.write("$ " + shlex.join(command) + "\n")
    log.flush()
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", timeout=30, check=False)
    log.write(result.stdout + "\n")
    log.flush()
    if result.returncode:
        raise RuntimeError(f"hdc exited {result.returncode}: {result.stdout}")
    if shell:
        status = EXIT_STATUS.search(result.stdout)
        if status is None:
            raise RuntimeError(f"Device exit status missing; inspect capture.log: {result.stdout}")
        if int(status[1]):
            raise RuntimeError(f"Device command exited {status[1]}: {result.stdout}")
        return result.stdout[:status.start()]
    return result.stdout
