# Deploy motility without local Docker: pack source → S3 → EC2 installs CPU torch.
param(
    [string]$Region = "us-east-1",
    [string]$StackName = "spermmatch-motility",
    [string]$ApiKey = "",
    [string]$InstanceType = "t3.small",
    [switch]$Stop,
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Get-StackOutput([string]$Key) {
    $out = aws cloudformation describe-stacks `
        --stack-name $StackName `
        --region $Region `
        --query "Stacks[0].Outputs[?OutputKey=='$Key'].OutputValue" `
        --output text
    if (-not $out -or $out -eq "None") {
        throw "Stack output $Key not found. Is the stack deployed?"
    }
    return $out.Trim()
}

if ($Stop) {
    $id = Get-StackOutput "InstanceId"
    Write-Host "Stopping $id..."
    aws ec2 stop-instances --instance-ids $id --region $Region | Out-Null
    Write-Host "Stopped. Use -Start before the next demo."
    exit 0
}

if ($Start) {
    $id = Get-StackOutput "InstanceId"
    Write-Host "Starting $id..."
    aws ec2 start-instances --instance-ids $id --region $Region | Out-Null
    $url = Get-StackOutput "MotilityServiceUrl"
    Write-Host "Started. Wait for boot, then: curl $url/health"
    Write-Host "Vercel MOTILITY_SERVICE_URL=$url"
    exit 0
}

if (-not $ApiKey) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $ApiKey = [Convert]::ToBase64String($bytes).TrimEnd("=")
    Write-Host "Generated MOTILITY_SERVICE_API_KEY (save this): $ApiKey"
}

Write-Host "Checking AWS identity..."
aws sts get-caller-identity --region $Region | Out-Null
$AccountId = (aws sts get-caller-identity --query Account --output text --region $Region).Trim()
$Bucket = "spermmatch-motility-src-$AccountId"

Write-Host "Ensuring S3 bucket s3://$Bucket ..."
$ErrorActionPreference = "Continue"
aws s3api head-bucket --bucket $Bucket --region $Region 2>$null | Out-Null
$bucketOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $bucketOk) {
    if ($Region -eq "us-east-1") {
        aws s3api create-bucket --bucket $Bucket --region $Region | Out-Null
    } else {
        aws s3api create-bucket --bucket $Bucket --region $Region `
            --create-bucket-configuration "LocationConstraint=$Region" | Out-Null
    }
}

$TarPath = Join-Path $env:TEMP "motility-src.tgz"
Write-Host "Packing source → $TarPath"
if (Test-Path $TarPath) { Remove-Item $TarPath -Force }
# * Windows tar; include only what the motility service needs.
Push-Location $Root
try {
    tar -czf $TarPath `
        pyproject.toml `
        uv.lock `
        backend `
        ml `
        weights
} finally {
    Pop-Location
}

Write-Host "Uploading to s3://$Bucket/motility-src.tgz ..."
aws s3 cp $TarPath "s3://$Bucket/motility-src.tgz" --region $Region | Out-Null

$Template = Join-Path $PSScriptRoot "template.yaml"
Write-Host "Deploying/updating CloudFormation stack $StackName (replaces instance to re-bootstrap)..."
aws cloudformation deploy `
    --stack-name $StackName `
    --region $Region `
    --template-file $Template `
    --capabilities CAPABILITY_NAMED_IAM `
    --parameter-overrides `
        "ApiKey=$ApiKey" `
        "SourceBucket=$Bucket" `
        "SourceKey=motility-src.tgz" `
        "InstanceType=$InstanceType"

# * Bump a tag so a same-template redeploy still replaces UserData bootstrap when needed.
#   First deploy after Docker path already created resources; this update switches to S3 path.

$url = Get-StackOutput "MotilityServiceUrl"
$id = Get-StackOutput "InstanceId"
Write-Host ""
Write-Host "Stack ready. Instance $id is installing CPU torch (5-15 min). Log: /var/log/motility-bootstrap.log"
Write-Host "Health (when ready):  $url/health"
Write-Host ""
Write-Host "Set on Vercel:"
Write-Host "  MOTILITY_SERVICE_URL=$url"
Write-Host "  MOTILITY_SERVICE_API_KEY=$ApiKey"
Write-Host ""
Write-Host "After demos:  .\infra\motility\deploy.ps1 -Stop"
Write-Host "Before demos: .\infra\motility\deploy.ps1 -Start"
