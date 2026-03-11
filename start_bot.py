"""Double-fork daemon starter — process survives shell/terminal close."""
import os, sys, subprocess

# First fork
pid = os.fork()
if pid > 0:
    print(f"Daemon started (grandchild will be watchdog)")
    os._exit(0)

os.setsid()   # new session, no controlling terminal

# Second fork — ensures we're not a session leader (can't acquire tty)
pid = os.fork()
if pid > 0:
    os._exit(0)

# Grandchild: fully detached daemon
os.chdir('/home/user/SPX_Inclusion')
os.umask(0)

# Redirect stdin/stdout/stderr
devnull = open('/dev/null', 'r')
logfile = open('/tmp/spx_bot.log', 'a')
os.dup2(devnull.fileno(), sys.stdin.fileno())
os.dup2(logfile.fileno(), sys.stdout.fileno())
os.dup2(logfile.fileno(), sys.stderr.fileno())

os.execv(sys.executable, [sys.executable, 'telegram/watchdog.py'])
