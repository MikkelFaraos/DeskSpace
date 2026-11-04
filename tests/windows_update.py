"""Windows only: the update command's quoting, with spaces in every path. A setup that fails has to
restart the app, one that works must not."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fences.updates import install_command  # noqa: E402

folder = Path(tempfile.mkdtemp()) / "Program Files"
folder.mkdir()
marker = folder / "restarted.txt"
script = folder / "my app.py"
script.write_text(f"open(r'{marker}', 'w').write('yes')\n")


def run(exit_code):
    marker.unlink(missing_ok=True)
    setup = folder / f"set up {exit_code}.cmd"
    setup.write_text(f"@exit /b {exit_code}\n")
    subprocess.Popen(install_command(setup, sys.executable, script), creationflags=0x08000000 | 0x00000008)
    time.sleep(8)
    return marker.exists()


assert run(1), "a failed setup should restart the app"
assert not run(0), "a working setup should leave restarting to the new version"
print("update command works")
