#!/bin/bash
# Run via cron: * * * * * /home/user/SPX_Inclusion/keepalive.sh >> /tmp/spx_keepalive.log 2>&1
cd /home/user/SPX_Inclusion
if ! pgrep -f "telegram/polling.py" > /dev/null; then
    echo "$(date): polling.py not running — restarting"
    python start_bot.py
else
    echo "$(date): ok (pid=$(pgrep -f 'telegram/polling.py'))"
fi
