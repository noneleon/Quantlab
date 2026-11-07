// LEAN brokerage factory for the QuantLab gateway brokerage (OMS-007).
// LEAN config: "live-mode-brokerage": "QuantLabBrokerage", "quantlab-gateway-file": "<QUANTLAB_HOME>/run/gateway-<name>.json"

using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect;
using QuantConnect.Brokerages;
using QuantConnect.Configuration;
using QuantConnect.Interfaces;
using QuantConnect.Packets;
using QuantConnect.Securities;

namespace QuantLab.Brokerages
{
    public class QuantLabBrokerageFactory : BrokerageFactory
    {
        public const string GatewayFileKey = "quantlab-gateway-file";

        public QuantLabBrokerageFactory() : base(typeof(QuantLabBrokerage))
        {
        }

        public override Dictionary<string, string> BrokerageData => new()
        {
            [GatewayFileKey] = Config.Get(GatewayFileKey),
        };

        /// <summary>Cash account, no updates (QuantLabBrokerageModel); the QuantLab risk gate enforces the real limits.</summary>
        public override IBrokerageModel GetBrokerageModel(IOrderProvider orderProvider) => new QuantLabBrokerageModel();

        public override IBrokerage CreateBrokerage(LiveNodePacket job, IAlgorithm algorithm)
        {
            if (!job.BrokerageData.TryGetValue(GatewayFileKey, out var path) || string.IsNullOrWhiteSpace(path))
            {
                throw new ArgumentException($"{GatewayFileKey} is required: the run file written by `quantlab paper gateway`");
            }
            Symbol Resolve(string ticker) =>
                algorithm.Securities.Keys.FirstOrDefault(s => s.SecurityType == SecurityType.Equity && s.Value == ticker)
                ?? Symbol.Create(ticker, SecurityType.Equity, Market.USA);
            var brokerage = new QuantLabBrokerage(GatewayEndpoint.FromRunFile(path), Resolve, algorithm.AccountCurrency);
            return brokerage;
        }

        public override void Dispose()
        {
        }
    }
}
