#!/bin/sh
export BCN_AGENT_ID={{ agent_id }}
export BCN_AGENT_MODE={{ mode }}
exec {{ python }} -m bazaar_contrib_agent.bcc "$@"
