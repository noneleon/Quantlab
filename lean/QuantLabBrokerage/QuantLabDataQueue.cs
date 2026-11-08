// Live prices for a LEAN deployment from QuantLab (5c-4): the algorithm sees the same quotes the QuantLab risk
// gate checks (QuantLab's configured provider — mock replay or IBKR read-only), through the shared gateway
// connection. LEAN config: "data-queue-handler": ["QuantLabDataQueue"].
//
// Quotes carry their data state; anything but LIVE (e.g. SIMULATED replay, DELAYED) is reported once per symbol
// through the algorithm's log, so a strategy can never mistake simulated prices for live ones silently.

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using QuantConnect;
using QuantConnect.Configuration;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Logging;
using QuantConnect.Packets;
using QuantConnect.Util;

namespace QuantLab.Brokerages
{
    public class QuantLabDataQueue : IDataQueueHandler
    {
        private readonly IDataAggregator _aggregator;
        private readonly EventBasedDataQueueHandlerSubscriptionManager _subscriptions = new();
        private readonly ConcurrentDictionary<string, (Symbol Symbol, NodaTime.DateTimeZone ExchangeTimeZone)> _symbols = new();
        private readonly ConcurrentDictionary<string, string> _warnedStates = new();
        private GatewayClient? _client;

        /// <summary>Created by LEAN's composer; the gateway connection is the brokerage's (shared per run file).</summary>
        public QuantLabDataQueue()
            : this(Composer.Instance.GetExportedValueByTypeName<IDataAggregator>(
                Config.Get("data-aggregator", "QuantConnect.Lean.Engine.DataFeeds.AggregationManager"), forceTypeNameOnExisting: false))
        {
        }

        public QuantLabDataQueue(IDataAggregator aggregator, GatewayClient? client = null)
        {
            _aggregator = aggregator;
            _subscriptions.SubscribeImpl = (symbols, _) => Send(symbols, unsubscribe: false);
            _subscriptions.UnsubscribeImpl = (symbols, _) => Send(symbols, unsubscribe: true);
            if (client != null) Attach(client);
        }

        public bool IsConnected => _client?.IsConnected ?? false;

        public void SetJob(LiveNodePacket job)
        {
            if (_client != null) return;
            if (!job.BrokerageData.TryGetValue(QuantLabBrokerageFactory.GatewayFileKey, out var path) || string.IsNullOrWhiteSpace(path))
            {
                path = Config.Get(QuantLabBrokerageFactory.GatewayFileKey);
            }
            Attach(GatewayClient.Shared(path, TimeSpan.FromSeconds(10)));
        }

        private void Attach(GatewayClient client)
        {
            _client = client;
            client.MessageReceived += OnMessage;
            client.Connected += () => Send(_subscriptions.GetSubscribedSymbols().Distinct(), unsubscribe: false); // after a reconnect
        }

        public IEnumerator<BaseData>? Subscribe(SubscriptionDataConfig dataConfig, EventHandler newDataAvailableHandler)
        {
            if (dataConfig.Symbol.SecurityType != SecurityType.Equity || dataConfig.Symbol.IsCanonical()) return null;
            _symbols[dataConfig.Symbol.Value.ToUpperInvariant()] = (dataConfig.Symbol, dataConfig.ExchangeTimeZone);
            var enumerator = _aggregator.Add(dataConfig, newDataAvailableHandler);
            _subscriptions.Subscribe(dataConfig);
            return enumerator;
        }

        public void Unsubscribe(SubscriptionDataConfig dataConfig)
        {
            _subscriptions.Unsubscribe(dataConfig);
            _aggregator.Remove(dataConfig);
        }

        private bool Send(IEnumerable<Symbol> symbols, bool unsubscribe)
        {
            var tickers = symbols.Select(s => s.Value.ToUpperInvariant()).Distinct().ToList();
            if (tickers.Count == 0 || _client == null || !_client.IsConnected) return true; // sent on (re)connect
            try
            {
                _client.Send(new Dictionary<string, object> { ["type"] = "subscribe", ["symbols"] = tickers, ["unsubscribe"] = unsubscribe });
                return true;
            }
            catch (GatewayException exc)
            {
                Log.Error($"QuantLabDataQueue: subscription not sent: {exc.Message}");
                return false;
            }
        }

        private void OnMessage(JsonElement msg)
        {
            if (!msg.TryGetProperty("type", out var type) || type.GetString() != "quote") return;
            var ticker = msg.GetProperty("symbol").GetString() ?? "";
            if (!_symbols.TryGetValue(ticker, out var subscribed)) return;
            var symbol = subscribed.Symbol;
            var state = msg.TryGetProperty("state", out var st) ? st.GetString() ?? "" : "";
            if (state != "LIVE" && _warnedStates.TryAdd($"{ticker}:{state}", state))
            {
                Log.Trace($"QuantLabDataQueue: {ticker} prices are {state}, not LIVE (simulated/delayed data — not a performance claim)");
            }
            var utc = DateTime.Parse(msg.GetProperty("ts").GetString()!, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal);
            var time = utc.ConvertFromUtc(subscribed.ExchangeTimeZone); // LEAN data times are exchange-local
            decimal? Num(string name) =>
                msg.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDecimal() : null;
            var (bid, ask, last) = (Num("bid"), Num("ask"), Num("last"));
            if (bid is > 0 && ask is > 0)
            {
                _aggregator.Update(new Tick(time, symbol, "", "", Num("bid_size") ?? 0m, bid.Value, Num("ask_size") ?? 0m, ask.Value));
            }
            if (last is > 0)
            {
                _aggregator.Update(new Tick(time, symbol, "", "", 0m, last.Value)); // trade tick (no size from quotes)
            }
        }

        public void Dispose()
        {
            if (_client != null) _client.MessageReceived -= OnMessage;
            _subscriptions.Dispose();
        }
    }
}
