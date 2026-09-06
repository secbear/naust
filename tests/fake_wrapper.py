"""A sandbox-shaped wrapper: runs its argv as a child and forwards nothing.

bubblewrap behaves like this: signals sent to the wrapper are not passed to
the child, and the wrapper exits when the child exits. The supervisor must
therefore signal the child directly.
"""

import signal
import subprocess
import sys

signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.exit(subprocess.call(sys.argv[1:]))
