$ErrorActionPreference = 'Stop'
$env:BCN_AGENT_ID = '{{ agent_id }}'
$env:BCN_AGENT_MODE = '{{ mode }}'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:LANG = 'C.UTF-8'
$env:LC_ALL = 'C.UTF-8'
$python = "{{ python }}"
if ($MyInvocation.ExpectingInput) {
    $input | & $python -m bazaar_contrib_agent.bcc @args
} else {
    & $python -m bazaar_contrib_agent.bcc @args
}
exit $LASTEXITCODE
