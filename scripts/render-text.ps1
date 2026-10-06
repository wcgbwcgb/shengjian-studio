param([Parameter(Mandatory=$true)][string]$Manifest)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$spec = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($item in $spec.items) {
    $bitmap = New-Object System.Drawing.Bitmap([int]$spec.width, [int]$spec.height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $format = New-Object System.Drawing.StringFormat
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $font = New-Object System.Drawing.Font('Microsoft YaHei', [single]$item.font_size, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $shadow = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(195, 15, 26, 20))
    $rect = New-Object System.Drawing.RectangleF([single]30, [single]$item.y, [single]($spec.width - 60), [single]$item.height)
    while ($font.Size -gt 16 -and $graphics.MeasureString([string]$item.text, $font, [single]$rect.Width, $format).Height -gt $rect.Height) {
        $smaller = [single]($font.Size * 0.9)
        $font.Dispose()
        $font = New-Object System.Drawing.Font('Microsoft YaHei', $smaller, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    }
    $graphics.FillRectangle($shadow, $rect)
    $graphics.DrawString([string]$item.text, $font, $brush, $rect, $format)
    # Raw BGRA preserves transparency even in minimal video engines without PNG.
    $area = New-Object System.Drawing.Rectangle(0, 0, $bitmap.Width, $bitmap.Height)
    $pixels = $bitmap.LockBits($area, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    try {
        $bytes = New-Object byte[] ($bitmap.Width * $bitmap.Height * 4)
        [System.Runtime.InteropServices.Marshal]::Copy($pixels.Scan0, $bytes, 0, $bytes.Length)
        [System.IO.File]::WriteAllBytes([string]$item.path, $bytes)
    } finally { $bitmap.UnlockBits($pixels) }
    $font.Dispose(); $format.Dispose(); $brush.Dispose(); $shadow.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
}
