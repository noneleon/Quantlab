// QuantLab gateway brokerage for LEAN (OMS-007, DEC-0007 option A, DEC-0031).
//
// Every order the algorithm places is forwarded to the QuantLab OMS through the local gateway, so it passes the
// central risk gate, the kill switch and the paper broker exactly like a manual order; this plugin never talks to a
// broker. Fills come back as broker-confirmed execution reports and become LEAN order events.
//
// Safety rules:
// * Paper only: the gateway refuses anything else and the client refuses a gateway that is not in paper mode.
// * An order is reported Invalid to LEAN only when QuantLab says it was definitely not placed (risk rejection,
//   broker rejection, refused by the gateway before anything was stored). If the outcome is unknown (timeout,
//   dropped connection, failure after the OMS stored the order), LEAN keeps it as Submitted; the place request is
//   re-sent after reconnecting (idempotent within the run) and the gateway's replay resolves it.
// * Order events are emitted from one thread only (the gateway reader), in the gateway's stream order — fills
//   before the final state of their order.
// * Order updates are refused by the brokerage model (cancel and place a new order); unsupported order types,
//   time-in-force values and security types are refused before anything is sent.
// * A dropped connection is re-established automatically (same run, backoff up to 30 s).
// * Unfinished orders of an earlier run block new orders at the gateway; LEAN is told through a brokerage message.

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;
using System.Threading;
using QuantConnect;
using QuantConnect.Brokerages;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.TimeInForces;
using QuantConnect.Securities;

namespace QuantLab.Brokerages
{
    public class QuantLabBrokerage : Brokerage
    {
        private readonly GatewayClient _client;
        private readonly string _currency;
        private readonly Func<string, Symbol> _resolve;
        private readonly ConcurrentDictionary<int, Order> _orders = new();
        private readonly ConcurrentDictionary<int, decimal> _filled = new();
        private readonly ConcurrentDictionary<string, byte> _execs = new();
        private readonly ConcurrentDictionary<int, string> _lastState = new();
        private readonly ConcurrentDictionary<int, Dictionary<string, object?>> _unacked = new(); // place sent, no reply yet
        private readonly object _reconnectLock = new();
        private volatile bool _disposed;
        private Thread? _reconnector;

        /// <param name="resolveSymbol">Ticker to the algorithm's own <see cref="Symbol"/> (holdings must use the
        /// algorithm's symbols); the factory resolves from the algorithm's securities.</param>
        public QuantLabBrokerage(
            GatewayEndpoint endpoint, Func<string, Symbol> resolveSymbol, string accountCurrency = "USD", TimeSpan? timeout = null)
            : this(new GatewayClient(endpoint, timeout ?? TimeSpan.FromSeconds(10)), resolveSymbol, accountCurrency)
        {
        }

        public QuantLabBrokerage(GatewayClient client, Func<string, Symbol> resolveSymbol, string accountCurrency = "USD")
            : base("QuantLab")
        {
            _client = client;
            _resolve = resolveSymbol;
            _currency = accountCurrency;
            _client.MessageReceived += OnGatewayMessage;
            _client.Disconnected += OnDisconnected;
        }

        public override bool IsConnected => _client.IsConnected;

        public string? Run => _client.Run;

        /// <summary>First back-off of the automatic reconnect (doubles up to 30 s).</summary>
        public TimeSpan ReconnectDelay { get; set; } = TimeSpan.FromSeconds(1);

        internal GatewayClient Client => _client;

        public override void Connect()
        {
            _client.Connect();
            OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Reconnect, "QuantLabConnected",
                $"connected to QuantLab account {_client.Account} (paper), run {_client.Run}"));
            if (_client.Orphans.Count > 0)
            {
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabOrphans",
                    $"{_client.Orphans.Count} order(s) of an earlier run are still open " +
                    $"({string.Join(", ", _client.Orphans)}); new orders are refused until they are cancelled or resolved"));
            }
            foreach (var pending in _unacked.Values) // outcome unknown before the drop: ask again (idempotent)
            {
                try { _client.Send(pending); } catch (GatewayException) { }
            }
        }

        public override void Disconnect() => _client.Dispose();

        public override void Dispose()
        {
            _disposed = true;
            _client.Dispose();
            base.Dispose();
        }

        /// <summary>Cancel the unfinished orders of earlier runs of this deployment (risk-reducing).</summary>
        public JsonElement? CancelOrphans() =>
            _client.Request(new { type = "cancel_orphans" }, m => Type(m) == "orphans");

        // ---- connection ---------------------------------------------------------------------------------
        private void OnDisconnected(string reason)
        {
            OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Disconnect, "QuantLabDisconnected",
                $"QuantLab gateway connection lost: {reason}; reconnecting"));
            lock (_reconnectLock)
            {
                if (_disposed || _reconnector is { IsAlive: true }) return;
                _reconnector = new Thread(ReconnectLoop) { IsBackground = true, Name = "QuantLabGatewayReconnect" };
                _reconnector.Start();
            }
        }

        private void ReconnectLoop()
        {
            var delay = ReconnectDelay;
            while (!_disposed && !_client.IsConnected)
            {
                Thread.Sleep(delay);
                if (_disposed) return;
                try
                {
                    Connect(); // same run: the gateway replays this run's states and fills
                    return;
                }
                catch (GatewayException exc)
                {
                    Warn("QuantLabReconnectFailed", exc.Message);
                    delay = TimeSpan.FromSeconds(Math.Min(30, Math.Max(1, delay.TotalSeconds * 2)));
                }
            }
        }

        // ---- orders ------------------------------------------------------------------------------------
        public override bool PlaceOrder(Order order)
        {
            var refusal = Unsupported(order);
            if (refusal != null)
            {
                Emit(order, OrderStatus.Invalid, refusal);
                return false;
            }
            if (!_client.IsConnected)
            {
                Emit(order, OrderStatus.Invalid, "QuantLab gateway not connected: order not sent");
                return false;
            }
            _orders[order.Id] = order;
            var message = new Dictionary<string, object?>
            {
                ["type"] = "place",
                ["id"] = order.Id,
                ["symbol"] = order.Symbol.Value,
                ["quantity"] = order.Quantity,
                ["order_type"] = OrderTypeName(order),
                ["limit_price"] = LimitPrice(order),
                ["stop_price"] = StopPrice(order),
                ["time_in_force"] = order.TimeInForce is GoodTilCanceledTimeInForce ? "gtc" : "day",
            };
            _unacked[order.Id] = message;
            JsonElement? reply;
            try
            {
                reply = _client.Request(message, m => IsReplyTo(m, order.Id, "place"));
            }
            catch (GatewayException exc) // the send failed midway: it may have reached the gateway
            {
                Warn("QuantLabUnknownOutcome", $"order {order.Id}: {exc.Message}; outcome unknown, re-sent after reconnect");
                return true;
            }
            if (reply == null)
            {
                Warn("QuantLabUnknownOutcome", $"order {order.Id}: no reply from the QuantLab gateway yet; outcome unknown");
                return true;
            }
            // Events for the reply (Submitted / Invalid) are emitted by the reader thread (OnGatewayMessage).
            var r = reply.Value;
            if (Type(r) == "error") return !IsDefinitelyNotPlaced(r);
            return !IsRejected(Text(r, "state"));
        }

        public override bool UpdateOrder(Order order)
        {
            Warn("QuantLabNoUpdates",
                $"order {order.Id}: order updates are not supported by the QuantLab gateway; cancel and place a new order");
            return false;
        }

        public override bool CancelOrder(Order order)
        {
            JsonElement? reply;
            try
            {
                reply = _client.Request(new { type = "cancel", id = order.Id }, m => IsReplyTo(m, order.Id, "cancel"));
            }
            catch (GatewayException exc)
            {
                Warn("QuantLabCancelFailed", $"order {order.Id}: cancel not sent: {exc.Message}");
                return false;
            }
            if (reply == null || Type(reply.Value) == "error")
            {
                var why = reply == null ? "no reply" : Text(reply.Value, "message");
                Warn("QuantLabCancelFailed", $"order {order.Id}: cancel failed: {why}");
                return false;
            }
            return Text(reply.Value, "state") is "CANCELLED" or "PENDING_CANCEL";
        }

        /// <summary>A new run has no open orders of its own; earlier runs' orders are reported as orphans instead.</summary>
        public override List<Order> GetOpenOrders() => new();

        public override List<Holding> GetAccountHoldings()
        {
            var snapshot = Snapshot();
            var holdings = new List<Holding>();
            foreach (var p in snapshot.GetProperty("positions").EnumerateArray())
            {
                holdings.Add(new Holding
                {
                    Symbol = _resolve(p.GetProperty("symbol").GetString()!),
                    Quantity = p.GetProperty("quantity").GetDecimal(),
                    AveragePrice = p.TryGetProperty("average_price", out var ap) ? ap.GetDecimal() : 0m,
                    CurrencySymbol = Currencies.GetCurrencySymbol(_currency),
                });
            }
            return holdings;
        }

        public override List<CashAmount> GetCashBalance() =>
            new() { new CashAmount(Snapshot().GetProperty("cash").GetDecimal(), _currency) };

        private JsonElement Snapshot() =>
            _client.Request(new { type = "snapshot" }, m => Type(m) == "snapshot")
            ?? throw new GatewayException("QuantLab gateway did not answer the snapshot request");

        // ---- gateway → LEAN (reader thread only) -------------------------------------------------------
        private void OnGatewayMessage(JsonElement msg)
        {
            switch (Type(msg))
            {
                case "fill":
                    HandleFill(msg);
                    break;
                case "order":
                    if (Text(msg, "reply") == "place") _unacked.TryRemove(Id(msg), out _);
                    HandleOrderState(msg);
                    break;
                case "error":
                    HandleError(msg);
                    break;
            }
        }

        private void HandleError(JsonElement msg)
        {
            var id = Id(msg);
            if (id < 0)
            {
                Warn("QuantLabGateway", Text(msg, "message"));
                return;
            }
            if (Text(msg, "reply") != "place") return; // cancel failures are reported by CancelOrder
            _unacked.TryRemove(id, out _);
            if (!_orders.TryGetValue(id, out var order)) return;
            if (IsDefinitelyNotPlaced(msg) && !_lastState.ContainsKey(id)) // also when the reply came late
            {
                _lastState[id] = "NOT_PLACED";
                Emit(order, OrderStatus.Invalid, "QuantLab refused the order: " + Text(msg, "message"));
            }
            else
            {
                Warn("QuantLabUnknownOutcome", $"order {id}: {Text(msg, "message")}; resolved by reconciliation");
            }
        }

        private void HandleFill(JsonElement msg)
        {
            var exec = Text(msg, "exec_id");
            if (exec.Length == 0)
            {
                Warn("QuantLabBadFill", "fill without an execution id ignored (reconciliation will show it)");
                return;
            }
            if (!_execs.TryAdd(exec, 0)) return; // replayed after a reconnect: already applied
            var id = Id(msg);
            if (!_orders.TryGetValue(id, out var order))
            {
                Warn("QuantLabUnknownFill", $"fill {exec} for order {id}, which this algorithm instance did not place");
                return;
            }
            var quantity = msg.GetProperty("quantity").GetDecimal();
            var filled = _filled.AddOrUpdate(id, Math.Abs(quantity), (_, f) => f + Math.Abs(quantity));
            var status = filled >= Math.Abs(order.Quantity) - 0.000001m ? OrderStatus.Filled : OrderStatus.PartiallyFilled;
            var fee = new OrderFee(new CashAmount(msg.GetProperty("commission").GetDecimal(), _currency));
            var ts = DateTime.Parse(Text(msg, "ts"), CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal);
            OnOrderEvent(new OrderEvent(order, ts, fee, $"QuantLab fill {exec} ({Text(msg, "price_state")} prices)")
            {
                Status = status,
                FillQuantity = quantity, // signed: negative for sells
                FillPrice = msg.GetProperty("price").GetDecimal(),
            });
        }

        private void HandleOrderState(JsonElement msg)
        {
            var id = Id(msg);
            var state = Text(msg, "state");
            if (!_orders.TryGetValue(id, out var order)) return;
            if (_lastState.TryGetValue(id, out var last) && last == state) return; // replay of the same state
            _lastState[id] = state;
            var reason = Text(msg, "reason");
            switch (state)
            {
                case "SUBMITTED":
                case "ACCEPTED":
                    Emit(order, OrderStatus.Submitted, "working at QuantLab (risk gate passed)");
                    break;
                case "PENDING_CANCEL":
                    Emit(order, OrderStatus.CancelPending, "cancel requested");
                    break;
                case "CANCELLED":
                case "EXPIRED":
                    Emit(order, OrderStatus.Canceled, state == "EXPIRED" ? "expired (DAY order)" : "cancelled");
                    break;
                case "RISK_REJECTED":
                case "REJECTED":
                case "NOT_PLACED":
                    Emit(order, OrderStatus.Invalid, $"QuantLab {state.ToLowerInvariant().Replace('_', ' ')}: {reason}");
                    break;
                case "SUBMIT_UNKNOWN":
                case "PENDING_SUBMIT":
                    Warn("QuantLabUnknownOutcome", $"order {id}: {state}; QuantLab reconciliation resolves it (never resubmitted)");
                    break;
                // PARTIALLY_FILLED / FILLED come with their fills (HandleFill), which carry the quantities
            }
        }

        private void Emit(Order order, OrderStatus status, string message) =>
            OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero, message) { Status = status });

        private void Warn(string code, string message) =>
            OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, code, message));

        // ---- mapping -----------------------------------------------------------------------------------
        public static string? Unsupported(Order order)
        {
            if (order.SecurityType != SecurityType.Equity) return $"QuantLab trades equities only ({order.SecurityType})";
            if (order.Quantity == 0) return "quantity must not be 0";
            if (order.TimeInForce is not (DayTimeInForce or GoodTilCanceledTimeInForce))
                return $"time in force {order.TimeInForce.GetType().Name} is not supported (day or GTC)";
            return order.Type is OrderType.Market or OrderType.Limit or OrderType.StopMarket or OrderType.StopLimit
                ? null
                : $"order type {order.Type} is not supported (market, limit, stop, stop-limit)";
        }

        private static string OrderTypeName(Order order) => order.Type switch
        {
            OrderType.Limit => "limit",
            OrderType.StopMarket => "stop",
            OrderType.StopLimit => "stop_limit",
            _ => "market",
        };

        private static decimal? LimitPrice(Order order) => order switch
        {
            LimitOrder l => l.LimitPrice,
            StopLimitOrder sl => sl.LimitPrice,
            _ => null,
        };

        private static decimal? StopPrice(Order order) => order switch
        {
            StopMarketOrder s => s.StopPrice,
            StopLimitOrder sl => sl.StopPrice,
            _ => null,
        };

        private static bool IsRejected(string state) => state is "RISK_REJECTED" or "REJECTED" or "NOT_PLACED";

        private static bool IsDefinitelyNotPlaced(JsonElement m) =>
            m.TryGetProperty("outcome", out var o) && o.GetString() == "not_placed";

        /// <summary>The gateway's direct answer to our place/cancel request (pushed updates carry no "reply").</summary>
        private static bool IsReplyTo(JsonElement m, int id, string request) =>
            Type(m) is "order" or "error" && Text(m, "reply") == request && Id(m) == id;

        private static int Id(JsonElement m) =>
            m.TryGetProperty("id", out var i) && i.ValueKind == JsonValueKind.Number ? i.GetInt32() : -1;

        private static string Type(JsonElement m) => Text(m, "type");

        private static string Text(JsonElement m, string name) =>
            m.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
    }
}
