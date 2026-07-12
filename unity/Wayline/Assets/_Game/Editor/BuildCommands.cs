using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Xml;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.OSXStandalone;
using UnityEngine;

namespace Wayline.Editor
{
    public static class BuildCommands
    {
        private const string LibSystem = "/usr/lib/libSystem.B.dylib";
        private const int ExecutePermission = 1;
        private const int DarwinStatBufferSize = 256;
        private const int DarwinStatModeOffset = 4;
        private const int ErrorNoEntry = 2;
        private const int ErrorNotDirectory = 20;
        private const int MachO64HeaderSize = 32;
        private const int MachOLoadCommandHeaderSize = 8;
        private const int Segment64CommandSize = 72;
        private const int MainCommandSize = 24;
        private const uint Segment64Command = 0x19;
        private const uint MainCommand = 0x80000028;
        private const uint VmProtectionExecute = 0x4;
        private const uint MaximumLoadCommandCount = 4096;
        private const uint MachO32Magic = 0xFEEDFACE;
        private const uint MachO64Magic = 0xFEEDFACF;
        private const uint MachO32SwappedMagic = 0xCEFAEDFE;
        private const uint MachO64SwappedMagic = 0xCFFAEDFE;
        private const uint FatMachOMagic = 0xBEBAFECA;
        private const uint FatMachOSwappedMagic = 0xCAFEBABE;
        private const uint FatMachO64Magic = 0xBFBAFECA;
        private const uint FatMachO64SwappedMagic = 0xCAFEBABF;
        private const uint CpuTypeArm64 = 0x0100000C;
        private const uint MachOExecutableFileType = 2;
        private const uint MaximumMachOFileType = 12;
        private const uint BuildVersionCommand = 0x32;
        private const uint VersionMinimumMacOSCommand = 0x24;
        private const uint MaximumMacOSMinimumVersion = 13U << 16;
        private const uint NativeFileTypeMask = 0xF000;
        private const uint NativeDirectoryType = 0x4000;
        private const uint NativeRegularFileType = 0x8000;
        private const uint NativeSymlinkType = 0xA000;

        private enum NativeNodeKind
        {
            Missing,
            RegularFile,
            Directory,
            Symlink,
            Other
        }

        private enum MachOInspection
        {
            NotMachO,
            ValidThinArm64,
            Invalid
        }

        public const string AcceptanceScene =
            "Assets/_Game/Scenes/Arena_Graybox.unity";

        public const string DevelopmentOutputPath =
            "Builds/MacDevelopment/Wayline-Development-arm64.app";

        public static BuildPlayerOptions CreateMacArm64DevelopmentOptions()
        {
            return new BuildPlayerOptions
            {
                scenes = new[] { AcceptanceScene },
                locationPathName = Path.GetFullPath(Path.Combine(
                    ProjectRoot,
                    DevelopmentOutputPath)),
                target = BuildTarget.StandaloneOSX,
                targetGroup = BuildTargetGroup.Standalone,
                options = BuildOptions.Development
            };
        }

        public static void ValidateAcceptanceBuildInputs(BuildPlayerOptions options)
        {
            if (options.scenes == null ||
                options.scenes.Length != 1 ||
                !string.Equals(
                    options.scenes[0],
                    AcceptanceScene,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "The development acceptance build requires exactly Arena_Graybox.");
            }

            if (options.target != BuildTarget.StandaloneOSX ||
                options.targetGroup != BuildTargetGroup.Standalone)
            {
                throw new InvalidOperationException(
                    "The development acceptance build requires standalone macOS.");
            }

            if (options.options != BuildOptions.Development)
            {
                throw new InvalidOperationException(
                    "Only BuildOptions.Development is permitted.");
            }

            if (options.extraScriptingDefines != null &&
                options.extraScriptingDefines.Length != 0)
            {
                throw new InvalidOperationException(
                    "Manual scripting defines are prohibited.");
            }

            var expectedOutput = Path.GetFullPath(Path.Combine(
                ProjectRoot,
                DevelopmentOutputPath));
            var requestedOutput = Path.GetFullPath(options.locationPathName ?? string.Empty);
            if (!string.Equals(requestedOutput, expectedOutput, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "The output path must be the canonical ignored development app.");
            }

            var roundTrip = Path.GetRelativePath(ProjectRoot, requestedOutput)
                .Replace(Path.DirectorySeparatorChar, '/');
            if (!string.Equals(
                    roundTrip,
                    DevelopmentOutputPath,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "The development output escaped its canonical project path.");
            }

            RequireNoSymlinkedPathComponents(ProjectRoot, requestedOutput);

            RequireFile(AcceptanceScene);
            RequireFile(AcceptanceScene + ".meta");

            var configuredScenes = EditorBuildSettings.scenes;
            if (configuredScenes == null ||
                configuredScenes.Length != 1 ||
                !configuredScenes[0].enabled ||
                !string.Equals(
                    configuredScenes[0].path,
                    AcceptanceScene,
                    StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Editor build settings must enable only Arena_Graybox.");
            }
        }

        public static void RunMacArm64MonoTransaction(Action buildAction)
        {
            if (buildAction == null)
                throw new ArgumentNullException(nameof(buildAction));

            var namedTarget = NamedBuildTarget.Standalone;
            var originalBackend = PlayerSettings.GetScriptingBackend(namedTarget);
            var originalArchitecture = PlayerSettings.GetArchitecture(namedTarget);
            var originalBuildProfileArchitecture = UserBuildSettings.architecture;
            try
            {
                PlayerSettings.SetScriptingBackend(
                    namedTarget,
                    ScriptingImplementation.Mono2x);
                PlayerSettings.SetArchitecture(namedTarget, 1);
                UserBuildSettings.architecture = OSArchitecture.ARM64;
                buildAction();
            }
            finally
            {
                UserBuildSettings.architecture = originalBuildProfileArchitecture;
                PlayerSettings.SetScriptingBackend(namedTarget, originalBackend);
                PlayerSettings.SetArchitecture(namedTarget, originalArchitecture);
                AssetDatabase.SaveAssets();
            }
        }

        public static void BuildMacArm64DevelopmentAcceptance()
        {
            var options = CreateMacArm64DevelopmentOptions();
            ValidateAcceptanceBuildInputs(options);

            RunMacArm64MonoTransaction(() =>
            {
                CleanExactOutput(options.locationPathName);
                var report = BuildPipeline.BuildPlayer(options);
                var summary = report.summary;
                if (summary.result != BuildResult.Succeeded || summary.totalErrors != 0)
                {
                    throw new InvalidOperationException(
                        "Wayline Mac development build failed: " +
                        summary.result + " with " + summary.totalErrors + " errors.");
                }

                ValidateBuiltBundle(options.locationPathName);
                Debug.Log(
                    "Wayline internal development acceptance app built at " +
                    options.locationPathName);
            });
        }

        public static void ValidateBuiltBundle(string appPath)
        {
            if (string.IsNullOrWhiteSpace(appPath))
                throw new ArgumentException("An app path is required.", nameof(appPath));

            var fullAppPath = Path.GetFullPath(appPath);
            RequireNoSymlinkedPathComponents(
                TrustedBoundaryForBundle(fullAppPath),
                fullAppPath);
            if (!fullAppPath.EndsWith(".app", StringComparison.OrdinalIgnoreCase) ||
                GetNativeNodeKind(fullAppPath) != NativeNodeKind.Directory)
            {
                throw new InvalidOperationException("The built macOS app is missing.");
            }

            RequireNotSymlink(fullAppPath);
            AuditDirectoryTree(fullAppPath);

            var infoPlist = Path.Combine(fullAppPath, "Contents", "Info.plist");
            RequireNativeRegularFile(
                infoPlist,
                "The built app has no regular Contents/Info.plist.");

            var executableName = ReadBundleExecutable(infoPlist);
            if (!IsSafeExecutableName(executableName))
            {
                throw new InvalidOperationException(
                    "CFBundleExecutable must be one safe bundle filename.");
            }

            var macDirectory = Path.Combine(fullAppPath, "Contents", "MacOS");
            RequireNativeDirectory(
                macDirectory,
                "The built app has no regular Contents/MacOS directory.");

            var executablePath = Path.Combine(macDirectory, executableName);
            RequireNativeRegularFile(
                executablePath,
                "CFBundleExecutable does not name a regular player file.");

            RequireNotSymlink(executablePath);
            if (!HasExecutablePermission(executablePath))
            {
                throw new InvalidOperationException(
                    "The macOS player file is not executable.");
            }

            if (!IsArm64MachOExecutable(executablePath))
            {
                throw new InvalidOperationException(
                    "CFBundleExecutable is not an ARM64 Mach-O executable.");
            }
        }

        private static void CleanExactOutput(string outputPath)
        {
            var fullOutput = Path.GetFullPath(outputPath);
            var expectedOutput = Path.GetFullPath(Path.Combine(
                ProjectRoot,
                DevelopmentOutputPath));
            if (!string.Equals(fullOutput, expectedOutput, StringComparison.Ordinal))
                throw new InvalidOperationException("Refusing to clean a noncanonical output.");

            RequireNoSymlinkedPathComponents(ProjectRoot, fullOutput);
            ValidateCleanupTreeForDeletion(fullOutput);
            var outputKind = GetNativeNodeKind(fullOutput);
            if (outputKind == NativeNodeKind.Directory)
            {
                Directory.Delete(fullOutput, true);
            }
            else if (outputKind == NativeNodeKind.RegularFile)
            {
                File.Delete(fullOutput);
            }

            var outputParent = Path.GetDirectoryName(fullOutput);
            Directory.CreateDirectory(outputParent);
            RequireNoSymlinkedPathComponents(ProjectRoot, outputParent);
            RequireNativeDirectory(
                outputParent,
                "The development output parent is not a regular directory.");
        }

        public static void ValidateCleanupTreeForDeletion(string outputPath)
        {
            if (string.IsNullOrWhiteSpace(outputPath))
            {
                throw new ArgumentException(
                    "An output path is required.",
                    nameof(outputPath));
            }

            var fullOutput = Path.GetFullPath(outputPath);
            var rootKind = GetNativeNodeKind(fullOutput);
            if (rootKind == NativeNodeKind.Missing)
                return;

            if (rootKind == NativeNodeKind.Symlink ||
                rootKind == NativeNodeKind.Other)
            {
                throw new InvalidOperationException(
                    "Unsafe cleanup output node: " + fullOutput);
            }

            if (rootKind == NativeNodeKind.RegularFile)
                return;

            var pending = new Stack<string>();
            pending.Push(fullOutput);
            while (pending.Count != 0)
            {
                var directory = pending.Pop();
                foreach (var entry in Directory.EnumerateFileSystemEntries(directory))
                {
                    var kind = GetNativeNodeKind(entry);
                    if (kind == NativeNodeKind.Symlink ||
                        kind == NativeNodeKind.Other ||
                        kind == NativeNodeKind.Missing)
                    {
                        throw new InvalidOperationException(
                            "Unsafe cleanup child node: " + entry);
                    }

                    if (kind == NativeNodeKind.Directory)
                        pending.Push(entry);
                }
            }
        }

        private static void AuditDirectoryTree(string root)
        {
            RequireNativeDirectory(
                root,
                "The bundle audit root is not a regular directory.");
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count != 0)
            {
                var directory = pending.Pop();
                foreach (var entry in Directory.EnumerateFileSystemEntries(directory))
                {
                    var kind = GetNativeNodeKind(entry);
                    if (kind == NativeNodeKind.Symlink)
                        throw new InvalidOperationException(
                            "Symlinks are prohibited: " + entry);
                    if (kind != NativeNodeKind.RegularFile &&
                        kind != NativeNodeKind.Directory)
                    {
                        throw new InvalidOperationException(
                            "Special filesystem nodes are prohibited: " + entry);
                    }

                    var relativePath = Path.GetRelativePath(root, entry);
                    if (IsForbiddenPayloadPath(relativePath))
                    {
                        throw new InvalidOperationException(
                            "Forbidden payload found in development app: " +
                            relativePath);
                    }

                    if (kind == NativeNodeKind.RegularFile)
                    {
                        RequireValidNativeImageIfMachO(entry, relativePath);
                    }
                    else
                    {
                        pending.Push(entry);
                    }
                }
            }
        }

        private static bool IsForbiddenPayloadPath(string path)
        {
            var normalized = (path ?? string.Empty)
                .Replace('\\', '/')
                .ToLowerInvariant();
            var segments = normalized.Split(
                new[] { '/' },
                StringSplitOptions.RemoveEmptyEntries);
            foreach (var segment in segments)
            {
                if (segment.StartsWith(".env", StringComparison.Ordinal) ||
                    segment.EndsWith(".gguf", StringComparison.Ordinal) ||
                    segment.EndsWith(".safetensors", StringComparison.Ordinal) ||
                    segment.EndsWith(".jsonl", StringComparison.Ordinal) ||
                    segment.EndsWith(".csv", StringComparison.Ordinal) ||
                    segment.EndsWith(".ipynb", StringComparison.Ordinal) ||
                    segment.EndsWith(".py", StringComparison.Ordinal) ||
                    segment.EndsWith(".pyc", StringComparison.Ordinal) ||
                    segment == "model_manifest_v1.json" ||
                    segment == "descriptor_binding_release_receipt_v1.json" ||
                    segment == "package_receipt_v1.json" ||
                    segment == "wayline_parity_report_v1.json" ||
                    segment == "package_manifest_v1.json")
                {
                    return true;
                }
            }

            var compact = CompactAsciiAlphaNumeric(normalized);
            return compact.Contains("waylineforge") ||
                   compact.Contains("llamaserver") ||
                   compact.Contains("reviewedcache");
        }

        private static string CompactAsciiAlphaNumeric(string value)
        {
            var compact = new StringBuilder(value.Length);
            foreach (var character in value)
            {
                if ((character >= 'a' && character <= 'z') ||
                    (character >= '0' && character <= '9'))
                {
                    compact.Append(character);
                }
            }

            return compact.ToString();
        }

        private static void RequireNotSymlink(string path)
        {
            if (IsSymlink(path))
                throw new InvalidOperationException("Symlinks are prohibited: " + path);
        }

        private static bool IsSymlink(string path)
        {
            return GetNativeNodeKind(path) == NativeNodeKind.Symlink;
        }

        private static void RequireNoSymlinkedPathComponents(
            string trustedBoundary,
            string path)
        {
            var fullBoundary = Path.GetFullPath(trustedBoundary);
            var fullPath = Path.GetFullPath(path);
            if (!IsPathAtOrBelow(fullBoundary, fullPath))
            {
                throw new InvalidOperationException(
                    "The path escaped its trusted boundary: " + fullPath);
            }

            RequireNotSymlink(fullBoundary);
            var relative = Path.GetRelativePath(fullBoundary, fullPath);
            if (relative == ".")
                return;

            var current = fullBoundary;
            foreach (var component in relative.Split(Path.DirectorySeparatorChar))
            {
                if (component.Length == 0 || component == ".")
                    continue;

                current = Path.Combine(current, component);
                RequireNotSymlink(current);
            }
        }

        private static string TrustedBoundaryForBundle(string fullAppPath)
        {
            if (IsPathAtOrBelow(ProjectRoot, fullAppPath))
                return ProjectRoot;

            var temporaryRoot = Path.GetFullPath(Path.GetTempPath());
            if (IsPathAtOrBelow(temporaryRoot, fullAppPath))
                return temporaryRoot;

            return Path.GetPathRoot(fullAppPath);
        }

        private static bool IsPathAtOrBelow(string boundary, string path)
        {
            var relative = Path.GetRelativePath(
                Path.GetFullPath(boundary),
                Path.GetFullPath(path));
            return relative == "." ||
                   (!Path.IsPathRooted(relative) &&
                    relative != ".." &&
                    !relative.StartsWith(
                        ".." + Path.DirectorySeparatorChar,
                        StringComparison.Ordinal));
        }

        private static string ReadBundleExecutable(string infoPlist)
        {
            try
            {
                var settings = new XmlReaderSettings
                {
                    DtdProcessing = DtdProcessing.Ignore,
                    XmlResolver = null,
                    MaxCharactersInDocument = 1024 * 1024
                };
                var document = new XmlDocument { XmlResolver = null };
                using (var reader = XmlReader.Create(infoPlist, settings))
                    document.Load(reader);

                var keys = document.SelectNodes(
                    "/plist/dict/key[.='CFBundleExecutable']");
                if (keys == null || keys.Count != 1)
                {
                    throw new InvalidOperationException(
                        "Info.plist must contain exactly one CFBundleExecutable key.");
                }

                var value = keys[0].SelectSingleNode("following-sibling::*[1]");
                if (value == null || value.Name != "string")
                {
                    throw new InvalidOperationException(
                        "CFBundleExecutable must have one string value.");
                }

                return value.InnerText;
            }
            catch (InvalidOperationException)
            {
                throw;
            }
            catch (Exception exception) when (
                exception is IOException ||
                exception is UnauthorizedAccessException ||
                exception is XmlException)
            {
                throw new InvalidOperationException(
                    "Contents/Info.plist is invalid.",
                    exception);
            }
        }

        private static bool IsSafeExecutableName(string executableName)
        {
            return !string.IsNullOrWhiteSpace(executableName) &&
                   string.Equals(
                       executableName,
                       executableName.Trim(),
                       StringComparison.Ordinal) &&
                   executableName != "." &&
                   executableName != ".." &&
                   executableName.IndexOf('/') < 0 &&
                   executableName.IndexOf('\\') < 0 &&
                   string.Equals(
                       Path.GetFileName(executableName),
                       executableName,
                       StringComparison.Ordinal);
        }

        private static bool HasExecutablePermission(string path)
        {
            return RuntimeInformation.IsOSPlatform(OSPlatform.OSX) &&
                   Access(path, ExecutePermission) == 0;
        }

        private static void RequireNativeRegularFile(string path, string message)
        {
            if (GetNativeNodeKind(path) != NativeNodeKind.RegularFile)
                throw new InvalidOperationException(message);
        }

        private static void RequireNativeDirectory(string path, string message)
        {
            if (GetNativeNodeKind(path) != NativeNodeKind.Directory)
                throw new InvalidOperationException(message);
        }

        private static NativeNodeKind GetNativeNodeKind(string path)
        {
            if (!RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
            {
                throw new PlatformNotSupportedException(
                    "Wayline's macOS bundle validator requires native lstat.");
            }

            var metadata = Marshal.AllocHGlobal(DarwinStatBufferSize);
            try
            {
                if (LStat(path, metadata) != 0)
                {
                    var error = Marshal.GetLastWin32Error();
                    if (error == ErrorNoEntry || error == ErrorNotDirectory)
                        return NativeNodeKind.Missing;

                    throw new InvalidOperationException(
                        "Native lstat failed for " + path + " with errno " + error + ".");
                }

                var mode = unchecked((ushort)Marshal.ReadInt16(
                    metadata,
                    DarwinStatModeOffset));
                switch ((uint)mode & NativeFileTypeMask)
                {
                    case NativeRegularFileType:
                        return NativeNodeKind.RegularFile;
                    case NativeDirectoryType:
                        return NativeNodeKind.Directory;
                    case NativeSymlinkType:
                        return NativeNodeKind.Symlink;
                    default:
                        return NativeNodeKind.Other;
                }
            }
            finally
            {
                Marshal.FreeHGlobal(metadata);
            }
        }

        private static void RequireValidNativeImageIfMachO(
            string path,
            string relativePath)
        {
            if (InspectMachO(path) == MachOInspection.Invalid)
            {
                throw new InvalidOperationException(
                    "Mach-O payload must be structurally valid thin ARM64 " +
                    "and target macOS 13.0 or earlier: " + relativePath);
            }
        }

        private static MachOInspection InspectMachO(string path)
        {
            try
            {
                using (var stream = File.OpenRead(path))
                using (var reader = new BinaryReader(stream))
                {
                    if (stream.Length < sizeof(uint))
                        return MachOInspection.NotMachO;

                    var magic = reader.ReadUInt32();
                    if (!IsMachOMagic(magic))
                        return MachOInspection.NotMachO;
                    if (magic != MachO64Magic || stream.Length < MachO64HeaderSize)
                        return MachOInspection.Invalid;

                    var cpuType = reader.ReadUInt32();
                    reader.ReadUInt32();
                    var fileType = reader.ReadUInt32();
                    var commandCount = reader.ReadUInt32();
                    var commandTableSize = reader.ReadUInt32();
                    reader.ReadUInt32();
                    reader.ReadUInt32();
                    if (cpuType != CpuTypeArm64 ||
                        fileType == 0 ||
                        fileType > MaximumMachOFileType ||
                        commandCount == 0 ||
                        commandCount > MaximumLoadCommandCount)
                    {
                        return MachOInspection.Invalid;
                    }

                    var fileLength = (ulong)stream.Length;
                    var commandTableStart = (ulong)MachO64HeaderSize;
                    if (commandTableSize > fileLength - commandTableStart)
                        return MachOInspection.Invalid;

                    var commandTableEnd = commandTableStart + commandTableSize;
                    var cursor = commandTableStart;
                    var hasSegment = false;
                    for (uint index = 0; index < commandCount; index++)
                    {
                        if (cursor > commandTableEnd ||
                            commandTableEnd - cursor < MachOLoadCommandHeaderSize)
                        {
                            return MachOInspection.Invalid;
                        }

                        stream.Position = (long)cursor;
                        var command = reader.ReadUInt32();
                        var commandSize = reader.ReadUInt32();
                        if (commandSize < MachOLoadCommandHeaderSize ||
                            (commandSize & 7) != 0 ||
                            commandSize > commandTableEnd - cursor)
                        {
                            return MachOInspection.Invalid;
                        }

                        if (command == Segment64Command)
                        {
                            if (commandSize < Segment64CommandSize)
                                return MachOInspection.Invalid;

                            stream.Position = (long)cursor + 40;
                            var fileOffset = reader.ReadUInt64();
                            var fileSize = reader.ReadUInt64();
                            stream.Position = (long)cursor + 64;
                            var sectionCount = reader.ReadUInt32();
                            var requiredSize = (ulong)Segment64CommandSize +
                                               (ulong)sectionCount * 80U;
                            if (requiredSize != commandSize ||
                                fileOffset > fileLength ||
                                fileSize > fileLength - fileOffset)
                            {
                                return MachOInspection.Invalid;
                            }

                            hasSegment = true;
                        }
                        else if (command == BuildVersionCommand)
                        {
                            if (commandSize < 24)
                                return MachOInspection.Invalid;

                            stream.Position = (long)cursor + 12;
                            var minimumVersion = reader.ReadUInt32();
                            stream.Position = (long)cursor + 20;
                            var toolCount = reader.ReadUInt32();
                            var requiredSize = 24UL + (ulong)toolCount * 8U;
                            if (requiredSize != commandSize ||
                                minimumVersion > MaximumMacOSMinimumVersion)
                            {
                                return MachOInspection.Invalid;
                            }
                        }
                        else if (command == VersionMinimumMacOSCommand)
                        {
                            if (commandSize != 16)
                                return MachOInspection.Invalid;

                            stream.Position = (long)cursor + 8;
                            if (reader.ReadUInt32() > MaximumMacOSMinimumVersion)
                                return MachOInspection.Invalid;
                        }

                        cursor += commandSize;
                    }

                    return cursor == commandTableEnd && hasSegment
                        ? MachOInspection.ValidThinArm64
                        : MachOInspection.Invalid;
                }
            }
            catch (IOException)
            {
                return MachOInspection.Invalid;
            }
            catch (UnauthorizedAccessException)
            {
                return MachOInspection.Invalid;
            }
        }

        private static bool IsMachOMagic(uint magic)
        {
            return magic == MachO32Magic ||
                   magic == MachO64Magic ||
                   magic == MachO32SwappedMagic ||
                   magic == MachO64SwappedMagic ||
                   magic == FatMachOMagic ||
                   magic == FatMachOSwappedMagic ||
                   magic == FatMachO64Magic ||
                   magic == FatMachO64SwappedMagic;
        }

        private static bool IsArm64MachOExecutable(string path)
        {
            try
            {
                using (var stream = File.OpenRead(path))
                using (var reader = new BinaryReader(stream))
                {
                    if (stream.Length < MachO64HeaderSize)
                        return false;

                    var magic = reader.ReadUInt32();
                    var cpuType = reader.ReadUInt32();
                    reader.ReadUInt32();
                    var fileType = reader.ReadUInt32();
                    var commandCount = reader.ReadUInt32();
                    var commandTableSize = reader.ReadUInt32();
                    reader.ReadUInt32();
                    reader.ReadUInt32();
                    if (magic != MachO64Magic ||
                        cpuType != CpuTypeArm64 ||
                        fileType != MachOExecutableFileType ||
                        commandCount == 0 ||
                        commandCount > MaximumLoadCommandCount)
                    {
                        return false;
                    }

                    var fileLength = (ulong)stream.Length;
                    var commandTableStart = (ulong)MachO64HeaderSize;
                    if (commandTableSize > fileLength - commandTableStart)
                        return false;

                    var commandTableEnd = commandTableStart + commandTableSize;
                    var cursor = commandTableStart;
                    var executableRanges = new List<Tuple<ulong, ulong>>();
                    ulong? entryOffset = null;
                    for (uint index = 0; index < commandCount; index++)
                    {
                        if (cursor > commandTableEnd ||
                            commandTableEnd - cursor < MachOLoadCommandHeaderSize)
                        {
                            return false;
                        }

                        stream.Position = (long)cursor;
                        var command = reader.ReadUInt32();
                        var commandSize = reader.ReadUInt32();
                        if (commandSize < MachOLoadCommandHeaderSize ||
                            (commandSize & 7) != 0 ||
                            commandSize > commandTableEnd - cursor)
                        {
                            return false;
                        }

                        if (command == Segment64Command)
                        {
                            if (commandSize < Segment64CommandSize)
                                return false;

                            stream.Position = (long)cursor + 40;
                            var fileOffset = reader.ReadUInt64();
                            var fileSize = reader.ReadUInt64();
                            stream.Position = (long)cursor + 60;
                            var initialProtection = reader.ReadUInt32();
                            if (fileOffset > fileLength ||
                                fileSize > fileLength - fileOffset)
                            {
                                return false;
                            }

                            if ((initialProtection & VmProtectionExecute) != 0 &&
                                fileSize != 0)
                            {
                                executableRanges.Add(Tuple.Create(
                                    fileOffset,
                                    fileOffset + fileSize));
                            }
                        }
                        else if (command == MainCommand)
                        {
                            if (commandSize < MainCommandSize || entryOffset.HasValue)
                                return false;

                            stream.Position = (long)cursor + 8;
                            entryOffset = reader.ReadUInt64();
                        }

                        cursor += commandSize;
                    }

                    if (cursor != commandTableEnd ||
                        !entryOffset.HasValue ||
                        entryOffset.Value < commandTableEnd ||
                        entryOffset.Value >= fileLength)
                    {
                        return false;
                    }

                    foreach (var range in executableRanges)
                    {
                        if (entryOffset.Value >= range.Item1 &&
                            entryOffset.Value < range.Item2)
                        {
                            return true;
                        }
                    }

                    return false;
                }
            }
            catch (IOException)
            {
                return false;
            }
            catch (UnauthorizedAccessException)
            {
                return false;
            }
        }

        [DllImport(LibSystem, EntryPoint = "lstat", SetLastError = true)]
        private static extern int LStat(string path, IntPtr metadata);

        [DllImport(LibSystem, EntryPoint = "access", SetLastError = true)]
        private static extern int Access(string path, int mode);

        private static string ProjectRoot =>
            Path.GetFullPath(Path.Combine(Application.dataPath, ".."));

        private static void RequireFile(string assetPath)
        {
            var path = Path.GetFullPath(Path.Combine(ProjectRoot, assetPath));
            if (!File.Exists(path))
                throw new InvalidOperationException("Required build input is missing: " + assetPath);
        }
    }
}
