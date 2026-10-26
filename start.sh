#!/bin/sh
cd "$(dirname "$0")"
echo "WaveRise - run on this computer"
echo "Log in to Instagram in your browser first (Firefox works best)."
printf "Which browser are you logged in with? [firefox] "
read B
[ -z "$B" ] && B=firefox
python3 run_local.py --browser "$B"
