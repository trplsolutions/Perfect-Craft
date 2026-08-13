import frappe
from frappe import _
from frappe.utils import flt, getdate


AUTO_PREFIX = "Monthly Production Overhead |"


def apply_monthly_production_overhead(doc, method=None):
    # Remove previously auto-created rows so repeated Save
    # does not duplicate overhead.
    remove_auto_overhead_rows(doc)

    # Only Draft Manufacture Stock Entries
    if doc.docstatus != 0:
        return

    if doc.purpose != "Manufacture":
        return

    if not doc.company or not doc.posting_date:
        return

    finished_rows = get_finished_goods_rows(doc)

    # Drawing, Cutting, Mixture, Unpacked etc.
    # should not get monthly production overhead.
    if not finished_rows:
        return

    if len(finished_rows) > 1:
        frappe.throw(
            _(
                "Monthly Production Overhead currently supports "
                "one Finished Goods item per Manufacture Stock Entry."
            )
        )

    fg_row = finished_rows[0]

    # qty in Stock UOM
    finished_qty = (
        flt(fg_row.qty)
        * flt(fg_row.conversion_factor or 1)
    )

    if finished_qty <= 0:
        return

    setting = get_monthly_setting(doc)

    if not setting:
        frappe.throw(
            _(
                "No submitted Monthly Production Setting found "
                "for Company {0} on Posting Date {1}."
            ).format(
                frappe.bold(doc.company),
                frappe.bold(doc.posting_date),
            )
        )

    setting_doc = frappe.get_doc(
        "Monthly Production Setting",
        setting.name,
    )

    expected_qty = flt(setting_doc.expected_qty)

    if expected_qty <= 0:
        frappe.throw(
            _(
                "Expected Qty must be greater than zero in "
                "Monthly Production Setting {0}."
            ).format(
                frappe.bold(setting_doc.name)
            )
        )

    # Use Monthly Production Setting Cost Center
    # if Stock Entry does not already have one.
    if not doc.cost_center:
        doc.cost_center = setting_doc.cost_center

    total_allocated = 0

    for expense in setting_doc.expenses:
        expense_amount = flt(expense.expense_amount)

        if not expense.type_of_expense:
            continue

        if expense_amount <= 0:
            continue

        # Example:
        # 6000 / 1000 = Rs 6 per Kg
        expense_per_qty = (
            expense_amount / expected_qty
        )

        # 20 Kg * Rs 6 = Rs 120
        allocated_amount = (
            expense_per_qty * finished_qty
        )

        doc.append(
            "additional_costs",
            {
                "expense_account": expense.type_of_expense,
                "description": (
                    f"{AUTO_PREFIX} "
                    f"{setting_doc.name} | "
                    f"{expense.type_of_expense}"
                ),
                "amount": allocated_amount,
            },
        )

        total_allocated += allocated_amount

    if total_allocated <= 0:
        frappe.throw(
            _(
                "No production overhead amount found in "
                "Monthly Production Setting {0}."
            ).format(
                frappe.bold(setting_doc.name)
            )
        )


def get_finished_goods_rows(doc):
    rows = []

    for row in doc.get("items", []):
        if not row.item_code:
            continue

        # Finished item must be incoming
        if not row.t_warehouse:
            continue

        item_group = frappe.get_cached_value(
            "Item",
            row.item_code,
            "item_group",
        )

        # Our final packed items are in this Item Group.
        if item_group == "Finished Goods":
            rows.append(row)

    return rows


def get_monthly_setting(doc):
    posting_date = getdate(doc.posting_date)

    settings = frappe.get_all(
        "Monthly Production Setting",
        filters={
            "company": doc.company,
            "docstatus": 1,
            "from_date": ("<=", posting_date),
            "to_date": (">=", posting_date),
        },
        fields=[
            "name",
            "cost_center",
        ],
        order_by="creation desc",
    )

    if not settings:
        return None

    # If Stock Entry already has Cost Center,
    # use matching Monthly Production Setting.
    if doc.cost_center:
        matching = [
            row
            for row in settings
            if row.cost_center == doc.cost_center
        ]

        if not matching:
            frappe.throw(
                _(
                    "No Monthly Production Setting found for "
                    "Cost Center {0} on {1}."
                ).format(
                    frappe.bold(doc.cost_center),
                    frappe.bold(doc.posting_date),
                )
            )

        if len(matching) > 1:
            frappe.throw(
                _(
                    "Multiple Monthly Production Settings found "
                    "for Cost Center {0} on {1}."
                ).format(
                    frappe.bold(doc.cost_center),
                    frappe.bold(doc.posting_date),
                )
            )

        return matching[0]

    # If Cost Center is blank and there is one setting,
    # automatically use it.
    if len(settings) == 1:
        return settings[0]

    frappe.throw(
        _(
            "Multiple Monthly Production Settings exist for "
            "{0} on {1}. Please select Cost Center on Stock Entry."
        ).format(
            frappe.bold(doc.company),
            frappe.bold(doc.posting_date),
        )
    )


def remove_auto_overhead_rows(doc):
    manual_rows = []

    for row in doc.get("additional_costs", []):
        description = row.description or ""

        if not description.startswith(AUTO_PREFIX):
            manual_rows.append(row)

    doc.set("additional_costs", manual_rows)
