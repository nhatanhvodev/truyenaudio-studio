param(
    [ValidateSet('refuse', 'qwen', 'vieneu', 'piper')]
    [string]$Provider = 'refuse',
    [switch]$AllowPaid,
    [string]$AuthorizationId,
    [string]$CloudConsentId,
    [string]$Text = 'sample',
    [int]$Seconds = 30,
    [string]$Region = 'local',
    [string]$ExecutablePath,
    [string]$ModelPath,
    [string]$OutputDir,
    [string]$ReportRoot,
    [string]$QwenEndpoint,
    [string]$DatabasePath,
    [string]$ProjectId,
    [string]$ProviderProfileId
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

function Get-NonEmptySegments([string]$Value) {
    return @($Value -split "(`r`n|`n|`r)+" | Where-Object { $_.Trim().Length -gt 0 })
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString($sha256.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-TextHash([string]$Value) {
    return Get-Sha256Hex ([System.Text.Encoding]::UTF8.GetBytes($Value))
}

function Write-Report([hashtable]$Payload) {
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $targetReportRoot = if ($ReportRoot) { $ReportRoot } else { Join-Path $repoRoot 'data\smoke-reports' }
    New-Item -ItemType Directory -Force -Path $targetReportRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $path = Join-Path $targetReportRoot "real-provider-smoke-$stamp.json"
    $Payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding UTF8
    Write-Host "Report: $path"
}

function Invoke-QwenDbValidation([hashtable]$Report) {
    if (-not $DatabasePath -or -not $ProjectId -or -not $ProviderProfileId) {
        Fail-Smoke $Report 'QWEN_DB_CONTEXT_REQUIRED'
    }
    if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) {
        Fail-Smoke $Report 'QWEN_DATABASE_MISSING'
    }
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $python = 'py'
    }
    $validator = @'
import json
import sqlite3
import sys
from datetime import datetime, timezone

db_path, project_id, profile_id, authorization_id, consent_id, requested_endpoint = sys.argv[1:7]
now = datetime.now(timezone.utc).isoformat()

def fail(code):
    print(json.dumps({"ok": False, "code": code}))
    raise SystemExit(0)

try:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
except sqlite3.Error:
    fail("QWEN_DATABASE_OPEN_FAILED")

profile = connection.execute(
    """
    SELECT id, adapter_name, model, region, secret_ref, config_json, enabled
    FROM provider_profiles
    WHERE id = ?
    """,
    (profile_id,),
).fetchone()
if profile is None or not profile["enabled"] or profile["adapter_name"] != "qwen":
    fail("QWEN_PROVIDER_PROFILE_INVALID")
if not profile["model"] or not profile["region"] or not profile["secret_ref"]:
    fail("QWEN_PROVIDER_PROFILE_INCOMPLETE")
config = json.loads(profile["config_json"] or "{}")
endpoint = config.get("endpoint")
if not endpoint:
    fail("QWEN_PROFILE_ENDPOINT_REQUIRED")
if requested_endpoint and requested_endpoint != endpoint:
    fail("QWEN_ENDPOINT_MISMATCH")

project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
if project is None:
    fail("QWEN_PROJECT_NOT_FOUND")

consent = connection.execute(
    """
    SELECT policy_snapshot_artifact_id, status
    FROM cloud_processing_consents
    WHERE id = ? AND project_id = ? AND provider_profile_id = ?
    """,
    (consent_id, project_id, profile_id),
).fetchone()
if consent is None or consent["status"] != "GRANTED":
    fail("QWEN_CONSENT_NOT_GRANTED")
if not consent["policy_snapshot_artifact_id"]:
    fail("QWEN_POLICY_SNAPSHOT_REQUIRED")
policy = connection.execute(
    "SELECT status FROM artifacts WHERE id = ?",
    (consent["policy_snapshot_artifact_id"],),
).fetchone()
if policy is None or policy["status"] != "READY":
    fail("QWEN_POLICY_SNAPSHOT_NOT_READY")

authorization = connection.execute(
    """
    SELECT status, expires_at
    FROM budget_authorizations
    WHERE id = ?
    """,
    (authorization_id,),
).fetchone()
if authorization is None or authorization["status"] not in ("HELD", "COMMITTED"):
    fail("QWEN_AUTHORIZATION_INVALID")
if authorization["expires_at"] <= now:
    fail("QWEN_AUTHORIZATION_EXPIRED")

for unit in ("INPUT_TOKEN", "OUTPUT_TOKEN"):
    row = connection.execute(
        """
        SELECT id
        FROM rate_cards
        WHERE provider = 'qwen'
          AND model = ?
          AND region = ?
          AND unit = ?
          AND verified_at IS NOT NULL
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to > ?)
        """,
        (profile["model"], profile["region"], unit, now, now),
    ).fetchone()
    if row is None:
        fail("QWEN_RATE_CARD_MISSING")

print(json.dumps({
    "ok": True,
    "endpoint": endpoint,
    "model": profile["model"],
    "region": profile["region"],
}))
'@
    $validationJson = $validator | & $python - $DatabasePath $ProjectId $ProviderProfileId $AuthorizationId $CloudConsentId $QwenEndpoint
    if ($LASTEXITCODE -ne 0 -or -not $validationJson) {
        Fail-Smoke $Report 'QWEN_DB_VALIDATION_FAILED'
    }
    $validation = $validationJson | ConvertFrom-Json
    if (-not $validation.ok) {
        Fail-Smoke $Report ([string]$validation.code)
    }
    $script:QwenEndpoint = [string]$validation.endpoint
    $Report.model = [string]$validation.model
    $Report.region = [string]$validation.region
}

function Fail-Smoke([hashtable]$Report, [string]$Code) {
    $Report.status = 'FAIL'
    $Report.error_code = $Code
    Write-Host "FAIL: $Code"
    Write-Report $Report
    exit 1
}

function Read-UInt16Le([byte[]]$Bytes, [int]$Offset) {
    return [System.BitConverter]::ToUInt16($Bytes, $Offset)
}

function Read-UInt32Le([byte[]]$Bytes, [int]$Offset) {
    return [System.BitConverter]::ToUInt32($Bytes, $Offset)
}

function Test-WavFile([string]$Path, [int]$MinimumSeconds, [int]$MaximumSeconds) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return @{ valid = $false; error_code = 'WAV_MISSING' }
    }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 44) {
        return @{ valid = $false; error_code = 'WAV_TOO_SMALL' }
    }
    $riff = [System.Text.Encoding]::ASCII.GetString($bytes, 0, 4)
    $wave = [System.Text.Encoding]::ASCII.GetString($bytes, 8, 4)
    if ($riff -ne 'RIFF' -or $wave -ne 'WAVE') {
        return @{ valid = $false; error_code = 'WAV_BAD_HEADER' }
    }

    $offset = 12
    $format = $null
    $channels = $null
    $sampleRate = $null
    $byteRate = $null
    $bitsPerSample = $null
    $dataBytes = $null
    while ($offset + 8 -le $bytes.Length) {
        $chunkId = [System.Text.Encoding]::ASCII.GetString($bytes, $offset, 4)
        $chunkSize = [int](Read-UInt32Le $bytes ($offset + 4))
        $chunkData = $offset + 8
        if ($chunkData + $chunkSize -gt $bytes.Length) {
            return @{ valid = $false; error_code = 'WAV_TRUNCATED' }
        }
        if ($chunkId -eq 'fmt ' -and $chunkSize -ge 16) {
            $format = Read-UInt16Le $bytes $chunkData
            $channels = Read-UInt16Le $bytes ($chunkData + 2)
            $sampleRate = Read-UInt32Le $bytes ($chunkData + 4)
            $byteRate = Read-UInt32Le $bytes ($chunkData + 8)
            $bitsPerSample = Read-UInt16Le $bytes ($chunkData + 14)
        }
        elseif ($chunkId -eq 'data') {
            $dataBytes = $chunkSize
        }
        $offset = $chunkData + $chunkSize + ($chunkSize % 2)
    }
    if (-not $format -or -not $channels -or -not $sampleRate -or -not $byteRate -or -not $bitsPerSample -or -not $dataBytes) {
        return @{ valid = $false; error_code = 'WAV_MISSING_CHUNKS' }
    }
    if ($format -ne 1) {
        return @{ valid = $false; error_code = 'WAV_NOT_PCM' }
    }
    $duration = [math]::Round(($dataBytes / $byteRate), 3)
    if ($duration -lt $MinimumSeconds -or $duration -gt $MaximumSeconds) {
        return @{ valid = $false; error_code = 'WAV_DURATION_OUT_OF_RANGE'; duration_seconds = $duration }
    }
    return @{
        valid = $true
        duration_seconds = $duration
        format = @{
            channels = $channels
            sample_rate_hz = $sampleRate
            bits_per_sample = $bitsPerSample
        }
        byte_size = $bytes.Length
        artifact_sha256 = Get-Sha256Hex $bytes
    }
}

function Invoke-QwenSmoke([hashtable]$Report) {
    Invoke-QwenDbValidation $Report
    if (-not $QwenEndpoint) {
        Fail-Smoke $Report 'QWEN_ENDPOINT_REQUIRED'
    }
    try {
        $body = @{
            model = $Report.model
            text = $Text
        } | ConvertTo-Json -Compress
        $headers = @{
            'X-Smoke-Authorization-Id' = $AuthorizationId
            'X-Smoke-Cloud-Consent-Id' = $CloudConsentId
        }
        $response = Invoke-RestMethod -Method Post -Uri $QwenEndpoint -ContentType 'application/json' -Headers $headers -Body $body -TimeoutSec 60
        $responseJson = $response | ConvertTo-Json -Compress -Depth 10
        $Report.status = 'PASS'
        $Report.provider_response_sha256 = Get-TextHash $responseJson
        Write-Host 'PASS: QWEN_ONE_SEGMENT_CALL_COMPLETED'
        Write-Report $Report
    }
    catch {
        Fail-Smoke $Report 'QWEN_CALL_FAILED'
    }
}

function Invoke-LocalTtsSmoke([hashtable]$Report) {
    if (-not $ExecutablePath -or -not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
        Fail-Smoke $Report 'LOCAL_TTS_EXECUTABLE_MISSING'
    }
    if (-not $ModelPath -or -not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) {
        Fail-Smoke $Report 'LOCAL_TTS_MODEL_MISSING'
    }
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $targetOutputRoot = if ($OutputDir) { $OutputDir } else { Join-Path $repoRoot 'data\smoke-artifacts' }
    New-Item -ItemType Directory -Force -Path $targetOutputRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $wavPath = Join-Path $targetOutputRoot "$Provider-smoke-$stamp.wav"

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo.FileName = (Resolve-Path -LiteralPath $ExecutablePath).Path
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardInput = $Provider -eq 'piper'
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.RedirectStandardOutput = $true
    if ($Provider -eq 'piper') {
        [void]$process.StartInfo.ArgumentList.Add('--model')
        [void]$process.StartInfo.ArgumentList.Add((Resolve-Path -LiteralPath $ModelPath).Path)
        [void]$process.StartInfo.ArgumentList.Add('--output_file')
        [void]$process.StartInfo.ArgumentList.Add($wavPath)
    }
    else {
        [void]$process.StartInfo.ArgumentList.Add('--model')
        [void]$process.StartInfo.ArgumentList.Add((Resolve-Path -LiteralPath $ModelPath).Path)
        [void]$process.StartInfo.ArgumentList.Add('--output')
        [void]$process.StartInfo.ArgumentList.Add($wavPath)
        [void]$process.StartInfo.ArgumentList.Add('--seconds')
        [void]$process.StartInfo.ArgumentList.Add([string]$Seconds)
        [void]$process.StartInfo.ArgumentList.Add('--text')
        [void]$process.StartInfo.ArgumentList.Add($Text)
    }
    [void]$process.Start()
    if ($Provider -eq 'piper') {
        $process.StandardInput.WriteLine($Text)
        $process.StandardInput.Close()
    }
    if (-not $process.WaitForExit(90000)) {
        $process.Kill()
        Fail-Smoke $Report 'LOCAL_TTS_TIMEOUT'
    }
    if ($process.ExitCode -ne 0) {
        Fail-Smoke $Report 'LOCAL_TTS_COMMAND_FAILED'
    }

    $wav = Test-WavFile $wavPath 20 60
    if (-not $wav.valid) {
        Fail-Smoke $Report $wav.error_code
    }
    $Report.status = 'PASS'
    $Report.duration_seconds = $wav.duration_seconds
    $Report.wav_format = $wav.format
    $Report.artifact_sha256 = $wav.artifact_sha256
    $Report.artifact_byte_size = $wav.byte_size
    Write-Host 'PASS: LOCAL_TTS_WAV_VALID'
    Write-Report $Report
}

$hanCount = Count-Han $Text
$segments = Get-NonEmptySegments $Text
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
    if ($segments.Count -ne 1) {
        Write-Host 'REFUSED: qwen smoke allows exactly one non-empty segment.'
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

$report = @{
    status = 'RUNNING'
    provider = $Provider
    model = $estimate.model
    region = $Region
    han_count = $hanCount
    segment_count = $segments.Count
    text_sha256 = Get-TextHash $Text
    authorization_id_present = [bool]$AuthorizationId
    cloud_consent_id_present = [bool]$CloudConsentId
    secret_redacted = $true
    full_text_redacted = $true
}

if ($Provider -eq 'qwen') {
    Invoke-QwenSmoke $report
} else {
    $report.duration_seconds = $Seconds
    Invoke-LocalTtsSmoke $report
}
