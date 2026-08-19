frappe.ui.form.on("Stock Reconciliation", {
    refresh(frm) {
        toggle_production_fields(frm);

        if (
            frm.doc.docstatus === 0 &&
            frm.doc.purpose === "Stock Reconciliation"
        ) {
            fetch_monthly_setting(frm);
        }
    },

    purpose(frm) {
        toggle_production_fields(frm);

        if (
            frm.doc.purpose === "Stock Reconciliation"
        ) {
            fetch_monthly_setting(frm);
        }
    },

    company(frm) {
        if (
            frm.doc.purpose === "Stock Reconciliation"
        ) {
            fetch_monthly_setting(frm);
        }
    },

    cost_center(frm) {
        if (
            frm.doc.purpose === "Stock Reconciliation"
        ) {
            fetch_monthly_setting(frm);
        }
    },

    posting_date(frm) {
        if (
            frm.doc.purpose === "Stock Reconciliation"
        ) {
            fetch_monthly_setting(frm);
        }
    },

    custom_actual_qty(frm) {
        calculate_reconciliation(frm);
    }
});


frappe.ui.form.on(
    "Stock Reconciliation CTC",
    {
        actual_expense_amount(frm) {
            calculate_reconciliation(frm);
        }
    }
);


frappe.ui.form.on(
    "Stock Reconciliation Item",
    {
        custom_item_valuation_rate(frm) {
            calculate_item_valuation(frm);
        },

        qty(frm) {
            calculate_item_valuation(frm);
        },

        item_code(frm, cdt, cdn) {
            setTimeout(() => {
                set_original_valuation_rate(
                    frm,
                    cdt,
                    cdn
                );
            }, 500);
        },

        warehouse(frm, cdt, cdn) {
            setTimeout(() => {
                set_original_valuation_rate(
                    frm,
                    cdt,
                    cdn
                );
            }, 500);
        }
    }
);


function toggle_production_fields(frm) {
    const show =
        frm.doc.purpose ===
        "Stock Reconciliation";

    frm.toggle_display(
        [
            "custom_expected_per_qty_amount",
            "custom_actual_qty",
            "custom_adjustment_per_qty",
            "custom_reconciliation_expenses"
        ],
        show
    );
}


function fetch_monthly_setting(frm) {

    if (
        frm.doc.purpose !== "Stock Reconciliation" ||
        !frm.doc.company ||
        !frm.doc.cost_center ||
        !frm.doc.posting_date
    ) {
        return;
    }

    // Preserve any actual values already entered.
    const old_actual_values = {};

    (
        frm.doc.custom_reconciliation_expenses ||
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

            frm.set_value(
                "custom_expected_per_qty_amount",
                flt(
                    data.expected_per_qty_amount
                )
            );

            frm.clear_table(
                "custom_reconciliation_expenses"
            );

            (
                data.expenses || []
            ).forEach(expense => {

                const row =
                    frm.add_child(
                        "custom_reconciliation_expenses"
                    );

                row.type_of_expense =
                    expense.type_of_expense;

                row.expected_expense_amount =
                    flt(
                        expense.expected_expense_amount
                    );

                // Manager requirement:
                // fetch Actual Expense Amount
                // from Monthly Production Setting initially.

                if (
                    Object.prototype
                        .hasOwnProperty
                        .call(
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
                "custom_reconciliation_expenses"
            );

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

    const expected_per_qty =
        flt(
            frm.doc
                .custom_expected_per_qty_amount
        );

    let total_adjustment = 0;

    (
        frm.doc
            .custom_reconciliation_expenses ||
        []
    ).forEach(row => {

        const actual_expense =
            flt(
                row.actual_expense_amount
            );

        // Manager Formula:
        //
        // Actual Per Qty Amount =
        // Actual Expense Amount / Actual Qty

        const actual_per_qty =
            actual_qty > 0
                ? (
                    actual_expense /
                    actual_qty
                )
                : 0;

        row.actual_per_qty_amount =
            actual_per_qty;

        // Manager Formula:
        //
        // Value After Calculation =
        // Actual Per Qty Amount
        // -
        // Expected Per Qty Amount

        const difference =
            actual_qty > 0
                ? (
                    actual_per_qty -
                    expected_per_qty
                )
                : 0;

        row.value_after_calculation =
            difference;

        total_adjustment +=
            difference;
    });

    frm.doc.custom_adjustment_per_qty =
        total_adjustment;

    frm.refresh_field(
        "custom_reconciliation_expenses"
    );

    frm.refresh_field(
        "custom_adjustment_per_qty"
    );

    calculate_item_valuation(frm);

    frm.dirty();
}


function set_original_valuation_rate(
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

    if (!row) {
        return;
    }

    if (
        row.custom_item_valuation_rate ===
            undefined ||
        row.custom_item_valuation_rate ===
            null ||
        row.custom_item_valuation_rate === ""
    ) {
        row.custom_item_valuation_rate =
            flt(
                row.current_valuation_rate ||
                row.valuation_rate
            );

        frm.refresh_field("items");
    }

    calculate_item_valuation(frm);
}


function calculate_item_valuation(frm) {

    if (
        frm.doc.purpose !==
        "Stock Reconciliation"
    ) {
        return;
    }

    const adjustment =
        flt(
            frm.doc.custom_adjustment_per_qty
        );

    (
        frm.doc.items || []
    ).forEach(row => {

        const qty =
            flt(row.qty);

        let original_rate =
            row.custom_item_valuation_rate;

        if (
            original_rate === undefined ||
            original_rate === null ||
            original_rate === ""
        ) {

            original_rate =
                flt(
                    row.current_valuation_rate ||
                    row.valuation_rate
                );

            row.custom_item_valuation_rate =
                original_rate;
        }

        original_rate =
            flt(original_rate);

        // ======================================
        // MANAGER FORMULA EXACTLY
        //
        // After Valuation Rate =
        // (
        //   Item Valuation Rate
        //   +
        //   Adjustment Per Qty
        // )
        // / Qty
        // ======================================

        let after_valuation_rate = 0;

        if (qty > 0) {
            after_valuation_rate =
                (
                    original_rate +
                    adjustment
                ) / qty;
        }

        // Standard ERPNext valuation field
        row.valuation_rate =
            after_valuation_rate;

        // Standard ERPNext amount
        row.amount =
            qty *
            after_valuation_rate;

        row.quantity_difference =
            qty -
            flt(row.current_qty);

        row.amount_difference =
            flt(row.amount) -
            flt(row.current_amount);
    });

    frm.refresh_field("items");
}
