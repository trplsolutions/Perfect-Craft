import calendar
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt, formatdate, getdate

from erpnext.stock.utils import get_stock_balance

from pc_production.stock_entry import AUTO_PREFIX


QTY_TOLERANCE = 0.000001
AMOUNT_TOLERANCE = 0.005
MONTH_END_TIME = "23:59:59"


class MonthEndProductionCostAdjustment(Document):

    def validate(self):
        self.set_from_monthly_setting()
        self.validate_selected_period()
        self.validate_duplicate()
        self.calculate()
        self.validate_expense_rows()
        self.validate_transfer_rows()
        self.validate_item_rows()
        self.validate_posting_accounts()

    def before_submit(self):
        self.calculate()

        if flt(self.actual_produced_qty) <= 0:
            frappe.throw(
                _(
                    "Actual Produced Qty must be greater than zero before submission."
                )
            )

        rows_for_review = [
            row
            for row in self.production_items
            if cint(row.manual_review)
        ]

        if (
            rows_for_review
            and not cint(
                self.manual_quantity_confirmation
            )
        ):
            items = ", ".join(
                row.item_code
                for row in rows_for_review
            )

            frappe.throw(
                _(
                    "Manual quantity review is required for: {0}. "
                    "Review Remaining Qty, Sold / Final Issue Qty "
                    "and Next Stage Transfers, then tick the "
                    "confirmation checkbox."
                ).format(
                    frappe.bold(items)
                )
            )

        for row in self.production_items:
            item_adjustment = (
                flt(self.adjustment_per_qty)
                * flt(row.remaining_qty)
            )

            if (
                abs(item_adjustment)
                <= AMOUNT_TOLERANCE
            ):
                continue

            if (
                cint(row.has_serial_no)
                or cint(row.has_batch_no)
            ):
                frappe.throw(
                    _(
                        "Automatic month-end Stock Reconciliation "
                        "is intentionally blocked for serialized/"
                        "batched Item {0}. ERPNext requires "
                        "serial/batch-specific reconciliation."
                    ).format(
                        frappe.bold(
                            row.item_code
                        )
                    )
                )

    def on_submit(self):
        stock_reconciliation = (
            self.create_stock_reconciliation()
        )

        journal_entry = (
            self.create_journal_entry(
                stock_reconciliation
            )
        )

        if stock_reconciliation:
            self.db_set(
                "stock_reconciliation",
                stock_reconciliation.name,
                update_modified=False,
            )

        if journal_entry:
            self.db_set(
                "journal_entry",
                journal_entry.name,
                update_modified=False,
            )

    def before_cancel(self):
        self.validate_no_submitted_downstream_adjustment()

    def on_cancel(self):
        self.cancel_linked_document(
            "Journal Entry",
            self.journal_entry,
        )

        self.cancel_linked_document(
            "Stock Reconciliation",
            self.stock_reconciliation,
        )

    # =========================================================
    # PERIOD / MONTHLY PRODUCTION SETTING
    # =========================================================

    @frappe.whitelist()
    def resolve_monthly_production_setting(self):
        """
        Automatically load the complete month-end snapshot.

        User normally selects only:
        - Month
        - Cost Center

        Company comes from the default company.
        Year defaults to the current year.

        The system then automatically:
        - resolves Monthly Production Setting
        - sets From Date / To Date
        - loads expected expense data
        - fetches production movement
        - calculates produced / remaining / sold / next stage
        """

        if self.docstatus != 0:
            frappe.throw(
                _(
                    "Month-end data can only be loaded "
                    "while the document is in Draft."
                )
            )

        missing = []

        if not self.company:
            missing.append(
                _("Company")
            )

        if not self.cost_center:
            missing.append(
                _("Cost Center")
            )

        if not self.month:
            missing.append(
                _("Month")
            )

        if not self.year:
            missing.append(
                _("Year")
            )

        if missing:
            frappe.throw(
                _(
                    "Please select: {0}."
                ).format(
                    ", ".join(
                        missing
                    )
                )
            )

        setting = (
            self.get_matching_monthly_production_setting(
                throw_if_missing=True
            )
        )

        self.monthly_production_setting = (
            setting.name
        )

        self.load_expected_values_from_setting(
            setting
        )

        self.validate_selected_period()

        self.build_month_snapshot(
            force_actual_from_gl=False
        )

        self.calculate()

        self.validate_expense_rows()
        self.validate_transfer_rows()
        self.validate_item_rows()

        return {
            "doc": self.as_dict()
        }


    def set_from_monthly_setting(self):
        """
        Resolve Monthly Production Setting from:

        Company + Cost Center + Month + Year.

        From Date and To Date are fetched from the setting.
        """

        if not (
            self.company
            and self.cost_center
            and self.month
            and self.year
        ):
            return

        setting = (
            self.get_matching_monthly_production_setting(
                throw_if_missing=True
            )
        )

        self.monthly_production_setting = (
            setting.name
        )

        self.load_expected_values_from_setting(
            setting
        )


    def has_complete_period_selection(self):
        return bool(
            self.company
            and self.cost_center
            and self.month
            and self.year
            and self.from_date
            and self.to_date
        )

    def validate_period_selection_complete(self):
        missing = []

        if not self.company:
            missing.append(
                _("Company")
            )

        if not self.cost_center:
            missing.append(
                _("Cost Center")
            )

        if not self.month:
            missing.append(
                _("Month")
            )

        if not self.year:
            missing.append(
                _("Year")
            )

        if not self.from_date:
            missing.append(
                _("From Date")
            )

        if not self.to_date:
            missing.append(
                _("To Date")
            )

        if missing:
            frappe.throw(
                _(
                    "Please select the following before fetching "
                    "the Monthly Production Setting: {0}."
                ).format(
                    ", ".join(
                        missing
                    )
                )
            )

    def get_matching_monthly_production_setting(
        self,
        throw_if_missing=False,
    ):
        """
        Find exactly one submitted Monthly Production Setting.

        Matching fields:
        - Company
        - Cost Center
        - Month
        - Year

        Selected dates must also match the setting dates.
        """

        if not (
            self.company
            and self.cost_center
            and self.month
            and self.year
        ):
            if throw_if_missing:
                frappe.throw(
                    _(
                        "Select Company, Cost Center, Month and Year first."
                    )
                )

            return None

        filters = {
            "company":
                self.company,
            "cost_center":
                self.cost_center,
            "month":
                self.month,
            "year":
                cint(
                    self.year
                ),
            "docstatus":
                1,
        }

        settings = frappe.get_all(
            "Monthly Production Setting",
            filters=filters,
            fields=[
                "name",
                "from_date",
                "to_date",
            ],
            order_by="creation asc",
            limit_page_length=2,
        )

        if not settings:
            self.monthly_production_setting = None

            if throw_if_missing:
                frappe.throw(
                    _(
                        "No submitted Monthly Production Setting was found "
                        "for Company {0}, Cost Center {1}, Month {2}, "
                        "Year {3}."
                    ).format(
                        frappe.bold(
                            self.company
                        ),
                        frappe.bold(
                            self.cost_center
                        ),
                        frappe.bold(
                            self.month
                        ),
                        frappe.bold(
                            self.year
                        ),
                    )
                )

            return None

        if len(settings) > 1:
            frappe.throw(
                _(
                    "More than one submitted Monthly Production Setting "
                    "exists for Company {0}, Cost Center {1}, Month {2}, "
                    "Year {3}. Keep only one submitted setting for "
                    "the same period."
                ).format(
                    frappe.bold(
                        self.company
                    ),
                    frappe.bold(
                        self.cost_center
                    ),
                    frappe.bold(
                        self.month
                    ),
                    frappe.bold(
                        self.year
                    ),
                )
            )

        setting = frappe.get_doc(
            "Monthly Production Setting",
            settings[0].name,
        )

        self.validate_selected_dates_against_setting(
            setting
        )

        return setting

    def validate_selected_dates_against_setting(
        self,
        setting,
    ):
        if not (
            self.from_date
            and self.to_date
        ):
            return

        selected_from = getdate(
            self.from_date
        )

        selected_to = getdate(
            self.to_date
        )

        setting_from = getdate(
            setting.from_date
        )

        setting_to = getdate(
            setting.to_date
        )

        if (
            selected_from != setting_from
            or selected_to != setting_to
        ):
            frappe.throw(
                _(
                    "The selected dates do not match Monthly Production "
                    "Setting {0}. For {1} {2}, use From Date {3} "
                    "and To Date {4}."
                ).format(
                    frappe.bold(
                        setting.name
                    ),
                    self.month,
                    self.year,
                    formatdate(
                        setting_from
                    ),
                    formatdate(
                        setting_to
                    ),
                )
            )

    def load_expected_values_from_setting(
        self,
        setting,
    ):
        if setting.docstatus != 1:
            frappe.throw(
                _(
                    "Monthly Production Setting {0} "
                    "must be submitted first."
                ).format(
                    frappe.bold(
                        setting.name
                    )
                )
            )

        if setting.company != self.company:
            frappe.throw(
                _(
                    "Monthly Production Setting Company does not "
                    "match the selected Company."
                )
            )

        if setting.cost_center != self.cost_center:
            frappe.throw(
                _(
                    "Monthly Production Setting Cost Center does not "
                    "match the selected Cost Center."
                )
            )

        if setting.month != self.month:
            frappe.throw(
                _(
                    "Monthly Production Setting Month does not "
                    "match the selected Month."
                )
            )

        if (
            cint(
                setting.year
            )
            != cint(
                self.year
            )
        ):
            frappe.throw(
                _(
                    "Monthly Production Setting Year does not "
                    "match the selected Year."
                )
            )

        self.from_date = (
            setting.from_date
        )

        self.to_date = (
            setting.to_date
        )

        self.expected_expense_amount = flt(
            setting.total_expense_amount
        )

        self.expected_qty = flt(
            setting.expected_qty
        )

        self.expected_cost_per_qty = flt(
            setting.per_qty_amount
        )

        self.posting_time = (
            MONTH_END_TIME
        )

        if not self.stock_adjustment_account:
            self.stock_adjustment_account = (
                frappe.get_cached_value(
                    "Company",
                    self.company,
                    "stock_adjustment_account",
                )
            )


    def validate_selected_period(self):
        if not self.has_complete_period_selection():
            return

        from_date = getdate(
            self.from_date
        )

        to_date = getdate(
            self.to_date
        )

        if from_date > to_date:
            frappe.throw(
                _(
                    "From Date cannot be after To Date."
                )
            )

        try:
            month_number = list(
                calendar.month_name
            ).index(
                self.month
            )

        except ValueError:
            frappe.throw(
                _(
                    "Invalid Month selected: {0}."
                ).format(
                    self.month
                )
            )

        selected_year = cint(
            self.year
        )

        if (
            from_date.year != selected_year
            or from_date.month != month_number
        ):
            frappe.throw(
                _(
                    "From Date must belong to {0} {1}."
                ).format(
                    self.month,
                    self.year,
                )
            )

        if (
            to_date.year != selected_year
            or to_date.month != month_number
        ):
            frappe.throw(
                _(
                    "To Date must belong to {0} {1}."
                ).format(
                    self.month,
                    self.year,
                )
            )

        if self.monthly_production_setting:
            setting = frappe.get_doc(
                "Monthly Production Setting",
                self.monthly_production_setting,
            )

            self.validate_selected_dates_against_setting(
                setting
            )

    # =========================================================
    # FETCH / RECALCULATE
    # =========================================================

    @frappe.whitelist()
    def fetch_month_data(
        self,
        force_actual_from_gl=0,
    ):
        if self.docstatus != 0:
            frappe.throw(
                _(
                    "Month data can only be refreshed while "
                    "the document is in Draft."
                )
            )

        self.set_from_monthly_setting()
        self.validate_selected_period()

        self.build_month_snapshot(
            force_actual_from_gl=cint(
                force_actual_from_gl
            )
        )

        self.calculate()
        self.validate_expense_rows()
        self.validate_transfer_rows()
        self.validate_item_rows()

        return {
            "doc": self.as_dict()
        }

    @frappe.whitelist()
    def recalculate(self):
        if self.docstatus != 0:
            frappe.throw(
                _(
                    "Only a Draft month-end adjustment can be recalculated."
                )
            )

        self.set_from_monthly_setting()
        self.validate_selected_period()

        self.calculate()
        self.validate_expense_rows()
        self.validate_transfer_rows()
        self.validate_item_rows()

        return {
            "doc": self.as_dict()
        }

    # =========================================================
    # DUPLICATE CONTROL
    # =========================================================

    def validate_duplicate(self):
        if not self.monthly_production_setting:
            return

        filters = {
            "monthly_production_setting":
                self.monthly_production_setting,
            "docstatus": (
                "<",
                2,
            ),
        }

        if self.name:
            filters["name"] = (
                "!=",
                self.name,
            )

        existing = frappe.db.exists(
            "Month End Production Cost Adjustment",
            filters,
        )

        if existing:
            frappe.throw(
                _(
                    "A Month End Production Cost Adjustment "
                    "already exists for Monthly Production "
                    "Setting {0}: {1}."
                ).format(
                    frappe.bold(
                        self.monthly_production_setting
                    ),
                    frappe.bold(
                        existing
                    ),
                )
            )

    # =========================================================
    # BUILD MONTH SNAPSHOT
    # =========================================================

    def build_month_snapshot(
        self,
        force_actual_from_gl=False,
    ):
        if not (
            self.company
            and self.cost_center
            and self.from_date
            and self.to_date
        ):
            frappe.throw(
                _(
                    "Select Company, Cost Center, Month, Year, "
                    "From Date and To Date first."
                )
            )

        movement = (
            get_manufacturing_movement(
                company=self.company,
                from_date=self.from_date,
                to_date=self.to_date,
                cost_center=self.cost_center,
            )
        )

        produced = (
            movement[
                "produced"
            ]
        )

        if not produced:
            frappe.throw(
                _(
                    "No submitted Manufacture Stock Entry "
                    "finished quantity was found for Cost Center "
                    "{0} between {1} and {2}."
                ).format(
                    frappe.bold(
                        self.cost_center
                    ),
                    frappe.bold(
                        self.from_date
                    ),
                    frappe.bold(
                        self.to_date
                    ),
                )
            )

        self.build_expense_rows(
            own_production_vouchers=
                movement[
                    "own_production_vouchers"
                ],
            force_actual_from_gl=
                force_actual_from_gl,
        )

        self.build_transfer_rows(
            movement[
                "next_stage_transfers"
            ]
        )

        self.build_item_rows(
            produced=produced,
            own_production_vouchers=
                movement[
                    "own_production_vouchers"
                ],
        )

        unmapped = (
            movement[
                "unmapped_consumption"
            ]
        )

        if unmapped:
            self.calculation_note = _(
                "Some manufacturing consumption could not be mapped "
                "to a target Cost Center. Review the Next Stage "
                "Transfers before submission."
            )

    def build_expense_rows(
        self,
        own_production_vouchers,
        force_actual_from_gl=False,
    ):
        """
        Expense accounts and expected amounts come from
        Monthly Production Setting.

        Actual Expense is ALWAYS entered manually by the user.

        No GL amount is automatically fetched here.
        """

        setting = frappe.get_doc(
            "Monthly Production Setting",
            self.monthly_production_setting,
        )

        existing_actual = {
            row.expense_account:
                flt(
                    row.actual_expense_amount
                )
            for row
            in self.actual_expenses
            if row.expense_account
        }

        self.set(
            "actual_expenses",
            [],
        )

        for source in setting.expenses:
            if not source.type_of_expense:
                continue

            actual_amount = (
                existing_actual.get(
                    source.type_of_expense,
                    0,
                )
            )

            self.append(
                "actual_expenses",
                {
                    "expense_account":
                        source.type_of_expense,

                    "expected_expense_amount":
                        flt(
                            source.expense_amount
                        ),

                    "actual_expense_amount":
                        actual_amount,
                },
            )


    def build_transfer_rows(
        self,
        detected_transfers,
    ):
        existing = {
            (
                row.item_code,
                row.target_cost_center,
            ):
                flt(
                    row.consumed_qty
                )
            for row
            in self.next_stage_transfers
            if (
                row.item_code
                and row.target_cost_center
            )
        }

        self.set(
            "next_stage_transfers",
            [],
        )

        for key in sorted(
            detected_transfers
        ):
            item_code, target_cost_center = (
                key
            )

            detected_qty = flt(
                detected_transfers[
                    key
                ]
            )

            consumed_qty = (
                existing.get(
                    key,
                    detected_qty,
                )
            )

            self.append(
                "next_stage_transfers",
                {
                    "item_code":
                        item_code,
                    "target_cost_center":
                        target_cost_center,
                    "detected_qty":
                        detected_qty,
                    "consumed_qty":
                        consumed_qty,
                },
            )

    def build_item_rows(
        self,
        produced,
        own_production_vouchers,
    ):
        item_codes = sorted(
            produced
        )

        opening_qty = (
            get_item_quantity_map(
                company=self.company,
                item_codes=item_codes,
                before_date=
                    self.from_date,
            )
        )

        closing_qty = (
            get_item_quantity_map(
                company=self.company,
                item_codes=item_codes,
                upto_date=
                    self.to_date,
            )
        )

        other_receipts = (
            get_other_net_receipts(
                company=self.company,
                item_codes=item_codes,
                from_date=
                    self.from_date,
                to_date=
                    self.to_date,
                own_production_vouchers=
                    own_production_vouchers,
            )
        )

        transfer_qty = (
            defaultdict(float)
        )

        for row in self.next_stage_transfers:
            transfer_qty[
                row.item_code
            ] += flt(
                row.consumed_qty
            )

        old_items = {
            row.item_code:
                row
            for row
            in self.production_items
            if row.item_code
        }

        self.set(
            "production_items",
            [],
        )

        for item_code in item_codes:
            produced_qty = flt(
                produced[
                    item_code
                ]
            )

            opening = flt(
                opening_qty.get(
                    item_code
                )
            )

            closing = flt(
                closing_qty.get(
                    item_code
                )
            )

            other_in = flt(
                other_receipts.get(
                    item_code
                )
            )

            next_qty = flt(
                transfer_qty.get(
                    item_code
                )
            )

            item_data = (
                frappe.get_cached_value(
                    "Item",
                    item_code,
                    [
                        "stock_uom",
                        "has_serial_no",
                        "has_batch_no",
                    ],
                    as_dict=True,
                )
            )

            if not item_data:
                frappe.throw(
                    _(
                        "Item {0} does not exist."
                    ).format(
                        frappe.bold(
                            item_code
                        )
                    )
                )

            reasons = []

            if (
                abs(
                    opening
                )
                > QTY_TOLERANCE
            ):
                reasons.append(
                    _(
                        "opening stock exists"
                    )
                )

            if (
                other_in
                > QTY_TOLERANCE
            ):
                reasons.append(
                    _(
                        "other positive receipts exist in the month"
                    )
                )

            if (
                closing
                < -QTY_TOLERANCE
            ):
                reasons.append(
                    _(
                        "month-end company stock is negative"
                    )
                )

            if (
                next_qty
                - produced_qty
                > QTY_TOLERANCE
            ):
                reasons.append(
                    _(
                        "detected next-stage consumption exceeds "
                        "current production"
                    )
                )

            if (
                max(
                    closing,
                    0,
                )
                + next_qty
                - produced_qty
                > QTY_TOLERANCE
            ):
                reasons.append(
                    _(
                        "closing stock plus next-stage usage exceeds "
                        "current production"
                    )
                )

            manual_review = bool(
                reasons
            )

            safe_remaining = min(
                max(
                    closing,
                    0,
                ),
                max(
                    produced_qty
                    - next_qty,
                    0,
                ),
            )

            safe_sold = max(
                produced_qty
                - next_qty
                - safe_remaining,
                0,
            )

            old = old_items.get(
                item_code
            )

            if (
                old
                and manual_review
            ):
                remaining = flt(
                    old.remaining_qty
                )

                sold = flt(
                    old.sold_qty
                )

            else:
                remaining = (
                    safe_remaining
                )

                sold = (
                    safe_sold
                )

            self.append(
                "production_items",
                {
                    "item_code":
                        item_code,
                    "stock_uom":
                        item_data.stock_uom,
                    "produced_qty":
                        produced_qty,
                    "opening_qty":
                        opening,
                    "other_receipt_qty":
                        other_in,
                    "closing_qty":
                        closing,
                    "next_stage_qty":
                        next_qty,
                    "remaining_qty":
                        remaining,
                    "sold_qty":
                        sold,
                    "cogs_account":
                        (
                            old.cogs_account
                            if (
                                old
                                and old.cogs_account
                            )
                            else
                            get_item_expense_account(
                                item_code,
                                self.company,
                            )
                        ),
                    "manual_review":
                        (
                            1
                            if manual_review
                            else 0
                        ),
                    "review_reason":
                        ", ".join(
                            reasons
                        ),
                    "has_serial_no":
                        cint(
                            item_data.has_serial_no
                        ),
                    "has_batch_no":
                        cint(
                            item_data.has_batch_no
                        ),
                },
            )

    # =========================================================
    # CALCULATIONS
    # =========================================================

    def calculate(self):
        existing_warning = (
            self.calculation_note
            or ""
        ).strip()

        if (
            "could not be mapped"
            not in existing_warning
        ):
            existing_warning = ""

        self.actual_produced_qty = sum(
            flt(
                row.produced_qty
            )
            for row
            in self.production_items
        )

        transfer_by_item = (
            defaultdict(float)
        )

        for row in self.next_stage_transfers:
            if row.item_code:
                transfer_by_item[
                    row.item_code
                ] += flt(
                    row.consumed_qty
                )

        for row in self.production_items:
            row.next_stage_qty = flt(
                transfer_by_item.get(
                    row.item_code
                )
            )

        expected_qty = flt(
            self.expected_qty
        )

        actual_qty = flt(
            self.actual_produced_qty
        )

        total_actual = 0
        own_adjustment = 0

        for row in self.actual_expenses:
            expected_amount = flt(
                row.expected_expense_amount
            )

            actual_amount = flt(
                row.actual_expense_amount
            )

            expected_per_qty = (
                expected_amount
                / expected_qty
                if expected_qty > 0
                else 0
            )

            actual_per_qty = (
                actual_amount
                / actual_qty
                if actual_qty > 0
                else 0
            )

            adjustment_per_qty = (
                actual_per_qty
                - expected_per_qty
                if actual_qty > 0
                else 0
            )

            adjustment_amount = (
                actual_amount
                - (
                    expected_per_qty
                    * actual_qty
                )
                if actual_qty > 0
                else 0
            )

            row.expected_per_qty_amount = (
                expected_per_qty
            )

            row.actual_per_qty_amount = (
                actual_per_qty
            )

            row.adjustment_per_qty = (
                adjustment_per_qty
            )

            row.adjustment_amount = (
                adjustment_amount
            )

            total_actual += (
                actual_amount
            )

            own_adjustment += (
                adjustment_amount
            )

        self.actual_expense_amount = (
            total_actual
        )

        self.actual_cost_per_qty = (
            total_actual
            / actual_qty
            if actual_qty > 0
            else 0
        )

        self.own_adjustment_amount = (
            own_adjustment
        )

        components = (
            self.get_current_cost_components()
        )

        upstream_amount = sum(
            flt(
                component[
                    "amount"
                ]
            )
            for component
            in components
            if (
                component[
                    "origin_adjustment"
                ]
                != self.name
            )
        )

        self.upstream_adjustment_amount = (
            upstream_amount
        )

        self.total_adjustment_amount = (
            own_adjustment
            + upstream_amount
        )

        self.adjustment_per_qty = (
            flt(
                self.total_adjustment_amount
            )
            / actual_qty
            if actual_qty > 0
            else 0
        )

        manual_count = sum(
            1
            for row
            in self.production_items
            if cint(
                row.manual_review
            )
        )

        note_parts = []

        if existing_warning:
            note_parts.append(
                existing_warning
            )

        note_parts.extend(
            [
                _(
                    "Submit month-end adjustments in production order "
                    "(upstream stage first)."
                ),
                _(
                    "Upstream carry-in is read only from submitted "
                    "month-end adjustment documents."
                ),
            ]
        )

        if manual_count:
            note_parts.append(
                _(
                    "{0} production item(s) require manual "
                    "quantity review."
                ).format(
                    manual_count
                )
            )

        self.calculation_note = (
            " ".join(
                note_parts
            )
        )

    # =========================================================
    # VALIDATIONS
    # =========================================================

    def validate_expense_rows(self):
        if not self.monthly_production_setting:
            return

        setting = frappe.get_doc(
            "Monthly Production Setting",
            self.monthly_production_setting,
        )

        expected_accounts = [
            row.type_of_expense
            for row
            in setting.expenses
            if row.type_of_expense
        ]

        actual_accounts = [
            row.expense_account
            for row
            in self.actual_expenses
            if row.expense_account
        ]

        if (
            sorted(
                expected_accounts
            )
            != sorted(
                actual_accounts
            )
        ):
            frappe.throw(
                _(
                    "Actual Expenses must contain exactly the expense "
                    "accounts from Monthly Production Setting {0}. "
                    "Use Fetch / Refresh Month Data instead of adding "
                    "or deleting expense rows manually."
                ).format(
                    frappe.bold(
                        setting.name
                    )
                )
            )

        if (
            len(
                actual_accounts
            )
            != len(
                set(
                    actual_accounts
                )
            )
        ):
            frappe.throw(
                _(
                    "The same Expense Account cannot appear more than once."
                )
            )

        for row in self.actual_expenses:
            if (
                flt(
                    row.actual_expense_amount
                )
                < 0
            ):
                frappe.throw(
                    _(
                        "Actual Expense cannot be negative for account {0}."
                    ).format(
                        frappe.bold(
                            row.expense_account
                        )
                    )
                )

    def validate_transfer_rows(self):
        seen = set()

        produced_items = {
            row.item_code
            for row
            in self.production_items
        }

        for row in self.next_stage_transfers:
            key = (
                row.item_code,
                row.target_cost_center,
            )

            if key in seen:
                frappe.throw(
                    _(
                        "Duplicate Next Stage Transfer for Item {0} "
                        "and Cost Center {1}."
                    ).format(
                        frappe.bold(
                            row.item_code
                        ),
                        frappe.bold(
                            row.target_cost_center
                        ),
                    )
                )

            seen.add(
                key
            )

            if (
                row.item_code
                not in produced_items
            ):
                frappe.throw(
                    _(
                        "Next Stage Transfer Item {0} is not in "
                        "this month's produced items."
                    ).format(
                        frappe.bold(
                            row.item_code
                        )
                    )
                )

            if (
                row.target_cost_center
                == self.cost_center
            ):
                frappe.throw(
                    _(
                        "Next Stage Cost Center cannot be the same "
                        "as the current Cost Center."
                    )
                )

            if (
                flt(
                    row.consumed_qty
                )
                < 0
            ):
                frappe.throw(
                    _(
                        "Carry Forward Qty cannot be negative."
                    )
                )

            cc_company = (
                frappe.get_cached_value(
                    "Cost Center",
                    row.target_cost_center,
                    "company",
                )
            )

            if (
                cc_company
                != self.company
            ):
                frappe.throw(
                    _(
                        "Cost Center {0} does not belong to Company {1}."
                    ).format(
                        frappe.bold(
                            row.target_cost_center
                        ),
                        frappe.bold(
                            self.company
                        ),
                    )
                )

    def validate_item_rows(self):
        seen = set()

        for row in self.production_items:
            if row.item_code in seen:
                frappe.throw(
                    _(
                        "Item {0} is repeated in Production Items."
                    ).format(
                        frappe.bold(
                            row.item_code
                        )
                    )
                )

            seen.add(
                row.item_code
            )

            produced = flt(
                row.produced_qty
            )

            remaining = flt(
                row.remaining_qty
            )

            sold = flt(
                row.sold_qty
            )

            next_stage = flt(
                row.next_stage_qty
            )

            closing = flt(
                row.closing_qty
            )

            if (
                min(
                    produced,
                    remaining,
                    sold,
                    next_stage,
                )
                < -QTY_TOLERANCE
            ):
                frappe.throw(
                    _(
                        "Production quantities cannot be negative "
                        "for Item {0}."
                    ).format(
                        row.item_code
                    )
                )

            allocated = (
                remaining
                + sold
                + next_stage
            )

            if (
                abs(
                    allocated
                    - produced
                )
                > QTY_TOLERANCE
            ):
                frappe.throw(
                    _(
                        "Item {0}: Produced Qty ({1}) must equal "
                        "Remaining Qty ({2}) + Sold / Final Issue Qty ({3}) "
                        "+ Next Stage Qty ({4}). Current allocated "
                        "total is {5}."
                    ).format(
                        frappe.bold(
                            row.item_code
                        ),
                        produced,
                        remaining,
                        sold,
                        next_stage,
                        allocated,
                    )
                )

            if (
                remaining
                - max(
                    closing,
                    0,
                )
                > QTY_TOLERANCE
            ):
                frappe.throw(
                    _(
                        "Item {0}: Remaining Qty ({1}) cannot be greater "
                        "than the company-wide positive closing stock "
                        "({2}) at month end."
                    ).format(
                        frappe.bold(
                            row.item_code
                        ),
                        remaining,
                        closing,
                    )
                )

            if not row.cogs_account:
                frappe.throw(
                    _(
                        "COGS / Final Expense Account is required "
                        "for Item {0}."
                    ).format(
                        row.item_code
                    )
                )

    def validate_posting_accounts(self):
        if not self.company:
            return

        if not self.stock_adjustment_account:
            frappe.throw(
                _(
                    "Stock Adjustment Account is required. "
                    "Set it on Company {0} or select it on this document."
                ).format(
                    frappe.bold(
                        self.company
                    )
                )
            )

        accounts = {
            (
                self.stock_adjustment_account,
                self.cost_center,
            )
        }

        for row in self.production_items:
            if row.cogs_account:
                accounts.add(
                    (
                        row.cogs_account,
                        self.cost_center,
                    )
                )

        for component in (
            self.get_current_cost_components()
        ):
            accounts.add(
                (
                    component[
                        "expense_account"
                    ],
                    component[
                        "origin_cost_center"
                    ],
                )
            )

        for (
            account,
            cost_center,
        ) in accounts:
            validate_account_for_auto_journal(
                account,
                self.company,
            )

            if cost_center:
                cc_company = (
                    frappe.get_cached_value(
                        "Cost Center",
                        cost_center,
                        "company",
                    )
                )

                if (
                    cc_company
                    != self.company
                ):
                    frappe.throw(
                        _(
                            "Cost Center {0} does not belong "
                            "to Company {1}."
                        ).format(
                            frappe.bold(
                                cost_center
                            ),
                            frappe.bold(
                                self.company
                            ),
                        )
                    )

    # =========================================================
    # UPSTREAM COST COMPONENTS
    # =========================================================

    def get_current_cost_components(self):
        components = (
            defaultdict(float)
        )

        for row in self.actual_expenses:
            if not row.expense_account:
                continue

            key = (
                self.name,
                self.cost_center,
                row.expense_account,
            )

            components[
                key
            ] += flt(
                row.adjustment_amount
            )

        if not (
            self.company
            and self.cost_center
            and self.month
            and self.year
        ):
            return component_dicts(
                components,
                flt(
                    self.actual_produced_qty
                ),
            )

        upstream_adjustments = (
            find_upstream_adjustments(
                company=self.company,
                month=self.month,
                year=self.year,
                target_cost_center=
                    self.cost_center,
                exclude_name=self.name,
            )
        )

        for (
            upstream_name,
            transferred_qty,
        ) in upstream_adjustments.items():

            upstream_doc = (
                frappe.get_doc(
                    "Month End Production Cost Adjustment",
                    upstream_name,
                )
            )

            upstream_components = (
                get_submitted_cost_components(
                    upstream_doc,
                    visited=set(),
                )
            )

            upstream_qty = flt(
                upstream_doc.actual_produced_qty
            )

            if upstream_qty <= 0:
                frappe.throw(
                    _(
                        "Upstream Month End Adjustment {0} "
                        "has zero Actual Produced Qty."
                    ).format(
                        frappe.bold(
                            upstream_doc.name
                        )
                    )
                )

            for component in upstream_components:
                inherited_amount = (
                    flt(
                        component[
                            "amount"
                        ]
                    )
                    / upstream_qty
                    * flt(
                        transferred_qty
                    )
                )

                key = (
                    component[
                        "origin_adjustment"
                    ],
                    component[
                        "origin_cost_center"
                    ],
                    component[
                        "expense_account"
                    ],
                )

                components[
                    key
                ] += inherited_amount

        return component_dicts(
            components,
            flt(
                self.actual_produced_qty
            ),
        )

    # =========================================================
    # STOCK RECONCILIATION
    # =========================================================

    def create_stock_reconciliation(self):
        adjustment_per_qty = flt(
            self.adjustment_per_qty
        )

        if (
            abs(
                adjustment_per_qty
            )
            <= AMOUNT_TOLERANCE
        ):
            return None

        item_adjustments = {}
        item_codes = []

        for row in self.production_items:
            amount = (
                adjustment_per_qty
                * flt(
                    row.remaining_qty
                )
            )

            if (
                abs(
                    amount
                )
                > AMOUNT_TOLERANCE
            ):
                item_adjustments[
                    row.item_code
                ] = amount

                item_codes.append(
                    row.item_code
                )

        if not item_adjustments:
            return None

        balances = (
            get_warehouse_quantity_map(
                company=self.company,
                item_codes=item_codes,
                upto_date=self.to_date,
            )
        )

        sr = frappe.new_doc(
            "Stock Reconciliation"
        )

        sr.company = (
            self.company
        )

        sr.purpose = (
            "Stock Reconciliation"
        )

        sr.posting_date = (
            self.to_date
        )

        sr.posting_time = (
            self.posting_time
            or MONTH_END_TIME
        )

        sr.set_posting_time = 1

        sr.expense_account = (
            self.stock_adjustment_account
        )

        sr.cost_center = (
            self.cost_center
        )

        # ---------------------------------------------------------
        # Copy Month End calculation details to Stock Reconciliation
        # ---------------------------------------------------------

        sr.custom_expected_per_qty_amount = flt(
            self.expected_cost_per_qty
        )

        sr.custom_actual_per_qty_amount = flt(
            self.actual_cost_per_qty
        )

        sr.custom_actual_qty = flt(
            self.actual_produced_qty
        )

        sr.custom_adjustment_per_qty = flt(
            self.adjustment_per_qty
        )

        # ---------------------------------------------------------
        # Copy Production Expense detail
        # ---------------------------------------------------------

        sr.set(
            "custom_production_expenses",
            [],
        )

        for expense in self.actual_expenses:
            sr.append(
                "custom_production_expenses",
                {
                    "type_of_expense":
                        expense.expense_account,

                    "expected_expense_amount":
                        flt(
                            expense.expected_expense_amount
                        ),

                    "actual_expense_amount":
                        flt(
                            expense.actual_expense_amount
                        ),

                    "actual_per_qty_amount":
                        flt(
                            expense.actual_per_qty_amount
                        ),

                    "value_after_calculation":
                        flt(
                            expense.adjustment_per_qty
                        ),
                },
            )

        # ---------------------------------------------------------
        # IMPORTANT:
        # Month End has already calculated the valuation.
        # Do not run the legacy Stock Reconciliation formula again.
        # ---------------------------------------------------------

        sr.flags.skip_pc_production_reconciliation = (
            True
        )

        for item_code in item_codes:
            item_balances = {
                warehouse:
                    flt(
                        qty
                    )
                for (
                    item,
                    warehouse,
                ), qty
                in balances.items()
                if (
                    item == item_code
                    and abs(
                        flt(
                            qty
                        )
                    )
                    > QTY_TOLERANCE
                )
            }

            if any(
                qty < -QTY_TOLERANCE
                for qty
                in item_balances.values()
            ):
                frappe.throw(
                    _(
                        "Item {0} has negative stock in one or more "
                        "warehouses at month end. Automatic Stock "
                        "Reconciliation is blocked."
                    ).format(
                        frappe.bold(
                            item_code
                        )
                    )
                )

            positive_total = sum(
                qty
                for qty
                in item_balances.values()
                if qty > QTY_TOLERANCE
            )

            if positive_total <= 0:
                frappe.throw(
                    _(
                        "Item {0} has Remaining Qty but no positive "
                        "stock balance at month end."
                    ).format(
                        frappe.bold(
                            item_code
                        )
                    )
                )

            rate_delta = (
                flt(
                    item_adjustments[
                        item_code
                    ]
                )
                / positive_total
            )

            for (
                warehouse,
                warehouse_qty,
            ) in sorted(
                item_balances.items()
            ):
                if (
                    warehouse_qty
                    <= QTY_TOLERANCE
                ):
                    continue

                stock_balance = (
                    get_stock_balance(
                        item_code,
                        warehouse,
                        self.to_date,
                        self.posting_time
                        or MONTH_END_TIME,
                        with_valuation_rate=True,
                    )
                )

                if isinstance(
                    stock_balance,
                    (
                        list,
                        tuple,
                    ),
                ):
                    current_qty = flt(
                        stock_balance[
                            0
                        ]
                    )

                    current_rate = flt(
                        stock_balance[
                            1
                        ]
                    )

                else:
                    current_qty = (
                        warehouse_qty
                    )

                    current_rate = flt(
                        stock_balance
                    )

                if (
                    abs(
                        current_qty
                        - warehouse_qty
                    )
                    > QTY_TOLERANCE
                ):
                    current_qty = (
                        warehouse_qty
                    )

                new_rate = (
                    current_rate
                    + rate_delta
                )

                if (
                    new_rate
                    < -AMOUNT_TOLERANCE
                ):
                    frappe.throw(
                        _(
                            "Item {0} in Warehouse {1} would get "
                            "a negative valuation rate ({2}). "
                            "Review the actual expense and "
                            "quantity allocation."
                        ).format(
                            frappe.bold(
                                item_code
                            ),
                            frappe.bold(
                                warehouse
                            ),
                            new_rate,
                        )
                    )

                sr.append(
                    "items",
                    {
                        "item_code":
                            item_code,
                        "warehouse":
                            warehouse,
                        "qty":
                            current_qty,
                        "valuation_rate":
                            max(
                                new_rate,
                                0,
                            ),
                    },
                )

        if not sr.items:
            return None

        sr.flags.ignore_permissions = (
            True
        )

        sr.flags.skip_pc_production_reconciliation = (
            True
        )

        sr.insert(
            ignore_permissions=True
        )

        sr.flags.skip_pc_production_reconciliation = (
            True
        )

        sr.submit()

        return sr

    # =========================================================
    # JOURNAL ENTRY
    # =========================================================

    def create_journal_entry(
        self,
        stock_reconciliation=None,
    ):
        actual_qty = flt(
            self.actual_produced_qty
        )

        if actual_qty <= 0:
            return None

        components = (
            self.get_current_cost_components()
        )

        if not components:
            return None

        total_remaining_qty = sum(
            flt(
                row.remaining_qty
            )
            for row
            in self.production_items
        )

        total_sold_qty = sum(
            flt(
                row.sold_qty
            )
            for row
            in self.production_items
        )

        theoretical_remaining = sum(
            flt(
                component[
                    "per_qty"
                ]
            )
            * total_remaining_qty
            for component
            in components
        )

        actual_sr_difference = (
            flt(
                stock_reconciliation.difference_amount
            )
            if stock_reconciliation
            else 0
        )

        if (
            stock_reconciliation
            and abs(
                theoretical_remaining
            )
            > AMOUNT_TOLERANCE
        ):
            remaining_scale = (
                actual_sr_difference
                / theoretical_remaining
            )

        elif stock_reconciliation:
            remaining_scale = 1

        else:
            remaining_scale = 0

        signed_lines = (
            defaultdict(float)
        )

        if (
            stock_reconciliation
            and abs(
                actual_sr_difference
            )
            > AMOUNT_TOLERANCE
        ):
            signed_lines[
                (
                    self.stock_adjustment_account,
                    self.cost_center,
                )
            ] += actual_sr_difference

        for item in self.production_items:
            sold_amount = (
                flt(
                    self.adjustment_per_qty
                )
                * flt(
                    item.sold_qty
                )
            )

            if (
                abs(
                    sold_amount
                )
                > AMOUNT_TOLERANCE
            ):
                signed_lines[
                    (
                        item.cogs_account,
                        self.cost_center,
                    )
                ] += sold_amount

        for component in components:
            remaining_component = (
                flt(
                    component[
                        "per_qty"
                    ]
                )
                * total_remaining_qty
                * remaining_scale
            )

            sold_component = (
                flt(
                    component[
                        "per_qty"
                    ]
                )
                * total_sold_qty
            )

            resolved_component = (
                remaining_component
                + sold_component
            )

            if (
                abs(
                    resolved_component
                )
                > AMOUNT_TOLERANCE
            ):
                signed_lines[
                    (
                        component[
                            "expense_account"
                        ],
                        component[
                            "origin_cost_center"
                        ],
                    )
                ] -= resolved_component

        signed_lines = defaultdict(
            float,
            {
                key:
                    value
                for (
                    key,
                    value,
                )
                in signed_lines.items()
                if (
                    abs(
                        value
                    )
                    > AMOUNT_TOLERANCE
                )
            },
        )

        if not signed_lines:
            return None

        residual = sum(
            signed_lines.values()
        )

        if (
            abs(
                residual
            )
            > AMOUNT_TOLERANCE
        ):
            source_keys = [
                (
                    component[
                        "expense_account"
                    ],
                    component[
                        "origin_cost_center"
                    ],
                )
                for component
                in components
            ]

            source_keys = [
                key
                for key
                in source_keys
                if key in signed_lines
            ]

            if not source_keys:
                frappe.throw(
                    _(
                        "Unable to balance the month-end Journal Entry. "
                        "Review the variance allocation."
                    )
                )

            largest_key = max(
                source_keys,
                key=lambda key:
                    abs(
                        signed_lines[
                            key
                        ]
                    ),
            )

            signed_lines[
                largest_key
            ] -= residual

        if (
            abs(
                sum(
                    signed_lines.values()
                )
            )
            > AMOUNT_TOLERANCE
        ):
            frappe.throw(
                _(
                    "Month-end Journal Entry is not balanced. "
                    "Review the cost allocation."
                )
            )

        je = frappe.new_doc(
            "Journal Entry"
        )

        je.voucher_type = (
            "Journal Entry"
        )

        je.company = (
            self.company
        )

        je.posting_date = (
            self.to_date
        )

        je.user_remark = _(
            "Month-end production cost adjustment {0} "
            "for Cost Center {1}."
        ).format(
            self.name,
            self.cost_center,
        )

        for (
            account,
            cost_center,
        ), signed_amount in sorted(
            signed_lines.items()
        ):
            if (
                abs(
                    signed_amount
                )
                <= AMOUNT_TOLERANCE
            ):
                continue

            row = {
                "account":
                    account,
                "cost_center":
                    cost_center,
                "debit_in_account_currency":
                    (
                        signed_amount
                        if signed_amount > 0
                        else 0
                    ),
                "credit_in_account_currency":
                    (
                        -signed_amount
                        if signed_amount < 0
                        else 0
                    ),
            }

            je.append(
                "accounts",
                row,
            )

        if not je.accounts:
            return None

        je.flags.ignore_permissions = (
            True
        )

        je.insert(
            ignore_permissions=True
        )

        je.submit()

        return je

    # =========================================================
    # CANCELLATION
    # =========================================================

    def validate_no_submitted_downstream_adjustment(
        self,
    ):
        target_cost_centers = {
            row.target_cost_center
            for row
            in self.next_stage_transfers
            if row.target_cost_center
        }

        if not target_cost_centers:
            return

        downstream = (
            frappe.get_all(
                "Month End Production Cost Adjustment",
                filters={
                    "company":
                        self.company,
                    "month":
                        self.month,
                    "year":
                        self.year,
                    "cost_center":
                        (
                            "in",
                            list(
                                target_cost_centers
                            ),
                        ),
                    "docstatus":
                        1,
                },
                pluck="name",
            )
        )

        if downstream:
            frappe.throw(
                _(
                    "Cancel downstream Month End Production Cost "
                    "Adjustment(s) first: {0}. They may contain "
                    "variance carried forward from this document."
                ).format(
                    ", ".join(
                        downstream
                    )
                )
            )

    @staticmethod
    def cancel_linked_document(
        doctype,
        name,
    ):
        if (
            not name
            or not frappe.db.exists(
                doctype,
                name,
            )
        ):
            return

        doc = frappe.get_doc(
            doctype,
            name,
        )

        if doc.docstatus == 1:
            doc.flags.ignore_permissions = (
                True
            )

            doc.cancel()


# =============================================================
# MANUFACTURING MOVEMENT
# =============================================================

def get_manufacturing_movement(
    company,
    from_date,
    to_date,
    cost_center,
):
    entries = frappe.get_all(
        "Stock Entry",
        filters={
            "company":
                company,
            "docstatus":
                1,
            "purpose":
                (
                    "in",
                    [
                        "Manufacture",
                        "Material Consumption for Manufacture",
                    ],
                ),
            "posting_date":
                (
                    "between",
                    [
                        from_date,
                        to_date,
                    ],
                ),
        },
        fields=[
            "name",
            "purpose",
            "cost_center",
            "work_order",
        ],
        limit_page_length=0,
    )

    if not entries:
        return {
            "produced":
                {},
            "own_production_vouchers":
                set(),
            "next_stage_transfers":
                {},
            "unmapped_consumption":
                [],
        }

    entry_map = {
        row.name:
            row
        for row
        in entries
    }

    details = frappe.get_all(
        "Stock Entry Detail",
        filters={
            "parent":
                (
                    "in",
                    list(
                        entry_map
                    ),
                )
        },
        fields=[
            "parent",
            "item_code",
            "transfer_qty",
            "qty",
            "conversion_factor",
            "is_finished_item",
            "is_scrap_item",
            "s_warehouse",
            "t_warehouse",
            "cost_center",
        ],
        limit_page_length=0,
    )

    work_order_cost_center = {}

    def target_cost_center(
        entry,
        detail=None,
    ):
        if entry.cost_center:
            return entry.cost_center

        if entry.work_order:
            if (
                entry.work_order
                not in work_order_cost_center
            ):
                work_order_cost_center[
                    entry.work_order
                ] = frappe.get_cached_value(
                    "Work Order",
                    entry.work_order,
                    "cost_center",
                )

            if (
                work_order_cost_center[
                    entry.work_order
                ]
            ):
                return (
                    work_order_cost_center[
                        entry.work_order
                    ]
                )

        if (
            detail
            and detail.cost_center
        ):
            return (
                detail.cost_center
            )

        return None

    produced = (
        defaultdict(float)
    )

    own_production_vouchers = (
        set()
    )

    for row in details:
        entry = (
            entry_map[
                row.parent
            ]
        )

        if (
            entry.purpose
            != "Manufacture"
        ):
            continue

        is_finished = (
            cint(
                row.is_finished_item
            )
            or (
                bool(
                    row.t_warehouse
                )
                and not row.s_warehouse
                and not cint(
                    row.is_scrap_item
                )
            )
        )

        if not is_finished:
            continue

        if (
            target_cost_center(
                entry,
                row,
            )
            != cost_center
        ):
            continue

        qty = (
            flt(
                row.transfer_qty
            )
            or (
                flt(
                    row.qty
                )
                * flt(
                    row.conversion_factor
                    or 1
                )
            )
        )

        if qty > 0:
            produced[
                row.item_code
            ] += qty

            own_production_vouchers.add(
                entry.name
            )

    produced_items = set(
        produced
    )

    next_stage_transfers = (
        defaultdict(float)
    )

    unmapped_consumption = []

    if produced_items:
        for row in details:
            if (
                row.item_code
                not in produced_items
                or not row.s_warehouse
            ):
                continue

            is_finished = (
                cint(
                    row.is_finished_item
                )
                or (
                    bool(
                        row.t_warehouse
                    )
                    and not row.s_warehouse
                    and not cint(
                        row.is_scrap_item
                    )
                )
            )

            if is_finished:
                continue

            entry = (
                entry_map[
                    row.parent
                ]
            )

            target_cc = (
                target_cost_center(
                    entry,
                    row,
                )
            )

            if not target_cc:
                unmapped_consumption.append(
                    row.parent
                )

                continue

            if (
                target_cc
                == cost_center
            ):
                continue

            qty = (
                flt(
                    row.transfer_qty
                )
                or (
                    flt(
                        row.qty
                    )
                    * flt(
                        row.conversion_factor
                        or 1
                    )
                )
            )

            if qty > 0:
                next_stage_transfers[
                    (
                        row.item_code,
                        target_cc,
                    )
                ] += qty

    return {
        "produced":
            dict(
                produced
            ),
        "own_production_vouchers":
            own_production_vouchers,
        "next_stage_transfers":
            dict(
                next_stage_transfers
            ),
        "unmapped_consumption":
            sorted(
                set(
                    unmapped_consumption
                )
            ),
    }


# =============================================================
# ACTUAL EXPENSE FROM GL
# =============================================================

def get_gross_actual_expense(
    company,
    cost_center,
    account,
    from_date,
    to_date,
    own_production_vouchers,
):
    gle = frappe.qb.DocType(
        "GL Entry"
    )

    result = (
        frappe.qb.from_(
            gle
        )
        .select(
            Sum(
                gle.debit
                - gle.credit
            ).as_(
                "net_amount"
            )
        )
        .where(
            (
                gle.company
                == company
            )
            & (
                gle.account
                == account
            )
            & (
                gle.cost_center
                == cost_center
            )
            & (
                gle.posting_date
                >= getdate(
                    from_date
                )
            )
            & (
                gle.posting_date
                <= getdate(
                    to_date
                )
            )
            & (
                gle.is_cancelled
                == 0
            )
        )
    ).run(
        as_dict=True
    )

    net_amount = (
        flt(
            result[
                0
            ].net_amount
        )
        if result
        else 0
    )

    capitalized_expected = 0

    if own_production_vouchers:
        additional_costs = (
            frappe.get_all(
                "Landed Cost Taxes and Charges",
                filters={
                    "parent":
                        (
                            "in",
                            list(
                                own_production_vouchers
                            ),
                        ),
                    "parenttype":
                        "Stock Entry",
                    "expense_account":
                        account,
                },
                fields=[
                    "description",
                    "amount",
                    "base_amount",
                ],
                limit_page_length=0,
            )
        )

        auto_amount = sum(
            flt(
                row.base_amount
                or row.amount
            )
            for row
            in additional_costs
            if (
                row.description
                or ""
            ).startswith(
                AUTO_PREFIX
            )
        )

        if (
            auto_amount
            > AMOUNT_TOLERANCE
        ):
            stock_entry_gl = (
                frappe.qb.from_(
                    gle
                )
                .select(
                    Sum(
                        gle.credit
                        - gle.debit
                    ).as_(
                        "capitalized_credit"
                    )
                )
                .where(
                    (
                        gle.company
                        == company
                    )
                    & (
                        gle.account
                        == account
                    )
                    & (
                        gle.voucher_type
                        == "Stock Entry"
                    )
                    & (
                        gle.voucher_no.isin(
                            list(
                                own_production_vouchers
                            )
                        )
                    )
                    & (
                        gle.is_cancelled
                        == 0
                    )
                )
            ).run(
                as_dict=True
            )

            gl_credit = (
                flt(
                    stock_entry_gl[
                        0
                    ].capitalized_credit
                )
                if stock_entry_gl
                else 0
            )

            capitalized_expected = min(
                auto_amount,
                max(
                    gl_credit,
                    0,
                ),
            )

    return (
        net_amount
        + capitalized_expected
    )


# =============================================================
# STOCK QUANTITY HELPERS
# =============================================================

def get_item_quantity_map(
    company,
    item_codes,
    before_date=None,
    upto_date=None,
):
    if not item_codes:
        return {}

    sle = frappe.qb.DocType(
        "Stock Ledger Entry"
    )

    query = (
        frappe.qb.from_(
            sle
        )
        .select(
            sle.item_code,
            Sum(
                sle.actual_qty
            ).as_(
                "qty"
            ),
        )
        .where(
            (
                sle.company
                == company
            )
            & (
                sle.item_code.isin(
                    item_codes
                )
            )
            & (
                sle.is_cancelled
                == 0
            )
        )
    )

    if before_date:
        query = query.where(
            sle.posting_date
            < getdate(
                before_date
            )
        )

    if upto_date:
        query = query.where(
            sle.posting_date
            <= getdate(
                upto_date
            )
        )

    rows = (
        query
        .groupby(
            sle.item_code
        )
        .run(
            as_dict=True
        )
    )

    return {
        row.item_code:
            flt(
                row.qty
            )
        for row
        in rows
    }


def get_warehouse_quantity_map(
    company,
    item_codes,
    upto_date,
):
    if not item_codes:
        return {}

    sle = frappe.qb.DocType(
        "Stock Ledger Entry"
    )

    rows = (
        frappe.qb.from_(
            sle
        )
        .select(
            sle.item_code,
            sle.warehouse,
            Sum(
                sle.actual_qty
            ).as_(
                "qty"
            ),
        )
        .where(
            (
                sle.company
                == company
            )
            & (
                sle.item_code.isin(
                    item_codes
                )
            )
            & (
                sle.posting_date
                <= getdate(
                    upto_date
                )
            )
            & (
                sle.is_cancelled
                == 0
            )
        )
        .groupby(
            sle.item_code,
            sle.warehouse,
        )
    ).run(
        as_dict=True
    )

    return {
        (
            row.item_code,
            row.warehouse,
        ):
            flt(
                row.qty
            )
        for row
        in rows
    }


def get_other_net_receipts(
    company,
    item_codes,
    from_date,
    to_date,
    own_production_vouchers,
):
    if not item_codes:
        return {}

    rows = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "company":
                company,
            "item_code":
                (
                    "in",
                    item_codes,
                ),
            "posting_date":
                (
                    "between",
                    [
                        from_date,
                        to_date,
                    ],
                ),
            "is_cancelled":
                0,
        },
        fields=[
            "item_code",
            "voucher_type",
            "voucher_no",
            "actual_qty",
        ],
        limit_page_length=0,
    )

    by_voucher = (
        defaultdict(float)
    )

    for row in rows:
        by_voucher[
            (
                row.item_code,
                row.voucher_type,
                row.voucher_no,
            )
        ] += flt(
            row.actual_qty
        )

    other = (
        defaultdict(float)
    )

    own_production_vouchers = set(
        own_production_vouchers
    )

    for (
        item_code,
        _voucher_type,
        voucher_no,
    ), net_qty in by_voucher.items():

        if (
            voucher_no
            in own_production_vouchers
        ):
            continue

        if (
            net_qty
            > QTY_TOLERANCE
        ):
            other[
                item_code
            ] += net_qty

    return dict(
        other
    )


# =============================================================
# ACCOUNT HELPERS
# =============================================================

def get_item_expense_account(
    item_code,
    company,
):
    account = frappe.db.get_value(
        "Item Default",
        {
            "parent":
                item_code,
            "parenttype":
                "Item",
            "company":
                company,
        },
        "expense_account",
    )

    if account:
        return account

    item_group = (
        frappe.get_cached_value(
            "Item",
            item_code,
            "item_group",
        )
    )

    if item_group:
        account = (
            frappe.db.get_value(
                "Item Default",
                {
                    "parent":
                        item_group,
                    "parenttype":
                        "Item Group",
                    "company":
                        company,
                },
                "expense_account",
            )
        )

        if account:
            return account

    account = (
        frappe.get_cached_value(
            "Company",
            company,
            "default_expense_account",
        )
    )

    if not account:
        frappe.throw(
            _(
                "No Expense/COGS Account is configured for Item {0}. "
                "Set Item Default, Item Group Default, or Company "
                "Default Expense Account."
            ).format(
                frappe.bold(
                    item_code
                )
            )
        )

    return account


def validate_account_for_auto_journal(
    account,
    company,
):
    if not account:
        frappe.throw(
            _(
                "Account is required for automatic month-end posting."
            )
        )

    data = frappe.get_cached_value(
        "Account",
        account,
        [
            "company",
            "is_group",
            "account_currency",
        ],
        as_dict=True,
    )

    if not data:
        frappe.throw(
            _(
                "Account {0} does not exist."
            ).format(
                frappe.bold(
                    account
                )
            )
        )

    if (
        data.company
        != company
    ):
        frappe.throw(
            _(
                "Account {0} does not belong to Company {1}."
            ).format(
                frappe.bold(
                    account
                ),
                frappe.bold(
                    company
                ),
            )
        )

    if cint(
        data.is_group
    ):
        frappe.throw(
            _(
                "Account {0} is a Group Account; "
                "select a ledger account."
            ).format(
                account
            )
        )

    company_currency = (
        frappe.get_cached_value(
            "Company",
            company,
            "default_currency",
        )
    )

    if (
        data.account_currency
        and data.account_currency
        != company_currency
    ):
        frappe.throw(
            _(
                "Automatic month-end Journal Entry currently requires "
                "company-currency accounts. Account {0} uses {1}, "
                "while Company {2} uses {3}."
            ).format(
                account,
                data.account_currency,
                company,
                company_currency,
            )
        )


# =============================================================
# UPSTREAM / NEXT STAGE HELPERS
# =============================================================

def find_upstream_adjustments(
    company,
    month,
    year,
    target_cost_center,
    exclude_name=None,
):
    transfer_rows = (
        frappe.get_all(
            "Month End Production Cost Transfer",
            filters={
                "target_cost_center":
                    target_cost_center
            },
            fields=[
                "parent",
                "consumed_qty",
            ],
            limit_page_length=0,
        )
    )

    if not transfer_rows:
        return {}

    parent_names = sorted(
        {
            row.parent
            for row
            in transfer_rows
            if row.parent
        }
    )

    if exclude_name:
        parent_names = [
            name
            for name
            in parent_names
            if (
                name
                != exclude_name
            )
        ]

    if not parent_names:
        return {}

    parent_filters = {
        "name":
            (
                "in",
                parent_names,
            ),
        "company":
            company,
        "month":
            month,
        "year":
            year,
        "docstatus":
            1,
    }

    valid_parents = set(
        frappe.get_all(
            "Month End Production Cost Adjustment",
            filters=
                parent_filters,
            pluck="name",
            limit_page_length=0,
        )
    )

    totals = (
        defaultdict(float)
    )

    for row in transfer_rows:
        if (
            row.parent
            in valid_parents
        ):
            totals[
                row.parent
            ] += flt(
                row.consumed_qty
            )

    return dict(
        totals
    )


def get_submitted_cost_components(
    doc,
    visited,
):
    if doc.name in visited:
        frappe.throw(
            _(
                "Circular month-end production cost carry-forward "
                "detected at {0}."
            ).format(
                frappe.bold(
                    doc.name
                )
            )
        )

    visited = set(
        visited
    )

    visited.add(
        doc.name
    )

    components = (
        defaultdict(float)
    )

    for row in doc.actual_expenses:
        if row.expense_account:
            components[
                (
                    doc.name,
                    doc.cost_center,
                    row.expense_account,
                )
            ] += flt(
                row.adjustment_amount
            )

    upstream_adjustments = (
        find_upstream_adjustments(
            company=
                doc.company,
            month=
                doc.month,
            year=
                doc.year,
            target_cost_center=
                doc.cost_center,
            exclude_name=
                doc.name,
        )
    )

    for (
        upstream_name,
        transferred_qty,
    ) in upstream_adjustments.items():

        if (
            upstream_name
            in visited
        ):
            frappe.throw(
                _(
                    "Circular month-end production cost carry-forward "
                    "detected through {0}."
                ).format(
                    frappe.bold(
                        upstream_name
                    )
                )
            )

        upstream_doc = (
            frappe.get_doc(
                "Month End Production Cost Adjustment",
                upstream_name,
            )
        )

        upstream_components = (
            get_submitted_cost_components(
                upstream_doc,
                visited=visited,
            )
        )

        upstream_qty = flt(
            upstream_doc.actual_produced_qty
        )

        if upstream_qty <= 0:
            frappe.throw(
                _(
                    "Upstream adjustment {0} has zero "
                    "Actual Produced Qty."
                ).format(
                    frappe.bold(
                        upstream_doc.name
                    )
                )
            )

        for component in upstream_components:
            amount = (
                flt(
                    component[
                        "amount"
                    ]
                )
                / upstream_qty
                * flt(
                    transferred_qty
                )
            )

            key = (
                component[
                    "origin_adjustment"
                ],
                component[
                    "origin_cost_center"
                ],
                component[
                    "expense_account"
                ],
            )

            components[
                key
            ] += amount

    return component_dicts(
        components,
        flt(
            doc.actual_produced_qty
        ),
    )


def component_dicts(
    component_map,
    produced_qty,
):
    result = []

    for (
        origin_adjustment,
        origin_cost_center,
        expense_account,
    ), amount in component_map.items():

        result.append(
            {
                "origin_adjustment":
                    origin_adjustment,
                "origin_cost_center":
                    origin_cost_center,
                "expense_account":
                    expense_account,
                "amount":
                    flt(
                        amount
                    ),
                "per_qty":
                    (
                        flt(
                            amount
                        )
                        / produced_qty
                        if produced_qty > 0
                        else 0
                    ),
            }
        )

    return result