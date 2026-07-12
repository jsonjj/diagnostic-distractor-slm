using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;
using Wayline.Learning.Client;

namespace Wayline.Tests.Learning
{
    public sealed class WaylineForgeProcessHostTests
    {
        private string _temporaryRoot;
        private string _packageRoot;
        private string _runtimeRoot;

        [SetUp]
        public void SetUp()
        {
            _temporaryRoot = Path.Combine(
                Path.GetTempPath(),
                "wayline-unity-host-" + Guid.NewGuid().ToString("N"));
            _packageRoot = Path.Combine(_temporaryRoot, "package");
            _runtimeRoot = Path.Combine(_temporaryRoot, "runtime");
            Directory.CreateDirectory(_packageRoot);
            Directory.CreateDirectory(_runtimeRoot);
            File.WriteAllText(Path.Combine(_packageRoot, "WaylineForge"), string.Empty);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_temporaryRoot))
                Directory.Delete(_temporaryRoot, true);
        }

        [Test]
        public async Task StartupReceiptComesFromPrivateInheritedPipeAndNeverFromArgumentsOrLogs()
        {
            const string token =
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
            var process = FakeWaylineProcess.WithReceipt(
                "{\"host\":\"127.0.0.1\",\"launchToken\":\"" + token +
                "\",\"port\":49152,\"schemaVersion\":\"wayline.startup.v1\"}");
            var launcher = new FakeWaylineProcessLauncher(process);
            using var host = new WaylineForgeProcessHost(
                launcher,
                _ => new ReadyHealthProbe());

            await host.StartAsync(Options(), CancellationToken.None);

            Assert.That(host.State, Is.EqualTo(WaylineForgeHostState.Ready));
            Assert.That(launcher.StartInfo.StartupDescriptor, Is.EqualTo(3));
            Assert.That(launcher.StartInfo.CaptureStandardOutput, Is.False);
            CollectionAssert.Contains(launcher.StartInfo.Arguments, "--startup-fd");
            var descriptorIndex = launcher.StartInfo.Arguments.IndexOf("--startup-fd");
            Assert.That(launcher.StartInfo.Arguments[descriptorIndex + 1], Is.EqualTo("3"));
            CollectionAssert.DoesNotContain(launcher.StartInfo.Arguments, token);
            StringAssert.DoesNotContain(token, host.Connection.ToString());
            Assert.That(process.StandardOutputReadCount, Is.EqualTo(1));

            var transport = new UnityWebRequestTransport(host.Connection);
            using (var request = transport.CreateRequest(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Get,
                    "/v1/runtime-state",
                    null,
                    "session-001")))
            {
                var origins = request.Headers.GetValues("Origin").ToArray();
                Assert.That(
                    origins,
                    Is.EqualTo(new[] { "http://127.0.0.1:49152" }));
                Assert.That(
                    IsBearerHeader(request.Headers.Authorization?.ToString()),
                    Is.True);
                Assert.That(
                    request.Headers.GetValues("X-Wayline-Session-Id").Single() ==
                    "session-001",
                    Is.True);
            }
        }

        [Test]
        public async Task ExitSeventyEightWithoutReceiptIsSafeRuntimeUnavailable()
        {
            var process = FakeWaylineProcess.WithExitCode(78);
            var launcher = new FakeWaylineProcessLauncher(process);
            using var host = new WaylineForgeProcessHost(
                launcher,
                _ => new ReadyHealthProbe());

            await host.StartAsync(Options(), CancellationToken.None);

            Assert.That(host.State, Is.EqualTo(WaylineForgeHostState.RuntimeUnavailable));
            Assert.That(host.Connection, Is.Null);
            Assert.That(host.PublicFailureCode, Is.EqualTo("runtime_unavailable"));
        }

        [Test]
        public void MalformedOrNonLoopbackReceiptFailsClosed()
        {
            const string receipt =
                "{\"host\":\"0.0.0.0\",\"launchToken\":\"" +
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" +
                "\",\"port\":49152,\"schemaVersion\":\"wayline.startup.v1\"}";
            var process = FakeWaylineProcess.WithReceipt(receipt);
            var launcher = new FakeWaylineProcessLauncher(process);
            using var host = new WaylineForgeProcessHost(
                launcher,
                _ => new ReadyHealthProbe());

            Assert.ThrowsAsync<WaylineHostException>(async () =>
                await host.StartAsync(Options(), CancellationToken.None));

            Assert.That(host.State, Is.EqualTo(WaylineForgeHostState.Failed));
            Assert.That(process.TerminatedTree, Is.True);
            Assert.That(host.Connection, Is.Null);
        }

        [Test]
        public async Task DisposingAReadyHostTerminatesTheOwnedProcessTree()
        {
            var process = FakeWaylineProcess.WithReceipt(
                "{\"host\":\"127.0.0.1\",\"launchToken\":\"" +
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" +
                "\",\"port\":49152,\"schemaVersion\":\"wayline.startup.v1\"}");
            var launcher = new FakeWaylineProcessLauncher(process);
            var host = new WaylineForgeProcessHost(
                launcher,
                _ => new ReadyHealthProbe());
            await host.StartAsync(Options(), CancellationToken.None);

            host.Dispose();

            Assert.That(process.TerminatedTree, Is.True);
            Assert.That(process.Disposed, Is.True);
            Assert.That(host.State, Is.EqualTo(WaylineForgeHostState.Stopped));
        }

        [Test]
        public async Task MacLauncherPreservesOneArgumentContainingSpacesOnPrivatePipe()
        {
            var launcher = new MacPosixWaylineProcessLauncher();
            var argument = Path.Combine(_temporaryRoot, "folder with spaces", "runtime root");
            var startInfo = new WaylineProcessStartInfo(
                "/usr/bin/python3",
                _temporaryRoot,
                new[]
                {
                    "-c",
                    "import os,sys; os.write(int(sys.argv[1]), (sys.argv[2]+'\\n').encode())",
                    "3",
                    argument
                },
                3);

            using (var process = launcher.Start(startInfo))
            {
                var line = await process.ReadStartupLineAsync(CancellationToken.None);
                Assert.That(line, Is.EqualTo(argument));
                Assert.That(
                    await process.WaitForExitAsync(CancellationToken.None),
                    Is.Zero);
            }
        }

        [Test]
        public async Task MacStartupReadCancellationUnblocksWhileChildKeepsPipeOpen()
        {
            var launcher = new MacPosixWaylineProcessLauncher();
            var startInfo = new WaylineProcessStartInfo(
                "/usr/bin/python3",
                _temporaryRoot,
                new[]
                {
                    "-c",
                    "import time; time.sleep(30)"
                },
                3);
            var process = launcher.Start(startInfo);

            try
            {
                using var cancellation = new CancellationTokenSource();
                var read = process.ReadStartupLineAsync(cancellation.Token);
                cancellation.CancelAfter(75);

                var completed = await Task.WhenAny(read, Task.Delay(1_500));

                Assert.That(
                    completed,
                    Is.SameAs(read),
                    "A cancelled startup read must not remain blocked in read(2).");
                Assert.CatchAsync<OperationCanceledException>(async () => await read);
            }
            finally
            {
                await TerminateAndReapAsync(process);
            }
        }

        [Test]
        public async Task MacDisposeAllowsTermAwareChildToExitAndReapsIt()
        {
            var launcher = new MacPosixWaylineProcessLauncher();
            var startInfo = new WaylineProcessStartInfo(
                "/usr/bin/python3",
                _temporaryRoot,
                new[]
                {
                    "-c",
                    "import os,signal,sys,time; " +
                    "signal.signal(signal.SIGTERM, lambda signum,frame: sys.exit(23)); " +
                    "os.write(3,b'ready\\n'); time.sleep(30)"
                },
                3);
            var process = launcher.Start(startInfo);

            try
            {
                Assert.That(
                    await process.ReadStartupLineAsync(CancellationToken.None),
                    Is.EqualTo("ready"));

                process.Dispose();

                using var timeout = new CancellationTokenSource(3_000);
                Assert.That(
                    await process.WaitForExitAsync(timeout.Token),
                    Is.EqualTo(23),
                    "Dispose must give SIGTERM a bounded graceful-exit window before SIGKILL.");
            }
            finally
            {
                await TerminateAndReapAsync(process);
            }
        }

        [Test]
        public async Task MacDisposeKillsTermIgnoringChildAfterGraceAndReapsIt()
        {
            var launcher = new MacPosixWaylineProcessLauncher();
            var startInfo = new WaylineProcessStartInfo(
                "/usr/bin/python3",
                _temporaryRoot,
                new[]
                {
                    "-c",
                    "import os,signal,time; " +
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); " +
                    "os.write(3,b'ready\\n'); time.sleep(30)"
                },
                3);
            var process = launcher.Start(startInfo);

            try
            {
                Assert.That(
                    await process.ReadStartupLineAsync(CancellationToken.None),
                    Is.EqualTo("ready"));

                process.Dispose();

                using var timeout = new CancellationTokenSource(3_000);
                Assert.That(
                    await process.WaitForExitAsync(timeout.Token),
                    Is.EqualTo(137),
                    "A SIGTERM-ignoring child must be killed and reaped after the grace period.");
            }
            finally
            {
                await TerminateAndReapAsync(process);
            }
        }

        private static async Task TerminateAndReapAsync(IWaylineProcess process)
        {
            process.TerminateTree();
            await process.WaitForExitAsync(CancellationToken.None);
            process.Dispose();
        }

        private WaylineForgeLaunchOptions Options()
        {
            return new WaylineForgeLaunchOptions(
                _packageRoot,
                _runtimeRoot,
                "http://127.0.0.1:49152");
        }

        private static bool IsBearerHeader(string value)
        {
            if (value == null || value.Length != 71 || !value.StartsWith("Bearer "))
                return false;
            for (var index = 7; index < value.Length; index++)
            {
                var character = value[index];
                if (!char.IsDigit(character) && (character < 'a' || character > 'f'))
                    return false;
            }
            return true;
        }
    }

    internal sealed class FakeWaylineProcessLauncher : IWaylineProcessLauncher
    {
        private readonly IWaylineProcess _process;

        public FakeWaylineProcessLauncher(IWaylineProcess process)
        {
            _process = process;
        }

        public WaylineProcessStartInfo StartInfo { get; private set; }

        public IWaylineProcess Start(WaylineProcessStartInfo startInfo)
        {
            StartInfo = startInfo;
            return _process;
        }
    }

    internal sealed class FakeWaylineProcess : IWaylineProcess
    {
        private readonly Task<string> _startupLine;
        private readonly Task<int> _exitCode;

        private FakeWaylineProcess(Task<string> startupLine, Task<int> exitCode)
        {
            _startupLine = startupLine;
            _exitCode = exitCode;
        }

        public bool TerminatedTree { get; private set; }

        public bool Disposed { get; private set; }

        public int StandardOutputReadCount { get; private set; }

        public static FakeWaylineProcess WithReceipt(string receipt)
        {
            var neverExits = new TaskCompletionSource<int>();
            return new FakeWaylineProcess(Task.FromResult(receipt), neverExits.Task);
        }

        public static FakeWaylineProcess WithExitCode(int exitCode)
        {
            return new FakeWaylineProcess(
                Task.FromResult<string>(null),
                Task.FromResult(exitCode));
        }

        public Task<string> ReadStartupLineAsync(CancellationToken cancellationToken)
        {
            StandardOutputReadCount++;
            return _startupLine;
        }

        public Task<int> WaitForExitAsync(CancellationToken cancellationToken)
        {
            return _exitCode;
        }

        public void TerminateTree()
        {
            TerminatedTree = true;
        }

        public void Dispose()
        {
            Disposed = true;
        }
    }

    internal sealed class ReadyHealthProbe : IWaylineHealthProbe
    {
        public Task CheckReadyAsync(CancellationToken cancellationToken)
        {
            return Task.CompletedTask;
        }
    }
}
