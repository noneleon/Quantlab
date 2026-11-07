// Client side of the QuantLab LEAN gateway protocol (OMS-007, DEC-0031): newline-delimited JSON over a loopback
// TCP connection. Plain .NET (no LEAN types) so it is testable on its own.
//
// * The first message is `hello` with the token from the gateway's run file. A new algorithm start sends no run
//   id and adopts the one the gateway issues in `welcome`; reconnects send that run id again, so orders placed
//   in this run are recognised (idempotent) and never sent twice.
// * `Request` sends a message and waits for the matching reply; every received message is also raised on
//   `MessageReceived`, so fills and state changes reach the brokerage however they arrive.
// * A dropped connection raises `Disconnected`; `Connect` can be called again and re-joins the same run.

using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace QuantLab.Brokerages
{
    /// <summary>Connection details from QUANTLAB_HOME/run/gateway-&lt;deployment&gt;.json (owner-only file).</summary>
    public sealed record GatewayEndpoint(string Deployment, string Host, int Port, string Token)
    {
        public static GatewayEndpoint FromRunFile(string path)
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            var r = doc.RootElement;
            return new GatewayEndpoint(
                r.GetProperty("deployment").GetString() ?? throw new InvalidDataException("deployment missing"),
                r.GetProperty("host").GetString() ?? "127.0.0.1",
                r.GetProperty("port").GetInt32(),
                r.GetProperty("token").GetString() ?? throw new InvalidDataException("token missing"));
        }

        public override string ToString() => $"{Deployment}@{Host}:{Port}"; // never the token
    }

    public sealed class GatewayException : Exception
    {
        public GatewayException(string message) : base(message) { }
    }

    public sealed class GatewayClient : IDisposable
    {
        private readonly GatewayEndpoint _endpoint;
        private readonly TimeSpan _timeout;
        private readonly object _sync = new();
        private readonly List<(Func<JsonElement, bool> Match, TaskCompletionSource<JsonElement> Reply)> _waiters = new();
        private TcpClient? _tcp;
        private NetworkStream? _stream;
        private Thread? _reader;
        private volatile bool _connected;
        private int _generation; // a reader of an older connection never touches a newer one

        public GatewayClient(GatewayEndpoint endpoint, TimeSpan timeout)
        {
            _endpoint = endpoint;
            _timeout = timeout;
        }

        /// <summary>The run id the gateway issued for this algorithm start (null before the first connect).</summary>
        public string? Run { get; private set; }

        /// <summary>Unfinished orders of earlier runs reported at connect (new orders are refused while any exist).</summary>
        public IReadOnlyList<string> Orphans { get; private set; } = Array.Empty<string>();

        public string Account { get; private set; } = "";

        public bool IsConnected => _connected;

        public event Action<JsonElement>? MessageReceived;

        public event Action<string>? Disconnected;

        public void Connect()
        {
            lock (_sync)
            {
                CloseSocket();
                var tcp = new TcpClient { NoDelay = true };
                bool connected;
                try
                {
                    connected = tcp.ConnectAsync(_endpoint.Host, _endpoint.Port).Wait(_timeout);
                }
                catch (AggregateException exc) // refused / unreachable: surfaced as GatewayException like any failure
                {
                    tcp.Dispose();
                    var reason = exc.InnerException?.Message ?? exc.Message;
                    throw new GatewayException($"QuantLab gateway {_endpoint} not reachable: {reason}");
                }
                if (!connected)
                {
                    tcp.Dispose();
                    throw new GatewayException($"QuantLab gateway {_endpoint} did not accept the connection");
                }
                _tcp = tcp;
                _stream = tcp.GetStream();
                _stream.ReadTimeout = (int)_timeout.TotalMilliseconds;
                var hello = new Dictionary<string, object?>
                {
                    ["type"] = "hello",
                    ["token"] = _endpoint.Token,
                    ["deployment"] = _endpoint.Deployment,
                };
                if (Run != null) hello["run"] = Run; // a reconnect re-joins this run
                JsonElement welcome;
                try
                {
                    WriteLine(hello);
                    welcome = ReadLineBlocking();
                }
                catch (Exception exc) when (exc is IOException or JsonException or SocketException)
                {
                    CloseSocket();
                    throw new GatewayException($"QuantLab gateway {_endpoint} did not complete the handshake: {exc.Message}");
                }
                if (welcome.GetProperty("type").GetString() != "welcome")
                {
                    CloseSocket();
                    throw new GatewayException("QuantLab gateway refused the connection: " + Describe(welcome));
                }
                if (welcome.TryGetProperty("mode", out var mode) && mode.GetString() != "paper")
                {
                    CloseSocket();
                    throw new GatewayException("QuantLab gateway is not in paper mode; refusing to trade");
                }
                Run = welcome.GetProperty("run").GetString();
                Account = welcome.TryGetProperty("account", out var a) ? a.GetString() ?? "" : "";
                var orphans = new List<string>();
                if (welcome.TryGetProperty("orphans", out var o) && o.ValueKind == JsonValueKind.Array)
                {
                    foreach (var item in o.EnumerateArray()) orphans.Add(item.GetString() ?? "");
                }
                Orphans = orphans;
                _stream.ReadTimeout = Timeout.Infinite;
                _connected = true;
                var generation = _generation;
                var stream = _stream;
                _reader = new Thread(() => ReadLoop(stream, generation)) { IsBackground = true, Name = "QuantLabGatewayReader" };
                _reader.Start();
            }
        }

        /// <summary>Send a message and wait for the first reply accepted by <paramref name="isReply"/>.</summary>
        /// <returns>The reply, or null on timeout (the outcome is then unknown).</returns>
        public JsonElement? Request(object message, Func<JsonElement, bool> isReply, TimeSpan? timeout = null)
        {
            var waiter = (isReply, new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously));
            lock (_waiters) _waiters.Add(waiter);
            try
            {
                Send(message);
                return waiter.Item2.Task.Wait(timeout ?? _timeout) ? waiter.Item2.Task.Result : null;
            }
            finally
            {
                lock (_waiters) _waiters.Remove(waiter);
            }
        }

        public void Send(object message)
        {
            lock (_sync)
            {
                if (!_connected || _stream == null) throw new GatewayException("not connected to the QuantLab gateway");
                try
                {
                    WriteLine(message);
                }
                catch (Exception exc) when (exc is IOException or ObjectDisposedException or SocketException)
                {
                    throw new GatewayException($"sending to the QuantLab gateway failed: {exc.Message}");
                }
            }
        }

        /// <summary>Tests only: drop the socket as a network failure would.</summary>
        internal void DropConnection()
        {
            lock (_sync) _tcp?.Client.Shutdown(SocketShutdown.Both);
        }

        private void WriteLine(object message)
        {
            var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(message) + "\n");
            _stream!.Write(bytes, 0, bytes.Length);
        }

        private readonly StringBuilder _line = new();

        private JsonElement ReadLineBlocking()
        {
            var buffer = new byte[1];
            _line.Clear();
            while (true)
            {
                var n = _stream!.Read(buffer, 0, 1);
                if (n == 0) throw new GatewayException("QuantLab gateway closed the connection");
                if (buffer[0] == (byte)'\n')
                {
                    if (_line.Length == 0) continue;
                    using var doc = JsonDocument.Parse(_line.ToString());
                    return doc.RootElement.Clone();
                }
                _line.Append((char)buffer[0]); // ASCII JSON from the gateway; non-ASCII arrives \u-escaped
            }
        }

        private void ReadLoop(NetworkStream stream, int generation)
        {
            string reason = "connection closed";
            try
            {
                using var reader = new StreamReader(stream, Encoding.UTF8, false, 4096, leaveOpen: true);
                string? line;
                while ((line = reader.ReadLine()) != null)
                {
                    if (line.Length == 0) continue;
                    JsonElement msg;
                    using (var doc = JsonDocument.Parse(line)) msg = doc.RootElement.Clone();
                    lock (_waiters)
                    {
                        foreach (var (match, reply) in _waiters)
                        {
                            if (!reply.Task.IsCompleted && match(msg)) { reply.TrySetResult(msg); break; }
                        }
                    }
                    if (generation != Volatile.Read(ref _generation)) return; // superseded by a reconnect
                    MessageReceived?.Invoke(msg);
                }
            }
            catch (Exception exc) when (exc is IOException or ObjectDisposedException or JsonException or SocketException)
            {
                reason = exc.Message;
            }
            lock (_sync)
            {
                if (generation != _generation) return; // an older connection: the current one is unaffected
                _connected = false;
            }
            Disconnected?.Invoke(reason);
        }

        private static string Describe(JsonElement msg) =>
            msg.TryGetProperty("message", out var m) ? m.GetString() ?? "" : msg.ToString();

        private void CloseSocket()
        {
            _connected = false;
            _generation++; // an intentional close: the old reader ends silently (no Disconnected event)
            try { _stream?.Dispose(); } catch (IOException) { }
            try { _tcp?.Dispose(); } catch (SocketException) { }
            _stream = null;
            _tcp = null;
        }

        public void Dispose()
        {
            lock (_sync) CloseSocket();
        }
    }
}
