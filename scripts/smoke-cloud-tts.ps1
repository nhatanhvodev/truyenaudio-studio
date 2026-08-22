param(
  [switch]$AllowPaid,
  [ValidateSet('google','gemini','eleven')]
  [string]$Provider,
  [string]$AuthorizationId,
  [string]$CloudConsentId,
  [int]$ExpectedMaxVnd
)

$ErrorActionPreference = 'Stop'

if (-not $AllowPaid) {
  throw 'ABORT: pass -AllowPaid to run a paid-provider smoke.'
}
if (-not $Provider -or -not $AuthorizationId -or -not $CloudConsentId -or $ExpectedMaxVnd -le 0) {
  throw 'ABORT: Provider, AuthorizationId, CloudConsentId and ExpectedMaxVnd are required.'
}
if ($ExpectedMaxVnd -gt 5000) {
  throw 'ABORT: one-segment smoke cap must be <= 5000 VND.'
}

$confirmation = Read-Host "Type RUN $Provider to confirm one paid TTS smoke"
if ($confirmation -ne "RUN $Provider") {
  throw 'ABORT: confirmation mismatch.'
}

$report = [ordered]@{
  provider = $Provider
  authorizationId = $AuthorizationId
  cloudConsentId = $CloudConsentId
  expectedMaxVnd = $ExpectedMaxVnd
  textChars = 62
  status = 'NOT_RUN'
  note = 'Preflight gates passed. Real provider invocation must be wired to the configured local API before marking PASS.'
}

$outDir = Join-Path (Get-Location) 'data\smoke'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$path = Join-Path $outDir "cloud-tts-$Provider-redacted.json"
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path -Encoding UTF8
Write-Host "NOT_RUN: wrote redacted preflight report $path"
