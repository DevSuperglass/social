/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";

registerPatch({
    name: "MessagingNotificationHandler",
    recordMethods: {
        async _handleNotificationPinUnknowingChannel({ id, isServerPinned, last_interest_dt, attendance_type }) {
            let channel = this.messaging.models['Thread'].findFromIdentifyingData({
                id: id,
                model: 'mail.channel',
            });

            if (!channel) {
                channel = await this._handleNotificationGetUnknowingChannel({ id });
                if (channel) {
                    channel.pin();
                    if (attendance_type) {
                        channel.update({ attendance_type });
                    }
                }
                return;
            }

            if (attendance_type) {
                channel.update({ attendance_type });
            }

            this._handleNotificationChannelLastInterestDateTimeChanged({
                id,
                isServerPinned,
                last_interest_dt,
            });
        },
    },
});
