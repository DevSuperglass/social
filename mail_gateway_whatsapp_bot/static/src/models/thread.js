/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";
import { attr } from "@mail/model/model_field";

registerPatch({
    name: "Thread",
    recordMethods: {
        convertData(data) {
            const data2 = this._super(data);
            if ('attendance_type' in data) {
                data2.attendance_type = data.attendance_type;
            }
            return data2;
        },
    },
    fields: {
        attendance_type: attr({
            default: null,
        }),
    },
});
