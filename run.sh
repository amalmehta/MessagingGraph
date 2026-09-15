#!/usr/bin/env bash
# Demo data. For your own messages: ./mg import, then ./mg serve
cd "$(dirname "$0")"
exec ./mg serve --synthetic "$@"
