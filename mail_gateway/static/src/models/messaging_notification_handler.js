/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";

registerPatch({
    name: "MessagingNotificationHandler",
        recordMethods: {
            async _handleNotificationChannelMessage({ id: channelId, message: messageData }) {
            let channel = this.messaging.models['Channel'].findFromIdentifyingData({ id: channelId });
            if (!channel || !channel.channel_type) {
                const res = await this.messaging.models['Thread'].performRpcChannelInfo({ ids: [channelId] });
                if (!this.exists() || !res || res.length === 0 || !res[0].channel) {
                    return;
                }
            }
            this._super({ id: channelId, message: messageData });

        },
        },
});