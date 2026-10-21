@echo off
set "BCN_AGENT_ID={{ agent_id }}"
set "BCN_AGENT_MODE={{ mode }}"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "LANG=C.UTF-8"
set "LC_ALL=C.UTF-8"
chcp 65001 >NUL 2>NUL
"{{ python }}" -m bazaar_contrib_agent.bcc %*
