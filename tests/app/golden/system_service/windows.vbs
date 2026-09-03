' Managed by bazaar-compute-node.
Option Explicit

Dim shell
Dim command
Dim exitCode

Set shell = CreateObject("WScript.Shell")
command = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ""/home/test-user/.bcn/bcn-run.ps1"""
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
