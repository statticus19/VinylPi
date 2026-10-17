$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $here 'project'
$working = Join-Path $here '_git-upload-work'
$repo = 'https://github.com/statticus19/VinylPi.git'
$branch = 'public-release-review'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git for Windows must be installed (https://git-scm.com/download/win).' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python is needed to run local security checks before pushing.' }
if (Test-Path $working) { throw "Refusing to replace existing directory $working. Move it aside before rerunning." }
Push-Location $source
try {
    python tools/public_content_gate.py
    if ($LASTEXITCODE -ne 0) { throw 'Content gate failed; upload stopped.' }
    python tools/prepublish_check.py
    if ($LASTEXITCODE -ne 0) { throw 'Prepublication check failed; upload stopped.' }
} finally { Pop-Location }
& git clone --no-checkout $repo $working
if ($LASTEXITCODE -ne 0) { throw 'Git clone failed.' }
Push-Location $working
try {
    & git checkout $branch
    if ($LASTEXITCODE -ne 0) { throw 'Review branch checkout failed. It should already exist.' }
    # Review branch starts from original main. Remove tracked files in this temporary clone only.
    & git rm -r -q --ignore-unmatch .
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage old tracked-file removals.' }
    Get-ChildItem -Force $source | Copy-Item -Destination $working -Recurse -Force
    & git add -A
    if ($LASTEXITCODE -ne 0) { throw 'git add failed.' }
    Write-Host '--- Proposed commit changes ---'
    & git status --short
    Write-Host '--- Verifying tracked files ---'
$sensitiveMatches = @(& git ls-files | Select-String '(^|/)(\.env|secrets\.h|token\.json|credentials\.json)$')
    if ($sensitiveMatches.Count -gt 0) { throw 'Sensitive file path found; upload stopped.' }
    & git diff --cached --check
    if ($LASTEXITCODE -ne 0) { throw 'Git whitespace check failed.' }
    & git -c user.name='VinylPi Release Preparation' -c user.email='vinylpi-release@users.noreply.github.com' commit -m 'Prepare audited NAS-free VinylPi public source for review'
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed.' }
    & git push origin $branch
    if ($LASTEXITCODE -ne 0) { throw 'Push failed. Check GitHub authentication.' }
    Write-Host 'Uploaded review branch: https://github.com/statticus19/VinylPi/tree/public-release-review'
} finally { Pop-Location }
