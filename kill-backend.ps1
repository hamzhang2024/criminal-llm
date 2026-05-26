# Kill all backend processes
taskkill /F /IM criminal-llm.exe 2>$null
taskkill /F /IM pythonw.exe 2>$null

# Check port 8080
$proc = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "Port 8080 occupied by PID: $($proc.OwningProcess)"
    Stop-Process -Id $proc.OwningProcess -Force
} else {
    Write-Host "Port 8080 is free"
}

Write-Host "Cleanup done"
