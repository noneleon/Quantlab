// QuantLab gateway brokerage for LEAN (OMS-007, DEC-0007 option A, DEC-0031).
//
// Every order the algorithm places is forwarded to the QuantLab OMS through the local gateway, so it passes the
// central risk gate, the kill switch and the paper broker exactly like a manual order; this plugin never talks to a
// broker. Fills come back as broker-confirmed execution reports and become LEAN order events.
//
// Safety rules:
// * Paper only: the gateway refuses anything else; this plugin has no live mode.
// * An order is reported Invalid to LEAN only when QuantLab says it was definitely not placed (risk rejection,
//   broker rejection, refused by the gateway). If the outcome is unknown (timeout, dropped connection), LEAN keeps
//   it as Submitted and the gateway's replay after reconnecting resolves it — a real order is never forgotten.
// * Order updates are not supported (cancel and place a new order); unsupported order types, time-in-force values
//   and security types are refused locally before anything is sent.
// * Unfinished orders of an earlier run block new orders at the gateway; LEAN is told through a brokerage message.

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
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
        private readonly ConcurrentDictionary<int, Order> _orders = new();
        private readonly ConcurrentDictionary<int, decimal> _filled = new();
        private readonly ConcurrentDictionary<string, byte> _execs = new();
        private readonly ConcurrentDictionary<(int, string), byte> _states = new();
        private readonly Func<string, Symbol> _resolve;

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
            _client.Disconnected += reason => OnMessage(new BrokerageMessageEvent(
                BrokerageMessageType.Disconnect, "QuantLabDisconnected", $"QuantLab gateway connection lost: {reason}"));
        }

        public override bool IsConnected => _client.IsConnected;

        public string? Run => _client.Run;

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
        }

        public override void Disconnect() => _client.Dispose();

        public override void Dispose()
        {
            _client.Dispose();
            base.Dispose();
        }

        /// <summary>Cancel the unfinished orders of earlier runs of this deployment (risk-reducing).</summary>
        public JsonElement? CancelOrphans() =>
            _client.Request(new { type = "cancel_orphans" }, m => Type(m) == "orphans");

        // ---- orders ------------------------------------------------------------------------------------
        public override bool PlaceOrder(Order order)
        {
            var refusal = Unsupported(order);
            if (refusal != null)
            {
                Emit(order, OrderStatus.Invalid, refusal);
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
            JsonElement? reply;
            try
            {
                reply = _client.Request(message, m => IsReplyTo(m, order.Id, "place"));
            }
            catch (GatewayException exc) // not connected: nothing was sent
            {
                Emit(order, OrderStatus.Invalid, $"QuantLab gateway unavailable: {exc.Message}");
                return false;
            }
            if (reply == null)
            {
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabUnknownOutcome",
                    $"order {order.Id}: no reply from the QuantLab gateway; outcome unknown, resolved after reconnect"));
                return true;
            }
            var r = reply.Value;
            if (Type(r) == "error")
            {
                var definite = r.TryGetProperty("outcome", out var o) && o.GetString() == "not_placed";
                if (definite)
                {
                    Emit(order, OrderStatus.Invalid, "QuantLab refused the order: " + Text(r, "message"));
                    return false;
                }
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabUnknownOutcome",
                    $"order {order.Id}: {Text(r, "message")}; outcome unknown, resolved by reconciliation"));
                return true;
            }
            HandleOrderState(r); // the reply is an "order" message: same handling as an asynchronous one
            return !IsRejected(Text(r, "state"));
        }

        public override bool UpdateOrder(Order order)
        {
            OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabNoUpdates",
                $"order {order.Id}: order updates are not supported by the QuantLab gateway; cancel and place a new order"));
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
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabCancelFailed",
                    $"order {order.Id}: cancel not sent: {exc.Message}"));
                return false;
            }
            if (reply == null || Type(reply.Value) == "error")
            {
                var why = reply == null ? "no reply" : Text(reply.Value, "message");
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabCancelFailed",
                    $"order {order.Id}: cancel failed: {why}"));
                return false;
            }
            HandleOrderState(reply.Value);
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

        // ---- gateway → LEAN ---------------------------------------------------------------------------
        private void OnGatewayMessage(JsonElement msg)
        {
            switch (Type(msg))
            {
                case "fill":
                    HandleFill(msg);
                    break;
                case "order":
                    HandleOrderState(msg);
                    break;
                case "error" when !msg.TryGetProperty("id", out var id) || id.ValueKind != JsonValueKind.Number:
                    OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabGateway", Text(msg, "message")));
                    break;
            }
        }

        private void HandleFill(JsonElement msg)
        {
            var exec = Text(msg, "exec_id");
            if (!_execs.TryAdd(exec, 0)) return; // replayed after a reconnect: already applied
            var id = msg.GetProperty("id").GetInt32();
            if (!_orders.TryGetValue(id, out var order))
            {
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabUnknownFill",
                    $"fill {exec} for order {id}, which this algorithm instance did not place"));
                return;
            }
            var quantity = msg.GetProperty("quantity").GetDecimal();
            var filled = _filled.AddOrUpdate(id, Math.Abs(quantity), (_, f) => f + Math.Abs(quantity));
            var status = filled >= Math.Abs(order.Quantity) ? OrderStatus.Filled : OrderStatus.PartiallyFilled;
            var fee = new OrderFee(new CashAmount(msg.GetProperty("commission").GetDecimal(), _currency));
            var ts = DateTime.Parse(Text(msg, "ts"), CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal);
            OnOrderEvent(new OrderEvent(order, ts, fee, $"QuantLab fill {exec} ({Text(msg, "price_state")} prices)")
            {
                Status = status,
                FillQuantity = quantity,
                FillPrice = msg.GetProperty("price").GetDecimal(),
            });
        }

        private void HandleOrderState(JsonElement msg)
        {
            var id = msg.GetProperty("id").GetInt32();
            var state = Text(msg, "state");
            if (!_orders.TryGetValue(id, out var order) || !_states.TryAdd((id, state), 0)) return;
            var reason = Text(msg, "reason");
            switch (state)
            {
                case "SUBMITTED":
                case "ACCEPTED":
                    Emit(order, OrderStatus.Submitted, "accepted by QuantLab (risk gate passed)");
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
                    OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabUnknownOutcome",
                        $"order {id}: {state}; QuantLab reconciliation resolves it (never resubmitted)"));
                    break;
                // PARTIALLY_FILLED / FILLED come with their fills (HandleFill), which carry the quantities
            }
        }

        private void Emit(Order order, OrderStatus status, string message) =>
            OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero, message) { Status = status });

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

        /// <summary>The gateway's direct answer to our place/cancel request (pushed updates carry no "reply").</summary>
        private static bool IsReplyTo(JsonElement m, int id, string request) =>
            Type(m) is "order" or "error"
            && Text(m, "reply") == request
            && m.TryGetProperty("id", out var i) && i.ValueKind == JsonValueKind.Number && i.GetInt32() == id;

        private static string Type(JsonElement m) => Text(m, "type");

        private static string Text(JsonElement m, string name) =>
            m.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
    }
}
