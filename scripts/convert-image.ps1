param([Parameter(Mandatory=$true)][string]$Source, [Parameter(Mandatory=$true)][string]$Target)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$picture = [System.Drawing.Image]::FromFile($Source)
try {
    $format = if ([System.IO.Path]::GetExtension($Target) -eq '.png') { [System.Drawing.Imaging.ImageFormat]::Png } else { [System.Drawing.Imaging.ImageFormat]::Bmp }
    $picture.Save($Target, $format)
} finally { $picture.Dispose() }
