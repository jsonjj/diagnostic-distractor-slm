using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Wayline.Save;

namespace Wayline.Flow.Runtime
{
    public sealed class RuntimeSessionSnapshot
    {
        internal RuntimeSessionSnapshot(ProfileDataV1 profile, FlowCheckpoint checkpoint)
        {
            Profile = profile ?? throw new ArgumentNullException(nameof(profile));
            Checkpoint = checkpoint ?? throw new ArgumentNullException(nameof(checkpoint));
        }

        public ProfileDataV1 Profile { get; }

        public FlowCheckpoint Checkpoint { get; }
    }

    public sealed class RuntimeSessionStore
    {
        public const string SchemaVersion = "wayline.runtime-session.v1";
        public const int MaximumFileBytes = 1024 * 1024;

        private static readonly JsonSerializerSettings Settings = new JsonSerializerSettings
        {
            Culture = CultureInfo.InvariantCulture,
            DateParseHandling = DateParseHandling.None,
            Formatting = Formatting.None,
            MissingMemberHandling = MissingMemberHandling.Error,
            NullValueHandling = NullValueHandling.Include
        };

        private static readonly HashSet<string> RootMembers = new HashSet<string>(
            new[] { "schemaVersion", "profile", "checkpoint" },
            StringComparer.Ordinal);

        private static readonly HashSet<string> CheckpointMembers = new HashSet<string>(
            new[]
            {
                "stableState",
                "worldId",
                "battleId",
                "combatVictoryPreserved",
                "committedTrialIds",
                "committedRewardIds",
                "rewardSourceCompletionId",
                "rewardAuthorityReceiptId"
            },
            StringComparer.Ordinal);

        private bool _primaryRejectedSinceLastSave;

        public RuntimeSessionStore(string primaryPath)
        {
            if (string.IsNullOrWhiteSpace(primaryPath))
                throw new ArgumentException("A runtime session path is required.", nameof(primaryPath));

            PrimaryPath = Path.GetFullPath(primaryPath);
            BackupPath = PrimaryPath + ".bak";
            TemporaryPath = PrimaryPath + ".tmp";
        }

        public string PrimaryPath { get; }

        public string BackupPath { get; }

        public string TemporaryPath { get; }

        public void Save(ProfileDataV1 profile, FlowCheckpoint checkpoint)
        {
            if (profile == null)
                throw new ArgumentNullException(nameof(profile));
            if (checkpoint == null)
                throw new ArgumentNullException(nameof(checkpoint));

            profile.Validate();
            var document = CreateDocument(profile, checkpoint);
            var json = document.ToString(Formatting.None);
            EnsureDirectory(PrimaryPath);
            WriteDurably(TemporaryPath, json);
            try
            {
                if (File.Exists(PrimaryPath))
                {
                    if (_primaryRejectedSinceLastSave)
                    {
                        File.Replace(
                            TemporaryPath,
                            PrimaryPath,
                            destinationBackupFileName: null,
                            ignoreMetadataErrors: true);
                    }
                    else
                    {
                        if (File.Exists(BackupPath))
                            File.Delete(BackupPath);
                        File.Replace(
                            TemporaryPath,
                            PrimaryPath,
                            BackupPath,
                            ignoreMetadataErrors: true);
                    }
                }
                else
                {
                    File.Move(TemporaryPath, PrimaryPath);
                }

                _primaryRejectedSinceLastSave = false;
            }
            finally
            {
                DeleteIfPresent(TemporaryPath);
            }
        }

        public RuntimeSessionSnapshot Load()
        {
            return Load(_ => true);
        }

        public RuntimeSessionSnapshot Load(
            Func<RuntimeSessionSnapshot, bool> candidateValidator)
        {
            if (candidateValidator == null)
                throw new ArgumentNullException(nameof(candidateValidator));

            _primaryRejectedSinceLastSave = false;
            var primaryExists = File.Exists(PrimaryPath);
            if (TryLoad(PrimaryPath, out var primary))
            {
                if (candidateValidator(primary))
                    return primary;
                _primaryRejectedSinceLastSave = true;
            }
            else if (primaryExists)
            {
                _primaryRejectedSinceLastSave = true;
            }

            if (TryLoad(BackupPath, out var backup) && candidateValidator(backup))
                return backup;

            throw new InvalidDataException("No valid Wayline runtime session is available.");
        }

        public void Delete()
        {
            DeleteIfPresent(TemporaryPath);
            DeleteIfPresent(BackupPath);
            DeleteIfPresent(PrimaryPath);
        }

        private static JObject CreateDocument(
            ProfileDataV1 profile,
            FlowCheckpoint checkpoint)
        {
            return new JObject
            {
                ["schemaVersion"] = SchemaVersion,
                ["profile"] = JObject.FromObject(
                    profile,
                    JsonSerializer.Create(Settings)),
                ["checkpoint"] = new JObject
                {
                    ["stableState"] = checkpoint.StableState.ToString(),
                    ["worldId"] = checkpoint.Battle == null
                        ? JValue.CreateNull()
                        : checkpoint.Battle.WorldId,
                    ["battleId"] = checkpoint.Battle == null
                        ? JValue.CreateNull()
                        : checkpoint.Battle.BattleId,
                    ["combatVictoryPreserved"] = checkpoint.CombatVictoryPreserved,
                    ["committedTrialIds"] = new JArray(checkpoint.CommittedTrialIds),
                    ["committedRewardIds"] = new JArray(checkpoint.CommittedRewardIds),
                    ["rewardSourceCompletionId"] = checkpoint.RewardSourceCompletionId == null
                        ? JValue.CreateNull()
                        : checkpoint.RewardSourceCompletionId,
                    ["rewardAuthorityReceiptId"] = checkpoint.RewardAuthorityReceiptId == null
                        ? JValue.CreateNull()
                        : checkpoint.RewardAuthorityReceiptId
                }
            };
        }

        private static bool TryLoad(string path, out RuntimeSessionSnapshot snapshot)
        {
            snapshot = null;
            if (!File.Exists(path))
                return false;

            try
            {
                var json = ReadBoundedUtf8(path);
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
                if (!(token is JObject root) || reader.Read())
                    return false;

                RequireExactMembers(root, RootMembers, "runtime session");
                if (ReadRequiredString(root, "schemaVersion") != SchemaVersion)
                    throw new InvalidDataException("Unsupported runtime session schema version.");
                if (!(root["profile"] is JObject profileObject))
                    throw new InvalidDataException("The runtime session profile must be an object.");
                if (!(root["checkpoint"] is JObject checkpointObject))
                    throw new InvalidDataException("The runtime session checkpoint must be an object.");

                var serializer = JsonSerializer.Create(Settings);
                var profile = profileObject.ToObject<ProfileDataV1>(serializer);
                profile?.Validate();
                if (profile == null)
                    throw new InvalidDataException("The runtime session profile is missing.");

                var checkpoint = ReadCheckpoint(checkpointObject);
                snapshot = new RuntimeSessionSnapshot(profile, checkpoint);
                return true;
            }
            catch (Exception error) when (
                error is IOException ||
                error is InvalidDataException ||
                error is UnauthorizedAccessException ||
                error is JsonException ||
                error is ArgumentException ||
                error is InvalidOperationException ||
                error is OverflowException)
            {
                snapshot = null;
                return false;
            }
        }

        private static FlowCheckpoint ReadCheckpoint(JObject source)
        {
            RequireExactMembers(source, CheckpointMembers, "runtime checkpoint");
            var stateName = ReadRequiredString(source, "stableState");
            if (!Enum.TryParse(stateName, ignoreCase: false, out FlowState state) ||
                !string.Equals(state.ToString(), stateName, StringComparison.Ordinal))
            {
                throw new InvalidDataException("The runtime checkpoint state is invalid.");
            }

            var worldId = ReadOptionalString(source, "worldId");
            var battleId = ReadOptionalString(source, "battleId");
            if ((worldId == null) != (battleId == null))
                throw new InvalidDataException("A checkpoint battle identity must be complete or absent.");
            var battle = worldId == null ? null : new FlowBattle(worldId, battleId);

            return new FlowCheckpoint(
                state,
                battle,
                ReadRequiredBoolean(source, "combatVictoryPreserved"),
                ReadIdentifiers(source, "committedTrialIds"),
                ReadIdentifiers(source, "committedRewardIds"),
                ReadOptionalString(source, "rewardSourceCompletionId"),
                ReadOptionalString(source, "rewardAuthorityReceiptId"));
        }

        private static void RequireExactMembers(
            JObject source,
            ISet<string> expected,
            string description)
        {
            var actual = source.Properties().Select(property => property.Name).ToArray();
            if (actual.Length != expected.Count || actual.Any(name => !expected.Contains(name)))
                throw new InvalidDataException($"The {description} has missing or unknown members.");
        }

        private static string[] ReadIdentifiers(JObject source, string name)
        {
            if (!(source[name] is JArray array))
                throw new InvalidDataException($"The checkpoint member '{name}' must be an array.");

            return array.Select(item =>
            {
                if (item.Type != JTokenType.String)
                    throw new InvalidDataException("Checkpoint identifiers must be strings.");
                var value = item.Value<string>();
                if (string.IsNullOrWhiteSpace(value))
                    throw new InvalidDataException("Checkpoint identifiers cannot be empty.");
                return value;
            }).ToArray();
        }

        private static string ReadRequiredString(JObject source, string name)
        {
            var token = source[name];
            if (token == null || token.Type != JTokenType.String)
                throw new InvalidDataException($"The member '{name}' must be a string.");
            var value = token.Value<string>();
            if (string.IsNullOrWhiteSpace(value))
                throw new InvalidDataException($"The member '{name}' cannot be empty.");
            return value;
        }

        private static string ReadOptionalString(JObject source, string name)
        {
            var token = source[name];
            if (token == null)
                throw new InvalidDataException($"The member '{name}' is required.");
            if (token.Type == JTokenType.Null)
                return null;
            if (token.Type != JTokenType.String)
                throw new InvalidDataException($"The member '{name}' must be a string or null.");
            var value = token.Value<string>();
            if (string.IsNullOrWhiteSpace(value))
                throw new InvalidDataException($"The member '{name}' cannot be empty.");
            return value;
        }

        private static bool ReadRequiredBoolean(JObject source, string name)
        {
            var token = source[name];
            if (token == null || token.Type != JTokenType.Boolean)
                throw new InvalidDataException($"The member '{name}' must be a Boolean.");
            return token.Value<bool>();
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

        private static string ReadBoundedUtf8(string path)
        {
            using var stream = new FileStream(
                path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read,
                bufferSize: 4096,
                FileOptions.SequentialScan);
            if (stream.Length > MaximumFileBytes)
                throw new InvalidDataException("The runtime session file exceeds the size limit.");

            using var reader = new StreamReader(
                stream,
                new UTF8Encoding(
                    encoderShouldEmitUTF8Identifier: false,
                    throwOnInvalidBytes: true),
                detectEncodingFromByteOrderMarks: true);
            return reader.ReadToEnd();
        }

        private static void EnsureDirectory(string path)
        {
            var parent = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(parent))
                throw new InvalidOperationException("A runtime session path must have a parent directory.");
            Directory.CreateDirectory(parent);
        }

        private static void DeleteIfPresent(string path)
        {
            if (File.Exists(path))
                File.Delete(path);
        }
    }
}
