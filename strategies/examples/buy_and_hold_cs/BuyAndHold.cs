using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;

namespace QuantLabStrategies
{
    // Same logic as LEAN's BasicTemplateAlgorithm, so a run on LEAN's bundled sample data must reproduce LEAN's
    // published statistics exactly (golden check for the native launcher, tests/integration/test_lean_native.py).
    public class BuyAndHold : QCAlgorithm
    {
        private Symbol _spy;

        public override void Initialize()
        {
            SetStartDate(2013, 10, 7);
            SetEndDate(2013, 10, 11);
            SetCash(100000);
            _spy = AddEquity("SPY", Resolution.Minute).Symbol;
        }

        public override void OnData(Slice data)
        {
            if (!Portfolio.Invested)
            {
                SetHoldings(_spy, 1);
            }
        }
    }
}
