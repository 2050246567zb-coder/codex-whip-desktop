param([string]$Port='COM10',[int]$Seconds=20,[string]$Command='PROBE',[string]$LogPath='')
$ErrorActionPreference='Stop'
$diagPort=[System.IO.Ports.SerialPort]::new($Port,115200)
$diagPort.DtrEnable=$false
$diagPort.RtsEnable=$false
$diagPort.Open()
$capture=[System.Text.StringBuilder]::new()
try {
    Start-Sleep -Milliseconds 1000
    $diagPort.WriteLine($Command)
    $diagUntil=[DateTime]::UtcNow.AddSeconds($Seconds)
    while([DateTime]::UtcNow -lt $diagUntil) {
        $part=$diagPort.ReadExisting()
        if($part) { [void]$capture.Append($part); Write-Output $part }
        Start-Sleep -Milliseconds 100
    }
} finally {
    $diagPort.Close()
    if($LogPath) { [System.IO.File]::WriteAllText($LogPath,$capture.ToString()) }
}
