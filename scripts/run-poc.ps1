param(
  [ValidateSet("fake", "vieneu", "qwen")]
  [string]$Provider = "fake",
  [switch]$AllowLocalModel,
  [switch]$AllowPaidProvider,
  [string]$AuthorizationId = "",
  [string]$CloudConsentId = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DataRoot = Join-Path $RepoRoot "data"

if ($Provider -eq "fake") {
  & $Python -c "from pathlib import Path; from app.modules.poc.report import build_fake_poc_report, write_poc_report; saved = write_poc_report(build_fake_poc_report(), Path(r'$DataRoot')); print('POC fake report: ' + saved['relative_path']); print('POC fake sha256: ' + saved['sha256'])"
  exit $LASTEXITCODE
}

if ($Provider -eq "vieneu") {
  if (-not $AllowLocalModel) {
    Write-Output "POC_LOCAL_MODEL_REQUIRED"
    exit 2
  }
  & $Python (Join-Path $PSScriptRoot "poc\vieneu_probe.py") --allow-local-model --model-path (Join-Path $RepoRoot "models\vieneu\model.bin") --executable (Join-Path $RepoRoot "models\vieneu\vieneu.exe") --output-dir (Join-Path $DataRoot "projects\poc\vieneu")
  exit $LASTEXITCODE
}

if ($Provider -eq "qwen") {
  if (-not $AllowPaidProvider -or [string]::IsNullOrWhiteSpace($AuthorizationId) -or [string]::IsNullOrWhiteSpace($CloudConsentId)) {
    Write-Output "POC_CLOUD_CONSENT_REQUIRED"
    exit 2
  }
  $confirmation = Read-Host "Estimated paid Qwen POC cost: <= 1000 VND. Type RUN_QWEN_POC to continue"
  if ($confirmation -ne "RUN_QWEN_POC") {
    Write-Output "POC_QWEN_CANCELLED"
    exit 2
  }
  & $Python (Join-Path $PSScriptRoot "poc\qwen_probe.py") --database-path (Join-Path $DataRoot "studio.sqlite3") --data-root $DataRoot --project-id "poc" --provider-profile-id "qwen" --authorization-id $AuthorizationId --cloud-consent-id $CloudConsentId --api-key $env:STUDIO_QWEN_API_KEY
  exit $LASTEXITCODE
}
