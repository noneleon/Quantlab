// Starts the real QuantLab paper OMS + LEAN gateway (tests/fixtures/gateway_harness.py, synthetic SIMULATED quotes)
// so the C# brokerage is tested against the actual Python side of the protocol, not a stand-in.

using System;
using System.Diagnostics;
using System.IO;

namespace QuantLab.Brokerages.Tests
{
    public sealed class GatewayHarness : IDisposable
    {
        private readonly Process _process;

        public GatewayHarness()
        {
            Workdir = Directory.CreateTempSubdirectory("quantlab-gw-").FullName;
            var root = RepoRoot();
            var uv = Environment.GetEnvironmentVariable("QUANTLAB_UV") ?? "uv";
            var psi = new ProcessStartInfo(uv, $"run --no-sync python tests/fixtures/gateway_harness.py \"{Workdir}\"")
            {
                WorkingDirectory = root,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
            };
            psi.Environment["QUANTLAB_HOME"] = Path.Combine(Workdir, "home");
            _process = Process.Start(psi) ?? throw new InvalidOperationException("could not start the gateway harness");
            _process.ErrorDataReceived += (_, _) => { };
            _process.BeginErrorReadLine();
            var deadline = DateTime.UtcNow.AddSeconds(60);
            while (DateTime.UtcNow < deadline)
            {
                var line = _process.StandardOutput.ReadLine();
                if (line == null) break;
                if (line.StartsWith("READY ", StringComparison.Ordinal))
                {
                    RunFile = line.Substring(6).Trim();
                    return;
                }
            }
            Dispose();
            throw new InvalidOperationException("the gateway harness did not start");
        }

        public string Workdir { get; }

        public string RunFile { get; } = "";

        public GatewayEndpoint Endpoint => GatewayEndpoint.FromRunFile(RunFile);

        public void EngageKillSwitch()
        {
            _process.StandardInput.WriteLine("kill");
            _process.StandardInput.Flush();
            while (_process.StandardOutput.ReadLine() is { } line && line != "KILLED") { }
        }

        private static string RepoRoot()
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null && !File.Exists(Path.Combine(dir.FullName, "pyproject.toml"))) dir = dir.Parent;
            return dir?.FullName ?? throw new InvalidOperationException("repository root (pyproject.toml) not found");
        }

        public void Dispose()
        {
            try
            {
                if (!_process.HasExited)
                {
                    _process.StandardInput.WriteLine("quit");
                    _process.StandardInput.Close();
                    if (!_process.WaitForExit(10_000)) _process.Kill(entireProcessTree: true);
                }
            }
            catch (InvalidOperationException) { }
            _process.Dispose();
            try { Directory.Delete(Workdir, recursive: true); } catch (IOException) { }
        }
    }
}
