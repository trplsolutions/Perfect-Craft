import frappe
from frappe import _
from frappe.utils import flt, getdate


@frappe.whitelist()
def get_monthly_reconciliation_data(company, cost_center, posting_date):
    """
    Fetch Monthly Production Setting according to:
    - Company
    - Cost Center
    - Posting Date / Month
    """

    setting = get_monthly_setting(
        company=company,
        cost_center=cost_center,
        posting_date=posting_date,
    )

    return {
        "setting_name": setting.name,
        "expected_qty": flt(setting.expected_qty),
        "expected_per_qty_amount": flt(setting.per_qty_amount),
        "expenses": [
            {
                "type_of_expense": row.type_of_expense,
                "expected_expense_amount": flt(row.expense_amount),
                "actual_expense_amount": flt(row.expense_amount),
            }
            for row in setting.expenses
        ],
    }


def apply_stock_reconciliation_calculation(doc, method=None):
    """
    Manager requested calculation.

    Runs on server before validation so values are correct
    even if document is created through API/import.
    """

    if doc.purpose != "Stock Reconciliation":
        return

    if not doc.company:
        return

    if not doc.cost_center:
        return

    if not doc.posting_date:
        return

    setting = get_monthly_setting(
        company=doc.company,
        cost_center=doc.cost_center,
        posting_date=doc.posting_date,
    )

    # ---------------------------------------------------------
    # Expected Per Qty Amount
    # From Monthly Production Setting
    # ---------------------------------------------------------

    doc.custom_expected_per_qty_amount = flt(
        setting.per_qty_amount
    )

    # ---------------------------------------------------------
    # Keep manually entered Actual Expense Amount
    # ---------------------------------------------------------

    existing_actual_amounts = {}

    for row in doc.get(
        "custom_reconciliation_expenses"
    ) or []:

        if row.type_of_expense:
            existing_actual_amounts[
                row.type_of_expense
            ] = flt(
                row.actual_expense_amount
            )

    # ---------------------------------------------------------
    # Rebuild expense child table from current Monthly Setting
    # ---------------------------------------------------------

    doc.set(
        "custom_reconciliation_expenses",
        [],
    )

    actual_qty = flt(
        doc.custom_actual_qty
    )

    expected_per_qty = flt(
        doc.custom_expected_per_qty_amount
    )

    total_adjustment = 0

    for expense in setting.expenses:

        expected_expense = flt(
            expense.expense_amount
        )

        # First time:
        # Actual Expense Amount comes from Monthly Setting.
        #
        # If user later changes actual amount,
        # preserve that manually entered amount.

        if expense.type_of_expense in existing_actual_amounts:
            actual_expense = existing_actual_amounts[
                expense.type_of_expense
            ]
        else:
            actual_expense = expected_expense

        # -----------------------------------------------------
        # Manager Formula:
        #
        # Actual Per Qty Amount
        # = Actual Expense Amount / Actual Qty
        # -----------------------------------------------------

        if actual_qty > 0:
            actual_per_qty = (
                actual_expense / actual_qty
            )
        else:
            actual_per_qty = 0

        # -----------------------------------------------------
        # Manager Formula:
        #
        # Value After Calculation
        # =
        # Actual Per Qty Amount
        # -
        # Expected Per Qty Amount
        # -----------------------------------------------------

        if actual_qty > 0:
            value_after_calculation = (
                actual_per_qty
                - expected_per_qty
            )
        else:
            value_after_calculation = 0

        doc.append(
            "custom_reconciliation_expenses",
            {
                "type_of_expense":
                    expense.type_of_expense,

                "expected_expense_amount":
                    expected_expense,

                "actual_expense_amount":
                    actual_expense,

                "actual_per_qty_amount":
                    actual_per_qty,

                "value_after_calculation":
                    value_after_calculation,
            },
        )

        total_adjustment += (
            value_after_calculation
        )

    # ---------------------------------------------------------
    # Parent:
    #
    # Adjustment Per Qty
    # = Sum Value After Calculation
    # ---------------------------------------------------------

    doc.custom_adjustment_per_qty = (
        total_adjustment
    )

    # User hasn't entered actual production quantity yet.
    if actual_qty <= 0:
        return

    apply_item_valuation(doc)


def apply_item_valuation(doc):
    """
    Manager formula:

    After Valuation Rate =
    (Item Valuation Rate + Adjustment Per Qty) / Qty
    """

    adjustment = flt(
        doc.custom_adjustment_per_qty
    )

    for row in doc.get("items") or []:

        qty = flt(row.qty)

        # -----------------------------------------------------
        # Original Item Valuation Rate
        # -----------------------------------------------------

        if row.get(
            "custom_item_valuation_rate"
        ) not in (None, ""):

            item_valuation_rate = flt(
                row.custom_item_valuation_rate
            )

        else:
            # Use ERPNext's current valuation if available.

            item_valuation_rate = flt(
                row.current_valuation_rate
                or row.valuation_rate
            )

            row.custom_item_valuation_rate = (
                item_valuation_rate
            )

        # -----------------------------------------------------
        # Manager Formula EXACTLY
        #
        # After Valuation Rate =
        # (Item Valuation Rate + Adjustment Per Qty) / Qty
        # -----------------------------------------------------

        if qty > 0:

            after_valuation_rate = (
                item_valuation_rate
                + adjustment
            ) / qty

        else:
            after_valuation_rate = 0

        # Standard ERPNext valuation field
        row.valuation_rate = (
            after_valuation_rate
        )

        # Keep standard Stock Reconciliation amount correct
        row.amount = (
            qty
            * after_valuation_rate
        )


def validate_stock_reconciliation(doc, method=None):
    """
    Validate only during Submit.
    """

    if doc.purpose != "Stock Reconciliation":
        return

    if not doc.company:
        frappe.throw(
            _("Company is required.")
        )

    if not doc.cost_center:
        frappe.throw(
            _(
                "Cost Center is required for "
                "Stock Reconciliation."
            )
        )

    if not doc.posting_date:
        frappe.throw(
            _("Posting Date is required.")
        )

    if flt(doc.custom_actual_qty) <= 0:
        frappe.throw(
            _(
                "Actual Qty must be "
                "greater than zero."
            )
        )

    if not doc.get(
        "custom_reconciliation_expenses"
    ):
        frappe.throw(
            _(
                "Production Expense rows "
                "are required."
            )
        )

    for row in doc.custom_reconciliation_expenses:

        if flt(
            row.actual_expense_amount
        ) < 0:

            frappe.throw(
                _(
                    "Actual Expense Amount cannot "
                    "be negative in row {0}."
                ).format(row.idx)
            )

    # Confirm setting still exists.
    get_monthly_setting(
        company=doc.company,
        cost_center=doc.cost_center,
        posting_date=doc.posting_date,
    )


def get_monthly_setting(
    company,
    cost_center,
    posting_date,
):
    """
    Get exactly one submitted Monthly Production Setting
    for Company + Cost Center + Posting Date.
    """

    if not company:
        frappe.throw(
            _("Company is required.")
        )

    if not cost_center:
        frappe.throw(
            _("Cost Center is required.")
        )

    if not posting_date:
        frappe.throw(
            _("Posting Date is required.")
        )

    posting_date = getdate(
        posting_date
    )

    settings = frappe.get_all(
        "Monthly Production Setting",
        filters={
            "company": company,
            "cost_center": cost_center,
            "docstatus": 1,

            "from_date": (
                "<=",
                posting_date,
            ),

            "to_date": (
                ">=",
                posting_date,
            ),
        },
        fields=[
            "name",
        ],
        order_by="creation desc",
        limit_page_length=2,
    )

    if not settings:

        frappe.throw(
            _(
                "No submitted Monthly Production "
                "Setting found for Company {0}, "
                "Cost Center {1} and Posting Date {2}."
            ).format(
                frappe.bold(company),
                frappe.bold(cost_center),
                frappe.bold(posting_date),
            )
        )

    if len(settings) > 1:

        frappe.throw(
            _(
                "Multiple Monthly Production Settings "
                "found for Company {0}, Cost Center {1} "
                "and Posting Date {2}."
            ).format(
                frappe.bold(company),
                frappe.bold(cost_center),
                frappe.bold(posting_date),
            )
        )

    return frappe.get_doc(
        "Monthly Production Setting",
        settings[0].name,
    )
