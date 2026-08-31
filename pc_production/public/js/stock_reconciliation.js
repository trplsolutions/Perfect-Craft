frappe.ui.form.on("Stock Reconciliation", {
    setup(frm) {
        setup_production_expense_grid(frm);
    },

    refresh(frm) {
        setup_production_expense_grid(frm);
        toggle_production_fields(frm);

        if (
            frm.doc.purpose !== "Stock Reconciliation" ||
            !frm.doc.company ||
            !frm.doc.cost_center ||
            !frm.doc.posting_date
        ) {
            return;
        }

        /*
         * IMPORTANT:
         *
         * For a NEW document:
         * fetch setting and populate fields/expense rows.
         *
         * For an already SAVED Draft:
         * only load expected_qty into temporary JS memory.
         * Do NOT rebuild the child table on refresh.
         *
         * This prevents the document from becoming
         * "Not Saved" immediately after Save.
         */
        if (frm.is_new()) {
            fetch_monthly_setting(frm, {
                populate_document: true,
                preserve_actual: true,
                show_message: false
            });
        } else {
            fetch_monthly_setting(frm, {
                populate_document: false,
                preserve_actual: true,
                show_message: false
            });
        }
    },

    purpose(frm) {
        toggle_production_fields(frm);

        if (frm.doc.purpose === "Stock Reconciliation") {
            fetch_monthly_setting(frm, {
                populate_document: true,
                preserve_actual: false,
                show_message: false
            });
        } else {
            clear_production_data(frm);
        }
    },

    company(frm) {
        if (frm.doc.purpose !== "Stock Reconciliation") {
            return;
        }

        fetch_monthly_setting(frm, {
            populate_document: true,
            preserve_actual: false,
            show_message: false
        });
    },

    cost_center(frm) {
        if (frm.doc.purpose !== "Stock Reconciliation") {
            return;
        }

        fetch_monthly_setting(frm, {
            populate_document: true,
            preserve_actual: false,
            show_message: true
        });
    },

    posting_date(frm) {
        if (frm.doc.purpose !== "Stock Reconciliation") {
            return;
        }

        fetch_monthly_setting(frm, {
            populate_document: true,
            preserve_actual: false,
            show_message: false
        });

        /*
         * ERPNext itself also re-fetches stock balance
         * when Posting Date changes.
         *
         * After those requests finish, capture the new
         * current valuation rate and apply our adjustment.
         */
        frappe.after_ajax(() => {
            capture_all_item_valuation_rates(frm);
        });
    },

    posting_time(frm) {
        if (frm.doc.purpose !== "Stock Reconciliation") {
            return;
        }

        frappe.after_ajax(() => {
            capture_all_item_valuation_rates(frm);
        });
    },

    custom_actual_qty(frm) {
        calculate_reconciliation(frm);
    }
});


frappe.ui.form.on("Stock Reconciliation CTC", {
    actual_expense_amount(frm) {
        calculate_reconciliation(frm);
    }
});


frappe.ui.form.on("Stock Reconciliation Item", {
    item_code(frm, cdt, cdn) {
        /*
         * Standard ERPNext first fetches:
         * - current qty
         * - current valuation rate
         * - current amount
         *
         * after_ajax makes sure we use those fetched values
         * only after ERPNext has finished its request.
         */
        frappe.after_ajax(() => {
            capture_item_valuation_rate(
                frm,
                cdt,
                cdn
            );
        });
    },

    warehouse(frm, cdt, cdn) {
        frappe.after_ajax(() => {
            capture_item_valuation_rate(
                frm,
                cdt,
                cdn
            );
        });
    },

    current_valuation_rate(frm, cdt, cdn) {
        /*
         * This is an additional safety trigger.
         * Standard ERPNext sets current_valuation_rate
         * after fetching stock balance.
         */
        frappe.after_ajax(() => {
            capture_item_valuation_rate(
                frm,
                cdt,
                cdn
            );
        });
    },

    qty(frm) {
        calculate_item_valuation(frm);
    }
});


function setup_production_expense_grid(frm) {
    const table_field =
        frm.fields_dict.custom_production_expenses;

    if (
        !table_field ||
        !table_field.grid
    ) {
        return;
    }

    /*
     * Expense accounts must come from the matching
     * Monthly Production Setting.
     *
     * User should only edit Actual Expense Amount.
     */
    table_field.grid.cannot_add_rows = true;
    table_field.grid.cannot_delete_rows = true;

    table_field.grid.refresh();
}


function toggle_production_fields(frm) {
    const show =
        frm.doc.purpose ===
        "Stock Reconciliation";

    frm.toggle_display(
        [
            "custom_expected_per_qty_amount",

            // NEW PARENT FIELD
            "custom_actual_per_qty_amount",

            "custom_actual_qty",
            "custom_adjustment_per_qty",
            "custom_production_expenses"
        ],
        show
    );
}


function clear_production_data(frm) {
    frm.__pc_expected_qty = 0;
    frm.__pc_setting_name = null;

    frm.set_value(
        "custom_expected_per_qty_amount",
        0
    );

    // NEW PARENT FIELD
    frm.set_value(
        "custom_actual_per_qty_amount",
        0
    );

    frm.set_value(
        "custom_adjustment_per_qty",
        0
    );

    frm.clear_table(
        "custom_production_expenses"
    );

    frm.refresh_field(
        "custom_production_expenses"
    );
}


function fetch_monthly_setting(
    frm,
    options = {}
) {
    const settings = Object.assign(
        {
            populate_document: true,
            preserve_actual: true,
            show_message: false
        },
        options
    );

    if (
        frm.doc.purpose !==
        "Stock Reconciliation"
    ) {
        return;
    }

    /*
     * Company, Cost Center and Posting Date
     * are all dynamic.
     *
     * Never call server until all three exist.
     */
    if (
        !frm.doc.company ||
        !frm.doc.cost_center ||
        !frm.doc.posting_date
    ) {
        return;
    }

    const old_actual_values = {};

    if (settings.preserve_actual) {
        (
            frm.doc.custom_production_expenses ||
            []
        ).forEach(row => {
            if (row.type_of_expense) {
                old_actual_values[
                    row.type_of_expense
                ] = flt(
                    row.actual_expense_amount
                );
            }
        });
    }

    frappe.call({
        method:
            "pc_production.stock_reconciliation.get_monthly_reconciliation_data",

        args: {
            company:
                frm.doc.company,

            cost_center:
                frm.doc.cost_center,

            posting_date:
                frm.doc.posting_date
        },

        callback(r) {
            if (
                r.exc ||
                !r.message
            ) {
                return;
            }

            const data = r.message;

            /*
             * No matching Monthly Production Setting.
             */
            if (!data.found) {
                frm.__pc_expected_qty = 0;
                frm.__pc_setting_name = null;

                if (settings.populate_document) {
                    frm.set_value(
                        "custom_expected_per_qty_amount",
                        0
                    );

                    // NEW PARENT FIELD
                    frm.set_value(
                        "custom_actual_per_qty_amount",
                        0
                    );

                    frm.set_value(
                        "custom_adjustment_per_qty",
                        0
                    );

                    frm.clear_table(
                        "custom_production_expenses"
                    );

                    frm.refresh_field(
                        "custom_production_expenses"
                    );
                }

                if (settings.show_message) {
                    frappe.show_alert(
                        {
                            message:
                                data.message ||
                                __(
                                    "No matching Monthly Production Setting found."
                                ),
                            indicator:
                                "orange"
                        },
                        7
                    );
                }

                return;
            }

            /*
             * Always keep these temporary values in JS memory.
             *
             * They are needed for correct row-wise expected
             * per-qty calculations.
             */
            frm.__pc_expected_qty =
                flt(
                    data.expected_qty
                );

            frm.__pc_setting_name =
                data.setting_name;

            /*
             * Saved Draft refresh:
             *
             * We intentionally stop here.
             * This means Save -> refresh will NOT rebuild rows
             * and therefore will NOT make the document dirty.
             */
            if (!settings.populate_document) {
                return;
            }

            frm.set_value(
                "custom_expected_per_qty_amount",
                flt(
                    data.expected_per_qty_amount
                )
            );

            frm.clear_table(
                "custom_production_expenses"
            );

            (
                data.expenses || []
            ).forEach(expense => {
                const row =
                    frm.add_child(
                        "custom_production_expenses"
                    );

                row.type_of_expense =
                    expense.type_of_expense;

                row.expected_expense_amount =
                    flt(
                        expense.expected_expense_amount
                    );

                /*
                 * Preserve manually entered actual amount
                 * only when requested.
                 *
                 * If Company / Cost Center / Date changed,
                 * preserve_actual is false so new setting
                 * starts with its own amounts.
                 */
                if (
                    settings.preserve_actual &&
                    Object.prototype.hasOwnProperty.call(
                        old_actual_values,
                        expense.type_of_expense
                    )
                ) {
                    row.actual_expense_amount =
                        old_actual_values[
                            expense.type_of_expense
                        ];
                } else {
                    row.actual_expense_amount =
                        flt(
                            expense.actual_expense_amount
                        );
                }
            });

            frm.refresh_field(
                "custom_production_expenses"
            );

            setup_production_expense_grid(frm);

            calculate_reconciliation(frm);
        }
    });
}


function calculate_reconciliation(frm) {
    if (
        frm.doc.purpose !==
        "Stock Reconciliation"
    ) {
        return;
    }

    const actual_qty =
        flt(
            frm.doc.custom_actual_qty
        );

    const expected_qty =
        flt(
            frm.__pc_expected_qty
        );

    /*
     * If document was loaded before expected_qty
     * was hydrated from server, do not calculate
     * incorrect values with zero denominator.
     */
    if (
        !expected_qty &&
        frm.doc.company &&
        frm.doc.cost_center &&
        frm.doc.posting_date
    ) {
        fetch_monthly_setting(
            frm,
            {
                populate_document: false,
                preserve_actual: true,
                show_message: false
            }
        );

        return;
    }

    let total_actual_expense = 0;
    let total_adjustment = 0;

    (
        frm.doc.custom_production_expenses ||
        []
    ).forEach(row => {
        const expected_expense =
            flt(
                row.expected_expense_amount
            );

        const actual_expense =
            flt(
                row.actual_expense_amount
            );

        /*
         * Parent total actual expense.
         */
        total_actual_expense +=
            actual_expense;

        /*
         * Expected expense per qty for THIS row:
         *
         * Expected Expense Amount / Expected Qty
         */
        const expected_expense_per_qty =
            expected_qty > 0
                ? (
                    expected_expense /
                    expected_qty
                )
                : 0;

        /*
         * Actual expense per qty for THIS row:
         *
         * Actual Expense Amount / Actual Qty
         */
        const actual_per_qty =
            actual_qty > 0
                ? (
                    actual_expense /
                    actual_qty
                )
                : 0;

        /*
         * Difference for THIS expense:
         *
         * Actual Per Qty - Expected Per Qty
         */
        const difference =
            (
                actual_qty > 0 &&
                expected_qty > 0
            )
                ? (
                    actual_per_qty -
                    expected_expense_per_qty
                )
                : 0;

        /*
         * Child table values.
         */
        row.actual_per_qty_amount =
            actual_per_qty;

        row.value_after_calculation =
            difference;

        total_adjustment +=
            difference;
    });

    /*
     * PARENT ACTUAL PER QTY AMOUNT
     *
     * Total Actual Expense / Actual Qty
     *
     * Example:
     *
     * Salary       = 5,050
     * Electricity  = 3,200
     *
     * Total = 8,250
     *
     * Actual Qty = 12,000
     *
     * 8,250 / 12,000
     * = 0.6875
     */
    const actual_per_qty_amount =
        actual_qty > 0
            ? (
                total_actual_expense /
                actual_qty
            )
            : 0;

    frm.set_value(
        "custom_actual_per_qty_amount",
        actual_per_qty_amount
    );

    /*
     * Parent Adjustment Per Qty
     *
     * Actual Per Qty Amount
     * -
     * Expected Per Qty Amount
     */
    const expected_per_qty_amount =
        flt(
            frm.doc.custom_expected_per_qty_amount
        );

    const adjustment_per_qty =
        actual_per_qty_amount -
        expected_per_qty_amount;

    frm.set_value(
        "custom_adjustment_per_qty",
        adjustment_per_qty
    );

    frm.refresh_field(
        "custom_production_expenses"
    );

    calculate_item_valuation(frm);

    /*
     * DO NOT use:
     *
     * frm.dirty();
     *
     * Frappe automatically marks the document dirty
     * when the user actually changes a field.
     */
}


function capture_item_valuation_rate(
    frm,
    cdt,
    cdn
) {
    if (
        frm.doc.purpose !==
        "Stock Reconciliation"
    ) {
        return;
    }

    const row =
        locals[cdt][cdn];

    if (
        !row ||
        !row.item_code ||
        !row.warehouse
    ) {
        return;
    }

    /*
     * ERPNext stores the value BEFORE reconciliation
     * in current_valuation_rate.
     *
     * THAT is our original Item Valuation Rate.
     */
    const current_rate =
        flt(
            row.current_valuation_rate
        );

    row.custom_item_valuation_rate =
        current_rate;

    apply_item_valuation_to_row(
        frm,
        row
    );

    frm.refresh_field("items");
}


function capture_all_item_valuation_rates(frm) {
    if (
        frm.doc.purpose !==
        "Stock Reconciliation"
    ) {
        return;
    }

    (
        frm.doc.items || []
    ).forEach(row => {
        if (
            !row.item_code ||
            !row.warehouse
        ) {
            return;
        }

        row.custom_item_valuation_rate =
            flt(
                row.current_valuation_rate
            );

        apply_item_valuation_to_row(
            frm,
            row
        );
    });

    frm.refresh_field("items");
}


function calculate_item_valuation(frm) {
    if (
        frm.doc.purpose !==
        "Stock Reconciliation"
    ) {
        return;
    }

    (
        frm.doc.items || []
    ).forEach(row => {
        if (!row.item_code) {
            return;
        }

        /*
         * If original valuation was not captured yet,
         * use ERPNext's current valuation rate.
         */
        if (
            row.custom_item_valuation_rate ===
                undefined ||
            row.custom_item_valuation_rate ===
                null ||
            row.custom_item_valuation_rate ===
                ""
        ) {
            row.custom_item_valuation_rate =
                flt(
                    row.current_valuation_rate
                );
        }

        apply_item_valuation_to_row(
            frm,
            row
        );
    });

    frm.refresh_field("items");
}


function apply_item_valuation_to_row(
    frm,
    row
) {
    const adjustment_per_qty =
        flt(
            frm.doc.custom_adjustment_per_qty
        );

    const item_valuation_rate =
        flt(
            row.custom_item_valuation_rate
        );

    /*
     * CORRECT ERPNext FORMULA:
     *
     * valuation_rate is already PER UNIT.
     *
     * Therefore:
     *
     * After Valuation Rate
     * =
     * Item Valuation Rate
     * +
     * Adjustment Per Qty
     *
     * DO NOT divide by Item Qty again.
     */
    const after_valuation_rate =
        item_valuation_rate +
        adjustment_per_qty;

    row.valuation_rate =
        after_valuation_rate;

    /*
     * Standard Stock Reconciliation amount.
     */
    row.amount =
        flt(row.qty) *
        after_valuation_rate;

    row.quantity_difference =
        flt(row.qty) -
        flt(row.current_qty);

    row.amount_difference =
        flt(row.amount) -
        flt(row.current_amount);
}