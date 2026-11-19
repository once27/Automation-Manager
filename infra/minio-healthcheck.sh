#!/usr/bin/bash
set -euo pipefail

exec 3<>/dev/tcp/127.0.0.1/9000
printf '%s\r\n' \
  'GET /minio/health/ready HTTP/1.0' \
  'Host: localhost' \
  'Connection: close' \
  '' \
  '' >&3

IFS=' ' read -r protocol status _ <&3
status=${status%$'\r'}
[[ $protocol == HTTP/* && $status == 200 ]]
