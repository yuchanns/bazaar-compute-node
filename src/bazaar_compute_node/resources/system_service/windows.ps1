# {{ managed_marker }}
$ErrorActionPreference = 'Stop'

$executable = {{ executable }}
$configPath = {{ config_path }}
$environmentScript = {{ environment_script }}
$logPath = {{ log_path }}
$liveDirectory = {{ live_directory }}
$stagingDirectory = "$liveDirectory.staging"
$previousDirectory = "$liveDirectory.old"

# bcn holds its own files open while it runs, so an upgrade installs beside them
# and the swap happens here, where no bcn process owns the directory yet. The
# whole thing only logs on failure: a rename that does not work is not a reason
# to leave the machine without a node.
try {
    if (-not (Test-Path -LiteralPath $liveDirectory)) {
        # a previous swap was interrupted between the two renames
        if (Test-Path -LiteralPath $stagingDirectory) {
            Move-Item -LiteralPath $stagingDirectory -Destination $liveDirectory
        } elseif (Test-Path -LiteralPath $previousDirectory) {
            Move-Item -LiteralPath $previousDirectory -Destination $liveDirectory
        }
    } elseif (Test-Path -LiteralPath $stagingDirectory) {
        if (Test-Path -LiteralPath $previousDirectory) {
            Remove-Item -LiteralPath $previousDirectory -Recurse -Force
        }
        Move-Item -LiteralPath $liveDirectory -Destination $previousDirectory
        Move-Item -LiteralPath $stagingDirectory -Destination $liveDirectory
    }
} catch {
    $_ | Out-String | Add-Content -LiteralPath $logPath -Encoding utf8
}

try {
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;

public static class BcnNoWindowProcess
{
    public static int Run(string executable, string configPath, string logPath)
    {
        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = executable;
        startInfo.Arguments = "run --config \"" + configPath + "\"";
        startInfo.UseShellExecute = false;
        startInfo.CreateNoWindow = true;
        startInfo.RedirectStandardInput = true;
        startInfo.RedirectStandardOutput = true;
        startInfo.RedirectStandardError = true;

        using (Process process = new Process())
        using (FileStream logStream = new FileStream(
            logPath,
            FileMode.Append,
            FileAccess.Write,
            FileShare.ReadWrite | FileShare.Delete
        ))
        {
            process.StartInfo = startInfo;
            process.Start();
            process.StandardInput.Close();

            object writeLock = new object();
            Task stdoutTask = CopyToLogAsync(
                process.StandardOutput.BaseStream,
                logStream,
                writeLock
            );
            Task stderrTask = CopyToLogAsync(
                process.StandardError.BaseStream,
                logStream,
                writeLock
            );

            process.WaitForExit();
            Task.WaitAll(stdoutTask, stderrTask);
            return process.ExitCode;
        }
    }

    private static async Task CopyToLogAsync(
        Stream source,
        Stream destination,
        object writeLock
    )
    {
        byte[] buffer = new byte[8192];
        int bytesRead;
        while ((bytesRead = await source.ReadAsync(buffer, 0, buffer.Length)) > 0)
        {
            lock (writeLock)
            {
                destination.Write(buffer, 0, bytesRead);
                destination.Flush();
            }
        }
    }
}
'@
} catch {
    $_ | Out-String | Add-Content -LiteralPath $logPath -Encoding utf8
    exit 1
}

if ($environmentScript -and (Test-Path -LiteralPath $environmentScript)) {
    try {
        . $environmentScript
    } catch {
        $_ | Out-String | Add-Content -LiteralPath $logPath -Encoding utf8
        exit 1
    }
}

try {
    $exitCode = [BcnNoWindowProcess]::Run($executable, $configPath, $logPath)
} catch {
    $_ | Out-String | Add-Content -LiteralPath $logPath -Encoding utf8
    exit 1
}
exit $exitCode
