import frappe
from frappe import _
from frappe.utils import cint, flt, getdate


AUTO_PREFIX = "Monthly Production Overhead |"


def apply_monthly_production_overhead(doc, method=None):
    """
    Add monthly production overhead to Manufacture Stock Entry.

    Match Monthly Production Setting dynamically using:
        Company
        Cost Center
        Posting Date

    No Company / Cost Center is hard-coded.
    """

    # Remove only our previous generated rows.
    # Manual Additional Costs remain untouched.
    remove_auto_overhead_rows(doc)

    if doc.docstatus != 0:
        return

    if doc.purpose != "Manufacture":
        return

    if not doc.company or not doc.posting_date:
        return

    finished_rows = get_finished_goods_rows(doc)

    if not finished_rows:
        # During incomplete draft, simply wait until
        # Finished Good is selected.
        return

    if len(finished_rows) > 1:
        frappe.throw(
            _(
                "Monthly Production Overhead supports "
                "one Finished Good item per Manufacture Stock Entry."
            )
        )

    finished_row = finished_rows[0]

    # Stock UOM quantity.
    finished_qty = flt(
        finished_row.transfer_qty
    )

    if finished_qty <= 0:
        finished_qty = (
            flt(finished_row.qty)
            * flt(
                finished_row.conversion_factor or 1
            )
        )

    if finished_qty <= 0:
        return

    # Prefer parent Cost Center.
    # If blank, use Finished Good row Cost Center.
    cost_center = (
        doc.cost_center
        or finished_row.cost_center
    )

    setting = get_monthly_setting(
        company=doc.company,
        cost_center=cost_center,
        posting_date=doc.posting_date,
    )

    expected_qty = flt(
        setting.expected_qty
    )

    if expected_qty <= 0:
        frappe.throw(
            _(
                "Expected Qty must be greater than zero "
                "in Monthly Production Setting {0}."
            ).format(
                frappe.bold(setting.name)
            )
        )

    # If Cost Center was blank but exactly one setting
    # resolved it, apply it to the document.
    if not doc.cost_center:
        doc.cost_center = setting.cost_center

    total_allocated = 0

    for expense in setting.expenses:

        if not expense.type_of_expense:
            continue

        expense_amount = flt(
            expense.expense_amount
        )

        if expense_amount <= 0:
            continue

        # Expected expense cost per unit.
        expense_per_qty = (
            expense_amount /
            expected_qty
        )

        # Amount allocated to THIS Manufacture entry.
        allocated_amount = (
            expense_per_qty *
            finished_qty
        )

        doc.append(
            "additional_costs",
            {
                "expense_account":
                    expense.type_of_expense,

                "description":
                    (
                        f"{AUTO_PREFIX} "
                        f"{setting.name} | "
                        f"{expense.type_of_expense}"
                    ),

                "amount":
                    allocated_amount,
            },
        )

        total_allocated += (
            allocated_amount
        )

    if total_allocated <= 0:
        frappe.throw(
            _(
                "No production overhead amount found in "
                "Monthly Production Setting {0}."
            ).format(
                frappe.bold(setting.name)
            )
        )


def get_finished_goods_rows(doc):
    """
    Use ERPNext's standard is_finished_item field.

    Do NOT depend only on Item Group name.
    """

    finished_rows = []

    for row in doc.get("items") or []:

        if not row.item_code:
            continue

        # Primary ERPNext method.
        if cint(row.is_finished_item):
            finished_rows.append(row)

    if finished_rows:
        return finished_rows

    # Safe fallback for older/manually prepared Manufacture
    # Stock Entries:
    #
    # Incoming row, not scrap, no source warehouse.
    for row in doc.get("items") or []:

        if not row.item_code:
            continue

        if not row.t_warehouse:
            continue

        if row.s_warehouse:
            continue

        if cint(row.is_scrap_item):
            continue

        finished_rows.append(row)

    return finished_rows


def get_monthly_setting(
    company,
    cost_center,
    posting_date,
):
    """
    Match Monthly Production Setting dynamically.

    Company and Cost Center can be anything.
    """

    posting_date = getdate(
        posting_date
    )

    filters = {
        "company": company,
        "docstatus": 1,
        "from_date": (
            "<=",
            posting_date,
        ),
        "to_date": (
            ">=",
            posting_date,
        ),
    }

    if cost_center:
        filters["cost_center"] = (
            cost_center
        )

    settings = frappe.get_all(
        "Monthly Production Setting",
        filters=filters,
        fields=[
            "name",
            "cost_center",
        ],
        order_by="creation desc",
        limit_page_length=2,
    )

    if not settings:
        if cost_center:
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

        frappe.throw(
            _(
                "No submitted Monthly Production Setting found "
                "for Company {0} and Posting Date {1}."
            ).format(
                frappe.bold(company),
                frappe.bold(posting_date),
            )
        )

    if len(settings) > 1:
        if not cost_center:
            frappe.throw(
                _(
                    "Multiple Monthly Production Settings exist "
                    "for Company {0} on {1}. "
                    "Please select the required Cost Center "
                    "on Stock Entry."
                ).format(
                    frappe.bold(company),
                    frappe.bold(posting_date),
                )
            )

        frappe.throw(
            _(
                "Multiple submitted Monthly Production Settings "
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


def remove_auto_overhead_rows(doc):
    """
    Remove previously generated Monthly Production Overhead rows
    before recalculating.

    Manual Additional Cost rows remain untouched.
    """

    manual_rows = []

    for row in doc.get(
        "additional_costs"
    ) or []:

        description = (
            row.description or ""
        )

        if not description.startswith(
            AUTO_PREFIX
        ):
            manual_rows.append(row)

    doc.set(
        "additional_costs",
        manual_rows,
    )
