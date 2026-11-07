// QuantLabBrokerage (OMS-007) against the real Python gateway and paper OMS. Synthetic SIMULATED quotes only.

using System;
using System.Collections.Concurrent;
using System.Linq;
using System.Reflection;
using System.Threading;
using QuantConnect;
using QuantConnect.Brokerages;
using QuantConnect.Orders;
using Xunit;

namespace QuantLab.Brokerages.Tests
{
    public sealed class QuantLabBrokerageTests : IDisposable
    {
        // Outside the LEAN engine there is no map-file provider, so the test builds the symbol without mapping.
        private static readonly Symbol Spy = new(SecurityIdentifier.GenerateEquity("SPY", QuantConnect.Market.USA, mapSymbol: false), "SPY");
        private readonly GatewayHarness _harness = new();
        private readonly ConcurrentQueue<OrderEvent> _events = new();
        private readonly ConcurrentQueue<BrokerageMessageEvent> _messages = new();
        private readonly QuantLabBrokerage _brokerage;
        private static int _nextId;

        public QuantLabBrokerageTests()
        {
            _brokerage = Attach(new QuantLabBrokerage(_harness.Endpoint, _ => Spy, timeout: TimeSpan.FromSeconds(10)));
            _brokerage.Connect();
        }

        private QuantLabBrokerage Attach(QuantLabBrokerage b)
        {
            b.OrdersStatusChanged += (_, evs) => evs.ForEach(_events.Enqueue);
            b.Message += (_, m) => _messages.Enqueue(m);
            return b;
        }

        public void Dispose()
        {
            _brokerage.Dispose();
            _harness.Dispose();
        }

        private static T WithId<T>(T order) where T : Order
        {
            typeof(Order).GetProperty("Id")!.SetValue(order, Interlocked.Increment(ref _nextId));
            return order;
        }

        private static MarketOrder Market(decimal qty) => WithId(new MarketOrder(Spy, qty, DateTime.UtcNow));

        private static LimitOrder Limit(decimal qty, decimal price) =>
            WithId(new LimitOrder(Spy, qty, price, DateTime.UtcNow));

        private OrderEvent WaitFor(Func<OrderEvent, bool> pred, int seconds = 10)
        {
            var deadline = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < deadline)
            {
                var hit = _events.FirstOrDefault(pred);
                if (hit != null) return hit;
                Thread.Sleep(20);
            }
            throw new Xunit.Sdk.XunitException("order event not received; got: " +
                string.Join(" | ", _events.Select(e => $"{e.OrderId} {e.Status} {e.FillQuantity} {e.Message}")));
        }

        [Fact]
        public void MarketOrderPassesTheRiskGateAndFillsComeBack()
        {
            var order = Market(10);
            Assert.True(_brokerage.PlaceOrder(order));
            var fill = WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Filled);
            Assert.Equal(10m, fill.FillQuantity);
            Assert.Equal(50.01m, fill.FillPrice);
            Assert.Contains("SIMULATED", fill.Message);
            var holding = Assert.Single(_brokerage.GetAccountHoldings());
            Assert.Equal(10m, holding.Quantity);
            Assert.Equal(50.01m, holding.AveragePrice);
            Assert.True(_brokerage.GetCashBalance().Single().Amount < 20_000m);
        }

        [Fact]
        public void RiskRejectionIsInvalidWithTheGateReason()
        {
            var order = Market(1000); // over max_order_quantity
            Assert.False(_brokerage.PlaceOrder(order));
            var ev = WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Invalid);
            Assert.Contains("order quantity", ev.Message);
        }

        [Fact]
        public void KillSwitchBlocksTheAlgorithm()
        {
            _harness.EngageKillSwitch();
            var order = Market(1);
            Assert.False(_brokerage.PlaceOrder(order));
            Assert.Contains("kill switch", WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Invalid).Message);
        }

        [Fact]
        public void LimitOrderWorksAndCanBeCancelled()
        {
            var order = Limit(2, 10m);
            Assert.True(_brokerage.PlaceOrder(order));
            WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Submitted);
            Assert.True(_brokerage.CancelOrder(order));
            WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Canceled);
        }

        [Fact]
        public void SellingWhatTheDeploymentDoesNotHoldIsRefused()
        {
            var order = Market(-5);
            Assert.False(_brokerage.PlaceOrder(order));
            var ev = WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Invalid);
            Assert.Contains("never sell positions they do not hold", ev.Message);
        }

        [Fact]
        public void UnsupportedOrdersAndUpdatesAreRefusedLocally()
        {
            var moo = WithId(new MarketOnOpenOrder(Spy, 1, DateTime.UtcNow));
            Assert.False(_brokerage.PlaceOrder(moo));
            Assert.Contains("not supported", WaitFor(e => e.OrderId == moo.Id && e.Status == OrderStatus.Invalid).Message);
            Assert.False(_brokerage.UpdateOrder(Limit(1, 10m)));
            Assert.Contains(_messages, m => m.Code == "QuantLabNoUpdates");
        }

        [Fact]
        public void ReconnectWithinTheRunNeverDuplicatesFills()
        {
            var order = Market(3);
            Assert.True(_brokerage.PlaceOrder(order));
            WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Filled);
            var run = _brokerage.Run;
            _brokerage.Disconnect();
            _brokerage.Connect(); // same run: the gateway replays state and fills
            Assert.Equal(run, _brokerage.Run);
            Thread.Sleep(500);
            Assert.Single(_events, e => e.OrderId == order.Id && e.FillQuantity != 0);
            Assert.True(_brokerage.PlaceOrder(order)); // the plugin resends after a reconnect: idempotent
            Thread.Sleep(300);
            Assert.Equal(3m, _brokerage.GetAccountHoldings().Single().Quantity);
        }

        [Fact]
        public void RestartedAlgorithmSeesOrphansAndCanCancelThem()
        {
            var resting = Limit(2, 10m);
            Assert.True(_brokerage.PlaceOrder(resting));
            WaitFor(e => e.OrderId == resting.Id && e.Status == OrderStatus.Submitted);
            _brokerage.Dispose(); // the algorithm crashes; its order keeps working
            using var restarted = Attach(new QuantLabBrokerage(_harness.Endpoint, _ => Spy));
            restarted.Connect();
            Assert.NotEqual(_brokerage.Run, restarted.Run);
            Assert.Contains(_messages, m => m.Code == "QuantLabOrphans");
            var blocked = Market(1);
            Assert.False(restarted.PlaceOrder(blocked));
            Assert.Contains("earlier run", WaitFor(e => e.OrderId == blocked.Id && e.Status == OrderStatus.Invalid).Message);
            var result = restarted.CancelOrphans();
            Assert.NotNull(result);
            Assert.Equal(0, result!.Value.GetProperty("remaining").GetArrayLength());
            var next = Market(1);
            Assert.True(restarted.PlaceOrder(next));
            WaitFor(e => e.OrderId == next.Id && e.Status == OrderStatus.Filled);
        }

        [Fact]
        public void WrongTokenIsRefused()
        {
            _brokerage.Dispose(); // the gateway serves one plugin connection at a time
            var bad = _harness.Endpoint with { Token = "wrong" };
            using var b = new QuantLabBrokerage(bad, _ => Spy);
            Assert.Throws<GatewayException>(b.Connect);
            Assert.False(b.IsConnected);
            Assert.DoesNotContain("wrong", bad.ToString()); // the token never appears in messages or logs
        }
    

        [Fact]
        public void FactoryRequiresTheGatewayRunFileAndUsesACashAccount()
        {
            using var factory = new QuantLabBrokerageFactory();
            var job = new QuantConnect.Packets.LiveNodePacket();
            Assert.Throws<ArgumentException>(() => factory.CreateBrokerage(job, null!));
            Assert.IsType<QuantLabBrokerageModel>(factory.GetBrokerageModel(null!));
        }
    

        // ---- safety review of the plugin (CHANGE-0037) ---------------------------------------------------

        [Fact]
        public void SellFillsAreNegativeAndCloseThePosition()
        {
            var buy = Market(5);
            Assert.True(_brokerage.PlaceOrder(buy));
            WaitFor(e => e.OrderId == buy.Id && e.Status == OrderStatus.Filled);
            var sell = Market(-5);
            Assert.True(_brokerage.PlaceOrder(sell));
            var fill = WaitFor(e => e.OrderId == sell.Id && e.Status == OrderStatus.Filled);
            Assert.Equal(-5m, fill.FillQuantity);
            Assert.Equal(49.99m, fill.FillPrice);
            Assert.Empty(_brokerage.GetAccountHoldings());
        }

        [Fact]
        public void ADroppedConnectionIsReestablishedAutomaticallyInTheSameRun()
        {
            _brokerage.ReconnectDelay = TimeSpan.FromMilliseconds(200);
            var run = _brokerage.Run;
            _brokerage.Client.DropConnection(); // network failure
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (DateTime.UtcNow < deadline && !_messages.Any(m => m.Code == "QuantLabDisconnected")) Thread.Sleep(20);
            while (DateTime.UtcNow < deadline && !_brokerage.IsConnected) Thread.Sleep(50);
            Assert.True(_brokerage.IsConnected);
            Assert.Equal(run, _brokerage.Run);
            var order = Market(1);
            Assert.True(_brokerage.PlaceOrder(order));
            WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Filled);
        }

        [Fact]
        public void AnIntentionalDisconnectDoesNotReconnectOrBreakTheNextConnection()
        {
            _brokerage.ReconnectDelay = TimeSpan.FromMilliseconds(100);
            _brokerage.Disconnect();
            Thread.Sleep(500);
            Assert.False(_brokerage.IsConnected); // no automatic reconnect after an intentional disconnect
            Assert.DoesNotContain(_messages, m => m.Code == "QuantLabDisconnected");
            _brokerage.Connect();
            Thread.Sleep(300); // the old reader has finished by now and must not mark the new connection dead
            Assert.True(_brokerage.IsConnected);
            var order = Market(1);
            Assert.True(_brokerage.PlaceOrder(order));
            WaitFor(e => e.OrderId == order.Id && e.Status == OrderStatus.Filled);
        }

        [Fact]
        public void BrokerageModelRefusesUpdatesAndUnsupportedOrders()
        {
            var model = new QuantLabBrokerageModel();
            Assert.Equal(AccountType.Cash, model.AccountType);
            Assert.False(model.CanUpdateOrder(null!, Limit(1, 10m), null!, out var update));
            Assert.Equal("QuantLabNoUpdates", update.Code);
            Assert.False(model.CanSubmitOrder(null!, WithId(new MarketOnOpenOrder(Spy, 1, DateTime.UtcNow)), out var submit));
            Assert.Equal("QuantLabUnsupported", submit.Code);
        }
    

        [Fact]
        public void AnUnreachableGatewayIsAGatewayException()
        {
            var endpoint = _harness.Endpoint;
            _brokerage.Dispose();
            _harness.Dispose(); // nothing listens on the port any more
            using var b = new QuantLabBrokerage(endpoint, _ => Spy);
            var exc = Assert.Throws<GatewayException>(b.Connect);
            Assert.Contains("not reachable", exc.Message);
        }
    }
}
