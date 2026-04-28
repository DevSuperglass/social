/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";

registerPatch({
    name: "DiscussSidebarCategory",
    fields: {
        filteredCategoryItems: {
            compute() {
                let categoryItems = this._super();
                categoryItems = categoryItems.filter(categoryItem => {
                    const thread = categoryItem.__values.get('thread');
                    if (!thread) return true;
                    if (thread.channel && thread.channel.channel_type === 'gateway') {
                        return thread.attendance_type === 'human';
                    }
                    return true;
                });
                return categoryItems;
            },
        },
    },
});
