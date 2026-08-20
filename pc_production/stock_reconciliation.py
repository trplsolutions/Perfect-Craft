import frappe
from frappe import _
from frappe.utils import flt, getdate


PRODUCTION_EXPENSE_TABLE = "custom_production_expenses"


@frappe.whitelist()
def get_monthly_reconciliation_data(
    company=None,
    cost_center=None,
    posting_date=None,
):
    """
    Fetch submitted Monthly Production Setting dynamically
    according to:
        Company + Cost Center + Posting Date

    Nothing is hard-coded.
    """

    if not company or not cost_center or not posting_date:
        return {
            "found": False,
            "message": "Company, Cost Center and Posting Date are required.",
        }

    setting = get_monthly_setting(
        company=company,
        cost_center=cost_center,
        posting_date=posting_date,
        throw_if_missing=False,
    )

    if not setting:
        return {
            "found": False,
            "message": (
                f"No submitted Monthly Production Setting found "
                f"for Company {company}, Cost Center {cost_center} "
                f"and Posting Date {posting_date}."
            ),
        }

    expected_qty = flt(setting.expected_qty)

    if expected_qty <= 0:
        frappe.throw(
            _(
                "Expected Qty must be greater than zero in "
                "Monthly Production Setting {0}."
            ).format(frappe.bold(setting.name))
        )

    expenses = []

    for row in setting.expenses:
        if not row.type_of_expense:
            continue

        expected_amount = flt(row.expense_amount)

        expenses.append(
            {
                "type_of_expense": row.type_of_expense,
                "expected_expense_amount": expected_amount,

                # Initial actual amount is copied from expected.
                # User can change it at month end.
                "actual_expense_amount": expected_amount,
            }
        )

    return {
        "found": True,
        "setting_name": setting.name,
        "expected_qty": expected_qty,
        "expected_per_qty_amount": flt(setting.per_qty_amount),
        "expenses": expenses,
    }


def apply_stock_reconciliation_calculation(doc, method=None):
    """
    Final server-side calculation.

    This guarantees correct results even if JS does not run,
    or document comes through API/import.
    """

    if doc.purpose != "Stock Reconciliation":
        return

    if not doc.company or not doc.cost_center or not doc.posting_date:
        return

    setting = get_monthly_setting(
        company=doc.company,
        cost_center=doc.cost_center,
        posting_date=doc.posting_date,
        throw_if_missing=True,
    )

    expected_qty = flt(setting.expected_qty)

    if expected_qty <= 0:
        frappe.throw(
            _(
                "Expected Qty must be greater than zero in "
                "Monthly Production Setting {0}."
            ).format(frappe.bold(setting.name))
        )

    # -----------------------------------------------------
    # Parent Expected Per Qty
    # -----------------------------------------------------

    doc.custom_expected_per_qty_amount = flt(
        setting.per_qty_amount
    )

    actual_qty = flt(doc.custom_actual_qty)

    # -----------------------------------------------------
    # Preserve manually entered actual expense values
    # -----------------------------------------------------

    existing_actual = {}

    for row in doc.get(PRODUCTION_EXPENSE_TABLE) or []:
        if row.type_of_expense:
            existing_actual[row.type_of_expense] = flt(
                row.actual_expense_amount
            )

    # -----------------------------------------------------
    # Rebuild rows from CURRENT matching Monthly Setting
    # -----------------------------------------------------

    doc.set(PRODUCTION_EXPENSE_TABLE, [])

    total_adjustment_per_qty = 0

    for expense in setting.expenses:

        if not expense.type_of_expense:
            continue

        expected_expense_amount = flt(
            expense.expense_amount
        )

        # Expected Per Qty FOR THIS EXPENSE
        expected_expense_per_qty = (
            expected_expense_amount / expected_qty
            if expected_qty > 0
            else 0
        )

        # Preserve user's actual amount.
        # First time use expected amount as initial value.
        if expense.type_of_expense in existing_actual:
            actual_expense_amount = existing_actual[
                expense.type_of_expense
            ]
        else:
            actual_expense_amount = expected_expense_amount

        # Actual Per Qty
        if actual_qty > 0:
            actual_per_qty_amount = (
                actual_expense_amount / actual_qty
            )

            # CORRECT FORMULA:
            # Actual expense per qty
            # minus expected expense per qty FOR THIS ROW
            value_after_calculation = (
                actual_per_qty_amount
                - expected_expense_per_qty
            )
        else:
            actual_per_qty_amount = 0
            value_after_calculation = 0

        doc.append(
            PRODUCTION_EXPENSE_TABLE,
            {
                "type_of_expense":
                    expense.type_of_expense,

                "expected_expense_amount":
                    expected_expense_amount,

                "actual_expense_amount":
                    actual_expense_amount,

                "actual_per_qty_amount":
                    actual_per_qty_amount,

                "value_after_calculation":
                    value_after_calculation,
            },
        )

        total_adjustment_per_qty += (
            value_after_calculation
        )

    # -----------------------------------------------------
    # Parent Adjustment Per Qty
    # -----------------------------------------------------

    doc.custom_adjustment_per_qty = (
        total_adjustment_per_qty
    )

    # Draft may be saved before Actual Qty is entered.
    if actual_qty <= 0:
        return

    apply_item_valuation(doc)


def apply_item_valuation(doc):
    """
    Correct ERPNext valuation logic.

    valuation_rate is already PER UNIT.

    Therefore:

    After Valuation Rate
    = Item Valuation Rate + Adjustment Per Qty
    """

    adjustment_per_qty = flt(
        doc.custom_adjustment_per_qty
    )

    for row in doc.get("items") or []:

        # Capture original/current valuation.
        if row.get("custom_item_valuation_rate") not in (
            None,
            "",
        ):
            item_valuation_rate = flt(
                row.custom_item_valuation_rate
            )

        else:
            item_valuation_rate = flt(
                row.current_valuation_rate
                or row.valuation_rate
            )

            row.custom_item_valuation_rate = (
                item_valuation_rate
            )

        after_valuation_rate = (
            item_valuation_rate
            + adjustment_per_qty
        )

        # Standard ERPNext field.
        row.valuation_rate = after_valuation_rate

        # Standard amount.
        row.amount = (
            flt(row.qty)
            * after_valuation_rate
        )


def validate_stock_reconciliation(doc, method=None):
    """
    Strict validation on Submit.
    """

    if doc.purpose != "Stock Reconciliation":
        return

    if not doc.company:
        frappe.throw(_("Company is required."))

    if not doc.cost_center:
        frappe.throw(_("Cost Center is required."))

    if not doc.posting_date:
        frappe.throw(_("Posting Date is required."))

    setting = get_monthly_setting(
        company=doc.company,
        cost_center=doc.cost_center,
        posting_date=doc.posting_date,
        throw_if_missing=True,
    )

    if flt(setting.expected_qty) <= 0:
        frappe.throw(
            _("Monthly Production Setting Expected Qty must be greater than zero.")
        )

    if flt(doc.custom_actual_qty) <= 0:
        frappe.throw(
            _("Actual Qty must be greater than zero.")
        )

    if not doc.get(PRODUCTION_EXPENSE_TABLE):
        frappe.throw(
            _("Production Expense rows are required.")
        )

    for row in doc.get(PRODUCTION_EXPENSE_TABLE):

        if not row.type_of_expense:
            frappe.throw(
                _("Type of Expense is required in row {0}.").format(
                    row.idx
                )
            )

        if flt(row.actual_expense_amount) < 0:
            frappe.throw(
                _(
                    "Actual Expense Amount cannot be negative "
                    "in row {0}."
                ).format(row.idx)
            )


def get_monthly_setting(
    company,
    cost_center,
    posting_date,
    throw_if_missing=True,
):
    """
    Dynamic Monthly Production Setting lookup.

    Match:
        Company
        Cost Center
        Posting Date between From Date / To Date
        Submitted only

    No Company or Cost Center is hard-coded.
    """

    if not company or not cost_center or not posting_date:
        if throw_if_missing:
            frappe.throw(
                _(
                    "Company, Cost Center and Posting Date "
                    "are required."
                )
            )
        return None

    posting_date = getdate(posting_date)

    settings = frappe.get_all(
        "Monthly Production Setting",
        filters={
            "company": company,
            "cost_center": cost_center,
            "docstatus": 1,
            "from_date": ("<=", posting_date),
            "to_date": (">=", posting_date),
        },
        fields=[
            "name",
        ],
        order_by="creation desc",
        limit_page_length=2,
    )

    if not settings:

        if throw_if_missing:
            frappe.throw(
                _(
                    "No submitted Monthly Production Setting found "
                    "for Company {0}, Cost Center {1} "
                    "and Posting Date {2}."
                ).format(
                    frappe.bold(company),
                    frappe.bold(cost_center),
                    frappe.bold(posting_date),
                )
            )

        return None

    if len(settings) > 1:
        frappe.throw(
            _(
                "Multiple submitted Monthly Production Settings "
                "found for Company {0}, Cost Center {1} "
                "and Posting Date {2}. "
                "Please keep only one setting for this period."
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
