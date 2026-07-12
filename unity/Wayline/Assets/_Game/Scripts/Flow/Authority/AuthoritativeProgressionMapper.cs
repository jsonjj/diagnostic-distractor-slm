using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Wayline.Learning.Contracts;

namespace Wayline.Flow.Authority
{
    /// <summary>
    /// Binds strict public progression responses to the expected Unity flow context.
    /// The receipt is a stable integrity identity for the validated public response;
    /// it is not a server signature and does not add a second authority.
    /// </summary>
    public static class AuthoritativeProgressionMapper
    {
        private const string ReceiptPrefix = "wayline.progression.v1:";
        private const string ReceiptMaterialVersion =
            "wayline.progression.receipt-material.v1";

        public static AuthoritativeTrialCompletion FromBattle(
            FlowBattle expectedBattle,
            string expectedBatchId,
            BattleComplete command,
            BattleCompleted response)
        {
            if (expectedBattle == null)
                throw new ArgumentNullException(nameof(expectedBattle));
            expectedBatchId = Require(expectedBatchId, nameof(expectedBatchId));
            if (command == null)
                throw new ArgumentNullException(nameof(command));
            if (response == null)
                throw new ArgumentNullException(nameof(response));

            StrictQuizValidator.Validate(command);
            StrictQuizValidator.Validate(response);
            if (!command.CombatWon)
            {
                throw new ArgumentException(
                    "A post-combat trial completion requires a won combat command.",
                    nameof(command));
            }
            RequireEqual(response.RequestId, command.RequestId, "request", nameof(response));
            RequireEqual(response.WorldId, expectedBattle.WorldId, "world", nameof(response));
            RequireEqual(response.BattleId, expectedBattle.BattleId, "battle", nameof(response));
            RequireEqual(response.BatchId, expectedBatchId, "batch", nameof(response));

            AuthoritativeNextStep next;
            if (!response.BossBattle)
            {
                if (response.WorldCleared || response.SealTrialRequired)
                {
                    throw new ArgumentException(
                        "A non-boss completion cannot clear a world or require a Seal Trial.",
                        nameof(response));
                }
                next = AuthoritativeNextStep.Reward;
            }
            else
            {
                if (response.WorldCleared == response.SealTrialRequired)
                {
                    throw new ArgumentException(
                        "A boss completion must clear the world or require a Seal Trial.",
                        nameof(response));
                }
                next = response.SealTrialRequired
                    ? AuthoritativeNextStep.SealTrial
                    : AuthoritativeNextStep.Reward;
            }

            return new AuthoritativeTrialCompletion(
                response.RequestId,
                Receipt(response),
                FlowTrialStage.Normal,
                expectedBattle,
                next);
        }

        public static AuthoritativeTrialCompletion FromSeal(
            FlowBattle expectedBattle,
            int expectedAttemptNumber,
            string expectedBatchId,
            SealTrialComplete command,
            SealTrialCompleted response)
        {
            if (expectedBattle == null)
                throw new ArgumentNullException(nameof(expectedBattle));
            if (expectedAttemptNumber < 1)
                throw new ArgumentOutOfRangeException(nameof(expectedAttemptNumber));
            expectedBatchId = Require(expectedBatchId, nameof(expectedBatchId));
            if (command == null)
                throw new ArgumentNullException(nameof(command));
            if (response == null)
                throw new ArgumentNullException(nameof(response));

            StrictQuizValidator.Validate(command);
            StrictQuizValidator.Validate(response);
            RequireEqual(response.RequestId, command.RequestId, "request", nameof(response));
            RequireEqual(response.WorldId, expectedBattle.WorldId, "world", nameof(response));
            if (response.AttemptNumber != expectedAttemptNumber)
            {
                throw new ArgumentException(
                    "The Seal completion belongs to a different attempt.",
                    nameof(response));
            }
            RequireEqual(response.BatchId, expectedBatchId, "batch", nameof(response));

            var next = response.WorldCleared
                ? AuthoritativeNextStep.Reward
                : response.AssistedRouteUnlocked
                    ? AuthoritativeNextStep.AssistedRoute
                    : AuthoritativeNextStep.SealTrial;
            return new AuthoritativeTrialCompletion(
                response.RequestId,
                Receipt(response),
                FlowTrialStage.Seal,
                expectedBattle,
                next);
        }

        public static AuthoritativeTrialCompletion FromAssisted(
            FlowBattle expectedBattle,
            string expectedRouteId,
            AssistedRouteComplete command,
            AssistedRouteCompleted response)
        {
            if (expectedBattle == null)
                throw new ArgumentNullException(nameof(expectedBattle));
            expectedRouteId = Require(expectedRouteId, nameof(expectedRouteId));
            if (command == null)
                throw new ArgumentNullException(nameof(command));
            if (response == null)
                throw new ArgumentNullException(nameof(response));

            StrictQuizValidator.Validate(command);
            StrictQuizValidator.Validate(response);
            RequireEqual(response.RequestId, command.RequestId, "request", nameof(response));
            RequireEqual(response.WorldId, expectedBattle.WorldId, "world", nameof(response));
            RequireEqual(response.RouteId, expectedRouteId, "route", nameof(response));
            if (response.Items.Count != command.Selections.Count)
            {
                throw new ArgumentException(
                    "The assisted completion selection count does not match its command.",
                    nameof(response));
            }
            for (var index = 0; index < response.Items.Count; index++)
            {
                var item = response.Items[index];
                var selection = command.Selections[index];
                if (!string.Equals(item.ItemId, selection.ItemId, StringComparison.Ordinal) ||
                    !string.Equals(
                        item.SelectedOptionId,
                        selection.OptionId,
                        StringComparison.Ordinal) ||
                    item.Confidence != selection.Confidence)
                {
                    throw new ArgumentException(
                        "The assisted completion does not echo its immutable selection.",
                        nameof(response));
                }
            }

            return new AuthoritativeTrialCompletion(
                response.RequestId,
                Receipt(response),
                FlowTrialStage.Assisted,
                expectedBattle,
                AuthoritativeNextStep.Reward);
        }

        private static string Receipt(BattleCompleted response)
        {
            return Hash(writer =>
            {
                writer.Add("battle-completed");
                writer.Add(response.SchemaVersion);
                writer.Add(response.RequestId);
                writer.Add(response.WorldId);
                writer.Add(response.BattleId);
                writer.Add(response.BatchId);
                writer.Add(response.FinalCorrect);
                writer.Add(response.ItemCount);
                writer.Add(response.BossBattle);
                writer.Add(response.WorldCleared);
                writer.Add(response.SealTrialRequired);
            });
        }

        private static string Receipt(SealTrialCompleted response)
        {
            return Hash(writer =>
            {
                writer.Add("seal-trial-completed");
                writer.Add(response.SchemaVersion);
                writer.Add(response.RequestId);
                writer.Add(response.WorldId);
                writer.Add(response.AttemptNumber);
                writer.Add(response.BatchId);
                writer.Add(response.FinalCorrect);
                writer.Add(response.ItemCount);
                writer.Add(response.Passed);
                writer.Add(response.WorldCleared);
                writer.Add(response.AssistedRouteUnlocked);
            });
        }

        private static string Receipt(AssistedRouteCompleted response)
        {
            return Hash(writer =>
            {
                writer.Add("assisted-route-completed");
                writer.Add(response.SchemaVersion);
                writer.Add(response.RequestId);
                writer.Add(response.WorldId);
                writer.Add(response.RouteId);
                writer.Add(response.WorkedExampleCount);
                writer.Add(response.SupportedMcqCount);
                writer.Add(response.FinalCorrect);
                writer.Add(response.WorldCleared);
                writer.Add(response.Items.Count);
                foreach (var item in response.Items)
                {
                    writer.Add(item.ItemId);
                    writer.Add(item.SelectedOptionId);
                    writer.Add(item.SelectedAnswer);
                    writer.Add(item.Confidence.ToString().ToLowerInvariant());
                    writer.Add(item.CorrectOptionId);
                    writer.Add(item.CorrectAnswer);
                    writer.Add(item.IsCorrect);
                    writer.Add(item.PossibleError);
                    writer.Add(item.ReliableMethod);
                    AddStrings(writer, item.TrustedSteps);
                    AddStrings(writer, item.CanonicalFeedback);
                }
            });
        }

        private static void AddStrings(
            LengthPrefixedWriter writer,
            IReadOnlyList<string> values)
        {
            writer.Add(values.Count);
            foreach (var value in values)
                writer.Add(value);
        }

        private static string Hash(Action<LengthPrefixedWriter> writeResponse)
        {
            using (var stream = new MemoryStream())
            {
                var writer = new LengthPrefixedWriter(stream);
                writer.Add(ReceiptMaterialVersion);
                writeResponse(writer);
                using (var sha256 = SHA256.Create())
                {
                    var digest = sha256.ComputeHash(stream.ToArray());
                    return ReceiptPrefix + ToLowerHex(digest);
                }
            }
        }

        private static string ToLowerHex(byte[] bytes)
        {
            var result = new StringBuilder(bytes.Length * 2);
            foreach (var value in bytes)
                result.Append(value.ToString("x2", CultureInfo.InvariantCulture));
            return result.ToString();
        }

        private static string Require(string value, string parameterName)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A stable identity is required.", parameterName);
            return value;
        }

        private static void RequireEqual(
            string actual,
            string expected,
            string identityName,
            string parameterName)
        {
            if (!string.Equals(actual, expected, StringComparison.Ordinal))
            {
                throw new ArgumentException(
                    "The authoritative response has a different " + identityName + " identity.",
                    parameterName);
            }
        }

        private sealed class LengthPrefixedWriter
        {
            private readonly Stream _stream;

            public LengthPrefixedWriter(Stream stream)
            {
                _stream = stream ?? throw new ArgumentNullException(nameof(stream));
            }

            public void Add(string value)
            {
                if (value == null)
                {
                    WriteLength(-1);
                    return;
                }
                var bytes = Encoding.UTF8.GetBytes(value);
                WriteLength(bytes.Length);
                _stream.Write(bytes, 0, bytes.Length);
            }

            public void Add(int value)
            {
                Add(value.ToString(CultureInfo.InvariantCulture));
            }

            public void Add(bool value)
            {
                Add(value ? "true" : "false");
            }

            private void WriteLength(int value)
            {
                unchecked
                {
                    _stream.WriteByte((byte)(value >> 24));
                    _stream.WriteByte((byte)(value >> 16));
                    _stream.WriteByte((byte)(value >> 8));
                    _stream.WriteByte((byte)value);
                }
            }
        }
    }
}
