using System;
using System.Globalization;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Wayline.Save
{
    public sealed class AtomicProfileStore
    {
        private static readonly JsonSerializerSettings Settings = new JsonSerializerSettings
        {
            Culture = CultureInfo.InvariantCulture,
            DateParseHandling = DateParseHandling.None,
            Formatting = Formatting.None,
            MissingMemberHandling = MissingMemberHandling.Error,
            NullValueHandling = NullValueHandling.Include
        };

        public AtomicProfileStore(string primaryPath)
        {
            if (string.IsNullOrWhiteSpace(primaryPath))
                throw new ArgumentException("A profile path is required.", nameof(primaryPath));
            PrimaryPath = Path.GetFullPath(primaryPath);
            BackupPath = PrimaryPath + ".bak";
            TemporaryPath = PrimaryPath + ".tmp";
        }

        public string PrimaryPath { get; }

        public string BackupPath { get; }

        public string TemporaryPath { get; }

        public void Save(ProfileDataV1 profile)
        {
            if (profile == null)
                throw new ArgumentNullException(nameof(profile));
            profile.Validate();
            var json = JsonConvert.SerializeObject(profile, Settings);
            EnsureDirectory(PrimaryPath);
            WriteDurably(TemporaryPath, json);
            try
            {
                if (File.Exists(PrimaryPath))
                {
                    if (File.Exists(BackupPath))
                        File.Delete(BackupPath);
                    File.Replace(TemporaryPath, PrimaryPath, BackupPath, ignoreMetadataErrors: true);
                }
                else
                {
                    File.Move(TemporaryPath, PrimaryPath);
                }
            }
            finally
            {
                if (File.Exists(TemporaryPath))
                    File.Delete(TemporaryPath);
            }
        }

        public ProfileDataV1 Load()
        {
            if (TryLoad(PrimaryPath, out var primary))
                return primary;
            if (TryLoad(BackupPath, out var backup))
                return backup;
            throw new InvalidDataException("No valid Wayline profile is available.");
        }

        public void ExportTo(string destinationPath)
        {
            if (string.IsNullOrWhiteSpace(destinationPath))
                throw new ArgumentException("An export path is required.", nameof(destinationPath));
            var destination = Path.GetFullPath(destinationPath);
            if (string.Equals(destination, PrimaryPath, StringComparison.Ordinal) ||
                string.Equals(destination, BackupPath, StringComparison.Ordinal) ||
                string.Equals(destination, TemporaryPath, StringComparison.Ordinal))
            {
                throw new ArgumentException("Export must use a separate path.", nameof(destinationPath));
            }
            var profile = Load();
            var json = JsonConvert.SerializeObject(profile, Settings);
            EnsureDirectory(destination);
            var temporary = destination + ".tmp";
            try
            {
                WriteDurably(temporary, json);
                if (File.Exists(destination))
                    File.Delete(destination);
                File.Move(temporary, destination);
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(temporary);
            }
        }

        public void Delete()
        {
            DeleteIfPresent(TemporaryPath);
            DeleteIfPresent(BackupPath);
            DeleteIfPresent(PrimaryPath);
        }

        private static bool TryLoad(string path, out ProfileDataV1 profile)
        {
            profile = null;
            if (!File.Exists(path))
                return false;
            try
            {
                var json = File.ReadAllText(path, Encoding.UTF8);
                using var reader = new JsonTextReader(new StringReader(json))
                {
                    DateParseHandling = DateParseHandling.None
                };
                var token = JToken.ReadFrom(reader, new JsonLoadSettings
                {
                    CommentHandling = CommentHandling.Ignore,
                    DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                    LineInfoHandling = LineInfoHandling.Load
                });
                if (token.Type != JTokenType.Object || reader.Read())
                    return false;
                var serializer = JsonSerializer.Create(Settings);
                profile = token.ToObject<ProfileDataV1>(serializer);
                profile?.Validate();
                return profile != null;
            }
            catch (Exception error) when (
                error is IOException ||
                error is UnauthorizedAccessException ||
                error is JsonException ||
                error is ArgumentException ||
                error is InvalidOperationException ||
                error is OverflowException)
            {
                profile = null;
                return false;
            }
        }

        private static void WriteDurably(string path, string text)
        {
            using var stream = new FileStream(
                path,
                FileMode.Create,
                FileAccess.Write,
                FileShare.None,
                bufferSize: 4096,
                FileOptions.WriteThrough);
            var bytes = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false).GetBytes(text);
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(flushToDisk: true);
        }

        private static void EnsureDirectory(string path)
        {
            var parent = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(parent))
                throw new InvalidOperationException("A profile path must have a parent directory.");
            Directory.CreateDirectory(parent);
        }

        private static void DeleteIfPresent(string path)
        {
            if (File.Exists(path))
                File.Delete(path);
        }
    }
}
