using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace Wayline.Learning.Client
{
    public sealed class UnityWebRequestTransport : IWaylineHttpTransport, IDisposable
    {
        private readonly WaylineForgeConnection _connection;
        private readonly HttpClient _client;

        public UnityWebRequestTransport(WaylineForgeConnection connection)
        {
            _connection = connection ?? throw new ArgumentNullException(nameof(connection));
            _client = new HttpClient(
                new HttpClientHandler
                {
                    UseProxy = false
                },
                true)
            {
                Timeout = TimeSpan.FromSeconds(15)
            };
        }

        public async Task<WaylineHttpResponse> SendAsync(
            WaylineHttpRequest request,
            CancellationToken cancellationToken)
        {
            if (request == null)
                throw new ArgumentNullException(nameof(request));
            using (var message = CreateRequest(request))
            using (var response = await _client.SendAsync(
                message,
                HttpCompletionOption.ResponseContentRead,
                cancellationToken))
            {
                var body = response.Content == null
                    ? string.Empty
                    : await response.Content.ReadAsStringAsync();
                return new WaylineHttpResponse((long)response.StatusCode, body);
            }
        }

        public void Dispose()
        {
            _client.Dispose();
        }

        internal HttpRequestMessage CreateRequest(WaylineHttpRequest request)
        {
            var method = request.Method == WaylineHttpMethod.Get
                ? HttpMethod.Get
                : HttpMethod.Post;
            var message = new HttpRequestMessage(
                method,
                new Uri(_connection.BaseUri, request.RelativePath));
            message.Headers.Authorization = new AuthenticationHeaderValue(
                "Bearer",
                _connection.LaunchToken);
            if (!message.Headers.TryAddWithoutValidation("Origin", _connection.UnityOrigin))
                throw new InvalidOperationException("Origin header is unavailable");
            if (request.SessionId != null &&
                !message.Headers.TryAddWithoutValidation(
                    "X-Wayline-Session-Id",
                    request.SessionId))
            {
                throw new InvalidOperationException("session header is unavailable");
            }
            if (request.Method == WaylineHttpMethod.Post)
            {
                message.Content = new StringContent(
                    request.Body ?? string.Empty,
                    Encoding.UTF8,
                    "application/json");
            }
            return message;
        }
    }
}
