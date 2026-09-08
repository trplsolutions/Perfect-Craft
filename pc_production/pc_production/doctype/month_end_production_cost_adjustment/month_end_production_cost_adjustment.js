frappe.ui.form.on(
    "Month End Production Cost Adjustment",
    {
        setup(frm) {
            setup_year_options(frm);

            frm.set_query(
                "cost_center",
                () => {
                    const filters = {
                        is_group: 0
                    };

                    if (frm.doc.company) {
                        filters.company =
                            frm.doc.company;
                    }

                    return {
                        filters: filters
                    };
                }
            );

            frm.set_query(
                "stock_adjustment_account",
                () => {
                    const filters = {
                        is_group: 0
                    };

                    if (frm.doc.company) {
                        filters.company =
                            frm.doc.company;
                    }

                    return {
                        filters: filters
                    };
                }
            );
        },

        onload(frm) {
            setup_year_options(frm);

            if (
                frm.is_new() &&
                !frm.doc.company
            ) {
                const default_company =
                    frappe.defaults.get_user_default(
                        "Company"
                    );

                if (default_company) {
                    frm.set_value(
                        "company",
                        default_company
                    );
                }
            }
        },

        refresh(frm) {
            setup_year_options(frm);

            /*
             * Expense accounts come automatically
             * from Monthly Production Setting.
             *
             * User should only enter Actual Expense.
             */
            if (
                frm.fields_dict.actual_expenses &&
                frm.fields_dict.actual_expenses.grid
            ) {
                frm.fields_dict
                    .actual_expenses
                    .grid
                    .cannot_add_rows = true;

                frm.fields_dict
                    .actual_expenses
                    .grid
                    .cannot_delete_rows = true;
            }

            /*
             * NO:
             * - Fetch / Refresh Month Data
             * - Fetch Actual Expenses from GL
             * - Recalculate
             *
             * Everything is automatic now.
             */

            if (
                frm.doc.stock_reconciliation
            ) {
                frm.add_custom_button(
                    __("Stock Reconciliation"),
                    () => {
                        frappe.set_route(
                            "Form",
                            "Stock Reconciliation",
                            frm.doc.stock_reconciliation
                        );
                    },
                    __("View")
                );
            }

            if (
                frm.doc.journal_entry
            ) {
                frm.add_custom_button(
                    __("Journal Entry"),
                    () => {
                        frappe.set_route(
                            "Form",
                            "Journal Entry",
                            frm.doc.journal_entry
                        );
                    },
                    __("View")
                );
            }

            show_review_warning(
                frm
            );
        },

        company(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            period_selection_changed(
                frm
            );
        },

        cost_center(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            period_selection_changed(
                frm
            );
        },

        month(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            period_selection_changed(
                frm
            );
        },

        year(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            period_selection_changed(
                frm
            );
        }
    }
);


/*
 * Actual Expense is entered manually.
 *
 * As soon as the user changes it,
 * recalculate the month-end result.
 */
frappe.ui.form.on(
    "Month End Production Cost Expense",
    {
        actual_expense_amount(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            auto_recalculate(
                frm
            );
        }
    }
);


/*
 * These values can be manually reviewed
 * in exceptional stock-attribution cases.
 *
 * If user changes them, recalculate immediately.
 */
frappe.ui.form.on(
    "Month End Production Cost Item",
    {
        remaining_qty(frm) {
            auto_recalculate(
                frm
            );
        },

        sold_qty(frm) {
            auto_recalculate(
                frm
            );
        },

        cogs_account(frm) {
            frm.dirty();
        }
    }
);


frappe.ui.form.on(
    "Month End Production Cost Transfer",
    {
        consumed_qty(frm) {
            auto_recalculate(
                frm
            );
        },

        target_cost_center(frm) {
            frm.dirty();
        }
    }
);


function setup_year_options(frm) {
    const current_year =
        new Date().getFullYear();

    const years = [
        ""
    ];

    for (
        let year =
            current_year - 10;

        year <=
            current_year + 10;

        year++
    ) {
        years.push(
            String(
                year
            )
        );
    }

    /*
     * Keep old document year available
     * even if it is outside the normal range.
     */
    if (
        frm.doc.year &&
        !years.includes(
            String(
                frm.doc.year
            )
        )
    ) {
        years.push(
            String(
                frm.doc.year
            )
        );
    }

    years.sort(
        (a, b) => {
            if (!a) {
                return -1;
            }

            if (!b) {
                return 1;
            }

            return (
                Number(a) -
                Number(b)
            );
        }
    );

    frm.set_df_property(
        "year",
        "options",
        years.join(
            "\n"
        )
    );

    /*
     * Current year automatically selected.
     */
    if (
        frm.is_new() &&
        !frm.doc.year
    ) {
        frm.set_value(
            "year",
            String(
                current_year
            )
        );
    }
}


function period_selection_changed(frm) {
    /*
     * Clear old period results first.
     */
    clear_month_end_result(
        frm
    );

    /*
     * Do nothing until the fields needed
     * to find Monthly Production Setting exist.
     */
    if (
        !frm.doc.company ||
        !frm.doc.cost_center ||
        !frm.doc.month ||
        !frm.doc.year
    ) {
        return;
    }

    /*
     * This one server call will now:
     *
     * 1. Find Monthly Production Setting
     * 2. Fetch From Date
     * 3. Fetch To Date
     * 4. Fetch expected expenses
     * 5. Fetch expected quantity
     * 6. Find manufacturing movement
     * 7. Calculate produced qty
     * 8. Calculate remaining/sold/next-stage qty
     */
    call_document_method(
        frm,
        "resolve_monthly_production_setting"
    );
}


function clear_month_end_result(frm) {
    frm.set_value(
        "monthly_production_setting",
        null
    );

    frm.set_value(
        "from_date",
        null
    );

    frm.set_value(
        "to_date",
        null
    );

    frm.set_value(
        "expected_expense_amount",
        0
    );

    frm.set_value(
        "expected_qty",
        0
    );

    frm.set_value(
        "expected_cost_per_qty",
        0
    );

    frm.set_value(
        "actual_expense_amount",
        0
    );

    frm.set_value(
        "actual_produced_qty",
        0
    );

    frm.set_value(
        "actual_cost_per_qty",
        0
    );

    frm.set_value(
        "own_adjustment_amount",
        0
    );

    frm.set_value(
        "upstream_adjustment_amount",
        0
    );

    frm.set_value(
        "total_adjustment_amount",
        0
    );

    frm.set_value(
        "adjustment_per_qty",
        0
    );

    frm.set_value(
        "manual_quantity_confirmation",
        0
    );

    frm.set_value(
        "calculation_note",
        ""
    );

    frm.clear_table(
        "actual_expenses"
    );

    frm.clear_table(
        "production_items"
    );

    frm.clear_table(
        "next_stage_transfers"
    );

    frm.refresh_field(
        "actual_expenses"
    );

    frm.refresh_field(
        "production_items"
    );

    frm.refresh_field(
        "next_stage_transfers"
    );
}


function auto_recalculate(frm) {
    if (
        frm.doc.docstatus !== 0
    ) {
        return;
    }

    if (
        !frm.doc.monthly_production_setting ||
        !frm.doc.actual_produced_qty
    ) {
        frm.dirty();
        return;
    }

    call_document_method(
        frm,
        "recalculate"
    );
}


function call_document_method(
    frm,
    method,
    args = {}
) {
    return frm.call(
        method,
        args
    ).then(
        (response) => {
            if (
                response.message &&
                response.message.doc
            ) {
                frappe.model.sync(
                    response.message.doc
                );
            }

            frm.refresh();
            frm.dirty();

            show_review_warning(
                frm
            );

            return response;
        }
    );
}


function show_review_warning(frm) {
    const review_rows =
        (
            frm.doc.production_items ||
            []
        ).filter(
            (row) =>
                cint(
                    row.manual_review
                ) === 1
        );

    if (
        !review_rows.length ||
        frm.doc.docstatus !== 0
    ) {
        return;
    }

    const items =
        review_rows
            .map(
                (row) =>
                    row.item_code
            )
            .join(
                ", "
            );

    frm.dashboard.set_headline_alert(
        __(
            "Manual quantity review required for: {0}",
            [
                items
            ]
        ),
        "orange"
    );
}