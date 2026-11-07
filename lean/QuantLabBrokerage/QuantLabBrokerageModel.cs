// Brokerage model for the QuantLab gateway (OMS-007): a cash account, no order updates, and only what the gateway
// supports. LEAN applies an update to its own order object before asking the brokerage, so updates must be refused
// here — otherwise LEAN's order and the order QuantLab works would differ.

using QuantConnect;
using QuantConnect.Brokerages;
using QuantConnect.Orders;
using QuantConnect.Securities;

namespace QuantLab.Brokerages
{
    public class QuantLabBrokerageModel : DefaultBrokerageModel
    {
        public QuantLabBrokerageModel() : base(AccountType.Cash)
        {
        }

        public override bool CanSubmitOrder(Security security, Order order, out BrokerageMessageEvent message)
        {
            var refusal = QuantLabBrokerage.Unsupported(order);
            if (refusal != null)
            {
                message = new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabUnsupported", refusal);
                return false;
            }
            return base.CanSubmitOrder(security, order, out message);
        }

        public override bool CanUpdateOrder(Security security, Order order, UpdateOrderRequest request, out BrokerageMessageEvent message)
        {
            message = new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabNoUpdates",
                "order updates are not supported by the QuantLab gateway; cancel the order and place a new one");
            return false;
        }
    }
}
