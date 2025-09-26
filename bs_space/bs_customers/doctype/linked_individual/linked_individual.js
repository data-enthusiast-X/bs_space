// Copyright (c) 2025, Best Solution® and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Linked Individual", {
//     first_name: function(frm) {
//         frm.set_value("full_name", `${frm.doc.first_name || ""} ${frm.doc.last_name || ""}`.trim());
//     },
//     last_name: function(frm) {
//         frm.set_value("full_name", `${frm.doc.first_name || ""} ${frm.doc.last_name || ""}`.trim());
//     },

//     visa_type: function(frm) {
//         if (frm.doc.visa_type === "Dependent") {
//             frm.set_value("parent_type", "Linked Individual");
//         } else {
//             frm.set_value("parent_type", "Customer");
//         }
//     },

//     primary_address: function(frm) {
//         if (frm.doc.primary_address) {
//             frappe.db.get_doc("Address", frm.doc.primary_address).then(addr => {
//                 frm.set_value("email", addr.email_id || "");
//                 frm.set_value("phone", addr.custom_phone_number || "");
//             });
//         }
//     },

//     refresh: function(frm) {
//         // Dependents child table filter
//         frm.fields_dict["dependents"].grid.get_field("dependent").get_query = function() {
//             return {
//                 filters: {
//                     visa_type: "Dependent"
//                 }
//             };
//         };

//         // Owned Companies child table filter
//         frm.fields_dict["owned_companies"].grid.get_field("company").get_query = function() {
//             return {
//                 filters: {
//                     disabled: 0 // only active customers
//                 }
//             };
//         };
//     }
// });

// // Prevent duplicate dependents in the same parent
// frappe.ui.form.on("Dependents of Individual", {
//     dependent: function(frm, cdt, cdn) {
//         let row = frappe.get_doc(cdt, cdn);
//         let duplicate = frm.doc.dependents.filter(r => r.dependent === row.dependent);
//         if (duplicate.length > 1) {
//             frappe.msgprint(__("This dependent is already added."));
//             frappe.model.set_value(cdt, cdn, "dependent", "");
//         }
//     }
// });

// // Prevent duplicate companies in the same parent
// frappe.ui.form.on("Companies of Individual", {
//     company: function(frm, cdt, cdn) {
//         let row = frappe.get_doc(cdt, cdn);
//         let duplicate = frm.doc.owned_companies.filter(r => r.company === row.company);
//         if (duplicate.length > 1) {
//             frappe.msgprint(__("This company is already added."));
//             frappe.model.set_value(cdt, cdn, "company", "");
//         }
//     }
// });


// Linked Individual — Client Script

frappe.ui.form.on("Linked Individual", {
  // keep full_name in sync
  first_name(frm) { set_full_name(frm); },
  last_name(frm)  { set_full_name(frm); },

  onload(frm) {
    ensure_dynamic_link_target(frm);
    set_parent_type_from_visa(frm);
  },

  refresh(frm) {
    ensure_dynamic_link_target(frm);
    set_parent_type_from_visa(frm);
    set_child_table_filters(frm);
    set_visa_parent_query(frm);
  },

  // drive parent_type from visa_type
  visa_type(frm) {
    set_parent_type_from_visa(frm);
    set_visa_parent_query(frm);
  },

  // autofill email/phone from selected Address
  primary_address(frm) {
    if (!frm.doc.primary_address) return;
    frappe.db.get_doc("Address", frm.doc.primary_address).then(addr => {
      frm.set_value("email", addr.email_id || "");
      frm.set_value("phone", addr.custom_phone_number || addr.phone || addr.phone_no || "");
    });
  },
});

/* -----------------------------
   Child tables: de-dup guards
------------------------------*/

frappe.ui.form.on("Dependents of Individual", {
  dependent(frm, cdt, cdn) {
    const row = frappe.get_doc(cdt, cdn);
    const list = (frm.doc.dependents || []).filter(r => r.dependent === row.dependent);
    if (row.dependent && list.length > 1) {
      frappe.msgprint(__("This dependent is already added."));
      frappe.model.set_value(cdt, cdn, "dependent", "");
    }
  }
});

frappe.ui.form.on("Companies of Individual", {
  company(frm, cdt, cdn) {
    const row = frappe.get_doc(cdt, cdn);
    const list = (frm.doc.owned_companies || []).filter(r => r.company === row.company);
    if (row.company && list.length > 1) {
      frappe.msgprint(__("This company is already added."));
      frappe.model.set_value(cdt, cdn, "company", "");
    }
  }
});

/* -----------------------------
   Helpers
------------------------------*/

function set_full_name(frm) {
  frm.set_value("full_name", `${frm.doc.first_name || ""} ${frm.doc.last_name || ""}`.trim());
}

/**
 * Visa rule:
 *  - Dependent -> parent_type = "Linked Individual"
 *  - Others    -> parent_type = "Customer"
 * Also clear visa_parent when target doctype flips.
 */
function set_parent_type_from_visa(frm) {
  const is_dependent = (frm.doc.visa_type === "Dependent");
  const target = is_dependent ? "Linked Individual" : "Customer";
  if (frm.doc.parent_type !== target) {
    frm.set_value("parent_type", target);
    if (frm.doc.visa_parent) frm.set_value("visa_parent", null);
    frm.refresh_field("visa_parent");
  }
}

/** Ensure the Dynamic Link field points to the field that stores the target doctype name */
function ensure_dynamic_link_target(frm) {
  const f = frm.get_field && frm.get_field("visa_parent");
  if (f) {
    // For Dynamic Link fields, "options" must equal the fieldname that holds the target doctype
    frm.set_df_property("visa_parent", "options", "parent_type");
  }
}

/** Query for visa_parent (NO 'doctype' filter; target is decided by Dynamic Link options) */
function set_visa_parent_query(frm) {
  if (!frm.get_field("visa_parent")) return;

  frm.set_query("visa_parent", () => {
    if (frm.doc.parent_type === "Linked Individual") {
      // Sponsor must be a Linked Individual who is NOT a Dependent, and not self
      return {
        filters: {
          visa_type: ["!=", "Dependent"],
          name: ["!=", frm.doc.name || ""]
        }
      };
    }
    // parent_type = Customer
    return { filters: { disabled: 0 } }; // only active Customers
  });
}

/** Filters for child tables (guard grid presence) */
function set_child_table_filters(frm) {
  if (frm.fields_dict.dependents && frm.fields_dict.dependents.grid) {
    frm.fields_dict.dependents.grid.get_field("dependent").get_query = function () {
      // Dependents must be Linked Individuals with visa_type = Dependent
      return { filters: { visa_type: "Dependent" } };
    };
  }
  if (frm.fields_dict.owned_companies && frm.fields_dict.owned_companies.grid) {
    frm.fields_dict.owned_companies.grid.get_field("company").get_query = function () {
      // Companies are Customers; show only active ones
      return { filters: { disabled: 0 } };
    };
  }
}

frappe.ui.form.on('Linked Individual', {
  refresh(frm) {
    const f = frm.get_field('dependents');
    if (!f || !f.grid) return;

    // no add / delete from UI
    f.grid.cannot_add_rows = true;
    f.grid.cannot_delete_rows = true;

    // hide footer controls (Add Row, Add Multiple, etc.)
    f.grid.wrapper.find('.grid-footer, .grid-add-row, .grid-add-multiple-rows').hide();

    // hide per-row delete icons & insert controls
    f.grid.wrapper.find('.grid-remove-rows').hide();
    f.grid.wrapper.find('.grid-insert-row-below, .grid-insert-row').hide();

    // optional: prevent inline new-row prompt when empty
    f.grid.wrapper.find('.grid-empty').hide();
  }
});
