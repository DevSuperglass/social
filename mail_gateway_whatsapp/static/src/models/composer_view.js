/** @odoo-module **/
import { registerPatch } from "@mail/model/model_core";
import { clear, link } from '@mail/model/model_field_command';
import session from "web.session";

registerPatch({
    name: "ComposerView",
    recordMethods: {
        async postMessage() {
            const composer = this.composer;
            const originalPostData = this._getMessageData();

            const params = {
                'thread_id': composer.thread.id,
                'thread_model': composer.thread.model,
            };

            try {
                composer.update({ isPostingMessage: true });

                if (composer.thread.model === 'mail.channel') {
                    Object.assign(originalPostData, { subtype_xmlid: 'mail.mt_comment' });
                } else {
                    Object.assign(originalPostData, { subtype_xmlid: composer.isLog ? 'mail.mt_note' : 'mail.mt_comment' });
                    if (!composer.isLog) {
                        params.context = { mail_post_autofollow: this.composer.activeThread.hasWriteAccess };
                    }
                }
                if (this.threadView && this.threadView.replyingToMessageView) {
                    originalPostData.parent_id = this.threadView.replyingToMessageView.message.id;
                }
                params.context = Object.assign(params.context || {}, session.user_context);

                const { threadView = {} } = this;
                const chatter = this.chatter;
                const { thread: chatterThread } = this.chatter || {};
                const { thread: threadViewThread } = threadView;

                const messaging = this.messaging;
                const originalAttachmentIds = originalPostData.attachment_ids || [];
                const originalAttachmentTokens = originalPostData.attachment_tokens || [];

                const messageDatas = [];

                if (originalAttachmentIds.length > 0) {
                    for (let i = 0; i < originalAttachmentIds.length; i++) {
                        const postDataForThisMessage = { ...originalPostData };
                        postDataForThisMessage.attachment_ids = [originalAttachmentIds[i]];
                        postDataForThisMessage.attachment_tokens = [originalAttachmentTokens[i]];
                        if (i > 0) {
                            postDataForThisMessage.body = "";
                        }
                        params.post_data = postDataForThisMessage;
                        const messageData = await messaging.rpc({ route: `/mail/message/post`, params });
                        messageDatas.push(messageData);
                    }
                } else {
                    params.post_data = originalPostData;
                    const messageData = await messaging.rpc({ route: `/mail/message/post`, params });
                    messageDatas.push(messageData);
                }

                for (const messageData of messageDatas) {
                    const message = messaging.models['Message'].insert(
                        messaging.models['Message'].convertData(messageData)
                    );
                    if (messaging.hasLinkPreviewFeature && !message.isBodyEmpty) {
                        messaging.rpc({
                            route: `/mail/link_preview`,
                            params: {
                                message_id: message.id
                            }
                        }, { shadow: true });
                    }
                    for (const threadView of message.originThread.threadViews) {
                        threadView.update({ hasAutoScrollOnMessageReceived: true });
                        threadView.addComponentHint('message-posted', { message });
                    }
                    if (chatter && chatter.exists() && chatter.hasParentReloadOnMessagePosted && messageData.recipients.length) {
                        chatter.reloadParentView();
                    }
                    if (chatterThread) {
                        if (this.exists()) {
                            this.delete();
                        }
                        if (chatterThread.exists()) {
                            chatterThread.fetchData(['followers', 'messages', 'suggestedRecipients']);
                        }
                    }
                    if (threadViewThread) {
                        if (threadViewThread === messaging.inbox.thread) {
                            messaging.notify({
                                message: sprintf(messaging.env._t(`Message posted on "%s"`), message.originThread.displayName),
                                type: 'info',
                            });
                            if (this.exists()) {
                                this.delete();
                            }
                        }
                        if (threadView && threadView.exists()) {
                            threadView.update({ replyingToMessageView: clear() });
                        }
                    }
                }

                if (composer.exists()) {
                    composer._reset();
                }

            } finally {
                if (composer.exists()) {
                    composer.update({ isPostingMessage: false });
                }
            }
        },

    }
});
