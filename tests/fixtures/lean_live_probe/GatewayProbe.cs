using System;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Orders;

namespace QuantLabStrategies
{
    // Test probe (OMS-007): one marketable limit buy of 1 SPY share through the QuantLab gateway, then stop.
    // If the market is closed (no live data) it seeds a SYNTHETIC price so LEAN accepts the order.
    public class GatewayProbe : QCAlgorithm
    {
        private Symbol _spy;
        private bool _sent;

        public override void Initialize()
        {
            SetCash(100000);
            _spy = AddEquity("SPY", Resolution.Minute, extendedMarketHours: true).Symbol;
            Schedule.On(DateRules.EveryDay(), TimeRules.Every(TimeSpan.FromSeconds(10)), () =>
            {
                if (_sent) return;
                _sent = true;
                if (Securities[_spy].Price == 0)
                {
                    // Closed market, no live data: seed a SYNTHETIC price so LEAN accepts the probe order.
                    Securities[_spy].SetMarketPrice(new QuantConnect.Data.Market.TradeBar(Time, _spy, 50m, 50m, 50m, 50m, 100, TimeSpan.FromMinutes(1)));
                    Log("PROBE seeded a synthetic SPY price (50.00)");
                }
                var ticket = LimitOrder(_spy, 1, 60m, tag: "quantlab gateway probe");
                Log($"PROBE placed order {ticket.OrderId} status {ticket.Status}");
            });
        }

        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            Log($"PROBE order event {orderEvent.OrderId} {orderEvent.Status} qty {orderEvent.FillQuantity} @ {orderEvent.FillPrice} :: {orderEvent.Message}");
            if (orderEvent.Status == OrderStatus.Filled || orderEvent.Status == OrderStatus.Invalid)
            {
                Log("PROBE done");
                Quit("probe complete");
            }
        }
    }
}
