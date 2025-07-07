
/** @odoo-module **/


import { registerPatch } from "@mail/model/model_core";

registerPatch({
    name: "ComposerView",
    recordMethods: {
        async postMessage() {
            const composer = this.composer;
            const originalPostData = structuredClone(this._getMessageData());
            const originalAttachmentIds = originalPostData.attachment_ids || [];

            const params = {
                'thread_id': composer.thread.id,
                'thread_model': composer.thread.model,
            };
            try {
                composer.update({ isPostingMessage: true });
                if (originalAttachmentIds.length > 0) {
                    for (let i = 0; i < originalAttachmentIds.length; i++) {
                        const postDataForThisMessage = { ...originalPostData };
                        postDataForThisMessage.attachment_ids = [originalPostData.attachment_ids[i]];
                        postDataForThisMessage.attachment_tokens = [originalPostData.attachment_tokens[i]];
                        params.post_data = postDataForThisMessage;
                        await this._sendMessage(params);
                    }
                } else {
                    params.post_data = originalPostData;
                    await this._sendMessage(params);
                }
                if (composer.exists()) {
                    composer._reset();
                }
            } catch (error) {
                if (composer.exists()) {
                    composer.update({ isPostingMessage: false });
                }
                throw error;
            } finally {
                if (composer.exists() && composer.isPostingMessage) {
                    composer.update({ isPostingMessage: false });
                }
            }
        },

        async _sendMessage(params) {
            const messaging = this.messaging;
            const messageData = await this.messaging.rpc({ route: `/mail/message/post`, params });
            if (!messaging.exists()) {
                return;
            }
            const message = messaging.models['Message'].insert(
                messaging.models['Message'].convertData(messageData)
            );
            for (const threadView of message.originThread.threadViews) {
                threadView.update({ hasAutoScrollOnMessageReceived: true });
                threadView.addComponentHint('message-posted', { message });
            }
        },
    }
});
