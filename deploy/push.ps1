# Deploy tay từ máy Windows lên VM (docs/deploy.md), dùng khi GitHub Actions không chạy được (vd. org hết hạn mức).
# Làm đúng việc của workflow Deploy: đưa bản `main` trên GitHub lên VM rồi build lại bằng docker compose.
#   powershell -ExecutionPolicy Bypass -File deploy/push.ps1 -VmHost 4.145.113.187
# Giữ nguyên ~/atomesus/.env và ~/atomesus/data trên VM. Build chạy nền trên VM; xem log: ssh ... "tail -f deploy.log"
param(
    [Parameter(Mandatory = $true)][string]$VmHost,
    [string]$User = "azureuser",
    [string]$Key = "$env:USERPROFILE\.ssh\atomesus.pem",
    [string]$Ref = "origin/main"
)
$ErrorActionPreference = "Stop"
$target = "$User@$VmHost"
$archive = Join-Path $env:TEMP "atomesus-deploy.tar.gz"

git fetch -q origin
$commit = git log --oneline -1 $Ref
Write-Host "Deploy $commit -> $VmHost"
git archive --format=tar.gz -o $archive $Ref
if ($LASTEXITCODE) { throw "git archive lỗi" }

scp -i $Key $archive "${target}:~/atomesus-deploy.tar.gz"
if ($LASTEXITCODE) { throw "scp lỗi" }
Remove-Item $archive

# Xóa code cũ (trừ .env, data/) để file đã bị xóa trong repo không còn trên VM, giống rsync --delete của workflow.
$remote = @'
set -e
cd ~/atomesus
find . -mindepth 1 -maxdepth 1 ! -name .env ! -name data -exec rm -rf {} +
tar -xzf ~/atomesus-deploy.tar.gz && rm ~/atomesus-deploy.tar.gz
setsid -f nohup sh -c 'docker compose --env-file .env -f deploy/compose.yml up -d --build --remove-orphans --wait --wait-timeout 900; echo EXIT=$?; docker image prune -f' > ~/deploy.log 2>&1 < /dev/null &
echo "Đang build trên VM (10-25 phút lần đầu, vài phút các lần sau)."
'@
$remote -replace "`r", "" | ssh -i $Key $target "bash -s"
if ($LASTEXITCODE) { throw "ssh lỗi" }
Write-Host "Xem tiến độ: ssh -i $Key $target `"tail -f deploy.log`"   (xong khi thấy EXIT=0)"
