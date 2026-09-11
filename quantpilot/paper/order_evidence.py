"""Daily-order evidence is not native cancelable-quantity authority."""

from decimal import Decimal
from quantpilot.packages.core.kis_paper import is_original_order


def daily_quantities_valid(row):
    quantities = (row.total_filled_quantity, row.remaining_quantity,
                  row.rejected_quantity, row.confirmed_cancel_quantity)
    return (
        row.order_quantity > 0
        and all(q >= 0 for q in quantities)
        and sum(quantities) == row.order_quantity
        and row.total_filled_amount >= 0
        and row.average_fill_price >= 0
        and (row.total_filled_quantity == 0) == (row.total_filled_amount == 0)
        and abs(row.total_filled_amount - row.average_fill_price * row.total_filled_quantity)
        <= Decimal("0.01")
        and (not row.cancelled or row.remaining_quantity == 0)
        and (row.confirmed_cancel_quantity == 0 or row.cancelled)
    )


def daily_identity_matches(dispatch, row, business_date):
    """Match the original order; forwarding ID and query branch are distinct."""
    return (
        dispatch.attempt_count == 1
        and dispatch.broker_business_date == business_date
        and row.order_date == business_date.strftime("%Y%m%d")
        and dispatch.broker_order_reference == row.order_number
        and dispatch.broker_order_branch_number is not None
        and dispatch.broker_order_branch_number == row.order_branch_number
        and dispatch.broker_order_time == row.order_time
        and is_original_order(row.original_order_number)
        and dispatch.symbol == row.symbol
        and dispatch.side == row.side
        and Decimal(str(dispatch.quantity)) == row.order_quantity
        and Decimal(str(dispatch.limit_price)) == row.order_price
        and Decimal(str(dispatch.cumulative_filled_quantity)) == row.total_filled_quantity
        and abs(sum((Decimal(str(f.notional)) for f in dispatch.fill_evidence),
                    Decimal(0)) - row.total_filled_amount) <= Decimal("0.01")
        and dispatch.status in {"accepted", "partially_filled"}
        and dispatch.reconciliation_status != "blocked"
        and daily_quantities_valid(row)
    )
