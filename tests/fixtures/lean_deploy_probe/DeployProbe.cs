using System;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Orders;

namespace QuantLabStrategies
{
    // Test strategy for `quantlab paper deploy` (5c-4): buys 1 SPY share on the first price it receives from
    // QuantLab (QuantLabDataQueue — synthetic quotes in tests), then stops. No seeded prices.
    public class DeployProbe : QCAlgorithm
    {
        private Symbol _spy;
        private bool _sent;

        public override void Initialize()
        {
            SetCash(100000);
            _spy = AddEquity("SPY", Resolution.Second).Symbol;
        }

        public override void OnData(Slice slice)
        {
            if (_sent || !slice.ContainsKey(_spy)) return;
            _sent = true;
            Log($"DEPLOY first price {Securities[_spy].Price}");
            MarketOrder(_spy, 1, tag: "quantlab deploy probe");
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            Log($"DEPLOY order event {orderEvent.OrderId} {orderEvent.Status} qty {orderEvent.FillQuantity} @ {orderEvent.FillPrice}");
            if (orderEvent.Status == OrderStatus.Filled || orderEvent.Status == OrderStatus.Invalid)
            {
                Quit("deploy probe complete");
            }
        }
    }
}
