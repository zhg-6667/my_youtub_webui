$ErrorActionPreference = 'Stop'

$target = Join-Path $PSScriptRoot '..\apps\web\public\fonts\bilibili-cover'
New-Item -ItemType Directory -Path $target -Force | Out-Null

$fonts = @(
  @('buding.ttf', 'c680e31b06410b8fd65b43f0e803ab61ce1bf508.ttf'),
  @('longshu-hongyi.ttf', 'ce4dd95f40874864789de59c7c394dab51451eb9.ttf'),
  @('tangguo.ttf', '0c15adf3e79d9a8129c1ee4d5da033c405a14c0e.ttf'),
  @('qingnian.ttf', '53aa92f4ecad355036578ec5ee6afdd074cfd19c.ttf'),
  @('meihei.ttf', '1bcf6260ca8e2bc8e03cbac3c6e98e517a37d5b9.ttf'),
  @('qingxia.ttf', '78241200cd83b220118fbe98ae54814cf1716ebe.ttf'),
  @('laihu.ttf', '122d833a0a050b64be9f3a2d7a8e4167dc63b30a.ttf'),
  @('dudu.ttf', '222f876e542931f8533d44a2acf12a50c1370be1.ttf'),
  @('qingchun.ttf', 'a669732463acad2c7870de79f5a27a7ac3d162ac.ttf'),
  @('chaohei.ttf', 'e70a32a9ab9d53030cc7dda2458ae6b7e78a74e1.ttf')
)

foreach ($font in $fonts) {
  $destination = Join-Path $target $font[0]
  Invoke-WebRequest -Uri ('https://i0.hdslb.com/bfs/creative/' + $font[1]) -OutFile $destination -UseBasicParsing
  Write-Output (Get-Item -LiteralPath $destination | Select-Object Name, Length)
}
