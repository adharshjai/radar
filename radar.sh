#!/bin/sh
# convenience wrapper: ./radar.sh sync | today | serve | demo
cd "$(dirname "$0")" && exec python3 -m radar "$@"
