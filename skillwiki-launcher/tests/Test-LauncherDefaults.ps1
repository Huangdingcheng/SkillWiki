$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/Start-SkillWikiDemo.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw ($parseErrors | Out-String)
}

# Evaluate only the pure argument builder, never the launcher's top-level code.
$builder = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'New-SkillWikiBackendArguments'
}, $false)
if (-not $builder) { throw 'Missing backend argument builder' }
Invoke-Expression $builder.Extent.Text

$backendParameter = $ast.ParamBlock.Parameters | Where-Object {
    $_.Name.VariablePath.UserPath -eq 'RepositoryBackend'
}
if ($backendParameter.DefaultValue.SafeGetValue() -ne 'git') {
    throw 'Demo launcher must default to Git persistence'
}

$gitArguments = @(New-SkillWikiBackendArguments -Port 8001 -Backend git -StorageDir 'C:\demo workspace\skill-storage-git')
if ($gitArguments[$gitArguments.IndexOf('--llm-retry-count') + 1] -ne '0') {
    throw 'Demo must disable automatic model retries'
}
if ($gitArguments -notcontains '--no-seed-demo' -or $gitArguments -contains '--seed-demo') {
    throw 'Synthetic demo fixtures must be opt-in'
}
if ($gitArguments[$gitArguments.IndexOf('--skill-storage-dir') + 1] -ne '"C:\demo workspace\skill-storage-git"') {
    throw 'Persistent storage path must remain quoted for Start-Process'
}

$memoryArguments = @(New-SkillWikiBackendArguments -Port 8001 -Backend memory -IncludeDemo)
if ($memoryArguments -contains '--skill-storage-dir' -or $memoryArguments -notcontains '--seed-demo') {
    throw 'Explicit memory and seed options must remain available'
}

$missingStorageRejected = $false
try {
    New-SkillWikiBackendArguments -Port 8001 -Backend git | Out-Null
} catch {
    $missingStorageRejected = $true
}
if (-not $missingStorageRejected) { throw 'Git backend must require its durable directory' }
Write-Output 'PASS: launcher syntax, Git default, zero retries, opt-in seeding, quoted path, memory mode'
