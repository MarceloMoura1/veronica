import { chatMessage, mergeChatMessages } from './chatHistory.mjs';

export function applyTranscriptionEvent(messages, event) {
    if (!event || !['user', 'assistant'].includes(event.role)) return messages;
    if (!event.message_id || !['input_transcription', 'output_transcription'].includes(event.source)) return messages;
    const existing = messages.find(message => message.id === event.message_id);
    if (existing && existing.role !== event.role) return messages;
    if (existing && !existing.streaming && !event.final) return messages;
    const next = event.message ? chatMessage(event.message) : chatMessage({
        id: event.message_id, role: event.role, content: event.text || '',
        timestamp: event.timestamp, source: event.source, streaming: !event.final,
        sequence: event.sequence,
    });
    return mergeChatMessages(messages, [next]);
}
