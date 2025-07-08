/** @odoo-module **/
import { registerPatch } from "@mail/model/model_core";
import { clear, link } from '@mail/model/model_field_command';
import session from "web.session";

registerPatch({
    name: "ComposerView",
    recordMethods: {
        async postMessage() {
            const composer = this.composer;
            const postData = this._getMessageData();
            const params = {
                'post_data': postData,
                'thread_id': composer.thread.id,
                'thread_model': composer.thread.model,
            };
            try {
                composer.update({ isPostingMessage: true });
                if (composer.thread.model === 'mail.channel') {
                    Object.assign(postData, {
                        subtype_xmlid: 'mail.mt_comment',
                    });
                } else {
                    Object.assign(postData, {
                        subtype_xmlid: composer.isLog ? 'mail.mt_note' : 'mail.mt_comment',
                    });
                    if (!composer.isLog) {
                        params.context = { mail_post_autofollow: this.composer.activeThread.hasWriteAccess };
                    }
                }
                if (this.threadView && this.threadView.replyingToMessageView && this.threadView.thread !== this.messaging.inbox.thread) {
                    postData.parent_id = this.threadView.replyingToMessageView.message.id;
                }
                params.context = Object.assign(params.context || {}, session.user_context);
                const { threadView = {} } = this;
                const chatter = this.chatter;
                const { thread: chatterThread } = this.chatter || {};
                const { thread: threadViewThread } = threadView;
                // Keep a reference to messaging: composer could be
                // unmounted while awaiting the prc promise. In this
                // case, this would be undefined.
                if (postData.attachment_ids.lenght > 0) {
                    for (let i = 0; i < postData.attachment_ids.lenght; i++) {
                        const postDataAttachments = { ...postData };
                        postDataAttachments.attachment_ids = [postData.attachment_ids[i]];
                        postDataAttachments.attachment_tokens = [postData.attachment_tokens[i]];
                        params.postData = postDataAttachments;
                        await this._sendMessage(params, chatter, chatterThread, threadViewThread, composer, threadView);
                    }
                } else {
                    await this._sendMessage(params, chatter, chatterThread, threadViewThread, composer, threadView);
                }
            } finally {
                if (composer.exists()) {
                    composer.update({ isPostingMessage: false });
                }
            }
        },
        async _sendMessage(params,  chatter, chatterThread, threadViewThread, composer, threadView) {
            // This helper function contains the logic to send one message and handle its response.
            // This avoids duplicating the response-handling code inside the loop.
            const messaging = this.messaging;
            const messageData = await this.messaging.rpc({ route: `/mail/message/post`, params });

            // This part is mostly from your original code, to handle UI updates
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
            // ... include other logic from your original response handling if needed ...
        }

    }
});
