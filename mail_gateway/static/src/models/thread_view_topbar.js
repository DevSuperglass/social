/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";
import { clear } from '@mail/model/model_field_command';

registerPatch({
    name: 'ThreadViewTopbar',
    recordMethods: {
        _applyThreadRename() {
            const newName = this.pendingThreadName;
            this.update({
                isEditingThreadName: false,
                pendingThreadName: clear(),
            });
            if (this.thread.channel.channel_type === 'gateway' && newName !== this.thread.name && newName) {
                this.thread.rename(newName);
            }
            if (this.thread.channel.channel_type === 'chat' && newName !== this.thread.channel.custom_channel_name) {
                this.thread.setCustomName(newName);
            }
            if (newName && this.thread.channel.channel_type === 'channel' && newName !== this.thread.name) {
                this.thread.rename(newName);
            }
            if (this.thread.channel.channel_type === 'group' && newName !== this.thread.name) {
                this.thread.rename(newName);
            }
        },
    },
});