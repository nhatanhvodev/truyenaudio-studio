param(
    [ValidateSet('refuse', 'qwen', 'vieneu', 'piper')]
    [string]$Provider = 'refuse',
    [switch]$AllowPaid,
    [string]$AuthorizationId,
    [string]$CloudConsentId,
    [string]$Text = 'sample',
    [int]$Seconds = 30,
    [string]$Region = 'local'
)

$ErrorActionPreference = 'Stop'

function Count-Han([string]$Value) {
    $count = 0
    foreach ($char in $Value.ToCharArray()) {
        if ([int][char]$char -ge 0x4e00 -and [int][char]$char -le 0x9fff) {
            $count += 1
        }
    }
    return $count
}

function Is-Uuid([string]$Value) {
    return $Value -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
}

function Write-Report([hashtable]$Payload) {
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $reportRoot = Join-Path $repoRoot 'data\smoke-reports'
    New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $path = Join-Path $reportRoot "real-provider-smoke-$stamp.json"
    $Payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding UTF8
    Write-Host "Report: $path"
}

$hanCount = Count-Han $Text
$model = 'piper-vais1000'
if ($Provider -eq 'qwen') {
    $model = 'qwen-mt'
}
elseif ($Provider -eq 'vieneu') {
    $model = 'vieneu-vi-int8'
}
$durationSeconds = $null
if ($Provider -in @('vieneu', 'piper')) {
    $durationSeconds = $Seconds
}
$estimate = [ordered]@{
    provider = $Provider
    model = $model
    region = $Region
    han_count = $hanCount
    duration_seconds = $durationSeconds
    paid_network = $Provider -eq 'qwen'
}

Write-Host ($estimate | ConvertTo-Json -Compress)

if ($Provider -eq 'refuse') {
    Write-Host 'REFUSED: choose -Provider qwen, vieneu, or piper and type RUN when prompted.'
    exit 0
}

if ($Provider -eq 'qwen') {
    if (-not $AllowPaid -or -not (Is-Uuid $AuthorizationId) -or -not (Is-Uuid $CloudConsentId)) {
        Write-Host 'REFUSED: qwen requires -AllowPaid plus UUID AuthorizationId and CloudConsentId.'
        exit 0
    }
    if ($hanCount -gt 500) {
        Write-Host 'REFUSED: qwen smoke allows one segment of at most 500 Han characters.'
        exit 0
    }
}

if ($Provider -in @('vieneu', 'piper') -and ($Seconds -lt 20 -or $Seconds -gt 60)) {
    Write-Host 'REFUSED: local TTS smoke duration must be between 20 and 60 seconds.'
    exit 0
}

$confirmation = Read-Host 'Type RUN to execute the real-provider smoke'
if ($confirmation -ne 'RUN') {
    Write-Host 'REFUSED: confirmation was not RUN.'
    exit 0
}

$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $textHash = [System.BitConverter]::ToString(
        $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))
    ).Replace('-', '').ToLowerInvariant()
}
finally {
    $sha256.Dispose()
}

$report = @{
    status = 'manual_required'
    provider = $Provider
    model = $estimate.model
    region = $Region
    han_count = $hanCount
    text_sha256 = $textHash
    authorization_id_present = [bool]$AuthorizationId
    cloud_consent_id_present = [bool]$CloudConsentId
    secret_redacted = $true
    full_text_redacted = $true
}

if ($Provider -eq 'qwen') {
    $report.status = 'ready_for_paid_qwen_manual_call'
    Write-Host 'READY: perform the single authorized qwen segment manually; do not paste secrets or full text into this report.'
} else {
    $report.status = 'ready_for_local_tts_manual_call'
    $report.duration_seconds = $Seconds
    Write-Host 'READY: run the local TTS adapter and verify a WAV between 20 and 60 seconds.'
}

Write-Report $report
