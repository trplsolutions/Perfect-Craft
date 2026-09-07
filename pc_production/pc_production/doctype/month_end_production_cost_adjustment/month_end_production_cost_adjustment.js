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

            if (
                frm.doc.docstatus === 0
            ) {
                frm.add_custom_button(
                    __("Fetch / Refresh Month Data"),
                    () => {
                        if (
                            !validate_period_fields(
                                frm
                            )
                        ) {
                            return;
                        }

                        call_document_method(
                            frm,
                            "fetch_month_data",
                            {
                                force_actual_from_gl: 0
                            }
                        );
                    }
                );

                frm.add_custom_button(
                    __("Fetch Actual Expenses from GL"),
                    () => {
                        if (
                            !validate_period_fields(
                                frm
                            )
                        ) {
                            return;
                        }

                        frappe.confirm(
                            __(
                                "This will replace the Actual Expense values with amounts reconstructed from the General Ledger for this Cost Center. Continue?"
                            ),
                            () => {
                                call_document_method(
                                    frm,
                                    "fetch_month_data",
                                    {
                                        force_actual_from_gl: 1
                                    }
                                );
                            }
                        );
                    },
                    __("Actions")
                );

                frm.add_custom_button(
                    __("Recalculate"),
                    () => {
                        if (
                            !validate_period_fields(
                                frm
                            )
                        ) {
                            return;
                        }

                        call_document_method(
                            frm,
                            "recalculate"
                        );
                    },
                    __("Actions")
                );
            }

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

            clear_period_result(
                frm,
                true
            );

            try_fetch_monthly_setting(
                frm
            );
        },

        cost_center(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            clear_period_result(
                frm
            );

            try_fetch_monthly_setting(
                frm
            );
        },

        month(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            clear_period_result(
                frm
            );

            try_fetch_monthly_setting(
                frm
            );
        },

        year(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            clear_period_result(
                frm
            );

            try_fetch_monthly_setting(
                frm
            );
        },

        from_date(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            if (
                !validate_selected_dates(
                    frm
                )
            ) {
                return;
            }

            clear_period_result(
                frm
            );

            try_fetch_monthly_setting(
                frm
            );
        },

        to_date(frm) {
            if (
                frm.doc.docstatus !== 0
            ) {
                return;
            }

            if (
                !validate_selected_dates(
                    frm
                )
            ) {
                return;
            }

            clear_period_result(
                frm
            );

            try_fetch_monthly_setting(
                frm
            );
        }
    }
);


frappe.ui.form.on(
    "Month End Production Cost Expense",
    {
        actual_expense_amount(frm) {
            frm.dirty();
        }
    }
);


frappe.ui.form.on(
    "Month End Production Cost Item",
    {
        remaining_qty(frm) {
            frm.dirty();
        },

        sold_qty(frm) {
            frm.dirty();
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
            frm.dirty();
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
        let year = current_year - 10;
        year <= current_year + 10;
        year++
    ) {
        years.push(
            String(year)
        );
    }

    /*
     * If an old submitted/draft document
     * contains a year outside the normal
     * range, keep that value available.
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


function has_period_selection(frm) {
    return Boolean(
        frm.doc.company &&
        frm.doc.cost_center &&
        frm.doc.month &&
        frm.doc.year &&
        frm.doc.from_date &&
        frm.doc.to_date
    );
}


function try_fetch_monthly_setting(frm) {
    if (
        frm.doc.docstatus !== 0
    ) {
        return;
    }

    if (
        !has_period_selection(
            frm
        )
    ) {
        return;
    }

    if (
        !validate_selected_dates(
            frm
        )
    ) {
        return;
    }

    call_document_method(
        frm,
        "resolve_monthly_production_setting"
    );
}


function clear_period_result(
    frm,
    clear_stock_account = false
) {
    frm.set_value(
        "monthly_production_setting",
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

    if (
        clear_stock_account
    ) {
        frm.set_value(
            "stock_adjustment_account",
            null
        );
    }

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


function validate_period_fields(frm) {
    const missing = [];

    if (
        !frm.doc.company
    ) {
        missing.push(
            __("Company")
        );
    }

    if (
        !frm.doc.cost_center
    ) {
        missing.push(
            __("Cost Center")
        );
    }

    if (
        !frm.doc.month
    ) {
        missing.push(
            __("Month")
        );
    }

    if (
        !frm.doc.year
    ) {
        missing.push(
            __("Year")
        );
    }

    if (
        !frm.doc.from_date
    ) {
        missing.push(
            __("From Date")
        );
    }

    if (
        !frm.doc.to_date
    ) {
        missing.push(
            __("To Date")
        );
    }

    if (
        missing.length
    ) {
        frappe.msgprint(
            {
                title: __(
                    "Missing Period Information"
                ),
                indicator: "orange",
                message: __(
                    "Please select: {0}",
                    [
                        missing.join(
                            ", "
                        )
                    ]
                )
            }
        );

        return false;
    }

    if (
        !validate_selected_dates(
            frm
        )
    ) {
        return false;
    }

    if (
        !frm.doc.monthly_production_setting
    ) {
        frappe.msgprint(
            {
                title: __(
                    "Monthly Production Setting Not Found"
                ),
                indicator: "orange",
                message: __(
                    "No Monthly Production Setting has been fetched for the selected Company, Cost Center, Month, Year and dates."
                )
            }
        );

        return false;
    }

    return true;
}


function validate_selected_dates(frm) {
    if (
        !frm.doc.from_date ||
        !frm.doc.to_date
    ) {
        return true;
    }

    const difference =
        frappe.datetime.get_diff(
            frm.doc.to_date,
            frm.doc.from_date
        );

    if (
        difference < 0
    ) {
        frappe.msgprint(
            {
                title: __(
                    "Invalid Date Range"
                ),
                indicator: "red",
                message: __(
                    "To Date cannot be before From Date."
                )
            }
        );

        return false;
    }

    return true;
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