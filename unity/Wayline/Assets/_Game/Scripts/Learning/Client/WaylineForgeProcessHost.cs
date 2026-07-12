using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Wayline.Learning.Client
{
    public enum WaylineForgeHostState
    {
        Stopped,
        Starting,
        Ready,
        RuntimeUnavailable,
        Failed
    }

    public sealed class WaylineForgeLaunchOptions
    {
        public WaylineForgeLaunchOptions(
            string packageRoot,
            string runtimeRoot,
            string unityOrigin)
        {
            PackageRoot = RequireAbsoluteDirectory(packageRoot, nameof(packageRoot));
            RuntimeRoot = RequireAbsoluteDirectory(runtimeRoot, nameof(runtimeRoot));
            ExecutablePath = Path.Combine(PackageRoot, "WaylineForge");
            if (!File.Exists(ExecutablePath))
                throw new ArgumentException("WaylineForge executable is unavailable", nameof(packageRoot));
            if (!Uri.TryCreate(unityOrigin, UriKind.Absolute, out var origin) ||
                origin.Scheme != Uri.UriSchemeHttp ||
                origin.Host != "127.0.0.1" ||
                !string.IsNullOrEmpty(origin.AbsolutePath.Trim('/')))
            {
                throw new ArgumentException("unityOrigin must be canonical loopback HTTP", nameof(unityOrigin));
            }
            UnityOrigin = origin.GetLeftPart(UriPartial.Authority);
        }

        public string PackageRoot { get; }

        public string RuntimeRoot { get; }

        public string ExecutablePath { get; }

        public string UnityOrigin { get; }

        private static string RequireAbsoluteDirectory(string value, string name)
        {
            if (string.IsNullOrEmpty(value) || !Path.IsPathRooted(value))
                throw new ArgumentException(name + " must be absolute", name);
            var normalized = Path.GetFullPath(value);
            if (!Directory.Exists(normalized))
                throw new ArgumentException(name + " is unavailable", name);
            return normalized;
        }
    }

    public sealed class WaylineProcessStartInfo
    {
        public WaylineProcessStartInfo(
            string executablePath,
            string workingDirectory,
            IEnumerable<string> arguments,
            int startupDescriptor)
        {
            ExecutablePath = executablePath ?? throw new ArgumentNullException(nameof(executablePath));
            WorkingDirectory = workingDirectory ?? throw new ArgumentNullException(nameof(workingDirectory));
            Arguments = new ReadOnlyCollection<string>(
                new List<string>(arguments ?? throw new ArgumentNullException(nameof(arguments))));
            if (startupDescriptor < 3 || startupDescriptor > 255)
                throw new ArgumentOutOfRangeException(nameof(startupDescriptor));
            StartupDescriptor = startupDescriptor;
        }

        public string ExecutablePath { get; }

        public string WorkingDirectory { get; }

        public IList<string> Arguments { get; }

        public int StartupDescriptor { get; }

        public bool CaptureStandardOutput => false;
    }

    public interface IWaylineProcessLauncher
    {
        IWaylineProcess Start(WaylineProcessStartInfo startInfo);
    }

    public interface IWaylineProcess : IDisposable
    {
        Task<string> ReadStartupLineAsync(CancellationToken cancellationToken);

        Task<int> WaitForExitAsync(CancellationToken cancellationToken);

        void TerminateTree();
    }

    public sealed class WaylineForgeConnection
    {
        internal WaylineForgeConnection(
            string host,
            int port,
            string launchToken,
            string unityOrigin)
        {
            BaseUri = new Uri($"http://{host}:{port}", UriKind.Absolute);
            LaunchToken = launchToken;
            UnityOrigin = unityOrigin;
        }

        public Uri BaseUri { get; }

        public string UnityOrigin { get; }

        internal string LaunchToken { get; }

        public override string ToString()
        {
            return $"WaylineForgeConnection(BaseUri={BaseUri}, LaunchToken=<redacted>)";
        }
    }

    public sealed class WaylineHostException : Exception
    {
        public WaylineHostException(string code)
            : base(code)
        {
            Code = code;
        }

        public string Code { get; }

        public override string ToString()
        {
            return $"WaylineHostException(Code={Code})";
        }
    }

    public sealed class WaylineForgeProcessHost : IDisposable
    {
        private const int StartupTimeoutMilliseconds = 10_000;
        private const int StartupDescriptor = 3;

        private readonly IWaylineProcessLauncher _launcher;
        private readonly Func<WaylineForgeConnection, IWaylineHealthProbe> _healthProbeFactory;
        private IWaylineProcess _process;
        private bool _disposed;

        public WaylineForgeProcessHost(
            IWaylineProcessLauncher launcher,
            Func<WaylineForgeConnection, IWaylineHealthProbe> healthProbeFactory)
        {
            _launcher = launcher ?? throw new ArgumentNullException(nameof(launcher));
            _healthProbeFactory = healthProbeFactory ??
                throw new ArgumentNullException(nameof(healthProbeFactory));
        }

        public WaylineForgeHostState State { get; private set; } =
            WaylineForgeHostState.Stopped;

        public WaylineForgeConnection Connection { get; private set; }

        public string PublicFailureCode { get; private set; }

        public async Task StartAsync(
            WaylineForgeLaunchOptions options,
            CancellationToken cancellationToken)
        {
            if (_disposed)
                throw new ObjectDisposedException(nameof(WaylineForgeProcessHost));
            if (State != WaylineForgeHostState.Stopped)
                throw new InvalidOperationException("Wayline Forge host is already started");
            if (options == null)
                throw new ArgumentNullException(nameof(options));

            State = WaylineForgeHostState.Starting;
            PublicFailureCode = null;
            var startInfo = new WaylineProcessStartInfo(
                options.ExecutablePath,
                options.PackageRoot,
                new[]
                {
                    "--runtime-root", options.RuntimeRoot,
                    "--unity-origin", options.UnityOrigin,
                    "--startup-fd", StartupDescriptor.ToString()
                },
                StartupDescriptor);
            try
            {
                _process = _launcher.Start(startInfo);
                if (_process == null)
                    throw new WaylineHostException("startup_failed");
                using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(
                    cancellationToken))
                {
                    timeout.CancelAfter(StartupTimeoutMilliseconds);
                    var line = await _process.ReadStartupLineAsync(timeout.Token);
                    if (line == null)
                    {
                        var exitCode = await _process.WaitForExitAsync(timeout.Token);
                        if (exitCode == 78)
                        {
                            State = WaylineForgeHostState.RuntimeUnavailable;
                            PublicFailureCode = "runtime_unavailable";
                            _process.Dispose();
                            _process = null;
                            return;
                        }
                        throw new WaylineHostException("startup_failed");
                    }

                    var connection = ParseReceipt(line, options.UnityOrigin);
                    var healthProbe = _healthProbeFactory(connection);
                    if (healthProbe == null)
                        throw new WaylineHostException("health_unavailable");
                    await healthProbe.CheckReadyAsync(timeout.Token);
                    Connection = connection;
                    State = WaylineForgeHostState.Ready;
                }
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                FailAndTerminate();
                throw new WaylineHostException("startup_timeout");
            }
            catch (OperationCanceledException)
            {
                StopOwnedProcess();
                State = WaylineForgeHostState.Stopped;
                throw;
            }
            catch (WaylineHostException)
            {
                FailAndTerminate();
                throw;
            }
            catch (Exception)
            {
                FailAndTerminate();
                throw new WaylineHostException("startup_failed");
            }
        }

        public void Dispose()
        {
            if (_disposed)
                return;
            _disposed = true;
            StopOwnedProcess();
            Connection = null;
            State = WaylineForgeHostState.Stopped;
        }

        private void FailAndTerminate()
        {
            StopOwnedProcess();
            Connection = null;
            PublicFailureCode = "runtime_unavailable";
            State = WaylineForgeHostState.Failed;
        }

        private void StopOwnedProcess()
        {
            if (_process == null)
                return;
            try
            {
                _process.TerminateTree();
            }
            catch (Exception)
            {
            }
            _process.Dispose();
            _process = null;
        }

        private static WaylineForgeConnection ParseReceipt(
            string line,
            string unityOrigin)
        {
            if (line.Length > 4096)
                throw new WaylineHostException("startup_receipt_invalid");
            try
            {
                var token = JToken.Parse(
                    line,
                    new JsonLoadSettings
                    {
                        DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error
                    });
                if (token.Type != JTokenType.Object)
                    throw new JsonSerializationException("receipt must be an object");
                var value = (JObject)token;
                var expected = new HashSet<string>(StringComparer.Ordinal)
                {
                    "host", "launchToken", "port", "schemaVersion"
                };
                foreach (var property in value.Properties())
                {
                    if (!expected.Remove(property.Name))
                        throw new JsonSerializationException("receipt member is invalid");
                }
                if (expected.Count != 0 ||
                    value["host"].Type != JTokenType.String ||
                    value["launchToken"].Type != JTokenType.String ||
                    value["port"].Type != JTokenType.Integer ||
                    value["schemaVersion"].Type != JTokenType.String)
                {
                    throw new JsonSerializationException("receipt shape is invalid");
                }
                var host = (string)value["host"];
                var launchToken = (string)value["launchToken"];
                var port = (int)value["port"];
                if (host != "127.0.0.1" ||
                    (string)value["schemaVersion"] != "wayline.startup.v1" ||
                    port < 1 || port > 65535 ||
                    !Regex.IsMatch(
                        launchToken ?? string.Empty,
                        "^[0-9a-f]{64}$",
                        RegexOptions.CultureInvariant))
                {
                    throw new JsonSerializationException("receipt values are invalid");
                }
                return new WaylineForgeConnection(host, port, launchToken, unityOrigin);
            }
            catch (JsonException)
            {
                throw new WaylineHostException("startup_receipt_invalid");
            }
            catch (OverflowException)
            {
                throw new WaylineHostException("startup_receipt_invalid");
            }
        }
    }

    public sealed class MacPosixWaylineProcessLauncher : IWaylineProcessLauncher
    {
        public IWaylineProcess Start(WaylineProcessStartInfo startInfo)
        {
            if (startInfo == null)
                throw new ArgumentNullException(nameof(startInfo));
            if (!File.Exists(startInfo.ExecutablePath))
                throw new WaylineHostException("startup_failed");

            var descriptors = new int[2];
            if (Native.pipe(descriptors) != 0)
                throw new WaylineHostException("startup_failed");
            var reader = descriptors[0];
            var writer = descriptors[1];
            IntPtr actions = IntPtr.Zero;
            IntPtr attributes = IntPtr.Zero;
            IntPtr argv = IntPtr.Zero;
            var allocatedArguments = new List<IntPtr>();
            var spawned = false;
            try
            {
                Native.fcntl(reader, Native.F_SETFD, Native.FD_CLOEXEC);
                RequireZero(Native.posix_spawn_file_actions_init(ref actions));
                if (writer != startInfo.StartupDescriptor)
                {
                    RequireZero(Native.posix_spawn_file_actions_adddup2(
                        ref actions,
                        writer,
                        startInfo.StartupDescriptor));
                }
                if (reader != startInfo.StartupDescriptor)
                    RequireZero(Native.posix_spawn_file_actions_addclose(ref actions, reader));
                if (writer != startInfo.StartupDescriptor)
                    RequireZero(Native.posix_spawn_file_actions_addclose(ref actions, writer));
                RequireZero(Native.posix_spawn_file_actions_addopen(
                    ref actions,
                    1,
                    "/dev/null",
                    Native.O_WRONLY,
                    0));
                RequireZero(Native.posix_spawn_file_actions_addopen(
                    ref actions,
                    2,
                    "/dev/null",
                    Native.O_WRONLY,
                    0));
                RequireZero(Native.posix_spawn_file_actions_addchdir_np(
                    ref actions,
                    startInfo.WorkingDirectory));

                RequireZero(Native.posix_spawnattr_init(ref attributes));
                RequireZero(Native.posix_spawnattr_setpgroup(ref attributes, 0));
                RequireZero(Native.posix_spawnattr_setflags(
                    ref attributes,
                    Native.POSIX_SPAWN_SETPGROUP));

                argv = AllocateArguments(
                    startInfo.ExecutablePath,
                    startInfo.Arguments,
                    allocatedArguments);
                var environmentPointer = Marshal.ReadIntPtr(Native._NSGetEnviron());
                var result = Native.posix_spawn(
                    out var processId,
                    startInfo.ExecutablePath,
                    ref actions,
                    ref attributes,
                    argv,
                    environmentPointer);
                RequireZero(result);
                spawned = true;
                Native.close(writer);
                writer = -1;
                return new MacPosixWaylineProcess(processId, reader);
            }
            finally
            {
                if (actions != IntPtr.Zero)
                    Native.posix_spawn_file_actions_destroy(ref actions);
                if (attributes != IntPtr.Zero)
                    Native.posix_spawnattr_destroy(ref attributes);
                foreach (var value in allocatedArguments)
                    Marshal.FreeHGlobal(value);
                if (argv != IntPtr.Zero)
                    Marshal.FreeHGlobal(argv);
                if (writer >= 0)
                    Native.close(writer);
                if (!spawned)
                    Native.close(reader);
            }
        }

        private static IntPtr AllocateArguments(
            string executable,
            IEnumerable<string> arguments,
            ICollection<IntPtr> allocated)
        {
            var values = new List<string> { executable };
            values.AddRange(arguments);
            var array = Marshal.AllocHGlobal((values.Count + 1) * IntPtr.Size);
            for (var index = 0; index < values.Count; index++)
            {
                var pointer = Marshal.StringToHGlobalAnsi(values[index]);
                allocated.Add(pointer);
                Marshal.WriteIntPtr(array, index * IntPtr.Size, pointer);
            }
            Marshal.WriteIntPtr(array, values.Count * IntPtr.Size, IntPtr.Zero);
            return array;
        }

        private static void RequireZero(int result)
        {
            if (result != 0)
                throw new WaylineHostException("startup_failed");
        }
    }

    internal sealed class MacPosixWaylineProcess : IWaylineProcess
    {
        private const int ReadPollMilliseconds = 50;
        private const int ExitPollMilliseconds = 10;
        private const int TerminationGraceMilliseconds = 1_000;
        private const int KillObservationMilliseconds = 250;

        private readonly int _processId;
        private readonly object _lifecycleGate = new object();
        private int _reader;
        private int _terminationStarted;
        private int _disposed;
        private bool _hasExitCode;
        private int _exitCode;

        public MacPosixWaylineProcess(int processId, int reader)
        {
            _processId = processId;
            _reader = reader;
        }

        public Task<string> ReadStartupLineAsync(CancellationToken cancellationToken)
        {
            var descriptor = _reader;
            if (descriptor < 0)
                throw new InvalidOperationException("startup pipe has already been consumed");
            return Task.Run(() => ReadLine(descriptor, cancellationToken));
        }

        public async Task<int> WaitForExitAsync(CancellationToken cancellationToken)
        {
            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (TryReap(out var exitCode))
                    return exitCode;
                await Task.Delay(ExitPollMilliseconds, cancellationToken)
                    .ConfigureAwait(false);
            }
        }

        public void TerminateTree()
        {
            if (Interlocked.CompareExchange(ref _terminationStarted, 1, 0) != 0)
                return;

            try
            {
                if (TreeHasExited())
                    return;

                SignalTree(Native.SIGTERM);
                if (WaitForTreeExit(TerminationGraceMilliseconds))
                    return;

                SignalTree(Native.SIGKILL);
                ReapLeaderAfterKill();
                WaitForProcessGroupExit(KillObservationMilliseconds);
            }
            finally
            {
                CloseReader();
            }
        }

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _disposed, 1) != 0)
                return;
            try
            {
                TerminateTree();
            }
            finally
            {
                CloseReader();
            }
        }

        private string ReadLine(int descriptor, CancellationToken cancellationToken)
        {
            var bytes = new List<byte>(256);
            var buffer = new byte[1];
            var pollDescriptors = new[]
            {
                new Native.PollDescriptor
                {
                    Descriptor = descriptor,
                    Events = Native.POLLIN
                }
            };
            try
            {
                while (bytes.Count <= 4096)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    pollDescriptors[0].ReturnedEvents = 0;
                    var pollResult = Native.poll(
                        pollDescriptors,
                        1,
                        ReadPollMilliseconds);
                    if (pollResult == 0)
                        continue;
                    if (pollResult < 0)
                    {
                        if (Marshal.GetLastWin32Error() == Native.EINTR)
                            continue;
                        throw new WaylineHostException("startup_failed");
                    }

                    cancellationToken.ThrowIfCancellationRequested();
                    var returnedEvents = pollDescriptors[0].ReturnedEvents;
                    if ((returnedEvents & Native.POLLNVAL) != 0)
                        throw new WaylineHostException("startup_failed");
                    if ((returnedEvents &
                         (Native.POLLIN | Native.POLLHUP | Native.POLLERR)) == 0)
                    {
                        continue;
                    }

                    var count = Native.read(descriptor, buffer, 1);
                    if (count == 0)
                        return bytes.Count == 0 ? null : Encoding.UTF8.GetString(bytes.ToArray());
                    if (count < 0)
                    {
                        if (Marshal.GetLastWin32Error() == Native.EINTR)
                            continue;
                        throw new WaylineHostException("startup_failed");
                    }
                    if (buffer[0] == (byte)'\n')
                        return Encoding.UTF8.GetString(bytes.ToArray());
                    bytes.Add(buffer[0]);
                }
                throw new WaylineHostException("startup_receipt_invalid");
            }
            finally
            {
                CloseReader();
            }
        }

        private bool TryReap(out int exitCode)
        {
            lock (_lifecycleGate)
                return TryReapUnderLock(out exitCode);
        }

        private bool TryReapUnderLock(out int exitCode)
        {
            if (_hasExitCode)
            {
                exitCode = _exitCode;
                return true;
            }

            while (true)
            {
                var result = Native.waitpid(
                    _processId,
                    out var status,
                    Native.WNOHANG);
                if (result == _processId)
                {
                    RecordExitUnderLock(status);
                    exitCode = _exitCode;
                    return true;
                }
                if (result == 0)
                {
                    exitCode = 0;
                    return false;
                }

                var error = Marshal.GetLastWin32Error();
                if (error == Native.EINTR)
                    continue;

                _exitCode = 1;
                _hasExitCode = true;
                exitCode = _exitCode;
                return true;
            }
        }

        private void SignalTree(int signal)
        {
            lock (_lifecycleGate)
            {
                var leaderExited = TryReapUnderLock(out _);
                if (leaderExited && !ProcessGroupExistsUnderLock())
                    return;

                if (Native.kill(-_processId, signal) == 0)
                    return;

                if (!leaderExited)
                    Native.kill(_processId, signal);
            }
        }

        private bool TreeHasExited()
        {
            lock (_lifecycleGate)
            {
                return TryReapUnderLock(out _) &&
                       !ProcessGroupExistsUnderLock();
            }
        }

        private bool WaitForTreeExit(int timeoutMilliseconds)
        {
            var elapsed = Stopwatch.StartNew();
            do
            {
                if (TreeHasExited())
                    return true;
                Thread.Sleep(ExitPollMilliseconds);
            }
            while (elapsed.ElapsedMilliseconds < timeoutMilliseconds);

            return TreeHasExited();
        }

        private void ReapLeaderAfterKill()
        {
            lock (_lifecycleGate)
            {
                if (_hasExitCode)
                    return;

                while (true)
                {
                    var result = Native.waitpid(_processId, out var status, 0);
                    if (result == _processId)
                    {
                        RecordExitUnderLock(status);
                        return;
                    }

                    var error = Marshal.GetLastWin32Error();
                    if (error == Native.EINTR)
                        continue;

                    _exitCode = 1;
                    _hasExitCode = true;
                    return;
                }
            }
        }

        private void WaitForProcessGroupExit(int timeoutMilliseconds)
        {
            var elapsed = Stopwatch.StartNew();
            while (ProcessGroupExists() &&
                   elapsed.ElapsedMilliseconds < timeoutMilliseconds)
            {
                Thread.Sleep(ExitPollMilliseconds);
            }
        }

        private bool ProcessGroupExists()
        {
            lock (_lifecycleGate)
                return ProcessGroupExistsUnderLock();
        }

        private bool ProcessGroupExistsUnderLock()
        {
            if (Native.kill(-_processId, 0) == 0)
                return true;
            return Marshal.GetLastWin32Error() == Native.EPERM;
        }

        private void RecordExitUnderLock(int status)
        {
            _exitCode = (status & 0x7f) == 0
                ? (status >> 8) & 0xff
                : 128 + (status & 0x7f);
            _hasExitCode = true;
        }

        private void CloseReader()
        {
            var descriptor = Interlocked.Exchange(ref _reader, -1);
            if (descriptor >= 0)
                Native.close(descriptor);
        }

    }

    internal static class Native
    {
        internal const int F_SETFD = 2;
        internal const int FD_CLOEXEC = 1;
        internal const int O_WRONLY = 1;
        internal const short POSIX_SPAWN_SETPGROUP = 0x0002;
        internal const int SIGTERM = 15;
        internal const int SIGKILL = 9;
        internal const int EINTR = 4;
        internal const int EPERM = 1;
        internal const int WNOHANG = 1;
        internal const short POLLIN = 0x0001;
        internal const short POLLERR = 0x0008;
        internal const short POLLHUP = 0x0010;
        internal const short POLLNVAL = 0x0020;

        private const string LibSystem = "/usr/lib/libSystem.B.dylib";

        [StructLayout(LayoutKind.Sequential)]
        internal struct PollDescriptor
        {
            internal int Descriptor;
            internal short Events;
            internal short ReturnedEvents;
        }

        [DllImport(LibSystem, SetLastError = true)]
        internal static extern int pipe([Out] int[] descriptors);

        [DllImport(LibSystem, SetLastError = true)]
        internal static extern int close(int descriptor);

        [DllImport(LibSystem, SetLastError = true)]
        internal static extern int fcntl(int descriptor, int command, int value);

        [DllImport(LibSystem, SetLastError = true)]
        internal static extern long read(int descriptor, [Out] byte[] buffer, ulong count);

        [DllImport(LibSystem, SetLastError = true)]
        internal static extern int poll(
            [In, Out] PollDescriptor[] descriptors,
            uint descriptorCount,
            int timeoutMilliseconds);

        [DllImport(LibSystem)]
        internal static extern IntPtr _NSGetEnviron();

        [DllImport(LibSystem)]
        internal static extern int posix_spawn_file_actions_init(ref IntPtr actions);

        [DllImport(LibSystem)]
        internal static extern int posix_spawn_file_actions_destroy(ref IntPtr actions);

        [DllImport(LibSystem)]
        internal static extern int posix_spawn_file_actions_adddup2(
            ref IntPtr actions,
            int descriptor,
            int childDescriptor);

        [DllImport(LibSystem)]
        internal static extern int posix_spawn_file_actions_addclose(
            ref IntPtr actions,
            int descriptor);

        [DllImport(LibSystem)]
        internal static extern int posix_spawn_file_actions_addopen(
            ref IntPtr actions,
            int descriptor,
            string path,
            int openFlags,
            int mode);

        [DllImport(LibSystem)]
        internal static extern int posix_spawn_file_actions_addchdir_np(
            ref IntPtr actions,
            string path);

        [DllImport(LibSystem)]
        internal static extern int posix_spawnattr_init(ref IntPtr attributes);

        [DllImport(LibSystem)]
        internal static extern int posix_spawnattr_destroy(ref IntPtr attributes);

        [DllImport(LibSystem)]
        internal static extern int posix_spawnattr_setpgroup(
            ref IntPtr attributes,
            int processGroup);

        [DllImport(LibSystem)]
        internal static extern int posix_spawnattr_setflags(
            ref IntPtr attributes,
            short flags);

        [DllImport(LibSystem)]
        internal static extern int posix_spawn(
            out int processId,
            string path,
            ref IntPtr actions,
            ref IntPtr attributes,
            IntPtr arguments,
            IntPtr environment);

        [DllImport(LibSystem, SetLastError = true)]
        internal static extern int waitpid(int processId, out int status, int options);

        [DllImport(LibSystem, SetLastError = true)]
        internal static extern int kill(int processId, int signal);
    }
}
