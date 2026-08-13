import calendar
from datetime import date

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


class MonthlyProductionSetting(Document):
    def validate(self):
        self.set_dates()
        self.calculate_totals()
        self.validate_expected_qty()
        self.validate_expense_accounts()
        self.validate_duplicate()

    def set_dates(self):
        if not self.month or not self.year:
            return

        month_no = MONTHS.get(self.month)

        if not month_no:
            frappe.throw(_("Invalid Month selected."))

        year = int(self.year)
        last_day = calendar.monthrange(year, month_no)[1]

        self.from_date = date(year, month_no, 1)
        self.to_date = date(year, month_no, last_day)

    def calculate_totals(self):
        total = sum(
            flt(row.expense_amount)
            for row in self.expenses
        )

        self.total_expense_amount = total

        if flt(self.expected_qty) > 0:
            self.per_qty_amount = (
                total / flt(self.expected_qty)
            )
        else:
            self.per_qty_amount = 0

    def validate_expected_qty(self):
        if flt(self.expected_qty) <= 0:
            frappe.throw(
                _("Expected Qty must be greater than zero.")
            )

    def validate_expense_accounts(self):
        used_accounts = set()

        for row in self.expenses:
            if not row.type_of_expense:
                frappe.throw(
                    _("Type of Expense is required in row {0}.").format(
                        row.idx
                    )
                )

            if flt(row.expense_amount) < 0:
                frappe.throw(
                    _("Expense Amount cannot be negative in row {0}.").format(
                        row.idx
                    )
                )

            if row.type_of_expense in used_accounts:
                frappe.throw(
                    _("Expense Account {0} is repeated.").format(
                        row.type_of_expense
                    )
                )

            used_accounts.add(row.type_of_expense)

            account = frappe.db.get_value(
                "Account",
                row.type_of_expense,
                ["company", "is_group"],
                as_dict=True,
            )

            if not account:
                frappe.throw(
                    _("Account {0} does not exist.").format(
                        row.type_of_expense
                    )
                )

            if account.company != self.company:
                frappe.throw(
                    _(
                        "Account {0} does not belong to Company {1}."
                    ).format(
                        row.type_of_expense,
                        self.company,
                    )
                )

            if account.is_group:
                frappe.throw(
                    _(
                        "Please select a ledger Account, "
                        "not a Group Account."
                    )
                )

    def validate_duplicate(self):
        if not (
            self.company
            and self.cost_center
            and self.month
            and self.year
        ):
            return

        filters = {
            "company": self.company,
            "cost_center": self.cost_center,
            "month": self.month,
            "year": self.year,
            "docstatus": ("<", 2),
        }

        if self.name:
            filters["name"] = ("!=", self.name)

        if frappe.db.exists(
            "Monthly Production Setting",
            filters,
        ):
            frappe.throw(
                _(
                    "Monthly Production Setting already exists "
                    "for this Company, Cost Center, Month and Year."
                )
            )