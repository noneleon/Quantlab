// Brokerage model for the QuantLab gateway (OMS-007): a cash account and only what the gateway supports. Order
// updates (OMS-006) go to QuantLab's risk gate; QuantLabBrokerage.UpdateOrder cancels an order whose update QuantLab
// does not apply, because LEAN has already changed its own order object by then.

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
            var refusal = QuantLabBrokerage.Unsupported(order);
            if (refusal != null)
            {
                message = new BrokerageMessageEvent(BrokerageMessageType.Warning, "QuantLabUnsupported", refusal);
                return false;
            }
            return base.CanUpdateOrder(security, order, request, out message);
        }
    }
}
