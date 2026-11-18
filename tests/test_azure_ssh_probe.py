"""Real child-process tests for SSH stdin isolation and enforced timeout."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def ssh_executable(tmp_path_factory):
    if os.name!='nt' or not shutil.which('powershell'):
        pytest.skip('Windows PowerShell compiler fixture')
    directory=tmp_path_factory.mktemp('ssh fixture with spaces')
    executable=directory/'fake ssh.exe'
    source='using System;\nusing System.IO;\nusing System.Diagnostics;\nusing System.Threading;\npublic static class FakeSsh {\n public static int Main(string[] args) {\n  File.WriteAllText(Environment.GetEnvironmentVariable("CARDS_SSH_PROBE_PID_FILE"),Process.GetCurrentProcess().Id.ToString());\n  if (Array.IndexOf(args,"-n")<0 || Array.IndexOf(args,"-T")<0 || Array.IndexOf(args,"fake key with spaces")<0) return 77;\n  if (Console.In.Read()!=-1) return 78;\n  Console.WriteLine("private-fake-stdout");\n  var mode=Environment.GetEnvironmentVariable("CARDS_SSH_PROBE_MODE");\n  if (mode=="timeout") Thread.Sleep(30000);\n  return mode=="failure" ? 23 : 0;\n }\n}'
    escaped=str(executable).replace("'","''")
    command="$ErrorActionPreference='Stop'\nAdd-Type -OutputAssembly '"+escaped+"' -OutputType ConsoleApplication -TypeDefinition @'\n"+source+"\n'@\n"
    compiled=subprocess.run([shutil.which('powershell'),'-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stderr
    # Exclude one-time scanning/JIT of a newly compiled unsigned fixture from the
    # probe deadline measurement; the production probe uses installed OpenSSH.
    env={**os.environ,'CARDS_SSH_PROBE_MODE':'success','CARDS_SSH_PROBE_PID_FILE':str(directory/'warm.pid')}
    warm=subprocess.run([str(executable),'-n','-T','-i','fake key with spaces'],input='',capture_output=True,text=True,env=env,timeout=60)
    assert warm.returncode==0,warm.stderr
    return executable


@pytest.mark.parametrize('mode',['success','failure','timeout'])
def test_ssh_probe_closes_stdin_and_bounds_only_its_child(tmp_path,mode,ssh_executable):
    directory=tmp_path/'ssh test folder'
    directory.mkdir()
    executable=str(ssh_executable).replace("'","''")
    pid_file=str(directory/'probe.pid').replace("'","''")
    script=r'''
. ./infra/azure/common.ps1
$fakePath='__EXECUTABLE__'
$env:CARDS_SSH_PROBE_MODE='MODE'
$env:CARDS_SSH_PROBE_PID_FILE='__PID_PATH__'
function Get-Command {param($Name,$CommandType,$ErrorAction); return [pscustomobject]@{Source=$fakePath}}
function Get-SshOptions {return @('-i','fake key with spaces')}
$script:Cfg=[pscustomobject]@{AdminUsername='azureuser'}
$timer=[Diagnostics.Stopwatch]::StartNew()
$ready=Test-SshReady '192.0.2.1' 1
if ($ready -ne ($env:CARDS_SSH_PROBE_MODE -eq 'success')) {throw 'Incorrect readiness result (stdin/argument/exit-code handling)'}
if ($timer.Elapsed.TotalSeconds -gt 8) {throw 'SSH attempt exceeded its hard timeout'}
$probeIdFromFile=[int](Get-Content -LiteralPath $env:CARDS_SSH_PROBE_PID_FILE)
$alive=$false
try {$child=[Diagnostics.Process]::GetProcessById($probeIdFromFile); $alive=-not $child.HasExited; $child.Dispose()} catch {}
if ($alive) {throw 'Timed-out readiness child was left running'}
exit 0
'''.replace('__EXECUTABLE__',executable).replace('__PID_PATH__',pid_file).replace("'MODE'",repr(mode))
    result=subprocess.run([shutil.which('powershell'),'-NoProfile','-NonInteractive','-Command',script],
                          cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'private-fake-stdout' not in result.stdout


def test_wait_ssh_retries_bounded_probe_instead_of_blocking_remote_command():
    exe=shutil.which('powershell') or shutil.which('pwsh')
    if not exe:
        pytest.skip('PowerShell unavailable')
    script=r'''
. ./infra/azure/common.ps1
$script:attempts=0
function Test-SshReady($Address,$TimeoutSeconds) {
 if ($TimeoutSeconds -gt 20 -or $TimeoutSeconds -lt 1) {throw 'Unbounded timeout'}
 $script:attempts++
 return $script:attempts -eq 3
}
function Invoke-Remote {throw 'Legacy blocking readiness call was used'}
function Start-Sleep {}
Wait-Ssh '192.0.2.1'
if ($script:attempts -ne 3) {throw 'Readiness retry behavior changed'}
'''
    result=subprocess.run([exe,'-NoProfile','-NonInteractive','-Command',script],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
