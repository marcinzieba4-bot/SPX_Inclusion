#!/bin/bash
cd "$(dirname "$0")"
kill $(ps aux | grep -E "watchdog.py|polling.py" | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 1
python start_bot.py
sleep 3
ps aux | grep -E "watchdog.py|polling.py" | grep -v grep
echo "Bot started. Logs: tail -f /tmp/spx_bot.log"
