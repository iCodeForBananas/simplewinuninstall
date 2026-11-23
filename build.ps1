<#
    Build SimpleWinUninstall into a single .exe, then prove it runs.

    One command, from anywhere:

        powershell -ExecutionPolicy Bypass -File "<repo>\build.ps1"

    It will, in order:
      1. check Python and tkinter, and stop with an exact fix if either is missing
      2. run the safety self-test, and refuse to build if it fails
      3. install/upgrade PyInstaller
      4. package dist\SimpleWinUninstall.exe
      5. launch the exe, confirm a window actually appears, screenshot it,
         confirm it wrote a log, and close it again
      6. print a REPORT block you can copy back verbatim

    Switches:
      -SkipSelfTest   skip step 2 (not recommended)
      -NoSmokeTest    skip step 5 (use on a machine with no interactive desktop)
#>

[CmdletBinding()]
param(
    [switch]$SkipSelfTest,
    [switch]$NoSmokeTest,
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$report = New-Object System.Collections.Generic.List[string]
function Say($text, $colour = "Gray") { Write-Host $text -ForegroundColor $colour }
function Note($text) { $report.Add($text) | Out-Null }

function Fail($problem, $fix) {
    Say "`nBLOCKED: $problem" "Red"
    Say "`nRun this, then run build.ps1 again:" "Yellow"
    Say "    $fix" "White"
    Note "RESULT: blocked - $problem"
    Note "FIX: $fix"
    Say "`n--- REPORT ---" "Cyan"
    $report | ForEach-Object { Write-Host $_ }
    exit 1
}

Say "SimpleWinUninstall build" "Cyan"
Say "Repo: $PSScriptRoot"
Note "repo: $PSScriptRoot"
Note "windows: $([System.Environment]::OSVersion.VersionString) / $env:PROCESSOR_ARCHITECTURE"

# -- 1. Python -------------------------------------------------------------
$py = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $py) {
    Fail "Python is not on PATH." `
         "winget install --id Python.Python.3.12 --source winget   (then open a NEW PowerShell window)"
}
$pyExe = $py.Source

# A Microsoft Store stub shim reports as python.exe but cannot run -c.
$pyVersion = & $pyExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $pyVersion) {
    Fail "'$pyExe' is on PATH but is not a working Python (likely the Microsoft Store stub)." `
         "winget install --id Python.Python.3.12 --source winget   (then open a NEW PowerShell window)"
}
$pyBits = & $pyExe -c "import struct; print(struct.calcsize('P') * 8)"
Say "Python $pyVersion ($pyBits-bit) at $pyExe" "Green"
Note "python: $pyVersion ($pyBits-bit) $pyExe"

$verOk = & $pyExe -c "import sys; print(1 if sys.version_info[:2] >= (3, 9) else 0)"
if ($verOk -ne "1") {
    Fail "Python $pyVersion is too old; 3.9 or newer is required." `
         "winget install --id Python.Python.3.12 --source winget"
}

# -- 2. tkinter ------------------------------------------------------------
$tk = & $pyExe -c "import tkinter; print(tkinter.TkVersion)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail "This Python has no tkinter, so the GUI cannot be built." `
         "winget install --id Python.Python.3.12 --source winget   (the python.org build includes tkinter; a stripped or conda build may not)"
}
Say "tkinter $tk" "Green"
Note "tkinter: $tk"

# -- 3. Self-test ----------------------------------------------------------
if ($SkipSelfTest) {
    Say "`nSkipping the self-test (-SkipSelfTest)." "Yellow"
    Note "selftest: SKIPPED"
} else {
    Say "`nRunning the safety self-test..." "Cyan"
    $selfTestOut = & $pyExe run.py --selftest 2>&1
    $selfTestRc = $LASTEXITCODE
    $summary = ($selfTestOut | Select-String -Pattern "checks passed|checks FAILED" |
                Select-Object -Last 1).Line
    if ($selfTestRc -ne 0) {
        # Print the whole thing: the failure detail (which paths, and why) is
        # on the lines after the FAIL summary, and filtering hid it.
        Say "`n----- full self-test output -----" "Red"
        $selfTestOut | ForEach-Object { Write-Host $_ }
        Say "----- end self-test output -----`n" "Red"
        Note "selftest: FAILED ($summary)"
        $selfTestOut | Select-String -Pattern "FAIL|->" | ForEach-Object {
            Note "  $($_.Line.Trim())"
        }
        Fail "The safety self-test failed, so nothing was built." `
             "$pyExe run.py --selftest    (read the FAIL lines, fix, then rebuild)"
    }
    Say $summary "Green"
    Note "selftest: PASSED - $summary"
}

# -- 4. PyInstaller --------------------------------------------------------
Say "`nInstalling/upgrading PyInstaller..." "Cyan"
& $pyExe -m pip install --upgrade --disable-pip-version-check pyinstaller
if ($LASTEXITCODE -ne 0) {
    Fail "pip could not install PyInstaller (no network, or a proxy/SSL block)." `
         "$pyExe -m pip install --upgrade pyinstaller    (read pip's error; add --proxy if you are behind one)"
}
$piVersion = & $pyExe -m PyInstaller --version 2>$null
Say "PyInstaller $piVersion" "Green"
Note "pyinstaller: $piVersion"

# -- 5. Package ------------------------------------------------------------
Say "`nPackaging (this takes a minute)..." "Cyan"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $pyExe -m PyInstaller winuninstall.spec --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) {
    Fail "PyInstaller exited with code $LASTEXITCODE." `
         "$pyExe -m PyInstaller winuninstall.spec --noconfirm    (run it directly to see the full error)"
}

$exe = Join-Path $PSScriptRoot "dist\SimpleWinUninstall.exe"
if (-not (Test-Path $exe)) {
    Fail "The build finished but dist\SimpleWinUninstall.exe is missing." `
         "$pyExe -m PyInstaller winuninstall.spec --noconfirm"
}
$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Say "`nBuilt: $exe ($sizeMb MB)" "Green"
Note "exe: $exe ($sizeMb MB)"

# -- 6. Smoke test: does the window actually appear? ----------------------
if ($NoSmokeTest) {
    Say "Skipping the launch test (-NoSmokeTest)." "Yellow"
    Note "smoketest: SKIPPED"
} else {
    Say "`nLaunching it to confirm the GUI renders..." "Cyan"

    $logDir = Join-Path $env:LOCALAPPDATA "SimpleWinUninstall\logs"
    $before = @()
    if (Test-Path $logDir) {
        $before = @(Get-ChildItem $logDir -Filter *.log -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty FullName)
    }

    # A one-file PyInstaller build extracts itself and runs the real app in a
    # CHILD process, so the handle Start-Process hands back can exit while the
    # app is still coming up. Look for the window by process name instead of
    # trusting that one handle.
    $procName = [System.IO.Path]::GetFileNameWithoutExtension($exe)
    Start-Process -FilePath $exe | Out-Null

    $title = ""
    $live = $null
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        $live = @(Get-Process -Name $procName -ErrorAction SilentlyContinue)
        $withWindow = $live | Where-Object { $_.MainWindowTitle } |
                      Select-Object -First 1
        if ($withWindow) { $title = $withWindow.MainWindowTitle; break }
        if ($live.Count -eq 0 -and $i -ge 2) { break }
    }

    if (-not $live -or $live.Count -eq 0) {
        Note "smoketest: FAILED - no $procName process is running"
        Fail "The exe was built but no process stayed alive, so it never opened a window." `
             "$pyExe run.py    (run from source to see the traceback the windowed exe swallows)"
    }

    # Screenshot FIRST, so that whatever is on screen is captured whether
    # this turns out to be the app or a crash dialog.
    #
    # PowerShell is not DPI-aware, so on a scaled display it is told the
    # screen is smaller than it is and captures only the top-left part of
    # it - which once made a correctly centred window look cut off. Declare
    # DPI awareness first, then capture just the app's own window.
    $shot = Join-Path $PSScriptRoot "dist\smoke-test.png"
    try {
        try {
            Add-Type -Namespace SWC -Name Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
'@
            [SWC.Win]::SetProcessDPIAware() | Out-Null
        } catch { }

        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        try {
            $appProc = Get-Process -Name $procName -ErrorAction SilentlyContinue |
                       Where-Object { $_.MainWindowHandle -ne 0 } |
                       Select-Object -First 1
            if ($appProc) {
                $rect = New-Object SWC.Win+RECT
                if ([SWC.Win]::GetWindowRect($appProc.MainWindowHandle, [ref]$rect)) {
                    $w = $rect.Right - $rect.Left
                    $h = $rect.Bottom - $rect.Top
                    if ($w -gt 50 -and $h -gt 50) {
                        $bounds = New-Object System.Drawing.Rectangle $rect.Left, $rect.Top, $w, $h
                    }
                }
            }
        } catch { }

        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $gfx = [System.Drawing.Graphics]::FromImage($bmp)
        $gfx.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
        $gfx.Dispose(); $bmp.Dispose()
        Say "Screenshot: $shot" "Green"
        Note "screenshot: $shot"
    } catch {
        Say "Could not capture a screenshot: $($_.Exception.Message)" "Yellow"
        Note "screenshot: not captured - $($_.Exception.Message)"
    }

    # A window is not enough. When the app dies, PyInstaller shows its own
    # "Unhandled exception in script" dialog - which is also a window, with a
    # title, owned by the same process. Treating that as success is how a
    # crash gets reported as a pass. Require the app's own title.
    $looksLikeCrash = $false
    foreach ($needle in @("Unhandled exception", "Fatal Error",
                          "Failed to execute", "Traceback", "Error")) {
        if ($title -like "*$needle*") { $looksLikeCrash = $true }
    }

    if ($title -like "SimpleWinUninstall*" -and -not $looksLikeCrash) {
        Say "Window is up: `"$title`"" "Green"
        Note "smoketest: PASSED - window title `"$title`""
    } elseif ($title) {
        Note "smoketest: FAILED - the only window was `"$title`""
        Say "`nThat window is not the app: `"$title`"" "Red"
        if (Test-Path $shot) { Say "The screenshot shows it: $shot" "Yellow" }
        Fail "The exe launched but opened an error dialog instead of the app window: `"$title`"" `
             "$pyExe run.py    (run from source to see the full traceback)"
    } else {
        Note "smoketest: FAILED - process alive but no window appeared in 30s"
        Fail "The exe is running but never opened a window." `
             "$pyExe run.py    (run from source to see what it is waiting on)"
    }

    # Close it the way a user would, then make sure nothing is left behind.
    foreach ($p in @(Get-Process -Name $procName -ErrorAction SilentlyContinue)) {
        try { $p.CloseMainWindow() | Out-Null } catch { }
    }
    Start-Sleep -Seconds 3
    foreach ($p in @(Get-Process -Name $procName -ErrorAction SilentlyContinue)) {
        try { $p.Kill() } catch { }
    }
    Say "Closed." "Green"

    # Did it write a log? That proves the app initialised, not just a window.
    $after = @()
    if (Test-Path $logDir) {
        $after = @(Get-ChildItem $logDir -Filter *.log -ErrorAction SilentlyContinue |
                   Sort-Object LastWriteTime -Descending)
    }
    $fresh = $after | Where-Object { $before -notcontains $_.FullName } | Select-Object -First 1
    if ($fresh) {
        Say "`nIt wrote a log: $($fresh.FullName)" "Green"
        Note "log: $($fresh.FullName)"
        Get-Content $fresh.FullName -TotalCount 4 | ForEach-Object { Say "    $_" }
    } else {
        Say "No new log file appeared in $logDir." "Yellow"
        Note "log: none written (expected one in $logDir)"
    }
}

# -- 7. Installer ---------------------------------------------------------
if ($Installer) {
    Say "`nBuilding the installer..." "Cyan"

    # Any installed major version, not just 6: newest first.
    $iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $roots = @(${env:ProgramFiles(x86)}, $env:ProgramFiles,
                   (Join-Path $env:LOCALAPPDATA "Programs")) | Where-Object { $_ }
        $found = foreach ($root in $roots) {
            Get-ChildItem -Path $root -Directory -Filter "Inno Setup*" -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName "ISCC.exe" } |
                Where-Object { Test-Path $_ }
        }
        $pick = $found | Sort-Object -Descending | Select-Object -First 1
        if ($pick) { $iscc = Get-Command $pick }
    }
    if (-not $iscc) {
        Fail "Inno Setup is not installed, so the installer cannot be built. (dist\SimpleWinUninstall.exe was built fine and works on its own.)" `
             "winget install --id JRSoftware.InnoSetup --source winget   (then run: .\build.ps1 -Installer)"
    }

    # Keep the installer's version in step with the app's.
    $appVersion = & $pyExe -c "import winuninstall; print(winuninstall.__version__)"
    if (-not $appVersion) { $appVersion = "0.1.0" }
    Say "Version $appVersion"

    & $iscc.Source "/DAppVersion=$appVersion" "installer\SimpleWinUninstall.iss"
    if ($LASTEXITCODE -ne 0) {
        Fail "Inno Setup exited with code $LASTEXITCODE." `
             "$($iscc.Source) installer\SimpleWinUninstall.iss    (run it directly for the full error)"
    }

    $setup = Join-Path $PSScriptRoot "dist\SimpleWinUninstallSetup.exe"
    if (-not (Test-Path $setup)) {
        Fail "Inno Setup finished but dist\SimpleWinUninstallSetup.exe is missing." `
             "$($iscc.Source) installer\SimpleWinUninstall.iss"
    }
    $setupMb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Say "`nInstaller: $setup ($setupMb MB)" "Green"
    Note "installer: $setup ($setupMb MB), version $appVersion"
}

# -- Report ---------------------------------------------------------------
Note "RESULT: success"
Say "`n--- REPORT (copy this back) ---" "Cyan"
$report | ForEach-Object { Write-Host $_ }
Say "`nThe exe is standalone: copy dist\SimpleWinUninstall.exe anywhere and double-click it." "Green"
Say "SmartScreen will warn once about an unsigned binary - More info, then Run anyway." "Gray"
